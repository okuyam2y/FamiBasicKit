# How far the program area can go

*Background. Why this project stops at 16KB and what the next step would cost. Nothing
here is needed to use a build or to change one — it is the arithmetic behind the ceiling,
with every guess marked as one.*

[日本語](area-ceiling.ja.md)

## The rule behind every ceiling

The program area is **one contiguous range**, and BASIC keeps the address of its last byte
in `$03/$04` and the end-of-program pointer in `$07/$08` — ordinary 16-bit pointers,
nothing banked.

Where it *starts* is the version's: `$6000` on V3, `$7000` on V1/V2.0A/V2.1A
([sav-format.md](../reference/sav-format.md)). Every figure below is V3's unless it says otherwise; the disk
builds move V2.1A's down to `$6000` and that move is why both end up with the same range.

∴ **dead space in the middle of the interpreter buys no program area.** To count, free
space has to be directly above where the area currently ends.

That is not a theory. The disk build found 1,522 bytes of dead message text at `$C5A1`
and the free-memory figure did not move by a byte; the space became the home of
`SAVE`/`LOAD` instead.

## What is in the V3 ROM

Read off the label table of [micahcowan/fbdasm](https://github.com/micahcowan/fbdasm), an
annotated disassembly of V3. Its addresses were checked against a local dump, which differs
from the one it was made against only in the 16-byte iNES header — the body is identical.

| | | |
|---|---|---|
| `$8000-$BDCD` | interpreter, editor, keyboard, sound, PPU | |
| `$BDCE-$BFCD` | cassette transfer, low level | 512 |
| `$BFCE-$C708` | `BGTOOL` (the graphics editor) | 1,851 |
| `$C709-$CCAA` | actor/sprite tables, `CRASH`, `CAN` | 1,442 |
| `$CCAB-$CEBE` | `tbl_KeywordTokens` — the reserved-word table | 532 |
| `$CEBF-$CF7A` | `jtbl_Commands` / `jtbl_Functions` | 188 |
| `$CF7B-$CFFF` | copyright strings, palette tables | 133 |
| `$D000-$D3FF` | `gameBackgroundMap` — for the built-in programs | 1,024 |
| `$D400-$FFF9` | `txtGame0`…`txtGame3` — the four built-in programs | |
| `$FFFA-$FFFF` | vectors | 6 |

`SAVE`/`LOAD` at the command level are elsewhere: `$9D44-$9EC8`, 389 bytes.

## The body needs 20,480 bytes

`$8000-$CFFF`. The built-in programs and their background map (`$D000` up) are left out:
MMC5 keeps them in banks of their own, and a disk build has no use for them.

**20,480 = 2.5 × 8KB.** That one fact decides everything below, because a cartridge maps
RAM in 8KB windows.

## The arithmetic on a cartridge

`$6000-$FFFF` is five 8KB windows.

| program area | windows left for the body | body needs | |
|---|---|---|---|
| 8KB | 4 (32,768) | 20,480 | ✅ |
| **16KB** | **3 (24,576)** | 20,480 | ✅ **4,096 to spare — this is the current build** |
| 24KB | 2 (16,384) | 20,480 | ❌ **short by 4,096** |
| 32KB | 1 (8,192) | 20,480 | ❌ |

The body needs 2.5 windows, so it occupies 3. Five minus three leaves two — **16KB, and
the 4,096 bytes of slack in the third window are why it fits at all.** To hand the program
a third window the body has to fit in two, which means losing exactly 4,096 bytes.

## What those 4,096 bytes would cost

| what goes | where | bytes | what is lost |
|---|---|---|---|
| `BGTOOL` | `$BFCE-$C708` | 1,851 | the graphics editor |
| cassette, low level | `$BDCE-$BFCD` | 512 | |
| `SAVE`/`LOAD` commands | `$9D44-$9EC8` | 389 | saving to tape at all |
| strings, palette tables | `$CF7B-$CFFF` | 133 | the boot banner, `CGSET`'s defaults |
| **subtotal** | | **2,885** | **1,211 short** |
| actor/sprite tables, `CRASH`, `CAN` | `$C709-$CCAA` | 1,442 | **`SPRITE` `MOVE` `POSITION` `CUT` `ERA` `CRASH` `CAN`** |
| **total** | | **4,327** | 231 to spare |

∴ **among the subsystems identified above, the last 1,211 bytes are payable only in
`SPRITE`.** Two things that is not: it is not a proof that no other 1,211 bytes exist —
V3's own dead space has never been measured, and the tool that could measure it soundly is
pinned to V2.1A (see "Not verified" below). And whether a Family BASIC without moving
characters is still the thing worth having is a judgement, not a measurement.

Two mechanical notes, if it is ever done:

- **A token cannot be deleted**, only its implementation. [token-numbering.md](../reference/token-numbering.md) rule 1
  freezes every number, so `$B4` still has to lead somewhere. **The disk build has done
  exactly this**: token `$B3` stays in the dispatch table and keeps pointing at `$AD5B`,
  and `$AD5B` was overwritten with `LDA #$01 / JMP $B237` — so a typed `GAME` raises
  `?SN ERROR` rather than running removed code. (A separate guard in the same build
  repoints overwritten *message* indices at a `?MSG` marker. That one is about data the
  routines overwrote, not about a frozen token, and citing it here as the precedent was
  wrong — found in review.)
- **Removing is also compacting.** Everything above the hole moves down, which is the same
  class of work as `fb-relocate.py` (860 references fixed on V3).

## Banking the pieces out does not help

Putting `BGTOOL` in a switched bank, the way the built-in programs already are, runs into
arithmetic rather than technique. **The switching window costs address space out of the
same 16,384 bytes.** With `X` bytes banked out and a window of `S`:

```
(20,480 − X) + S = 16,384      ∴  X = 4,096 + S
```

`BGTOOL` is the largest tenant at 1,851, so `S ≥ 1,851` and `X ≥ 5,947` — against
**4,194** bytes of identified bankable subsystems (1,851 + 1,442 + 512 + 389; the 133 of
strings and palette tables in the removal table are left out here because BASIC reads them
from anywhere, so they cannot sit in a window that comes and goes). **The more is pushed
out, the more window is needed to push it through.** Removing outright (4,096) is the
cheaper of the two.

⚠️ An earlier draft said 4,155, from a stale figure for the cassette command code — it was
350 there and 389 in the table above. Either number is under 5,947, so the conclusion does
not move (found in review).

Why the built-in programs *were* easy: they are **data**, read by a single copy loop
(`$ADB8`), not interactive code that calls back into BASIC. `BGTOOL` is not self-contained
either — one of its own routines, `BgtFile_GoToScreen0`, sits outside the block at `$AE96`.

## The disk build hits the same wall one window lower

The RAM adapter gives `$6000-$DFFF` — 32,768 bytes, flat, with the pseudo-vectors fixed at
`$DFF6-$DFFF`. There are no windows, so the body can start anywhere.

Every number below is either measured or subtracted from measured ones, so the subtraction
is shown rather than the answer. Inputs:

| | bytes | where it comes from |
|---|---|---|
| RAM `$6000-$DFFF` | 32,768 | NESdev wiki, [Family Computer Disk System](https://www.nesdev.org/wiki/Family_Computer_Disk_System) |
| pseudo-vectors `$DFF6-$DFFF` | 10 | same; fixed, cannot move |
| V3 body `$8000-$CFFF` | 20,480 | the label table above |
| the disk build's own `SAVE`/`LOAD` | 384 | `SAVE_SIZE` in `tools/fb-fds.py` |
| its power-on routine and IRQ stub | 88 | `V3_FDS_INIT` there, plus one `RTI` |

```
32,768 − 10 = 32,758   usable, $6000-$DFF5
20,480 + 384 + 88 = 20,952   everything that is not the user's program
32,758 − 20,952 = 11,806   program area, $6000-$8E1D
```

⚠️ **Rounding that start up to a page boundary does not work**: the body would then run
`$9000-$DFF5`, which is 20,470 bytes for something that needs 20,480. Ten bytes short —
so either the body starts on an odd address or ten bytes have to come from somewhere.
(An earlier draft of this file rounded up *and* counted the whole `$6000-$8FFF` as free,
and left the disk build's own 472 bytes out of the sum entirely. Found in review.)

For 16KB the body would have to fit in `$A000-$DFF5` = 16,374 against 20,952 — **short by
4,578**.

⚠️ Those are V3 numbers, and they describe a build **nobody has made**. `fb-fds.py` builds
a disk from either version, but both leave the body where the cartridge had it and take
`$6000-$7FFF` — V2.1A reaching `8126 BYTES FREE` and V3 `8182`. Getting to the 11,806 above
means moving the whole body up, which is a different job from what either build does today.

On V2.1A it is a worse job than on V3: that body fills `$8000-$DFFF` and reaches above
`$E000` in 11 places (V3: none), and its measured dead space is the 1,522-byte message
block, which is in the middle and so buys nothing.

∴ the disk is not the roomier medium it looks like. A cartridge puts the body in ROM;
a disk system has no ROM to put it in, so body and program share the same 32KB.

## Machine code, fragmented: the scarce resource changes

Machine code does not need one contiguous range, so the ceiling stops being about
addresses. In the current 16KB build, measured on the built ROM:

| | bytes | |
|---|---|---|
| **bank 4** | **8,192** | entirely `$FF` — unallocated. Switched in at `$C000-$DFFF` via `$5116` |
| **bank 7, `$F553-$FFF9`** | **2,727** | **resident** (`$E000-$FFFF` never switches). Leftovers of built-in program 4, dead because the loader reads bank 3's duplicate |
| the free area | 16,374 | shared with the BASIC program |

The way in is cheap: `CALL` is `jsr FrmEval` / `jmp ($0400)`, so **the destination is
`$0400/$0401` — two `POKE`s**. NMI is a RAM trampoline from the start (`$FFFA`→`$00ED`),
so a per-frame hook is two more.

Switching costs a rule: the `GAME` loader's practice is to **duplicate `$C000-$CFFF` into
the lower half of any bank switched in**, and to turn NMI off while it is switched. So a
bank carries **8,192 bytes if the code is self-contained**, or **4,096** if it still wants
BASIC's `$C000-$CFFF` underneath it.

∴ with overlays the ROM side grows as far as the mapper allows, and **what runs out
instead is resident space — 2,727 bytes.** Trampolines, interrupt handlers and the entry
stub all have to live where nothing switches.

## Numbers stop before the addresses do — and V3 stops sooner

Measured on hardware (MiSTer, 2026-08-25), typing four forms in one run on each disk:

```
V2.1A:  PEEK(50976) → 0      PEEK(-14560) → 0    PEEK(32767) → 0    PEEK(50976) → 0
V3   :  PEEK(54655) → ?OV    PEEK(-10881) → 0    PEEK(32767) → 0    PEEK(50976) → ?OV
```

**The same `50976` is accepted by V2.1A and overflows on V3**, so this is the version and
not the value. **V3's integer literals are signed 16-bit** (`-32768` to `32767`); V2.1A
takes `0` to `65535`. The signed form reaches the same byte on both, which is why the
hardware test types `PEEK(-10881)` rather than branching per version.

Two things this does and does not mean:

- **It is a limit on what can be typed, not on what can be addressed.** `POKE`/`PEEK` reach
  anywhere either way — you write the address as a negative number. A program that touches
  `$D57F` on V3 is written `PEEK(-10881)`, and the built-in programs use the other spelling
  for the same reason (`PEEK(&H4016)` in `GAME 0`; `&` is a character the Family Keyboard
  has and this project's key-injection tool does not).
- **It says nothing yet about the free-memory figure.** That is a different path - see
  "Not verified" below.

## Not verified

Everything below is arithmetic or inference, not measurement, and nothing here should be
built on before it is checked.

1. **Whether MMC5 can map 24KB of contiguous WRAM at all.** [mmc5-wram-banks.md](../reference/mmc5-wram-banks.md)
   covers `$5113`/`$5114` (`$6000-$7FFF` and `$8000-$9FFF`); the register that would put
   RAM at `$A000-$BFFF` is not imported. This is also the exact area where the project has
   already been bitten — the 16KB build has to *probe* at boot because the meaning of a
   bank number differs between implementations.
2. **Whether a window can be flipped RAM↔ROM while running.** If it can, `BGTOOL` could
   borrow `$A000-$BFFF` from the program area while it runs and give it back on exit —
   which would make a 24KB build possible without deleting anything.
3. **What `FRE(0)` and the `BYTES FREE` banner do above 32,767.** The *literal* half of
   this is now measured - see the section above - but that is the parser, and the banner is
   a different path: BASIC computes the figure and prints it, with no literal involved.
   Whether it goes negative, wraps, or prints something else is still unknown, and
   **neither build can be made to answer**: the disk tops out around 11,806 and the 16KB
   cartridge at 16,374, both well short. Answering it needs a build that reaches the figure,
   which is the thing this file is about not being able to make yet.
   ⚠️ An earlier draft of this file called the two limits "one byte apart" by counting all
   32,768 bytes of `$6000-$DFFF` - the figure the FDS section spends its time subtracting
   the vectors from (found in review). The 16-bit ceiling is a **cartridge**
   concern, for a design that maps RAM past `$DFFF`.
4. **`$5116` has never been driven from `CALL`.** The switching path is proven by the
   `GAME` loader; nobody has yet put code in bank 4 and run it. The 10,919 bytes above are
   "measured as empty", not "measured as usable".
5. **V3's own dead space is unmeasured.** `tools/fb-reach.py` is the sound tool for this
   and it is pinned to V2.1A's SHA-1. `fb-disasm.py --gaps` reports *unreached*, which is
   explicitly not the same as dead — it stops at jump tables, which is why `$C000-$DFFF`
   comes back at 0% coverage while plainly holding live code.

## Redoing the measurements

```bash
# the map, and what is unreached
./tools/fb-disasm.py "Family BASIC V3 (Japan).nes" --gaps
./tools/fb-disasm.py "Family BASIC V3 (Japan).nes" --xref D400-DFFF

# the token tables, read from the ROM rather than from a table written by hand
./tools/fb-basic-to-sav.py --tokens "Family BASIC V3 (Japan).nes"
```

Block extents come from that label table — the address of a label, minus the address of
the next one that belongs to something else. Bank contents were read straight
out of the built `.nes`: bank 4 holds 8,192 bytes of `$FF` and no other value.

The literal range is the one figure here that came off real hardware rather than a dump.
**The harness that types into a real machine is not included in this repository** — it is
tied to one specific MiSTer's connection settings, which is exactly the kind of thing that
does not belong in a published repository. Reproducing the measurement means typing the
four `PEEK` forms into each build by hand, or under an emulator, and reading the answers
off the screen; there is nothing else to it.

