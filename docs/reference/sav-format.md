# BASIC program storage format

*For anyone changing a build, or writing a tool that reads or writes a `.sav`. To just put
a program on the machine, read the [manual](../manual/building.md) instead.*

[日本語](sav-format.ja.md)

What `tools/fb-basic-to-sav.py` implements. The check on all of it is a round trip:
**decode the four built-in programs in the ROM (387 lines, 8,999 bytes), re-encode them,
and demand a byte-exact match** (`--selftest`).

## The layout differs by version

| | V1.0 / V2.0A / V2.1A | V3.0 |
|---|---|---|
| Start of area | `$7000` | `$6000` |
| Program body | `$703E` (offset `0x103E`) | `$6006` (offset `0x0006`) |
| Boot signature | `$703A`=`$5A` / **`$703B`=`$33`** | `$6001`=`$4C` |
| End-of-program pointer | `$703C/$703D` | `$6002/$6003` |
| End of area | `$77FF` (`$7FFF` when expanded) | `$6FFF` (`$7FFF` when expanded) |

⚠️ **You need both the signature and the end pointer.** With the signature alone you get
as far as "there is data on the cassette", but without the end pointer the program counts
as empty and `LIST` shows nothing.

### The V2-series restore path (from `$80FB`)

```
80D7  LDA #$5A / STA $703A     ; save side
80DC  LDA #$33 / STA $703B
80E3  LDA $07  / STA $703C     ; end of program (low)
80E8  LDA $08  / STA $703D     ;                (high)

80FB  LDA $703A / CMP #$5A     ; restore side (runs after a keypress on the title screen)
8102  LDA $703B / CMP #$33
8109  LDA $703C → $07 / LDA $703D → $08
8113  LDA #$3E / LDA #$70 → $05/$06   ; start of area, $703E
```

⚠️ The `CMP #$CC` at `$CF5B` is **a different path**. Reading that one and concluding
"the signature is `$CC`" leads to an endless hunt for why `LIST` comes up empty.

### The V3 header (checked at `$812B`)

| Address | `.sav` offset | Meaning |
|---|---|---|
| `$6001` | `0x0001` | **Signature `$4C`.** Anything else and the program is discarded (`CMP #$4C` at `$812E`) |
| `$6002`/`$6003` | `0x0002` | **End-of-program address** (one past the terminating `00`) |
| `$6004` | `0x0004` | Copied into zero page `$7C` at boot (purpose not investigated; `$00` works) |
| `$6006` | `0x0006` | **Start of the program body** (`$05/$06` receives `$6006`) |

## Line storage format

```
line = [length 1B (including itself)][line number 2B LE][body...][00]
body = token ($80 and up) / raw ASCII / number / string (quotes kept raw)
after the last line: 00, and the end pointer addresses the byte after it
```

**Past the end pointer, nothing is defined.** A `.sav` is a fixed-size RAM image, so
there is usually room left after the program - BASIC does not read it, because the end
pointer is what says where the program stops. Usually, not always: a program can fill the
area exactly, and then there is nothing after the terminator at all. Neither case is
special. A `.sav` written by hardware was seen with `0x20`
there; `fb-basic-to-sav.py` writes zeros for a fresh one and leaves whatever was there
with `--base`. This page used to specify the `0x20` fill as part of the format, which no
tool here produces and nothing requires.

### Numbers have three forms, plus one

An earlier version assumed everything was `$12`, which made any program containing
`GOTO`/`GOSUB` fail with `?SN ERROR` every time.

| Form | Bytes | What |
|---|---|---|
| Small integer | `$01`-`$0A` | **0-9** (value + 1; `$00` is the line terminator, hence the offset) |
| Integer | `$12 lo hi` | **10 and above** |
| Hex | `$11 lo hi` | written as `&Hxxxx` |
| Line number | `$0B lo hi` | after `GOTO`/`GOSUB`/`THEN`/`RESUME`/`RESTORE`, and after a following `,` |

⚠️ **The single-digit short form is V3 only** (V2-series encodes 0-9 as `$12 lo hi` too).

⚠️ **Everything after `REM` and `DATA` is stored raw to end of line** — neither tokenised
nor converted to numbers. Encoding `DATA 11,22` with numeric tokens makes `READ` fail with
`?TM ERROR`.

Example: `10 POKE 24576,65` → `0d 0a 00 9e 20 12 00 60 2c 12 41 00 00`

**Strings are raw ASCII including the quotes.** Only reserved words fold into single-byte
tokens. `,` `(` `)` stay as raw ASCII.

### The token table differs by version

**V3 has 109 words** (`$CCAB-$CEBE`); the V2 series has 88 (`$C128-$C2C5`).
Both ranges include the `$FF` that ends the table, and both were measured from the ROMs.
This page had `$C120-$C2BF` and `$CCAB-$CEBD`, which disagreed with what the tool says
about the same table - a good reason to take the addresses from a ROM rather than from a
page, which is what `fb-basic-to-sav.py` does.
The 21 words V3 adds: `TR` `FIND` `GAME` `BGTOOL` `AUTO` `DELETE` `RENUM` `FILTER`
`CLICK` `SCREEN` `BACKUP` `ERROR` `RESUME` `BGPUT` `BGGET` `CAN` `INSTR` `CRASH`
`ERR` `ERL` `VCT`.

This page used to say 87 and 22, with `SCR$` among the additions. **V2.1A has `SCR$`,
at `$E1` - the same number V3 gives it** (counted from both ROMs). That
is the same word a hand-written copy of the table dropped once before, which is the reason
the tools read the table from the ROM instead of keeping a list.

⚠️ **Using the V2 table on V3 turns every added word into individual ASCII
characters.** The table format is a repeating "token value (bit 7 set) → the word in
ASCII", so it can simply be re-read from the ROM.

⚠️ **`TAB` / `SPC` / `COLOR` are reserved words but are not implemented as statements**
(the V3 manual: "reserved for future expansion, so they cannot be used as variable
names"). On hardware they produce `?SN ERROR`.

## The self-test

```bash
./tools/fb-basic-to-sav.py --selftest "Family BASIC V3 (Japan).nes"
```

It decodes the four built-in programs and re-encodes them, demanding **not one byte
differs**. The reserved-word table is re-read from the ROM and cross-checked. The design
goal is to **keep the ground truth out of my own understanding** (bytes it could not
interpret, and therefore let through: 2.2%).

## Where the tool is documented

How to run `fb-basic-to-sav.py`, and which flag goes with which build, is in the
[manual](../manual/building.md).

## Aside

**`POKE` (`$9E`), `PEEK` (`$CF`) and `CALL` (`$AD`) are available.**
Even without expanding anything, WRAM that BASIC is not using can be reached through
`POKE`/`PEEK` as scratch storage:

```basic
POKE 24576,65 : PRINT PEEK(24576)   ' $6000 → returns 65
```
