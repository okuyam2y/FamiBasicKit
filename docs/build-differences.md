# What each build changes, and what it does not

[日本語](build-differences.ja.md)

Family BASIC ships as a cartridge. This repository rewrites that cartridge into several
builds, and they do not behave the same. This file is the list of what actually differs,
so that a program written on one and a habit learned on one do not quietly break on
another.

## No build adds a command

**Not one keyword has been added.** Every build answers exactly the reserved words its
stock ROM answered, and every one of those words still has the number it always had.

That is deliberate. A keyword's number is a file format - it is the byte that ends up in a
`.sav`, on a cassette and on a disk. What that costs is precise, and worth being precise
about: **changing the number a word already has, or giving a new word a number some other
word already claims, turns every program ever saved into a different program.** Putting a
new word on a number nothing uses does not - the reserved-word table carries an explicit
token byte per entry, so entries after the new one keep the numbers they had
(`docs/token-numbering.md`). The positional table is the *dispatch* table, and that is
where an insertion in the middle moves every later handler out of step (found in review). The rules that keep this true are in
`docs/token-numbering.md`, and its registry of additions is empty.

What changes instead is what some existing words **do**. There are two such words, and
both are on the disk build: `SAVE` and `LOAD`.

## The builds

| | NROM | MMC5 | Disk BASIC (FDS) | VRC7 |
|---|---|---|---|---|
| mapper | 0 | 5 | — (Famicom Disk System) | 85 |
| base ROM | V3 (also V1/V2) | V3 | **V2.1A** | undecided |
| program area | V3 `$6000-$7FFF`, V1/V2 **`$7000-$7FFF`** | `$6000-$9FFF` | `$6000-$7FFF` | undecided |
| area size | 8KB on V3, **4KB on V1/V2** | 16KB | 8KB | undecided |
| `BYTES FREE` with nothing loaded | V3 `8182`, V1 `4031`, V2.1A `4030` | `16374` | `8126` from V2.1A, `8182` from V3 | undecided |
| `SAVE` / `LOAD` | cassette, unchanged | cassette, unchanged | **the disk** - see below | cassette, unchanged |
| `BGGET` / `BGPUT` buffer | **V3 only**: `$7C00-$7FFF`. V1/V2 have neither word | **`$9C00-$9FFF`** | **V3 only**: `$7C00-$7FFF`, as on the cartridge. V2.1A has neither word | undecided |
| reachable by `POKE` | nothing extra | CHR banks, extended attributes, scanline IRQ, two extra square waves + PCM, the multiplier | **the FDS sound channel** (and CHR is RAM, so characters can be rewritten while a program runs) | **FM sound** |
| status | done, hardware-verified | done, hardware-verified | done, hardware-verified | not started |

The area size and the `BYTES FREE` figure are **different numbers** and the table keeps
them apart: the display subtracts the few bytes BASIC keeps for itself. The measured
figures come from `README.md`; the disk build's is what `fb-fds.py` reports.

### What "done" means for the disk build

`SAVE` and `LOAD` were confirmed on a real Famicom (RAM adapter + FDSKey) by typing them
in: save, clear, load, `LIST`, and the program came back.

**The current image has been through the whole sequence on hardware** (2026-08-25, MiSTer,
no hands): boot to the banner, `SAVE "HWTEST"` typed in, BIOS error byte zero, `LOAD` back,
`BG GRAPHIC`, the disk fetched off the SD card and checked byte by byte - the file count
went 3 to 4, the new header is `03 03 10 HWTEST`, it loads at `$603A`, the signature and
end pointer agree with its length, and **31 bytes changed in all**. Then a power cycle:
the banner again, the program not restored, and back when `LOAD` asks.

What has still never been done is writing to a real disk through a drive, as opposed to
the RAM adapter: no round trip here has turned a disk motor. Neither has writing to a real disk through a drive, as opposed to the RAM
adapter: the round trip above never turned a disk motor.

Saying so matters more than the table looking finished. The rule here is that "no error
appeared" is not a verification, and "verified on hardware" is a claim about a specific
image - this one is not that image (found in review).

**V1 and V2 start their area at `$7000`**, so widening it without moving the start stops
at 4KB. V3 starts at `$6000` and reaches 8KB. `docs/ram-expansion.md` has the detail.

### The word set differs before any of this

V3's reserved-word table holds 109 entries, V2.1A's 88 - counting everything in the
table, operators (`XOR`…`*`, 15 of them) included; 94 and 73 without them. Either way the
difference is the same 21 words, which V3 adds: `AUTO` `BACKUP`
`BGGET` `BGPUT` `BGTOOL` `CAN` `CLICK` `CRASH` `DELETE` `ERL` `ERR` `ERROR` `FILTER`
`FIND` `GAME` `INSTR` `RENUM` `RESUME` `SCREEN` `TR` `VCT`, and V2.1A has nothing V3
lacks.

**The disk can be built from either version**, so which of those 21 words are available
depends on the dump it was built from, not on the disk:

- **from V2.1A** - none of the 21, `GAME` and `BGGET`/`BGPUT` included. That is a property
  of the base ROM, not something the disk build removed
- **from V3** - all of them except `GAME`, which the disk build patches out because the
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

## Disk BASIC: `SAVE` and `LOAD`

### One program per disk

**A disk holds one saved program. Saving again replaces it.** From the machine there is no
way to keep two, no way to list what is on the disk, and no way to delete without
overwriting - Disk BASIC has the two words and no others. (On a PC, `fb-fds-file.py --list`
below does show what is on a disk image; the limit is the machine's, not the format's.) `LOAD` does
not choose by name - it reads whatever program is there, whatever you call it.

The name is still worth giving, because it is written to the disk and shown by tools that
read the disk, but it does not select anything.

### What to type

| | goes to |
|---|---|
| `SAVE "NAME"` / `LOAD "NAME"` | the disk |
| `SAVE` / `LOAD` (no argument) | the disk, under the name `BASIC` |
| `SAVE "DSK:NAME"` / `LOAD "DSK:NAME"` | the disk, said out loud |
| `SAVE "CAS:NAME"` / `LOAD "CAS:NAME"` | the cassette, by the cartridge's own routine (**12 characters** - see below) |

**Keep a name to 16 characters, whatever you are doing with it.**

**Saving to the disk you will be told**, because the disk's own limit is 8 characters and
that check refuses anything longer with error `240` - see below. What is worth knowing is
what happens on the way there, and to `LOAD` and `CAS:`, which have no such check: the
name is copied before anyone counts it, and a long one goes past the end of the buffer it
is copied into. That does no harm, and the reason is unusual enough to write down.

BASIC parses a name into a **16-byte buffer at `$0580`**, with a `$00` after it at `$0590`,
and the routine that fills it, the routine that compares two names, and this build's own
copy are each written for exactly those 16 bytes. The parser's copy loop, however, has no
length check: it writes as many bytes as the string has. So a name of 17 to about 31
characters writes a few bytes past `$0590` - and **nothing else in the ROM ever looks at
them**. No instruction in the 32KB names `$0591` upwards, and the only code that reaches
there by index is the parser itself. Past about 31 characters BASIC refuses the string
outright and nothing is copied at all.

Measured on hardware rather than reasoned about: 20 characters write four bytes past the
buffer, 26 write ten, 32 are refused, and BASIC carried on in every case. This is stock
Family BASIC - the cassette `SAVE` on an unmodified cartridge does the same - and this
build inherits it by calling the same parser, so `SAVE`, `LOAD` and `CAS:` all behave this
way. **Keeping to 16 characters avoids the whole question**, which is why the limit is
stated as 16 above - and this build's own name check stops at `$0590` for the same reason,
so a name it accepts or refuses is always decided inside the buffer.

Within that limit: **saving to the disk**, a name is at most 8 characters. That limit is
the disk file header's, which has 8 bytes for a name, and the check is in the `SAVE` path
only.

More precisely, because it is easy to get wrong: **spaces after the eighth character are
ignored, and a non-space ninth character is refused** (error `240`). The header pads short
names to eight with spaces anyway, so `SAVE "ABCDEFGH   "` is the same name as
`SAVE "ABCDEFGH"` and both work; `SAVE "ABCDEFGH X"` is nine characters and is refused
rather than quietly shortened. A space *inside* a name is kept - `SAVE "A B"` saves a
program called `A B`.

**That eight-character rule is the disk's, and only the disk's.** `LOAD` does not look at
the name at all, and `CAS:` hands it to the cartridge's routine - see below for what this
build does to it on the way. `LOAD` does not look at the name at all - it
reads the one program on the disk whatever you call it. Neither of those is a *safety*
limit, though; the 17-character overrun happens first, in the parser, for all of them.

**`CAS:` keeps 12 characters, not 16.** The tag has to be slid off the front before the
cartridge's routine sees the name, and that slide copies 12 bytes and pads the rest with
spaces - so a cassette name of 13 to 16 characters, which the cartridge itself accepts,
comes out truncated here. It is a limit of this build's `CAS:` handling and not of the
cassette. Nothing here has watched the cassette path run at all.

⚠️ **`CAS:` is untested.** No emulator here has a Data Recorder, so the cassette path has
only ever been checked as far as the hand-off. It is there so that turning a cartridge
habit into a disk habit does not cost you the old one, not because anyone has watched it
work.

### Turning the machine off and on does not bring the program back

The disk comes up exactly as it would if nothing had ever been saved to it - the
free-memory banner and all - and the program returns when `LOAD` asks for it.

This was the other way round until 2026-08-24, and the reason it changed is worth knowing
if you used it then: restoring automatically meant BASIC took a path that skips printing
`8126 BYTES FREE`, so from the first save onwards that disk never showed the free-memory
count again, and nothing you could type would bring it back. `PRINT FRE(0)` answers the
same question at any time.

### When a save or a load fails

The screen says `?TP ERROR` - the cassette's error, borrowed, because adding an error code
of its own would mean moving BASIC's error table. **The real reason is a number, and you
read it yourself:**

```
PRINT PEEK(50976)
```

| | |
|---|---|
| `0` | the last transfer went through |
| `3` | the disk is write protected |
| `41` | the disk is full |
| `240` | a ninth character that is not a space (trailing spaces are ignored) |
| `241` | `LOAD` found no program on the disk |
| `255` | nothing has run yet |

### What else the disk build changes

| | cartridge | disk |
|---|---|---|
| characters | CHR-ROM | CHR-RAM, loaded from the disk |
| built-in programs | in ROM above `$E000` | **gone** - on a disk that space is the BIOS |
| at power-on | title menu | straight into GAME BASIC, menu still answerable via `SYSTEM` |

## Getting programs on and off a disk from a PC

`tools/fb-fds-file.py` reaches inside a disk image that has been used, so a program does
not have to stay on the disk it was typed on.

```
$ ./tools/fb-fds-file.py disk.fds --list
$ ./tools/fb-fds-file.py disk.fds --extract -o program.bas
$ ./tools/fb-fds-file.py disk.fds --insert program.bas -o new.fds
```
**`--insert` can refuse a disk that is perfectly fine.** Replacing a long program with a
short one leaves the tail of the old one on the disk, deliberately - and a `REM` can hold
bytes shaped exactly like a file header, so the next long program may look as though it
would overwrite a file nobody counted. Nothing can tell those bytes from a file somebody
hid there; they are the same bytes. The refusal names the offset it found, and
**`--over-tail` writes over it** - use it when that offset is inside your own old program.


Nothing else to pass. The reserved words are read from the BASIC **on the disk** - the disk
carries its own interpreter, and that is the one that will run the program, so its table is
the only one that can be right. It briefly took a `--rom` instead, which made it possible to
tokenize with a V3 dump and write words this build has never heard of.

### The disk comes back in three shapes, and they are detected

| shape | where the side starts | who writes it |
|---|---|---|
| raw `.fds` | 0 | `fb-fds.py`, and **FDSKey**, which rewrites the `.fds` in place and keeps the original as `.fds.bak` |
| fwNES `.fds` | 16, behind `FDS\x1a` | other tools in the wild |
| MiSTer `.sav` | 16, behind 16 zero bytes | **MiSTer**, as a file beside the disk |

Three shapes, but only **two offsets**: the side starts at 0 or at 16. What separates the
two that share offset 16 is the 16 bytes in front - fwNES writes `FDS\x1a`, MiSTer leaves
them zero. The tool checks for the block-1 signature at both offsets rather than trusting
the file extension, because reading a side from the wrong offset means reading file
headers out of the middle of somebody's program.

### One program, replaced

`--insert` replaces the saved program; it does not add a second one, because the build
saves one program to a disk and `LOAD` reads whatever is there. It also refuses to write
over the disk it was given - `-o` is required.

### What "it worked" means here

The check that matters is the round trip against a disk a real Famicom wrote: extract the
program, insert it again, and the disk is **byte-for-byte identical** to the one the
hardware produced. That is stronger than "it printed something plausible" - it means the
tool builds the same bytes the machine does.

Bytes the decoder could not read come back as `\xNN` and are **counted in the output**, so
"it printed something" and "it understood all of it" stay apart.

## MMC5: what `POKE` reaches

The 16KB build maps all of `$6000-$9FFF` as RAM, and the MMC5's own registers stay
reachable at `$5000-$5FFF`. `docs/mmc5-wram-banks.md` covers which WRAM bank is which.

## VRC7 is not built

⚠️ **A 16KB free area and VRC7's FM sound cannot coexist.** The FM registers are at
`$9010` and `$9030`, inside the `$8000-$9FFF` that the 16KB build turns into RAM. So on a
16KB build `POKE 36880` writes to RAM, not to the sound chip. One or the other, in
separate ROMs - a cartridge has one mapper.
