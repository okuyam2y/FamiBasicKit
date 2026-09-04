# The pictures: reading and replacing a Family BASIC ROM's tiles

Everything the machine can draw — the characters `MOVE` puts on screen, the letters, the
kana — is **8KB at the end of the `.nes` file**: 512 tiles of 8×8 pixels, four shades each.
Nothing indexes it and nothing compresses it. The picture for tile `n` is the sixteen bytes
at `n × 16`.

| Tiles | What they are |
|---|---|
| 0–255 | the sixteen characters sprites are built from |
| 256–511 | what the text screen draws: tile `256 + code`, so `256 + 65` is `A` |

**Every dump carries the same 8KB.** V1.0, V2.0A, V2.1A and V3.0 are byte-identical here
(`ec06b3c44be7ee18133e3d32c82465dc`), so one edited sheet fits every version.

⚠️ **The disk build has no such block.** It keeps its tiles in RAM and loads them from the
disk, so there is nothing in that file to edit.

## Looking

    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --sheet tiles.png
    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --index characters.png
    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --map
    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --show 4

`--sheet` writes all 512 tiles as a 128×256 PNG, sixteen across and thirty-two down, one
pixel per pixel — the arrangement a debugging emulator's tile viewer uses. `--index` writes
one picture of each of the sixteen characters with the number `SPRITE()` wants for it.
`--show` draws a character in the terminal, facing by facing.

## Replacing

    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --apply tiles.png -o "V3 (new art).nes"

The four shades are `#FFFFFF`, `#AAAAAA`, `#555555` and `#000000`. Shade 0 is what the PPU
leaves transparent on a sprite and fills with the backdrop colour on the text screen; a
fully transparent pixel counts as shade 0, so erasing works the way an editor makes you
expect. Colours must land back on those four exactly — an editor that anti-aliased or
converted the palette will produce shades in between, and the tool stops rather than
guessing. `--snap` says "take the nearest of the four" and reports how many pixels it moved.

### ★ Change the art last

A built ROM takes new art perfectly well: the program and the header come through
untouched, and the machine draws the new tile. Confirmed under an emulator on 2026-08-31
for the 8KB NROM build, the 16KB MMC5 build and the VRC7 build.

Going the other way round does not work. `fb-relocate.py` and `fb-vrc7.py` check a digest
that covers the tiles as well as the program, and `fb-mmc5-16k.py` requires its two inputs
to carry the same tiles, so all three refuse a dump whose pictures have been touched. Those
guards are what make it safe for them to patch fixed addresses, so the order to work in is:

    dump → build → replace the art

(`fb-expand-basic-area.py` pins no digest and does accept edited art, if the 8KB NROM build
is all you want.)

⚠️ **New art changes the ROM's MD5.** The figures recorded around this repository —
`541f9769bd5c0243c29d7a2f5d72c177` for the 16KB build and the rest — are **for stock art**.
A build with new tiles is supposed to differ from them.

## Changing the art while the program runs

`--apply` changes the picture in the file, and after that nothing can change it: the
machine reads its tiles out of ROM. A program that wants pictures of its own - a board
game drawing its pieces, a title screen with its own lettering - needs the tiles in RAM.

⚠️ **Two of the builds here have them there**, and both of them keep nothing to edit in the
file:

* the **disk build**. The Famicom Disk System has 8KB of character RAM and the disk loads
  the picture into it at boot.
* the **16KB MMC5 build made with `--chr-ram`** (`./tools/fb-mmc5-16k.py ... --chr-ram`).
  That ROM declares character RAM instead of character ROM, carries the picture in a spare
  PRG bank, and copies it out at power-on.

So the warning above cuts both ways: these are the builds with nothing in the file to edit,
and the ones whose picture can be replaced *while BASIC is up*.

    $ ./tools/fb-chr.py "Family BASIC V3 (Japan).nes" --sheet tiles.png
    ... edit tiles.png ...
    $ ./tools/fb-pcg.py "Family BASIC V3 (Japan).nes" tiles.png -o pcg.bas

`fb-pcg.py` compares the sheet against the ROM's tiles and writes a **BASIC program** that
installs the ones that differ. Every tile that still matches is left out, so the program is
as short as the edit is small.

Nothing in it is a new instruction or a patched ROM. Family BASIC already has both doors:
`POKE` puts a short 6502 routine into the top of the free area, `CLEAR` keeps BASIC out of that
page, and `CALL` jumps to them. The routine turns NMI off, waits for vblank, points the PPU
at one tile, writes sixteen bytes, puts the scroll back where it can, and turns NMI on
again. Sixteen bytes fit inside vblank with room to spare, so rendering is never switched
off.

⚠️ **Putting the scroll back is not tidiness.** On this machine the register that addresses
video memory *is* the scroll register, and BASIC does not rewrite the scroll every frame -
only when it next draws something. Without the restore the picture sits displaced until
then: measured under an emulator at nineteen per cent of the screen for ten frames, ending
the moment BASIC printed. The routine reads the scroll back out of BASIC's own copy, so it
comes back to where BASIC left it rather than to zero.

⚠️ **From a V3 dump only.** Putting it back means knowing which byte BASIC keeps it in, and
that has been established for V3 alone. A disk built from V2.1A was driven with V3's
addresses and stayed displaced by a fifth of the screen - the addresses are somewhere else
there - so a program made from a V2 dump is emitted **without** the restore and the tool
says so when it does that. The picture is then displaced until BASIC next draws, which is
what happens with no restore at all; what it never does is write a byte nobody has checked
into the PPU. Measuring where the V2 series keeps its vertical scroll is what would end
this.

On the NROM, the ordinary MMC5 and the VRC7 builds the same program runs to the end, prints
nothing wrong, and **the picture does not change**. Writes to character ROM are dropped by
the board, not refused, so there is no error to see.

⚠️ **The `--chr-ram` build is not the default, and that is deliberate.** It has been seen to
work under an emulator, on a MiSTer, and on a Famicom through an EverDrive N8 PRO - but no
MMC5 cartridge was ever made with character RAM, so any other machine is untried. Where the
declaration is not honoured there is no picture at all - not a wrong picture, a screen of
noise - so it is a separate ROM you ask for rather than the one you get. The one real MMC5
cartridge tried here (an ETROM donor board, 2026-09-04) has a flash chip for its picture, so
the `--chr-ram` build cannot apply to it; the plain 16KB build is what went onto that board.

### What it costs

A tile is eighteen numbers in a `DATA` statement - where it goes, as two, and then its
sixteen bytes.
The tool tokenises what it wrote with the ROM's own reserved-word table **and that
version's own rule for single digits** (V3 keeps 0-9 in one byte, the V2 series spends
three), reports how many bytes BASIC stores it in, and refuses rather than emitting a
program that will not fit. What is usable is not the whole free area: two things come off
it. Line 10 hands the top page to the routine, and the program's own variables are
allocated after it - one that only just fits answers `?OM ERROR` at its first assignment and
installs nothing. What the variables cost was asked of the machine with `FRE`, not guessed.

⚠️ **On a V3 build a kilobyte comes off as well.** `BGGET` and `BGPUT` share a 1KB screen
buffer that sits at the top of the free area, which is where the routine used to go.
Measured on a 16KB build with the routine in the top page: typing `BGGET` answered `OK` and
left 55 of the routine's first 64 bytes overwritten with screen data - no error, no
warning, and the next `CALL` runs whatever was on the screen. So the routine goes
underneath that kilobyte and `CLEAR` gives back 1,280 bytes rather than 256. The V2 series
has neither command and gives up only the page.

    3 tile(s) differ from Family BASIC V3 (Japan).nes: $00, $14B, $14F
      routine at $7B00, tile buffer $7B40, CLEAR $7AFF
      $7C00-$7FFF left alone: BGGET/BGPUT write their screen buffer there
      N lines / N bytes as V3 stores it (N left of N: N free, less what CLEAR takes
      back and what the program's own variables need)

⚠️ **The figures are the tool's, not this page's.** They move whenever the routine or the
free area does, and a page that repeats them is a page that goes quietly wrong - which it
did, twice, while this tool was being reviewed. Run it and read what it says.

What the free area starts from is worth knowing, because it is the machine's own number and
does not move: an area ending at `$7FFF` is `8182 BYTES FREE` on a disk built from V3 and
`8126` on one built from V2.1A. The default follows `--top` as well as the version - a
smaller area gives a smaller figure - and `--area` overrides it for a layout whose program
starts somewhere else.

Before writing anything it reads its own program back and runs it on a model of the 6502,
so a number dropped from a `DATA` line or a tile aimed at the wrong address stops the tool
instead of becoming a program that runs and draws the wrong picture. Driven under an
emulator on 2026-08-31 against a disk built from V3: after the program runs, all 8,192
bytes of character RAM are the edited sheet, the screen draws the new letter, and BASIC
carries on. The same day, on a `--chr-ram` cartridge build: the machine came up with the
dump's own 8,192 bytes in character RAM, the program replaced the tile it was given, and
`BGGET` afterwards left the routine untouched. And on a Famicom, with the same build on an
EverDrive N8 PRO and the program loaded from tape: every `O` on the screen became the shape
the sheet asked for **while the digit `0` did not** - two tiles that look alike, and only
the edited one changed.

The 16KB build's free area runs past `$8000`, where Family BASIC's numbers are negative -
16-bit signed. That was measured rather than assumed: `POKE &H9B00,123` reads back,
`I=5:POKE &H9B00+I,77` reads back at `&H9B05`, `CALL` returns, `CLEAR &H9AFF` is accepted
and `FRE(0)` afterwards is `15094` - which is what the tool's own rule says it should be -
and `CLEAR &HA000` is where the machine answers `?IL ERROR`.

    $ ./tools/fb-pcg.py "Family BASIC V3 (Japan).nes" tiles.png -o pcg.bas \
          --top 0x9FFF --area 16374

⚠️ **`--top` takes a free area that ends at or below `$9FFF` and on a page boundary** - the
last address has to be `$xxFF`. On a page boundary because the routine takes a whole page;
`$9FFF` because that is where the machine's own ceiling is (`CLEAR &HA000` is `?IL ERROR`).

## Which tiles are which character

`SPRITE(c, ...)` takes a character number 0–15. Each character is drawn as a 2×2 block of
tiles, and the ROM holds a table of those blocks per character. `--map` reads that table out
of the ROM it is given:

| c | tiles | | c | tiles |
|---|---|---|---|---|
| 0 | `$00-$0F`, `$14-$1B` | | 8 | `$98-$A3` |
| 1 | `$1C-$2B`, `$30-$37` | | 9 | `$A4-$AF` |
| 2 | `$38-$3F` | | 10 | `$B0-$B7` |
| 3 | `$40-$57` | | 11 | `$58-$5F` |
| 4 | `$60-$6F` | | 12 | `$D0`, `$D2`, `$D4`, `$EF` |
| 5 | `$70-$77` | | 13 | `$B8-$BF` |
| 6 | `$78-$8F` | | 14 | `$C0-$C7` |
| 7 | `$90-$97` | | 15 | `$C8-$CF` |

No tile belongs to two characters. The number the machine draws is

    tile = block[i] + offset[c] + 4 × frame

where `offset[c]` is a per-character constant and `frame` steps through the animation four
tiles at a time. Several characters share one block table and are told apart only by that
offset — characters 2, 7, 10, 11 and 14 are the same nine blocks read five different ways.

### How it is found, and the two places it is not uniform

The tables are located **by shape, not by address**: sixteen pointers whose targets are each
three more pointers, the middle of which is a sheet of nine (one per facing), each of those
a block of four tile numbers. Only one place in the ROM looks like that. It has to be done
this way because the address moves — `$C709` in V3.0, `$BC75` in V1.0 and V2.x.

* **The frame count is stored differently per version.** V1.0 and V2.x keep it already
  multiplied by sixteen, with the flips in a separate table; V3.0 packs the count into the
  low nibble and the flips into bits 6 and 7 of one byte. The numbers are the same either
  way. Read the wrong form and every character's tile list comes out empty.
* **Characters 0, 1 and 4 have a climbing frame written into the code**, not into any
  table: for facings 1 and 5 the ROM points at a block held in the instruction stream. Each
  of those is the same four tiles as the first frame, mirrored, so no tile is added — but
  stepping the number on by four the way every other facing works would name tiles that
  belong to a different character. With that left in, character 4 runs into character 5.

Neither is assumed. Both are held to two properties that fail loudly if the reading
drifts: the sixteen tile sets must come out **disjoint** — with the frame step left in
where the code overrides it, character 4 runs into character 5 — and they must be **the
same from every dump**, whose program areas share no addresses at all.

## What the checks rest on

A round trip — export, put back, compare — cannot catch a mistake made identically in both
directions. Read the two bit planes the wrong way round and write them back the wrong way
round and the file is unchanged, while every picture shown had two of its shades swapped.

So the shades are pinned to something written by someone else: the worked example on the
NESdev wiki's pattern-table page (<https://www.nesdev.org/wiki/PPU_pattern_tables>), which
gives sixteen bytes and the eight rows of shades they draw. Those bytes are decoded and
re-encoded on every run. The check is exercised by swapping the two planes in *both*
directions on purpose and requiring it to fail — a round trip does not notice that.
