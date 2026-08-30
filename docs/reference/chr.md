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
