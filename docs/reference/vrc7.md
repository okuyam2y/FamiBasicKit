# The VRC7 build: FM sound from `POKE`

[日本語](vrc7.ja.md)

A Konami VRC7 cartridge carries a six-channel FM sound chip whose two ports are inside the
address space BASIC can already write to. This build puts V3 on that mapper, changes
nothing else about BASIC, and leaves the free area where plain mapper 0 leaves it.

**What it buys is the sound, not the room.** It carries the area over from whatever it is
given: expand first and you get 8KB and `8182 BYTES FREE`, exactly what
[ram-expansion.md](ram-expansion.md) gets out of V3 on mapper 0; feed it the stock dump
and you get V3's own 4KB. Either way VRC7's RAM window is `$6000-$7FFF` and there is no
more of it.

⚠️ **The 16KB build and this one are alternatives, permanently.** The FM ports live at
`$9010` and `$9030`, inside the `$8000-$9FFF` the 16KB MMC5 build turns into RAM. A
cartridge has one mapper, so it is 16KB of program space *or* FM sound, in separate ROMs.

## Building it

```sh
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"
./tools/fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"
```

The first step is optional — `fb-vrc7.py` keeps whatever area its input declares — but
without it you get V3's stock 4KB.

| | |
|---|---|
| from | `Family BASIC V3 (Japan).nes`, MD5 `0cc06af39cb084885c34233b1b93b975` |
| after the 8KB step | MD5 `f9339eba89ee5b63de2c4c2cad54b082` |
| the VRC7 build, from the 8KB one | MD5 `52094d2cd768e8fee8f839c933bbb485`, 73,744 bytes, `8182 BYTES FREE` |
| the VRC7 build, from the stock dump | MD5 `a87904a82b1fc9dbe92d5bef07b43728`, same size, V3's own 4KB |
| header | mapper 85, **submapper 2 (VRC7a)**, PRG 64KB, CHR 8KB, battery-backed WRAM as the input declared |

**Only these two inputs are accepted**: the stock V3 dump, and the 8KB build made from
it. `fb-vrc7.py` takes a SHA-256 of the whole image (allowing only the five bytes the 8KB
step changes, and only all five together) and compares the header against stock's byte for
byte, the RAM declaration having to match the area those five bytes describe. Anything
else stops the build. V1/V2 are refused for a second reason as well —
their `GAME` loader is a different routine. A dump that differs anywhere the structural
checks do not look would otherwise be converted faithfully, damage included.

## Playing a note

`POKE &H9010,<register>` selects an FM register; `POKE &H9030,<value>` writes it.
Three registers make a note on channel 0:

⚠️ **Write these addresses as `&H`, not in decimal.** V3's integer literals are signed
16-bit, so typing `36880` raises `?OV` before the line is even stored
([build-differences.md](build-differences.md#integer-literals-differ-by-version)). The
decoded Makimura programs, which drive this same chip on a real V3, use `&H9010` too.

| register | what it holds |
|---|---|
| `48` (`$30`) | instrument in the high nibble, **volume in the low nibble, inverted** — `0` is loudest, `15` the quietest. **No value silences the channel**; key off with register `32` to do that |
| `16` (`$10`) | the low 8 bits of the frequency number |
| `32` (`$20`) | bit 4 keys the note on, bits 3-1 the octave, bit 0 the 9th frequency bit; bit 5 sustains |

Channels 1 to 5 are the same registers plus 1 to 5: `$31`/`$11`/`$21` for channel 1, and
so on. Instruments 1 to 15 are built into the chip (1 buzzy bell, 2 guitar, 3 wurly,
4 flute, 5 clarinet, 6 synth, 7 trumpet, 8 organ, 9 bells, 10 vibes, 11 vibraphone,
12 tutti, 13 fretless, 14 synth bass, 15 sweep); instrument 0 is the one you define
yourself in registers `$00-$07`.

```basic
10 REM VRC7 FM TEST
20 A=&H9010:D=&H9030
30 POKE A,48:POKE D,16
40 FOR N=0 TO 11
50 POKE A,16:POKE D,100+N*12
60 POKE A,32:POKE D,24
70 PRINT "*";
80 FOR T=1 TO 300
90 NEXT
100 POKE A,32:POKE D,8
110 NEXT
120 PRINT:PRINT "FM DONE"
```

Line 30 picks instrument 1 at full volume; 50 sets the pitch; 60 keys the note on in
octave 4 (`24` = `$18`); 100 keys it off again by clearing bit 4.

The chip wants 6 CPU cycles of quiet after a write to `$9010` and 42 after `$9030`. **From
BASIC you can ignore that** — one `POKE` takes far longer than either. It matters only if
you drive the chip from machine code.

## `POKE` into `$8000-$FFFF` is no longer harmless

On mapper 0, storing into ROM space did nothing at all. Here every one of those addresses
is a mapper register, and a write lands on the hardware:

| address | `POKE` | what it does |
|---|---|---|
| `$8000` / `$8010` | `&H8000` / `&H8010` | switches the ROM bank at `$8000` / `$A000` — **the interpreter itself** |
| `$9000` | `&H9000` | switches the bank at `$C000` |
| `$A000`-`$D010` | | the eight character banks: the text turns to garbage |
| `$E000` | `&HE000` | nametable arrangement, expansion-sound mute (bit 6), and **WRAM enable (bit 7)**. Clearing bit 7 makes the whole program area read-only |
| `$F000` | `&HF000` | the scanline IRQ |

The stock ROM itself never writes there — measured, not assumed: a full boot in FCEUX
shows the init code's 18 register writes and nothing else.

## What has been checked

`fb-vrc7.py` executes its own output on a model of the 6502 and of VRC7 before writing the
file — run it and you see those checks. The rest was measured in FCEUX, by booting this
build and the mapper-0 build made from the same dump side by side, and on hardware.

| | |
|---|---|
| boots on hardware | ✅ MiSTer, 2026-08-29: `NS-HUBASIC V3.0` / `8182 BYTES FREE` |
| the program area | ✅ 8KB, and a program loaded from a `.sav` runs |
| `GAME 0` on hardware | ✅ the built-in program loads and runs (`PLAY ? Y/N=>Y`) — the rebuilt loader, on the machine |
| the boot screen | ✅ pixel-identical to the mapper-0 8KB build in FCEUX |
| everything the CPU sees at `$8000-$FFFF` | ✅ byte-for-byte the stock 32KB, except in four places: the init code, the rebuilt loader, the four-byte jump that replaces the stock loader call at `$AD96`, and the reset and IRQ vectors |
| `GAME 0`-`GAME 3` | ✅ load the same bytes as the stock loader, in the model and in FCEUX |
| `$9010` is the sound port, not a bank register | ✅ in FCEUX: writing it leaves `$C000-$DFFF` alone, while writing `$9000` visibly switches it |
| **the note itself** | ✅ **heard on hardware** (MiSTer, 2026-08-30): a program playing one note four times sounded all four times. ⚠️ **Not machine-checked.** FCEUX 2.6.6's `--soundrecord` writes no file here and its Lua cannot read the OPLL; a microphone recording on the development machine caught only one of the four bursts, so it is not an oracle either. Ears are the check |

## How the ROM is put together

VRC7 powers up with nothing configured — three switchable PRG banks, eight CHR banks and
the WRAM write-enable are all undefined — so init code has to run before BASIC's own reset
handler. It can only run from `$E000-$FFFF`, the one window whose contents are known at
power-on, and in the stock ROM **that window is full**: the built-in programs run from
`$D400` to `$FFF9` with no filler.

So each built-in program gets its own 8KB bank with the program at `$D000`, and the `GAME`
load routine is rebuilt to swap that bank into `$C000-$DFFF` around the copy — the same
move the 16KB MMC5 build makes, for the same reason. The copies at `$E000-$FFF9` are then
dead, and the init code and the loader go there.

| CPU address space | Contents |
|---|---|
| `$6000-$7FFF` | WRAM. All of it is BASIC's area after the 8KB step; from an unexpanded dump the area is the lower half, `$6000-$6FFF` |
| `$8000-$9FFF` | bank 4 = the stock `$8000-$9FFF`. **`$9010`/`$9030` are the FM ports** |
| `$A000-$BFFF` | bank 5 = the stock `$A000-$BFFF`, with the `GAME` entry repointed |
| `$C000-$DFFF` | bank 6 = the stock `$C000-$DFFF`, title graphic included |
| `$E000-$FFFF` | bank 7 = the stock `$E000-$FFFF` + init + loader, fixed |
| banks 0-3 | one built-in program each |

The init code writes the two register aliases (`$x008` and `$x010`) before the unambiguous
one, so it configures the machine correctly whether the board decodes registers with A3 or
with A4. That costs five extra writes and removes a whole class of "it works in this
emulator" from the build. The sound ports, though, exist **only** on the A4 board (VRC7a,
submapper 2, the one this header declares); on a VRC7b, `POKE &H9010` would switch a bank
instead of making a sound.
