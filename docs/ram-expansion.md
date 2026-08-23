# Expanding the program area — details

[日本語](ram-expansion.ja.md)

What exactly gets rewritten, and why that is enough. For usage, see the
[README](../README.md).

## 1. The limit is not set by how much RAM is present

At boot, Family BASIC stores the **address of the last byte of the program area** into
zero page `$03/$04`. That value is an immediate operand burned into the ROM, so
**it makes no difference how much RAM the hardware actually provides.**

The `BYTES FREE` figure and the ceiling `CLEAR` will accept both come from those two
bytes. Rewriting them is all it takes.

There are two sites:

1. **Boot-time init** — the immediate loaded into zero page `$03/$04`
2. **The `CLEAR` argument check** — a hard-coded "error if at or above this address" ceiling

⚠️ **V1.0 alone treats `$03/$04` as "the address *after* the last byte"** (`$7800`).
V2/V3 store the address of the last byte itself (`$77FF` / `$6FFF`). Tell them apart by
whether `LDA #$FF / STA $03` precedes it. `fb-expand-basic-area.py` detects this
automatically.

| Version | Stock area | Expanded |
|---|---|---|
| V1.0 / V2.0A / V2.1A | `$7000-$77FF` (2KB) | `$7000-$7FFF` (4KB) |
| V3.0 | `$6000-$6FFF` (4KB) | `$6000-$7FFF` (8KB) |

**The ceiling differs by version because the area *starts* in a different place.**
V1/V2 start at `$7000`, so without moving the start, extending to `$7FFF` gives 4KB.
V3 starts at `$6000`, so it reaches 8KB.

### One header byte changes too

Rewriting PRG accomplishes nothing if the hardware does not provide the RAM. In the
NES 2.0 header, **the high nibble of byte 10** is the NVRAM size
(`5`=2KB / `6`=4KB / `7`=8KB / `8`=16KB).

| Change | Header byte 10 |
|---|---|
| V1/V2 → 4KB | `$50` → `$60` |
| V3 → 8KB | `$60` → `$70` |

So the **total change is 2 PRG bytes plus 1 header byte.**

`fb-expand-basic-area.py` writes all three (doubling the declaration). Pass
`--keep-header` to leave the header alone — but **that ROM will have no effect on real
hardware.**

### ⚠️ What not to touch

**Do not extend the end address of the loop near `$ADC8` in V3 that copies a built-in
program from ROM to fill the area.** Extending it makes the source pointer run past the
end of ROM, and address wraparound can reach registers with side effects (PPU).
The end of the area is tracked through `$03/$04`, so there is no need to touch this loop.

## 2. Why MMC5 is not needed below 8KB

The 8KB at `$6000-$7FFF` is **a window that was always available for a cartridge to put
RAM in.** Mapper 0 (NROM) merely lacks bank-switching hardware; it does not prevent real
RAM from living in that window.

Real carts carry 2KB (V1/V2) or 4KB (V3) because of the SRAM chip that was fitted;
the rest of the window mirrors:

- V2's 2KB → four aliases: `$6000-$67FF` / `$6800-$6FFF` / `$7000-$77FF` / `$7800-$7FFF`
- V3's 4KB → two aliases: `$6000-$6FFF` / `$7000-$7FFF`

So **on hardware that provides RAM as declared in the header, mapper 0 reaches 8KB.**
Conversely, **the expanded builds are meaningless on an unmodified cart** — the aliases
fold back onto the same storage.

| Goal | What it takes |
|---|---|
| Boot V1/V2 on an EverDrive | **just add the header** (a missing header is what causes `Unformatted ROM`) |
| V1.0 → 4KB | 2 PRG bytes + 1 header byte |
| V2.1A → 4KB | 2 PRG bytes + 1 header byte |
| V3 → 8KB | 2 PRG bytes + 1 header byte |
| **V3 → 16KB** | **MMC5** (map RAM into `$8000-$9FFF` as well) |

⚠️ `BYTES FREE` is the figure **with no program loaded**. With a `.sav` in place
(`BASIC HOT START`) it drops by the size of the program. Compare like with like.

## 3. The 16KB build — relocating the interpreter

Once the 8KB window at `$6000-$7FFF` is used up, the only place left is `$8000` and
above. That is normally PRG-ROM, so **a mapper with bank switching (MMC5) is required** —
and **the 8KB BASIC interpreter living at `$8000-$9FFF` loses its home.**

The only space that can be freed is where the **four built-in programs** (a music demo
and three games) sit, at `$D400-$FFF9`. So the interpreter moves there and the built-in
programs get pushed out into banks.

⚠️ **There is nothing between 8KB and 16KB.** RAM maps in 8KB units, so 12KB is not an
option.

### Memory map after power-on

| CPU address space | Contents |
|---|---|
| `$6000-$7FFF` | WRAM block 0 (lower half of the free area) |
| `$8000-$9FFF` | **A second WRAM block** (upper half; bank number probed at boot → [mmc5-wram-banks.md](mmc5-wram-banks.md)) |
| `$A000-$BFFF` | ROM bank 5 (untouched, byte for byte) |
| `$C000-$DFFF` | ROM bank 6 (original `$C000-$CFFF` + first half of the relocated interpreter) |
| `$E000-$FFFF` | ROM bank 7 (second half + title graphic + init + loader + vectors) |
| Banks 0-3 | One built-in program each (lower half duplicates `$C000-$CFFF`) |

### `$5117` never has to be written

MMC5's `$5117` powers up as `$FF` — the last bank of ROM. **The init code and the vectors
were placed in that last bank (bank 7)**, so the right thing is visible from the instant
power comes up.

That sidesteps the classic MMC5 trap entirely: "the moment you write `$5117`, the bank
holding the code you are currently executing is swapped out from under you."

### Loading a built-in program

**Each program gets its own 8KB bank**, placed in the upper half (`$D000-$DFFF`).
The load loop keeps reading until the destination reaches `$7000` — at most 4,090 bytes —
and 4,090 < 4,096, so **it never runs off the end of the bank**: no cross-bank handling
is needed.

The lower half of each bank duplicates `$C000-$CFFF`. Since `$5116` swaps in 8KB units,
duplicating it means **only `$D000-$DFFF` disappears during the swap.**

⚠️ **NMI is disabled for the duration of the swap**, because the body of the NMI handler
(`$D971` after relocation) lives in the `$D000-$DFFF` that vanishes.

⚠️ **Re-enable with `LDA $32 / ORA #$80 / STA $2000`.** `$32` is BASIC's shadow copy of
`$2000`, but it **does not carry bit 7 (NMI enable)**. `LDA $32 / STA $2000` leaves NMI
off, and both the display and the keyboard go dead (observed once on hardware).

As a bonus, the original `GAME` hazard — reading past the end of ROM, wrapping toward
`$0000` and hitting PPU/APU registers — is gone in the 16KB build, because everything
stays within a bank.

### The `.sav` becomes 32KB

The 16KB build's `.sav` is **32KB** and is not compatible with the 8KB build's.
Block 0 maps to `$6000-$7FFF` and block 1 to `$8000-$9FFF`.

(On the MiSTer NES core this is because `save_sz` becomes 32KB whenever
`prg_nvram != 7`. `./tools/fb-basic-to-sav.py -V v3 --16k` picks the size for you.)

## 4. What the relocation had to fix

Moving `$8000-$9FFF` elsewhere means every value pointing into it has to be corrected.
**Full counts, measured on the actual V3 ROM:**

| Kind | Count | How they were found |
|---|---|---|
| Absolute `JSR`/`JMP`/data references | 860 | operands of disassembled instructions |
| Relative branches | 479 | **left alone** (the range was extended so targets move too) |
| Jump-table entries | 4 tables | the `LDA tbl,X / STA $19 / LDA tbl+1,X / STA $1A / JMP ($0019)` shape |
| Addresses built from two immediates | 2 | `LDA #<lo / STA zp / LDA #>hi / STA zp+1` |
| Reset / IRQ vectors | 2 | `$FFFC` / `$FFFE` |

⚠️ **Only part of the ROM is statically reachable**, so searching for "byte pairs that
look like `$80xx-$9Fxx`, therefore addresses" **will corrupt data**. The traps actually
hit are collected in [relocation-notes.md](relocation-notes.md).

`fb-relocate.py` **refuses to run unless the input is a stock V3** (it pins the PRG+CHR
SHA-256, all three vectors, the positions of undefined opcodes, and the entry points of
code fragments).

## 5. How it was verified

"No error appeared" is not verification. Three things were used here.

- **The model execution built into `fb-mmc5-16k.py`** — a minimal model of the 6502 and
  MMC5 bank switching that **actually runs the boot probe that was assembled**
  (`WRAM_MODELS`). It swaps the bank-number interpretation between `linear` / `chip` /
  `only8k` and prints how each one turns out on every build.
  The power-on values of `$5114-$5116` are **undefined**, so "unset" is treated as poison:
  referencing `$8000-$DFFF` before setting them fails on the spot
- **`fb-basic-to-sav.py --selftest`** — decodes the four built-in programs (387 lines,
  8,999 bytes) and re-encodes them, demanding a **byte-exact** match.
  The point is to **keep the ground truth out of my own understanding**
- **Screen-by-screen comparison against the stock ROM** — relocation defects show up as
  *rarely taken paths silently breaking*. So the test is not "it ran" but
  "**it produces the same screen as an unmodified V3**". That is what `tests/basic/`
  is for. Since a stock V3 only has 4KB free, anything larger is checked with
  `fb-gen-bigtest.py` instead (a program whose answer can be computed independently)

This mattered: **multi-model review turned up two real defects in a ROM already running
on hardware.** Both produced output that looked correct, passed every static check, and
did not reproduce during ordinary use. → [relocation-notes.md](relocation-notes.md)
