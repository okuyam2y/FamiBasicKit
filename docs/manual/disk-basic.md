# Disk BASIC: `SAVE` and `LOAD`
*For anyone typing on a disk build. To make one in the first place, see
[building.md](building.md).*

[日本語](disk-basic.ja.md)

## Typing on the disk build: `SAVE` and `LOAD`

The syntax is the one that is already there. What changed is where it goes.

### One program per disk

**A disk holds one saved program. Saving again replaces it.** From the machine there is
no way to keep two, no way to list what is on the disk, and no way to delete without
overwriting — Disk BASIC has the two words and no others. `LOAD` does not choose by name;
it reads whatever program is there, whatever you call it.

The name is still worth giving, because it is written to the disk and shown by tools that
read the disk. It just does not select anything.

### What to type

| | goes to |
|---|---|
| `SAVE "NAME"` / `LOAD "NAME"` | the disk |
| `SAVE` / `LOAD` (no argument) | the disk, under the name `BASIC` |
| `SAVE "DSK:NAME"` / `LOAD "DSK:NAME"` | the disk, said out loud |
| `SAVE "CAS:NAME"` / `LOAD "CAS:NAME"` | the cassette, by the cartridge's own routine (**12 characters** — see below) |

### How long a name may be

**Keep a name to 16 characters, whatever you are doing with it.**

**Saving to the disk you will be told**, because the disk's own limit is 8 characters and
that check refuses anything longer with error `240`. `LOAD` and `CAS:` have no such check:
the name is copied before anyone counts it, and a long one goes past the end of the buffer
it is copied into. That does no harm — no instruction in the ROM ever looks at those bytes
— and past about 31 characters BASIC refuses the string outright. Measured on hardware: 20
characters write four bytes past the buffer, 26 write ten, 32 are refused, and BASIC
carried on in every case. This is stock Family BASIC behaviour, inherited rather than
introduced. **Keeping to 16 avoids the whole question.**

Within that limit, **saving to the disk takes at most 8 characters**, because the disk
file header has 8 bytes for a name.

More precisely, because it is easy to get wrong: **spaces after the eighth character are
ignored, and a non-space ninth character is refused** (error `240`). The header pads short
names to eight with spaces anyway, so `SAVE "ABCDEFGH   "` is the same name as
`SAVE "ABCDEFGH"` and both work; `SAVE "ABCDEFGH X"` is nine characters and is refused
rather than quietly shortened. A space *inside* a name is kept — `SAVE "A B"` saves a
program called `A B`.

**That eight-character rule is the disk's, and only the disk's.** `LOAD` does not look at
the name at all.

**`CAS:` keeps 12 characters, not 16.** The tag has to be slid off the front before the
cartridge's routine sees the name, and that slide copies 12 bytes and pads the rest with
spaces — so a cassette name of 13 to 16 characters, which the cartridge itself accepts,
comes out truncated here. That is a limit of this build's `CAS:` handling, not of the
cassette.

⚠️ **`CAS:` is untested.** No emulator here has a Data Recorder, so the cassette path has
only ever been checked as far as the hand-off. It is there so that turning a cartridge
habit into a disk habit does not cost you the old one, not because anyone has watched it
work.

### When a save or a load fails

The screen says `?TP ERROR` — the cassette's error, borrowed, because an error code of its
own would mean moving BASIC's error table. **The real reason is a number, and you read it
yourself:**

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

⚠️ On a V3 disk, `PEEK(50976)` overflows: V3's integer literals are signed 16-bit, so type
`PRINT PEEK(-14560)` instead. It is the same address. V2.1A accepts either.

### Turning the machine off and on does not bring the program back

The disk comes up exactly as it would if nothing had ever been saved to it — the
free-memory banner and all — and the program returns when `LOAD` asks for it.

This was the other way round until 2026-08-24, and the reason it changed is worth knowing
if you used it then: restoring automatically meant BASIC took a path that skips printing
`8126 BYTES FREE`, so from the first save onwards that disk never showed the free-memory
count again, and nothing you could type would bring it back. `PRINT FRE(0)` answers the
same question at any time.

### What else is different on a disk

| | cartridge | disk |
|---|---|---|
| characters | CHR-ROM | CHR-RAM, loaded from the disk |
| built-in programs | in ROM above `$E000` | **gone** — on a disk that space is the BIOS |
| at power-on | title menu | straight into GAME BASIC, menu still answerable via `SYSTEM` |

**`GAME` is gone on a V3 disk** and raises `?SN ERROR`; the built-in programs it loads
live where the BIOS is. The graphics editor is still there on both versions, by different
routes: on a V2.1A disk `SYSTEM` then `2` reaches `BG GRAPHIC`, and V3 has it as the
`BGTOOL` command.

## Getting programs on and off a disk from a PC

`tools/fb-fds-file.py` reaches inside a disk image that has been used, so a program does
not have to stay on the disk it was typed on.

```
$ ./tools/fb-fds-file.py disk.fds --list
$ ./tools/fb-fds-file.py disk.fds --extract -o program.bas
$ ./tools/fb-fds-file.py disk.fds --insert program.bas -o new.fds
```

Nothing else to pass. The reserved words are read from the BASIC **on the disk** — the
disk carries its own interpreter, and that is the one that will run the program, so its
table is the only one that can be right.

`--insert` replaces the saved program rather than adding a second one, and it refuses to
write over the disk it was given, so `-o` is required.

**`--insert` can refuse a disk that is perfectly fine.** Replacing a long program with a
short one leaves the tail of the old one on the disk, deliberately — and a `REM` can hold
bytes shaped exactly like a file header, so the next long program may look as though it
would overwrite a file nobody counted. Nothing can tell those bytes from a file somebody
hid there; they are the same bytes. The refusal names the offset it found, and
**`--over-tail` writes over it** — use it when that offset is inside your own old program.

### The disk comes back in three shapes, and they are detected

| shape | where the side starts | who writes it |
|---|---|---|
| raw `.fds` | 0 | `fb-fds.py`, and **FDSKey**, which rewrites the `.fds` in place and keeps the original as `.fds.bak` |
| fwNES `.fds` | 16, behind `FDS\x1a` | other tools in the wild |
| MiSTer `.sav` | 16, behind 16 zero bytes | **MiSTer**, as a file beside the disk |

The tool checks for the block-1 signature at both offsets rather than trusting the file
extension, because reading a side from the wrong offset means reading file headers out of
the middle of somebody's program.

Bytes the decoder could not read come back as `\xNN` and are **counted in the output**, so
"it printed something" and "it understood all of it" stay apart.
