# What each build changes, and what it does not

*For anyone changing a build, or writing a program that has to run on more than one of
them. To just use a build, read the [manual](../manual/building.md) instead.*

[日本語](build-differences.ja.md)

Family BASIC ships as a cartridge. This repository rewrites that cartridge into several
builds, and they do not behave the same. This file is the list of what actually differs,
so that a program written on one and a habit learned on one do not quietly break on
another.

## No build adds a command

**Not one keyword has been added.** Every build answers exactly the reserved words its
stock ROM answered, and every one of those words still has the number it always had.

That is deliberate. A keyword's number is a file format — it is the byte that ends up in a
`.sav`, on a cassette and on a disk. What that costs is precise, and worth being precise
about: **changing the number a word already has, or giving a new word a number some other
word already claims, turns every program ever saved into a different program.** Putting a
new word on a number nothing uses does not — the reserved-word table carries an explicit
token byte per entry, so entries after the new one keep the numbers they had. The
positional table is the *dispatch* table, and that is where an insertion in the middle
moves every later handler out of step. The rules that keep this true are in
[token-numbering.md](token-numbering.md), and its registry of additions is empty.

What changes instead is what some existing words **do**. There are two such words, and
both are on the disk build: `SAVE` and `LOAD`.

## The builds

| | NROM | MMC5 | Disk BASIC (FDS) | VRC7 |
|---|---|---|---|---|
| mapper | 0 | 5 | — (Famicom Disk System) | 85 |
| base ROM | V3 (also V1/V2) | V3 | **V2.1A or V3** | V3 |
| program area | V3 `$6000-$7FFF`, V1/V2 **`$7000-$7FFF`** | `$6000-$9FFF` | `$6000-$7FFF` | `$6000-$7FFF` expanded, `$6000-$6FFF` from an unexpanded dump |
| area size | 8KB on V3, **4KB on V1/V2** | 16KB | 8KB | 8KB, or 4KB from an unexpanded dump |
| `BYTES FREE` with nothing loaded | V3 `8182`, V1 `4031`, V2.1A `4030` | `16374` | `8126` from V2.1A, `8182` from V3 | `8182`, expanded first |
| `SAVE` / `LOAD` | cassette, unchanged | cassette, unchanged | **the disk** | cassette, unchanged |
| `BGGET` / `BGPUT` buffer | **V3 only**: `$7C00-$7FFF`. V1/V2 have neither word | **`$9C00-$9FFF`** | **V3 only**: `$7C00-$7FFF`, as on the cartridge. V2.1A has neither word | it moves with the area, as on the cartridge: `$7C00-$7FFF` expanded, **`$6C00-$6FFF`** from an unexpanded dump |
| reachable by `POKE` | nothing extra | CHR banks, extended attributes, scanline IRQ, two extra square waves + PCM, the multiplier. **With `--chr-ram`, the characters as well**: that build declares CHR-RAM, carries the picture in a spare PRG bank and copies it out at power-on, so a running program can rewrite it | **the FDS sound channel** (and CHR is RAM, so characters can be rewritten while a program runs) | **FM sound** |
| status | done, hardware-verified | done, hardware-verified (`--chr-ram`: emulator, MiSTer, and a Famicom through an EverDrive N8 PRO - but **no MMC5 cartridge ever had character RAM**, so any other machine is untried and it is never the default) | done, hardware-verified | done, hardware-verified (the FM was **listened to**; no machine here can hear it) |

The area size and the `BYTES FREE` figure are **different numbers** and the table keeps
them apart: the display subtracts the few bytes BASIC keeps for itself.

**V1 and V2 start their area at `$7000`**, so widening it without moving the start stops
at 4KB. V3 starts at `$6000` and reaches 8KB. [ram-expansion.md](ram-expansion.md) has the
detail.

### What the 16KB build had to fix to keep `SAVE` working

The table says `SAVE` on the MMC5 build is the cassette, unchanged. Keeping it that way
took a patch, because widening the area past `$8000` broke it.

`SAVE` writes the program's length into the cassette header, and reached that length
through BASIC's general-purpose **signed** 16-bit subtract, which refuses an operand with
bit 15 set. While the area ended at `$6FFF` or `$7FFF` no address could have that bit. At
16KB the end of a large program does, and **`SAVE` alone** fails with `?OV ERROR`: `RUN`
still works and `BYTES FREE` is still right, so nothing shows until the program is written
out. It is a boundary and not a size — measured on hardware, a 7,607-byte program ending
below `$8000` saves and a larger one does not.

The builder patches **the caller, not the shared subtract**: making the subtract unsigned
would change every other subtraction in BASIC, so the length is computed inline instead,
in the 21 bytes already there. A 16,029-byte program has since gone out to a real data
recorder and come back — `SAVE`, power cycle, `LOAD`, `RUN`, answers matching.

**The NROM build never had this.** Its area ends at `$7FFF`, so the sign bit is out of
reach.

### What "done" means for the disk build

`SAVE` and `LOAD` were confirmed on a real Famicom (RAM adapter + FDSKey) by typing them
in: save, clear, load, `LIST`, and the program came back.

**The current image has been through the whole sequence on hardware** (2026-08-25, MiSTer,
no hands): boot to the banner, `SAVE "HWTEST"` typed in, BIOS error byte zero, `LOAD` back,
`BG GRAPHIC`, the disk fetched off the SD card and checked byte by byte — the file count
went 3 to 4, the new header is `03 03 10 HWTEST`, it loads at `$603A`, the signature and
end pointer agree with its length, and **31 bytes changed in all**. Then a power cycle:
the banner again, the program not restored, and back when `LOAD` asks.

**What has still never been done is writing to a real disk through a drive**, as opposed
to the RAM adapter: no round trip here has turned a disk motor.

Saying so matters more than the table looking finished. The rule here is that "no error
appeared" is not a verification, and "verified on hardware" is a claim about a specific
image.

The PC-side tool has an oracle of its own: extract the program from a disk a real Famicom
wrote, insert it again, and the disk is **byte-for-byte identical** to the one the hardware
produced. That is stronger than "it printed something plausible" — it means the tool builds
the same bytes the machine does.

### The word set differs before any of this

V3's reserved-word table holds 109 entries, V2.1A's 88 — counting everything in the
table, operators (`XOR`…`*`, 15 of them) included; 94 and 73 without them. Either way the
difference is the same 21 words, which V3 adds: `AUTO` `BACKUP`
`BGGET` `BGPUT` `BGTOOL` `CAN` `CLICK` `CRASH` `DELETE` `ERL` `ERR` `ERROR` `FILTER`
`FIND` `GAME` `INSTR` `RENUM` `RESUME` `SCREEN` `TR` `VCT`, and V2.1A has nothing V3
lacks.

**The disk can be built from either version**, so which of those 21 words are available
depends on the dump it was built from, not on the disk:

- **from V2.1A** — none of the 21, `GAME` and `BGGET`/`BGPUT` included. That is a property
  of the base ROM, not something the disk build removed
- **from V3** — all of them except `GAME`, which the disk build patches out because the
  built-in programs live in `$E000-$FFFF` and that is the BIOS on a disk; typing it raises
  `?SN ERROR`. `BGGET`/`BGPUT` work, with their buffer moved to `$7C00` along with the top
  of the area

The graphics editor is on both, by different routes: the V2.1A build keeps the title menu,
so `SYSTEM` then `2` reaches `BG GRAPHIC`, while V3 has it as the `BGTOOL` command and
needs no menu.

### The `BGGET`/`BGPUT` buffer moves with the ceiling

On V3 these two words share a 1KB buffer that sits at the top of the program area, and
**widening the area moves it**: `$7C00-$7FFF` on the 8KB build, `$9C00-$9FFF` on the 16KB
one. Stock V3 has it at `$6C00`.

This matters if you `POKE` or `PEEK` around it: the address is different per build, and
reading the stock address on a widened build reads the middle of your own program.

### Integer literals differ by version

**V3's integer literals are signed 16-bit** (`-32768` to `32767`); the V2 series takes `0`
to `65535`. The same `PEEK(50976)` is accepted by V2.1A and raises `?OV` on V3, so this is
the version and not the value. `PEEK` and `POKE` reach the same bytes either way — on V3
the address is written as a negative number, or with `&H`.

## Disk BASIC: what `SAVE` and `LOAD` became

**A disk holds one saved program. Saving again replaces it.** `LOAD` does not choose by
name; it reads whatever program is there. Disk BASIC has those two words and no others —
no listing, no delete. The syntax and the error numbers are in the [manual](../manual/building.md).

The save routines cost nothing. They sit over dialogue text in the built-in conversation
program, which cannot be reached on a disk build — `tools/fb-reach.py` is the argument,
and it is a tool rather than a claim: it answers "can this address ever run?" by following
the branch tables only where a reading instruction reaches them.

Structurally, what the disk build changes beyond those two words:

| | cartridge | disk |
|---|---|---|
| characters | CHR-ROM | CHR-RAM, loaded from the disk |
| built-in programs | in ROM above `$E000` | **gone** — on a disk that space is the BIOS |
| at power-on | title menu | straight into GAME BASIC, menu still answerable via `SYSTEM` |

## MMC5: what `POKE` reaches

The 16KB build maps all of `$6000-$9FFF` as RAM, and the MMC5's own registers stay
reachable at `$5000-$5FFF`. [mmc5-wram-banks.md](mmc5-wram-banks.md) covers which WRAM
bank is which, and why that has to be probed rather than assumed.

## VRC7: what `POKE` reaches

The FM sound chip, through two ports BASIC can already write to: `POKE &H9010` selects a
register and `POKE &H9030` writes its value. They are written in hex because V3's
literals stop at `32767` - see [above](#integer-literals-differ-by-version).
[vrc7.md](vrc7.md) has the build, the register map and a program that plays notes.

⚠️ **A 16KB free area and VRC7's FM sound cannot coexist.** The FM registers are inside
the `$8000-$9FFF` that the 16KB build turns into RAM. So on a 16KB build `POKE &H9010`
writes to RAM, not to the sound chip. One or the other, in separate ROMs — a cartridge has
one mapper. From V3 the VRC7 build's area is whatever its input had — 8KB after the
expansion step, the same as plain mapper 0 gives V3, and V3's own 4KB from an unexpanded
dump: **what VRC7 buys is the sound, not the room.**

**From V2.1A the same tool can widen it**, with `--8k`, to the full `$6000-$7FFF` and
`8126 BYTES FREE`. That is the one build where the mapper swap and the expansion happen in
the same step, because V2.1A's seventeen extra patch sites are the ones the disk build
already works out.

⚠️ **and the V2.1A VRC7 build loses the boot demo.** V2.1A fills every byte of
`$E000-$FFFF`, and the only part of it nothing else reaches is the conversation and
fortune-telling program's. The menu and everything after it survive. V3 does not pay this:
its four built-in programs move into their own banks, so the copies at `$E000-$FFF9` go
dead by themselves.
