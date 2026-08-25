# Expanding the program area of Family BASIC

[日本語](README.ja.md)

Tools and notes for raising the free-area limit of Family BASIC (NS-HuBASIC, Famicom)
from 4KB to **8KB**, and then to **16KB**.

| Version | Stock free area | Expanded | What it takes |
|---|---|---|---|
| V1.0 / V2.0A / V2.1A | ~2KB | **4KB** | 2 PRG bytes + 1 header byte |
| V3.0 | 4KB | **8KB** | 5 PRG bytes + 1 header byte |
| V3.0 | 4KB | **16KB** | MMC5 conversion + relocating the BASIC interpreter |
| V2.1A / V3.0 | 2KB / 4KB | **8KB, on a disk** | rebuilt as a Famicom Disk System image |

**The 16KB build is confirmed working on both the MiSTer FPGA NES core and an
EverDrive N8 PRO** (`16374 BYTES FREE`; a 9,816-byte program `LIST`s and `RUN`s).

> ⚠️ **No ROM data is included.** Supply your own Family BASIC `.nes` image.
> This repository ships Python scripts and documentation only.

## Documentation

Three kinds, in three directories, kept apart on purpose.

**[docs/manual/](docs/manual/)** — to use a build:

- [building.md](docs/manual/building.md) — which one to make, how to make it, getting a program in from a PC
- [disk-basic.md](docs/manual/disk-basic.md) — `SAVE` and `LOAD` on a disk build, and what the error numbers mean

**[docs/reference/](docs/reference/)** — to change one; read before you write, because getting
these wrong breaks quietly:

- [build-differences.md](docs/reference/build-differences.md) — what each build changes, and what it does not
- [ram-expansion.md](docs/reference/ram-expansion.md) — which bytes get rewritten, and why that is enough
- [sav-format.md](docs/reference/sav-format.md) — how a BASIC program is stored
- [token-numbering.md](docs/reference/token-numbering.md) — the rules for adding a keyword
- [mmc5-wram-banks.md](docs/reference/mmc5-wram-banks.md) — why an MMC5 bank number cannot be hard-coded

**[docs/background/](docs/background/)** — why things are the way they are; needed for neither
of the above:

- [area-ceiling.md](docs/background/area-ceiling.md) — why this stops at 16KB, and what the next step would cost
- [relocation-notes.md](docs/background/relocation-notes.md) — the traps hit while relocating 8KB of code

## Why it expands at all

**The amount of RAM BASIC uses is set by a constant burned into the ROM, not by how
much RAM is present.** Even when the hardware provides the full 8KB at `$6000-$7FFF`,
BASIC only touches its hard-coded range and the rest simply does not exist as far as
BASIC is concerned. So **changing a constant** widens the area (3 bytes in total for V1/V2, 6 for V3 —
the `CLEAR` ceiling and, on V3, the `BGGET` buffer are separate constants).

How far you can go depends on where the area *starts*:

- V1/V2 start at `$7000` → extending the end to `$7FFF` caps out at **4KB**
- V3 starts at `$6000` → extending to `$7FFF` gives **8KB** (V3 is the roomiest)

Going past 8KB means putting RAM at `$8000` and above — normally PRG-ROM territory —
which requires **bank switching (MMC5)**. It also evicts the 8KB BASIC interpreter that
lives at `$8000-$9FFF`, so **the interpreter has to be relocated wholesale**.

## MMC5 is not required below 16KB

A common misconception: **mapper 0 (NROM) does not mean "2KB of RAM max."** It only
means there is no bank-switching hardware; nothing stops 8KB of real RAM from sitting
in the `$6000-$7FFF` window. Real carts have 2KB (V1/V2) or 4KB (V3) because of the
**SRAM chip that was actually fitted** — the rest of the window is just mirrored.

So **on anything that honours the header's RAM declaration (emulators, FPGA cores,
flash carts), mapper 0 is enough all the way to 8KB.**

```bash
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"
```

This changes 5 PRG bytes for V3 (2 for V1/V2, which have no `BGGET`) plus byte 10 of
the iNES header (the NVRAM size declaration).

| Goal | Change | Measured on hardware |
|---|---|---|
| V1.0 → 4KB | 2 PRG bytes + 1 header byte | `4031 BYTES FREE` |
| V2.1A → 4KB | 2 PRG bytes + 1 header byte | `4030 BYTES FREE` |
| V3.0 → 8KB | 5 PRG bytes + 1 header byte | `8182 BYTES FREE` |

(Measured on an EverDrive N8 PRO. `BYTES FREE` is the figure with no program loaded.)

## The 16KB build (V3 only)

```bash
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"
```

The result is an MMC5 (mapper 5) ROM: PRG 64KB / CHR 8KB / NVRAM 16KB.

| CPU address space | Contents |
|---|---|
| `$6000-$7FFF` | WRAM block 0 — lower half of the free area |
| `$8000-$9FFF` | **A second WRAM block** — upper half (bank number probed at boot) |
| `$A000-$BFFF` | ROM bank 5 (always resident; patched in place — see [details](docs/reference/ram-expansion.md)) |
| `$C000-$DFFF` | ROM bank 6 (original `$C000-$CFFF` + first half of the relocated interpreter) |
| `$E000-$FFFF` | ROM bank 7 (second half + title graphic + init + loader + vectors) |
| Banks 0-3 | One built-in program each (lower half duplicates `$C000-$CFFF`) |

**All four built-in programs (`GAME 0`–`GAME 3`) still work**, including the `GAME 2,1`
background graphic — verified on hardware.

⚠️ **There is nothing between 8KB and 16KB.** RAM is mapped in 8KB units, so 12KB is not
an option.

See [docs/reference/ram-expansion.md](docs/reference/ram-expansion.md) for the full write-up.

## The disk build

```bash
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
./tools/fb-fds.py "Family BASIC V3 (Japan).nes"      --bios disksys.rom -o fcbasic3.fds
```

A Famicom Disk System image with **8,126 bytes free from V2.1A** and **8,182 from V3** -
and `SAVE` and `LOAD` that go to the disk, in the syntax that is already there:

    SAVE "NAME"        to the disk            LOAD "NAME"
    SAVE "DSK:NAME"    the disk, said aloud   LOAD "DSK:NAME"
    SAVE "CAS:NAME"    the cassette           LOAD "CAS:NAME"

**Not the roomiest build here** - the MMC5 one reaches 16,374 bytes. What the disk buys is
the medium: CHR is RAM, so characters can be rewritten while a program runs; the FDS sound
channel; and saving without a cassette. Choose it for those, not for space.

**The save routines cost nothing.** They sit over dialogue text in the built-in
conversation program, which cannot be reached on a disk build - `tools/fb-reach.py` is the
argument, and it is a tool rather than a claim: it answers "can this address ever run?" by
following the branch tables only where a reading instruction reaches them.

A disk carries one program. `SAVE` under a new name replaces it. `tools/fb-fds-file.py`
reaches inside a used disk from a PC, so a program typed on hardware can be pulled out as
text and kept in version control.

What to type on a disk build, and what the error numbers mean, are in the
[manual](docs/manual/building.md). The three builds side by side, and what each gives up, are in
[docs/reference/build-differences.md](docs/reference/build-differences.md).

## The MMC5 gotcha this work turned up

**The meaning of a bank number written to `$5114` differs between boards and
implementations.** In MMC5's PRG bank register, **bit 2 doubles as both "A15" and
"which RAM chip's /CE"** (NESdev wiki: `RAAA AaAA`), so the same number can land
somewhere else entirely.

| | `$5114 = $01` | `$5114 = $04` |
|---|---|---|
| Real ETROM (2 × 8KB) / EverDrive N8 PRO | still chip 0 = **a mirror of `$6000-$7FFF`** | chip 1 = a distinct 8KB ✅ |
| MiSTer NES core | block 1 ✅ | block 4 (falls outside the 32KB `.sav`) |
| Real EWROM (32KB, one chip) | second 8KB ✅ | open bus |

No commercial MMC5 game exercises this path (16KB of WRAM as two 8KB chips), so an
implementation can be missing it without anyone noticing. Hence **`fb-mmc5-16k.py`
stops guessing and probes at boot**: it writes through `$01`, reads back a marker in
`$6000-$7FFF`, and falls back to `$04` if the marker was clobbered (i.e. it was a mirror).

Anyone trying to use more than 8KB of WRAM on MMC5 will hit the same thing.
Details: [docs/reference/mmc5-wram-banks.md](docs/reference/mmc5-wram-banks.md).

## Getting programs in from a PC

BASIC source written as plain text converts straight to a `.sav` (battery-backed WRAM image):

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3, 8KB build
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3, 16KB build (32KB .sav)
```

The token table is re-read from the ROM and cross-checked. The self-test decodes all four
built-in programs (387 lines, 8,999 bytes) and re-encodes them, demanding a byte-exact match:

```bash
./tools/fb-basic-to-sav.py --selftest "Family BASIC V3 (Japan).nes"
```

Storage format: [docs/reference/sav-format.md](docs/reference/sav-format.md).

## The tools

| File | What it does |
|---|---|
| `tools/fb-expand-basic-area.py` | Rewrites the area-limit constants (up to 8KB; mapper unchanged) |
| `tools/fb-relocate.py` | Moves V3's interpreter from `$8000-$9FFF` to `$D000` and up |
| `tools/fb-mmc5-16k.py` | Builds the 16KB MMC5 ROM from the relocated image |
| `tools/fb-fds.py` | Builds the Famicom Disk System image, with disk `SAVE`/`LOAD` (V2.1A and V3) |
| `tools/fb-fds-file.py` | Reads and writes the saved program inside a used disk image, from a PC |
| `tools/fb-reach.py` | Answers "can this address ever run on the disk build?" soundly |
| `tools/fb-basic-to-sav.py` | Text BASIC → `.sav`, with a ROM-driven self-test |
| `tools/fb-disasm.py` | Recursive-descent disassembler; used to enumerate every address reference |
| `tools/fb-gen-bigtest.py` | Generates a test program large enough to fill the free area, plus its expected output |

Python 3 standard library only; no dependencies.

## Verified on

| Hardware | 8KB build | 16KB build |
|---|---|---|
| MiSTer FPGA (NES core) | ✅ | ✅ `16374 BYTES FREE` |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ |
| Unmodified original cart | ❌ mirrors back | ❌ |

⚠️ **Useless on an unmodified cart** — its 2KB/4KB SRAM just mirrors. These patches only
matter on hardware that provides RAM as declared in the header.

⚠️ **On hardware that genuinely has only 8KB of WRAM, the 16KB build's boot probe finds
nothing to switch to** and BASIC proceeds as if it had 16KB. Handling that would need a
further patch turning the "end of area" constant into a RAM reference.

## Input ROMs

The tools verify their input before doing anything (`fb-relocate.py` pins the PRG+CHR
SHA-256, all three vectors, and the positions of undefined opcodes).

| Version | ROM MD5 | NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

The 16KB path takes **V3.0 only** (PRG+CHR SHA-256
`c8c0b6c21bdda7503bab7592aea0f945a0259c18504bb241aafb1eabe65846f3`).

## Prior art

- **Makimura Seisakusho, "MMC5 BASIC v0.9β2"** — got to 16KB first (PRG 128KB, CHR RAM 8KB).
  **The distribution site is gone**; only
  [the description page survives on archive.org](https://web.archive.org/web/20221116224414/http://rdev.php.xdomain.jp/makimura/archive/family-basic/mmc5-basic).
  This project fits in PRG 64KB, keeps CHR as ROM, and keeps all four built-in programs
- **"ファミベの改造" (nigaMSX)** `http://niga2.sytes.net/msx/famibe.html` — independently
  documents that patching `$8570` in V2.1A from `$77` to `$7F` yields `4030 BYTES FREE`,
  matching what was derived here
- **[micahcowan/fbdasm](https://github.com/micahcowan/fbdasm)** — a disassembly of V3
- **ファミコン改造マニュアル Vol.2 / Vol.3** (三才ブックス, 1988; article by 熊沢文幸) —
  published the disk port as a manual procedure
- **[TakuikaNinja/FC-DiskBASIC](https://github.com/TakuikaNinja/FC-DiskBASIC)** — automates
  that procedure with the CC65 suite. Reading it is what showed the disk build was worth
  doing. `fb-fds.py` is a rewrite rather than a port and carries none of its code; what is
  deliberately not used is listed in that file
- **[NipponNoraneko/fdsbasicV3](https://github.com/NipponNoraneko/fdsbasicV3)** — V3 on the
  FDS with disk commands of its own. Its patch positions agree with the addresses measured
  here, which is useful corroboration in both directions

## License

Tools and documentation: MIT ([LICENSE](LICENSE)). **No ROM data is included.**
Family BASIC (NS-HuBASIC) is copyright Nintendo / SHARP / Hudson Soft.
