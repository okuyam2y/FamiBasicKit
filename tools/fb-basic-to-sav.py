#!/usr/bin/env python3
"""Convert BASIC written as plain text into a Family BASIC `.sav`.

  $ ./fb-basic-to-sav.py prog.bas -o out.sav                    # V2.1A (default)
  $ ./fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3, 8KB build

A `.sav` is WRAM `$6000-$7FFF` laid out from offset 0.

## The layout differs by version

| | V1.0 / V2.0A / V2.1A | V3.0 |
|---|---|---|
| Start of area | `$7000` | `$6000` |
| Program body | `$703E` (offset `0x103E`) | `$6006` (offset `0x0006`) |
| Boot signature | `$703A`=`$5A` / **`$703B`=`$33`** | `$6001`=`$4C` |
| End-of-program pointer | `$703C/$703D` | `$6002/$6003` |
| End of area | `$77FF` (`$7FFF` when expanded) | `$6FFF` (`$7FFF` when expanded) |

WARNING: **You need both the signature and the end pointer.** With the signature alone you
get as far as "there is data on the cassette", but without the end pointer the program
counts as empty and `LIST` shows nothing. The `CMP #$CC` at `$CF5B` is a different path.

## Line storage format (measured on hardware, checked byte for byte)

```
line = [length 1B (including itself)][line number 2B LE][body...][00]
body = token (1B, $80 and up) / raw ASCII / number / string (quotes kept raw)

Numbers have three forms plus one (established from the 387 lines of built-in programs):

| Form | Bytes | What |
|---|---|---|
| Small integer | `$01`-`$0A` | **0-9** (value + 1) |
| Integer | `$12 lo hi` | **10 and above** |
| Hex | `$11 lo hi` | written as `&Hxxxx` |
| Line number | `$0B lo hi` | after `GOTO`/`GOSUB`/`THEN`/`RESUME`/`RESTORE`, and a following `,` |

Example: 10 POKE 24576,65
    0d 0a 00 9e 20 12 00 60 2c 12 41 00 00
    ^^ ^^^^^ ^^ ^^ ^^^^^^^^ ^^ ^^^^^^^^ ^^
    13 ln10  PO sp 24576    ,  65       end

WARNING: **A version that wrote everything as `$12 lo hi` made any program containing
`GOTO`/`GOSUB` fail with `?SN ERROR`** (found on hardware). Everything after `REM` and
the shorthand quote is stored raw.

## The self-test (--selftest)

    $ ./fb-basic-to-sav.py --selftest "Family BASIC V3 (Japan).nes"

**The ground truth is the contents of the ROM, not my understanding of them.** It decodes
the four built-in programs (387 lines), re-encodes them, and demands not one byte differs.

The reserved-word table is re-read from the ROM and cross-checked too - **for whichever
version the ROM is**. Pass a V2 dump and it checks the V2 list; only V3 has built-in
programs to round-trip, so that part is skipped there. Running it against V3 alone is what
let `SCR$` sit missing from the V2 list.
```

A `00` terminator follows the last line. The end pointer addresses **the byte after it**.

The token table differs by version: **88 words at `$C128-$C2C5` for the V2 series**
(Rev 2) and **109 at `$CCAB-$CEBE` for V3** (`TR` `GAME` `INSTR` `RESUME` and 17 more are
V3 additions - 21 in all, the difference between the two counts). Using the V2 table on V3 turns every added word into individual ASCII
characters and breaks the program silently.
"""

import argparse
import re
import os
import sys

WRAM_BASE = 0x6000           # the CPU address that offset 0 of a .sav corresponds to

# Per-version layout. All addresses are CPU addresses.
LAYOUTS = {
    "v2": {
        "name": "V1.0 / V2.0A / V2.1A",
        "prog": 0x703E,               # start of the program body
        "sig": ((0x703A, 0x5A), (0x703B, 0x33)),
        "endptr": 0x703C,             # receives "the address after the terminator"
        "top": 0x77FF,                # end of area ($7FFF when expanded)
    },
    "v3": {
        "name": "V3.0",
        "prog": 0x6006,
        "sig": ((0x6001, 0x4C),),
        "endptr": 0x6002,
        "top": 0x6FFF,
    },
}
TOP_EXPANDED = 0x7FFF        # for a ROM patched by fb-expand-basic-area.py
TOP_16K = 0x9FFF             # the 16KB build from fb-mmc5-16k.py ($6000-$9FFF)
SAV_SIZES = (8192, 32768)    # 8KB for an MMC5 build; 32768 for a stock ROM on MiSTer

# Longer words must be matched first, so the order is preserved
TOKENS_V2 = [
    ("RESTORE", 0x84), ("POSITION", 0xA7), ("CSRLIN", 0xDB), ("INKEY$", 0xDE),
    ("RIGHT$", 0xDF), ("SYSTEM", 0x87), ("SPRITE", 0x8A), ("LINPUT", 0x90),
    ("RETURN", 0x83), ("LOCATE", 0xAE), ("STRIG", 0xD6), ("LEFT$", 0xE0),
    ("GOSUB", 0x81), ("PRINT", 0x8B), ("PAUSE", 0x8E), ("INPUT", 0x8F),
    ("CLEAR", 0x99), ("CGSET", 0x9F), ("COLOR", 0xA9), ("PALET", 0xAF),
    ("STICK", 0xD5), ("MID$", 0xD4), ("STR$", 0xCC), ("CHR$", 0xDC),
    ("HEX$", 0xDD), ("GOTO", 0x80), ("THEN", 0x85), ("LIST", 0x86),
    ("STEP", 0x89), ("NEXT", 0x8D), ("DATA", 0x91), ("READ", 0x93),
    ("STOP", 0x96), ("CONT", 0x97), ("POKE", 0x9E), ("VIEW", 0xA0),
    ("MOVE", 0xA1), ("PLAY", 0xA3), ("BEEP", 0xA4), ("LOAD", 0xA5),
    ("SAVE", 0xA6), ("CGEN", 0xAB), ("SWAP", 0xAC), ("CALL", 0xAD),
    ("PEEK", 0xCF), ("SGN", 0xD1), ("TAB", 0xD3), ("XPOS", 0xD7),
    # FOR was added after cross-checking the whole table against the ROM. It had been
    # missing, so "FOR" turned into "F" plus the OR token. Keep it ahead of "OR".
    # SCR$ was missing too, and for longer: the table check below only ever ran against
    # a V3 ROM, so nothing compared the V2 list to anything until 2026-08-24.
    ("SCR$", 0xE1),
    ("YPOS", 0xD8), ("FOR", 0x8C), ("RUN", 0x82), ("DIM", 0x94), ("REM", 0x95),
    ("CLS", 0x98), ("OFF", 0x9B), ("CUT", 0x9C), ("NEW", 0x9D),
    ("END", 0xA2), ("KEY", 0xA8), ("DEF", 0xAA), ("ERA", 0xB0),
    ("XOR", 0xEF), ("AND", 0xF1), ("NOT", 0xF2), ("MOD", 0xFB),
    ("ABS", 0xCA), ("ASC", 0xCB), ("FRE", 0xCD), ("LEN", 0xCE),
    ("RND", 0xD0), ("SPC", 0xD2), ("VAL", 0xD9), ("POS", 0xDA),
    ("TO", 0x88), ("IF", 0x92), ("ON", 0x9A), ("OR", 0xF0),
    ("<>", 0xF3), (">=", 0xF4), ("<=", 0xF5),
    ("=", 0xF6), (">", 0xF7), ("<", 0xF8),
    ("+", 0xF9), ("-", 0xFA), ("/", 0xFC), ("*", 0xFD),
]


# V3.0 reserved words. **Built by reading the <token><word> sequence out of the ROM at
# `$CCAB-$CEBE`.** An older hand-written table came from V2.1A and was missing the 21 words
# V3 adds (`TR` `FIND` `GAME` `BGTOOL` `AUTO` `DELETE` `RENUM` `FILTER` `CLICK` `SCREEN`
# `BACKUP` `ERROR` `RESUME` `BGPUT` `BGGET` `CAN` `INSTR` `CRASH` `ERR` `ERL` `VCT`).
# Converting without them turns those words into individual ASCII characters and breaks
# the program silently.
#
# `SCR$` is **not** one of them: V2.1A has it too, at the same `$E1`. It was listed here as
# a V3 addition, and counted V2.1A at 87 words instead of 88 - the same word that went
# missing from a hand-written copy once before. Corrected from both ROMs.
# WARNING: **these words cannot be used as variable names** (BASIC has the same rule).
TOKENS_V3 = [
    ("GOTO", 0x80), ("GOSUB", 0x81), ("RUN", 0x82), ("RETURN", 0x83),
    ("RESTORE", 0x84), ("THEN", 0x85), ("LIST", 0x86), ("SYSTEM", 0x87),
    ("TO", 0x88), ("STEP", 0x89), ("SPRITE", 0x8A), ("PRINT", 0x8B),
    ("FOR", 0x8C), ("NEXT", 0x8D), ("PAUSE", 0x8E), ("INPUT", 0x8F),
    ("LINPUT", 0x90), ("DATA", 0x91), ("IF", 0x92), ("READ", 0x93),
    ("DIM", 0x94), ("REM", 0x95), ("STOP", 0x96), ("CONT", 0x97),
    ("CLS", 0x98), ("CLEAR", 0x99), ("ON", 0x9A), ("OFF", 0x9B),
    ("CUT", 0x9C), ("NEW", 0x9D), ("POKE", 0x9E), ("CGSET", 0x9F),
    ("VIEW", 0xA0), ("MOVE", 0xA1), ("END", 0xA2), ("PLAY", 0xA3),
    ("BEEP", 0xA4), ("LOAD", 0xA5), ("SAVE", 0xA6), ("POSITION", 0xA7),
    ("KEY", 0xA8), ("COLOR", 0xA9), ("DEF", 0xAA), ("CGEN", 0xAB),
    ("SWAP", 0xAC), ("CALL", 0xAD), ("LOCATE", 0xAE), ("PALET", 0xAF),
    ("ERA", 0xB0), ("TR", 0xB1), ("FIND", 0xB2), ("GAME", 0xB3),
    ("BGTOOL", 0xB4), ("AUTO", 0xB5), ("DELETE", 0xB6), ("RENUM", 0xB7),
    ("FILTER", 0xB8), ("CLICK", 0xB9), ("SCREEN", 0xBA), ("BACKUP", 0xBB),
    ("ERROR", 0xBC), ("RESUME", 0xBD), ("BGPUT", 0xBE), ("BGGET", 0xBF),
    ("CAN", 0xC0), ("ABS", 0xCA), ("ASC", 0xCB), ("STR$", 0xCC),
    ("FRE", 0xCD), ("LEN", 0xCE), ("PEEK", 0xCF), ("RND", 0xD0),
    ("SGN", 0xD1), ("SPC", 0xD2), ("TAB", 0xD3), ("MID$", 0xD4),
    ("STICK", 0xD5), ("STRIG", 0xD6), ("XPOS", 0xD7), ("YPOS", 0xD8),
    ("VAL", 0xD9), ("POS", 0xDA), ("CSRLIN", 0xDB), ("CHR$", 0xDC),
    ("HEX$", 0xDD), ("INKEY$", 0xDE), ("RIGHT$", 0xDF), ("LEFT$", 0xE0),
    ("SCR$", 0xE1), ("INSTR", 0xE2), ("CRASH", 0xE3), ("ERR", 0xE4),
    ("ERL", 0xE5), ("VCT", 0xE6), ("XOR", 0xEF), ("OR", 0xF0),
    ("AND", 0xF1), ("NOT", 0xF2), ("<>", 0xF3), (">=", 0xF4),
    ("<=", 0xF5), ("=", 0xF6), (">", 0xF7), ("<", 0xF8),
    ("+", 0xF9), ("-", 0xFA), ("MOD", 0xFB), ("/", 0xFC),
    ("*", 0xFD),
]

# Match **longest first**, or `FOR` becomes `F` + `OR` and `INSTR` becomes `IN` + `STR`
TOKENS_BY_VERSION = {
    "v2": sorted(TOKENS_V2, key=lambda kv: -len(kv[0])),
    "v3": sorted(TOKENS_V3, key=lambda kv: -len(kv[0])),
}

# --- How numbers are stored (established from the 387 lines of built-in programs) -----
#   0-9         1 byte (value + 1 = $01-$0A)
#   10 and up   `$12 lo hi`
#   &Hxxxx      `$11 lo hi`
#   line number `$0B lo hi` (after GOTO / GOSUB / THEN / RESUME / RESTORE, and a `,` after)
# Evidence: among `$12 lo hi` values, **nothing below 10 ever appears**, while 10 appears
# 24 times. So the short form covers 0-9 and the long form starts at 10.
# WARNING: **the single-digit short form is V3 only.** The V2 series stores 0-9 as
# `$12 lo hi`. Source: micahcowan/fbdasm DETAILS.md, "This format for single-digit numbers
# is not used by Family BASIC v2." Without switching on version, V2 .sav files come out
# broken
SMALL_MAX = 9
SMALL_DIGITS_BY_VERSION = {"v2": False, "v3": True}
LINE_REF_AFTER = {"GOTO", "GOSUB", "THEN", "RESUME", "RESTORE"}
# Everything after these words is stored raw to end of line (neither tokenised nor
# converted to numbers)
RAW_AFTER = {"REM", "DATA"}


def encode_number(value, kind=0x12):
    if not -32768 <= value <= 65535:
        raise ValueError(f"number out of range: {value}")
    return bytes([kind, value & 0xFF, (value >> 8) & 0xFF])


NUMBER_TOKENS = (0x0B, 0x11, 0x12)      # each is followed by two operand bytes


def scan_literals(body):
    """Walk an encoded line body once.

    Returns `([(length, closed), ...], ends inside a string, operand bytes still owed)`.
    One walk answers all three because they are the same walk asked three times, and
    separate walks of the same rule are how they came to disagree.

    Each literal carries whether it was **closed**, because the certainty of the length
    rule differs between the two: a closed literal past the limit is measured, an
    unterminated one is not. A single "did the line end inside a string" cannot say which
    of several literals the over-long one was (found in review).

    ★ **One walk, answering both questions.** They were two functions with the same rule
    written twice, and the copies disagreed within a round: `literals_in` stepped over the
    two operand bytes after `$0B`, `$11` and `$12` while `inside_string` counted every
    `$22` in the line. So `A=34:DATA 11,22` - where 34 encodes as `12 22 00` - looked like
    an open string, `DATA` was taken for text instead of a word, and its fields were
    tokenised. This file says a few lines below what that costs: `READ` then fails with
    `?TM ERROR` on the machine. **A valid program, silently miscompiled** (found twice
    independently, one case being `GOTO 34: REM comment`).

    ⚠️ The operand skip only applies **outside** a string. Inside one, `$12` is a character.

    ⚠️ An unterminated literal is measured to the end of the line, which is where it runs
    to on the machine. Whether the 31-byte limit applies to it has not been measured -
    what was measured is a closed one - so counting it is the conservative reading, and
    `--allow-long-strings` is the way past it.
    """
    lengths, i, inside, start, pending = [], 0, False, 0, 0
    while i < len(body):
        if not inside and body[i] in NUMBER_TOKENS:
            if i + 3 > len(body):
                # The stream stops part-way through a number's two operand bytes. Those
                # bytes mean nothing on their own - `$91` after `$12` is half of a number,
                # not `DATA` - so a caller adding the next byte must not read it as syntax
                # (found in review).
                pending = i + 3 - len(body)
                break
            i += 3
            continue
        if body[i] == 0x22:
            if inside:
                lengths.append((i - start - 1, True))
                inside = False
            else:
                inside, start = True, i
        i += 1
    if inside:
        lengths.append((len(body) - start - 1, False))
    return lengths, inside, pending


def encode_body(text, tokens, small_digits=True, literals=None, state=None):
    """Encode a line body into a token stream.

    WARNING: **numbers have three storage forms** (see the notes near `SMALL_MAX`).
    A version that wrote everything as `$12 lo hi` made any program containing
    `GOTO`/`GOSUB` fail with `?SN ERROR` (found on hardware). Line numbers must be
    `$0B lo hi`.

    Pass a list as `literals` to be told how long each **string literal** was, in encoded
    bytes. Only this function knows which quotes are literals: everything after `REM`,
    `DATA` or `'` is stored raw, quotes and all, so counting quote bytes in the finished
    line cannot tell a literal from a comment (found in review).
    """
    out = bytearray()
    by_token = {tok: word for word, tok in tokens}
    i = 0
    in_name = False          # are we mid-identifier? (keeps `X0` from becoming X plus 0)
    expect_ref = False       # is the next number a line number? (survives spaces and `,`)

    def finish():
        """Report the string literals, measured on the bytes that will run.

        Not on the source spans. Two things defeat that: `\\x22` emits a real quote, so one
        written span can be two literals in memory (and was refused as one long one), and a
        literal written **entirely** in escapes never opens a source span at all, so it was
        not measured and slipped past the check completely (both found in review).

        So the quote bytes in the finished line are what is counted, up to wherever a
        `REM`, a `DATA` or an apostrophe turned the rest of the line into text nobody
        executes."""
        found, ends_inside, _owed = scan_literals(bytes(out[:raw_from[0]]))
        if literals is not None:
            literals.extend(found)
        if state is not None:
            # ⚠️ Handed back rather than recomputed. A caller that rescans the finished
            # line does not know where the `REM`/`DATA` tail began, so an unmatched quote
            # in a comment looked like an open string (found in review).
            state["ends_inside"] = ends_inside
        return bytes(out)

    raw_from = [None]        # where an unexecuted tail begins, if the line has one

    def inside_string():
        """Is the stream emitted so far inside an open string literal?

        ⚠️ **Quote state belongs to the bytes, not to the source.** `REM` and `DATA` end
        the executable part of a line - but only when they are words, and inside a string
        they are text. Writing the opening quote as `\\x22` put the encoder in a string
        that the *source* scanner could not see, so `PRINT \\x22REM ` followed by forty
        bytes and a closing `\\x22` cut the executable part at the `REM` and measured one
        byte instead of a 42-byte literal - a program the machine stops on, converted
        without complaint (found in review).

        The walk itself is `scan_literals`, shared with the length check, because the two
        used to be separate and disagreed.
        """
        return scan_literals(bytes(out))[1]

    def owes_operand():
        """Is the stream part-way through a number token's two operand bytes?"""
        return scan_literals(bytes(out))[2] > 0
    while i < len(text):
        ch = text[i]

        if text.startswith("\\x", i) and len(text) >= i + 4:
            # ★ **An escaped byte goes through the same state machine as everything else.**
            # It is a way of spelling a byte, and a byte can *be* a reserved word: `\\x80`
            # is `GOTO`, `\\x91` is `DATA`. So it arms the line-number expectation, or
            # starts a raw tail, exactly as the written word would.
            #
            # ⚠️ Three rounds were spent adding one byte value at a time here - first the
            # quote, then the backslash - and each time the next round found another. The
            # rule is not a list of bytes; it is "do what this byte means" (found in
            # review, which supplied `\\x80 100` storing a `$12` where GOTO needs `$0B`,
            # `\\x91 11,22` tokenising DATA's fields into `?TM ERROR`, and `\\xC1` before a
            # number breaking the round trip because `chr(0xC1).isalpha()` is true in
            # Python and has nothing to do with this machine).
            was_inside, was_operand = inside_string(), owes_operand()
            byte = int(text[i + 2:i + 4], 16)
            out.append(byte)
            i += 4
            if was_inside or was_operand:
                # Inside a string every byte is content; inside a number's operands every
                # byte is half of a number. Neither is syntax, so neither changes the
                # state - `\\x12\\x91\\x00` is the number 145, not a number and then `DATA`
                # (found in review).
                continue
            if in_name and 0x30 <= byte <= 0x39:
                # A digit inside an identifier stays part of the name, exactly as the
                # written path leaves `X0` alone. Losing that made `A\\x311=2` encode the
                # second `1` as a number (found in review).
                continue
            if byte == 0x27:
                # The apostrophe is stored as itself, not as a token, and the machine reads
                # it as "comment to end of line" wherever it finds it. Writing it started a
                # raw tail and spelling it did not, so `\\x27 100` tokenised a number into
                # what the machine treats as comment text (found in review).
                raw_from[0] = len(out)
                out += encode_raw(text[i:])
                return finish()
            word = by_token.get(byte)
            if word is not None:
                in_name = False
                expect_ref = word in LINE_REF_AFTER
                if word in RAW_AFTER:
                    raw_from[0] = len(out)
                    out += encode_raw(text[i:])
                    return finish()
            else:
                # ⚠️ ASCII, not Unicode. `chr(0xC1)` is a letter to Python and a byte with
                # no meaning to this BASIC.
                in_name = byte < 0x80 and chr(byte).isalpha()
                if byte not in (0x20, 0x2C):   # a space and a comma keep a line-number list
                    expect_ref = False
            continue

        if inside_string():
            # ★ **A quote byte opens a string on the machine, however it was written.**
            # Past one, everything up to the next quote byte is content: the interpreter
            # copies it, it does not tokenise it. This did tokenise it, so
            # `PRINT \\x22GOTO\\x22` came out as the single token `$80` between quotes -
            # a different program from the one written, produced without a word, and
            # measured as a one-byte literal so the length check waved it through
            # (found in review).
            #
            # Case is kept for the same reason: inside a string the machine stores what it
            # was given. The `"` path has always copied raw; this makes the escaped
            # opener behave the same way, which is the point - one rule for one thing.
            out += text[i].encode("ascii")
            i += 1
            in_name = expect_ref = False
            continue

        if ch == '"':
            # ★ **One operation: emit a quote byte.** Opening and closing are the same
            # thing, because on the machine they are the same byte - and the branch above
            # copies whatever follows while the stream is inside a string, so a literal
            # `"` and a `\\x22` are now interchangeable in both directions.
            #
            # ⚠️ This used to take the whole span up to the next `"` **in the source**,
            # which is a different notion of "string" from the one the machine has. So
            # `PRINT"A\\x22` was refused although its bytes are a closed string, and
            # `PRINT"A\\x22:GOTO 100:PRINT\\x22B"` left `GOTO` as characters instead of a
            # token (found in review - the third round in a row where this file decided
            # something about strings in the source's terms rather than the bytes').
            out.append(0x22)
            i += 1
            in_name = expect_ref = False
            continue

        if text.upper().startswith("&H", i) and not in_name:
            j = i + 2
            while j < len(text) and text[j] in "0123456789ABCDEFabcdef":
                j += 1
            out += encode_number(int(text[i + 2:j], 16), 0x11)
            i = j
            in_name = expect_ref = False
            continue

        if ch.isdigit() and not in_name:
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            v = int(text[i:j])
            if expect_ref:
                out += encode_number(v, 0x0B)          # line number
            elif small_digits and v <= SMALL_MAX:
                out.append(v + 1)                      # 0-9 in one byte (V3 only)
            else:
                out += encode_number(v)
            i = j
            in_name = False
            continue

        if ch.isdigit():                               # part of an identifier (e.g. X0)
            out += ch.encode("ascii")
            i += 1
            continue

        for word, tok in tokens:                       # reserved words
            if text.upper().startswith(word, i):
                out.append(tok)
                i += len(word)
                in_name = False
                expect_ref = word in LINE_REF_AFTER
                # Nothing after `REM` or `DATA` is tokenised. Encoding `DATA 11,22`
                # with numeric tokens makes `READ` fail with `?TM ERROR`
                # (measured on hardware)
                # No `inside_string()` here: the branch at the top of the loop already
                # took every character while the stream is inside one, so this line is
                # only reached outside. Guarding again read as if the state could be true
                # here, which is a check that cannot fail (found in review).
                if word in RAW_AFTER:
                    raw_from[0] = len(out)
                    out += encode_raw(text[i:])
                    return finish()
                break
        else:
            if ch == "'":                              # shorthand for REM; raw from here on
                raw_from[0] = len(out)
                out += encode_raw(text[i:])
                return finish()
            out += ch.upper().encode("ascii")          # spaces, punctuation, identifiers
            in_name = ch.isalpha()
            if ch not in " ,":                         # spaces and `,` continue a line-number list
                expect_ref = False
            i += 1

    return finish()


def encode_raw(text):
    """The inside of a string or a REM. Only `\\xNN` turns back into a raw byte."""
    out = bytearray()
    i = 0
    while i < len(text):
        if text.startswith("\\x", i) and len(text) >= i + 4:
            out.append(int(text[i + 2:i + 4], 16))
            i += 4
        else:
            out.append(ord(text[i]))
            i += 1
    return bytes(out)


def decode_body(data, tokens, small_digits=True):
    """The inverse of `encode_body`. **It exists for the round-trip test (--selftest).**

    Unprintable bytes are escaped as `\\xNN`. The round-trip test counts and reports how
    many were escaped, so bytes it failed to understand cannot quietly pass.
    """
    rev = {tok: word for word, tok in tokens}
    out = []
    escaped = 0
    i = 0

    def raw(b):
        nonlocal escaped
        if 0x20 <= b < 0x7F and b != 0x5C:
            return chr(b)
        escaped += 1
        return "\\x%02X" % b

    def keep_tail(text):
        """Spaces at the end of a line, escaped so they survive the trip back.

        `build_program` strips each source line's tail, and has to: trailing whitespace in
        a text file is noise, and that strip is also what handles CRLF input. So a line
        that really ends in spaces came back one or two bytes shorter, with nothing said -
        `--extract` then `--insert` quietly rewrote it and still reported that every byte
        was understood. Escaped, the bytes survive and are counted."""
        nonlocal escaped
        n = len(data) - len(bytes(data).rstrip(b"\x20"))
        if n and text.endswith(" " * n):
            escaped += n
            return text[:-n] + "\\x20" * n
        return text

    while i < len(data):
        b = data[i]
        if b == 0x22:                                   # string
            j = i + 1
            while j < len(data) and data[j] != 0x22:
                j += 1
            if j >= len(data):
                # The line ends inside the string. Closing it here made the text look
                # finished, reported nothing unread, and cost a byte on the way back:
                # re-encoding `PRINT"X"` gives four bytes where the disk had three, so
                # extract-then-insert silently rewrote the program.
                #
                # Escaping the quote and everything after it keeps the bytes exactly, and
                # puts them in the count of what was not understood - which is the honest
                # answer, because whether this line is legal is a question about the
                # machine and nothing in this repository settles it.
                for x in data[i:]:
                    escaped += 1
                    out.append("\\x%02X" % x)
                i = len(data)
                continue
            out.append('"' + "".join(raw(x) for x in data[i + 1:j]) + '"')
            i = j + 1
            continue
        if b in NUMBER_TOKENS:
            # A two-byte number needs two bytes after the token. A line that ends in the
            # middle of one is a corrupt line, not an IndexError.
            if i + 2 >= len(data):
                raise ValueError(f"a number token ${b:02X} at offset {i} has only "
                                 f"{len(data) - i - 1} of its 2 bytes")
            v = data[i + 1] | (data[i + 2] << 8)
            out.append("&H%04X" % v if b == 0x11 else str(v))
            i += 3
            continue
        if small_digits and 1 <= b <= SMALL_MAX + 1:
            out.append(str(b - 1))
            i += 1
            continue
        if b in rev:
            out.append(rev[b])
            i += 1
            if rev[b] in RAW_AFTER:                     # raw from here on
                out.append("".join(raw(x) for x in data[i:]))
                return keep_tail("".join(out)), escaped
            continue
        if b == 0x27:                                   # shorthand for REM
            out.append("'" + "".join(raw(x) for x in data[i + 1:]))
            return keep_tail("".join(out)), escaped
        out.append(raw(b))
        i += 1
    return keep_tail("".join(out)), escaped


# Measured on real hardware, 2026-08-25: a 31-byte string literal prints and a 32-byte one
# raises `?IL ERROR` when the line runs. The pair that showed it differ by one leading
# character and nothing else, and the shorter line as a whole is 42 characters, so the
# ceiling is on the literal rather than on the line:
#
#     PRINT"LAYING - STOP WITH POKE 31728,0"     31   prints
#     PRINT"PLAYING - STOP WITH POKE 31728,0"    32   ?IL ERROR
#
# Nintendo's own built-in programs agree as far as they go: across the 387 lines the
# selftest decodes there are 104 literals and the longest is 28 bytes. That is consistent
# with the limit, not a proof of it - nobody wrote a 31 to find out.
#
# ⚠️ Why it reports as `IL` is not established. The error table at `$B37F` reads
# `NF SN RG OD IL OV OM UL SO DD`, so `SO` - string overflow - exists and is a different
# code that this does not raise. The number is measured; the reason is not.
MAX_STRING = 31


def encode_line(number, body_text, tokens, small_digits, allow_long_strings=False):
    # The lengths come from `encode_body`, which is the only place that knows a quote is
    # opening a string rather than sitting inside a `REM` or a `DATA` tail. A first version
    # counted quote bytes in the finished line instead and refused
    # `10 REM "<32 characters>"` - a line the machine never looks at, because `REM` is not
    # executed (found in review).
    #
    # ⚠️ **Strings inside `DATA` are therefore not checked.** They are stored raw, and what
    # was measured was a literal in a statement that runs. Whether `READ` into a string
    # variable has the same ceiling has not been measured, and guessing it here would put
    # an unmeasured number in the same sentence as a measured one.
    literals = []
    body = encode_body(body_text, tokens, small_digits, literals)
    # Before the line-length check below, because a line can be legal by that one and still
    # hold a literal the machine will not print. It converted silently until 2026-08-25 and
    # the program then failed on the machine, at that line, with nothing on the PC saying so.
    # ★ The offending literal's **own** closure, not the line's. A closed 32-byte literal
    # followed by a short unterminated one was being reported as the unmeasured case
    # (found in review).
    offending = next(((n, c) for n, c in literals if n > MAX_STRING), None)
    over, closed = offending if offending else (None, True)
    if over is not None and not allow_long_strings:
        # ⚠️ The measurement is about the line **running**, and this refuses at conversion
        # - so a literal on a line the program never reaches is refused although it would
        # have run (found in review, with `10 GOTO 30 / 20 PRINT"<32>" / 30 PRINT"OK"`).
        # Deciding which lines run is not something a converter can do, so the choice is
        # between refusing a rare working program and letting through the common broken
        # one. It refuses, and says how to override - the message is the escape hatch.
        measured = ("the machine raises ?IL ERROR past "
                    f"{MAX_STRING} (measured)" if closed else
                    f"a closed literal past {MAX_STRING} raises ?IL ERROR (measured); "
                    f"whether an unterminated one does has **not** been measured, so this "
                    f"refusal is the conservative reading")
        raise ValueError(f"line {number} holds a string literal of {over} bytes; "
                         f"{measured}. If that line never runs, or you know better, "
                         f"pass --allow-long-strings.")
    line = bytes([number & 0xFF, number >> 8]) + body + b"\x00"
    # The line length is one leading byte, so 255 is the maximum. Past that, bytes()
    # raises an opaque "bytes must be in range(0, 256)"
    if len(line) + 1 > 255:
        raise ValueError(f"line {number} is too long ({len(line) + 1} bytes, limit 255)")
    return bytes([len(line) + 1]) + line


def build_program(source, tokens, small_digits, allow_long_strings=False):
    out = bytearray()
    lineno_seen = set()
    prev = -1
    for raw in source.splitlines():
        # ⚠️ Trailing spaces are stripped **unless the line ends inside a string**, where
        # they are content: `10 PRINT"X   ` holds three spaces on the machine, and
        # dropping them made the program differ from its source without saying so (found
        # in review). The state comes from encoding the line once - the same walk that
        # decides everything else about strings, rather than a second opinion.
        stripped = raw.rstrip()
        if stripped != raw:
            m0 = re.match(r"\s*(\d+)\s?(.*)$", raw)
            if m0:
                try:
                    st = {}
                    encode_body(m0.group(2), tokens, small_digits, state=st)
                    if st.get("ends_inside"):
                        stripped = raw
                except ValueError:
                    pass                      # the line is refused below, with its reason
        raw = stripped
        if not raw or raw.lstrip().startswith("'"):
            continue
        m = re.match(r"\s*(\d+)\s?(.*)$", raw)
        if not m:
            raise ValueError(f"no line number: {raw!r}")
        number = int(m.group(1))
        if number in lineno_seen:
            raise ValueError(f"duplicate line number: {number}")
        # BASIC walks the lines in memory **assuming they are in ascending order**.
        # Writing them out of order breaks LIST and GOTO. Silently sorting would change
        # the intent of the source, so fail instead
        if number < prev:
            raise ValueError(f"line numbers not ascending: {number} follows {prev}")
        prev = number
        lineno_seen.add(number)
        out += encode_line(number, m.group(2), tokens, small_digits,
                           allow_long_strings)
    out += b"\x00"                                     # end of program
    return bytes(out)


# Where the four built-in programs start in a stock V3 (32KB PRG). Same values as in
# `fb-mmc5-16k.py`
BUILTIN_PROGRAMS = (0xD400, 0xDBFE, 0xE682, 0xF308)
# How many lines each of them has, measured off the V3 dump once. The walk stops at a zero
# length byte, so damaging the first one silently skipped a whole program - see `selftest`.
BUILTIN_LINES = {0xD400: 56, 0xDBFE: 111, 0xE682: 103, 0xF308: 117}


def read_token_table(prg):
    """Pull the reserved words straight out of a PRG image.

    The table is `<token><word>` pairs, tokens from `$80` up, terminated by `$FF`. It is
    found by its first entry rather than by address, because V2 and V3 keep it in
    different places (`$C128` and `$CCAB`)."""
    i = prg.find(b"\x80GOTO")
    if i < 0:
        raise ValueError("reserved-word table not found")
    # Every read is bounds-checked. The table is found in a file, and a file can be
    # truncated in the middle of one: walking off the end then raised a bare IndexError
    # with a traceback, past every "refuse in words" message the callers put in front of it
    # A `ValueError` is what the callers already catch.
    out = []
    while True:
        if i >= len(prg):
            raise ValueError("the reserved-word table runs to the end of the image "
                             "without its $FF terminator")
        if prg[i] == 0xFF:
            return out
        tok, i = prg[i], i + 1
        word = ""
        while i < len(prg) and prg[i] < 0x80:
            word += chr(prg[i])
            i += 1
        if i >= len(prg):
            raise ValueError(f"the word for token ${tok:02X} runs to the end of the image")
        # A token with no word is not a reserved word. Accepting one put an empty string in
        # the table, which decodes to nothing (the token vanishes from the listing, counted
        # as understood) and matches at every position when encoding.
        if not word:
            raise ValueError(f"token ${tok:02X} in the reserved-word table has no word - "
                             f"the next byte is ${prg[i]:02X}, another token")
        out.append((word, tok))


# Where each index space starts. A command's entry in the dispatch table is at
# `token - $80`, a function's at `token - $CA`, so **the two spaces are not
# interchangeable and neither can be reordered**. `$EF-$FD` are the operators, handled
# elsewhere, and `$FF` terminates the reserved-word table.
CMD_BASE, FN_BASE, OP_BASE, TABLE_END = 0x80, 0xCA, 0xEF, 0xFF


def show_tokens(rom_path):
    """Print what a dump actually uses, and what is left.

    Adding a command means picking a number, and picking it off a hand-written table is
    how the wrong one gets picked. This reads the dump.

    A command's entry in the dispatch table is at `token - $80` and a function's at
    `token - $CA`. That table is positional, so a number can only be appended: inserting an
    entry in its middle moves every later handler one slot out of step with the token that
    selects it. (The reserved-word table this reads is not positional - it carries an
    explicit token byte per entry - so it is the dispatch table that forces this, and
    changing a number an existing word already has is what makes every program ever saved
    a different program.)"""
    raw = open(rom_path, "rb").read()
    if raw[:4] != b"NES\x1a":
        sys.exit(f"{rom_path}: not an iNES header")
    if len(raw) < 16:
        sys.exit(f"{rom_path}: an iNES header is 16 bytes, this file is {len(raw)}")
    prg = raw[16:16 + raw[4] * 16384]
    m = re.search(rb"NS-HUBASIC V\d\.\d[A-Z]?", prg)
    try:
        used = dict((t, w) for w, t in read_token_table(prg))
    except ValueError as e:
        # A 32KB file with an iNES header is not necessarily a Family BASIC dump, and
        # `read_token_table` says so by raising. `_run` only turns filesystem faults into
        # sentences, so this one arrived as a traceback (found in review).
        sys.exit(f"{rom_path}: {e}")

    print(f"## {rom_path}")
    print(f"  {m.group(0).decode() if m else '(no version string)'} / "
          f"{len(used)} reserved words")
    for name, lo, hi in (("commands ", CMD_BASE, FN_BASE - 1),
                         ("functions", FN_BASE, OP_BASE - 1)):
        taken = [t for t in range(lo, hi + 1) if t in used]
        free = [t for t in range(lo, hi + 1) if t not in used]
        print()
        print(f"  {name} ${lo:02X}-${hi:02X}: {len(taken)} used, {len(free)} free")
        for t in taken:
            print(f"    ${t:02X}  {used[t]}")
        print(f"    free: {ranges(free) or '(none)'}")
    print()
    print(f"  operators ${OP_BASE:02X}-${TABLE_END - 2:02X}: "
          + " ".join(f"${t:02X}={used[t]}" for t in range(OP_BASE, TABLE_END - 1)
                     if t in used))
    return True


def ranges(values):
    out = []
    for v in sorted(values):
        if out and v == out[-1][1] + 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return ", ".join(f"${a:02X}" if a == b else f"${a:02X}-${b:02X}" for a, b in out)


def selftest(rom_path):
    """**Test the converter against the ROM built-in programs as ground truth.**

    Using my (or the tool's) understanding as ground truth lets a wrong understanding
    round-trip and pass. So the ground truth is the real byte sequence: decode, re-encode,
    and demand **not one byte differs**. Bytes escaped as `\\xNN` were not understood, so
    they are counted and reported (a high count makes "it passed" mean little).
    """
    raw = open(rom_path, "rb").read()
    if len(raw) < 16 or raw[:4] != b"NES\x1a" or raw[4] != 2:
        sys.exit(f"{rom_path}: not a Family BASIC dump (iNES with a 32KB PRG)")
    prg = raw[16:16 + 0x8000]
    # ⚠️ The header *says* 32KB; this checks that the file actually carries it. A truncated
    # dump can still hold the version string and a complete word table, so it walked into
    # the built-in programs and indexed off the end - an `IndexError` traceback where a
    # sentence was required (found in review).
    if len(prg) != 0x8000:
        sys.exit(f"{rom_path}: the header declares a 32KB PRG but the file carries "
                 f"{len(prg)} bytes of it; this dump is truncated")

    def cpu(a):
        if not 0x8000 <= a < 0x10000:
            # A line record inside a built-in program that claims a length running past
            # the ROM. Refusing names the address; indexing raised a bare IndexError.
            sys.exit(f"{rom_path}: a built-in program reaches ${a:04X}, outside the PRG. "
                     f"This dump is damaged, or it is not the ROM it says it is.")
        return prg[a - 0x8000]

    m = re.search(rb"NS-HUBASIC V(\d)\.\d[A-Z]?", prg)
    if not m:
        sys.exit(f"{rom_path}: no version string, so which table to check is unknown")
    version = "v3" if m.group(1) == b"3" else "v2"
    print(f"  {m.group(0).decode()} -> checking the {version.upper()} table")

    # Re-read the reserved-word table from the ROM and cross-check it against the built-in
    # list. **A skewed list would round-trip unnoticed**, so pin it here. This ran against
    # V3 only until 2026-08-24, which is how `SCR$` stayed missing from the V2 list.
    try:
        from_rom = read_token_table(prg)
    except ValueError as e:
        sys.exit(f"{rom_path}: {e}")
    table = TOKENS_V3 if version == "v3" else TOKENS_V2
    if sorted(from_rom) != sorted(table):
        only_rom = sorted(set(from_rom) - set(table))
        only_tbl = sorted(set(table) - set(from_rom))
        sys.exit(f"the {version.upper()} reserved-word list disagrees with the ROM. "
                 f"ROM only: {only_rom} / list only: {only_tbl}")
    print(f"  reserved-word table: matches all {len(from_rom)} words in the ROM")

    if version != "v3":
        print("  (only V3 carries built-in programs, so the round-trip is skipped)")
        return True

    tokens = TOKENS_BY_VERSION["v3"]
    ok = True
    total_lines = total_bytes = total_escaped = 0
    # ⚠️ **How many lines each program has, pinned here.** A zero in a line-length byte
    # ends the walk, so damaging the *first* one skipped a whole program while `ok` stayed
    # true and the run still reported "every line round-tripped byte for byte" - the oracle
    # accepting a damaged ROM while saying it had checked it (found in review).
    #
    # These counts were read off this dump once and are the independent side of the check:
    # the walk finds them, and disagreeing means the dump is not the one this was measured
    # against. ★ They are per program on purpose - a total would let one program lose lines
    # while another gained them.
    for start in BUILTIN_PROGRAMS:
        a = start
        lines = bad = 0
        while True:
            ln = cpu(a)
            if ln == 0:
                break
            number = cpu(a + 1) | (cpu(a + 2) << 8)
            # ⚠️ The shape of the line, before its contents. A length under 4 cannot hold
            # the header and a terminator, and the last byte has to be the `$00` that ends
            # the line - this discarded it unchecked, so a ROM with a damaged terminator
            # still reported "every line round-tripped byte for byte". **The oracle was
            # accepting a damaged ROM while saying it had verified it** (found in review).
            if ln < 4 or cpu(a + ln - 1) != 0:
                sys.exit(f"{rom_path}: the built-in program at ${start:04X}, line {number}, "
                         f"claims {ln} bytes and ends with "
                         f"${cpu(a + ln - 1) if ln >= 4 else 0:02X}, not $00. This dump is "
                         f"damaged, so it cannot be used as ground truth.")
            body = bytes(cpu(x) for x in range(a + 3, a + ln - 1))
            try:
                text, escaped = decode_body(body, tokens)
            except ValueError as e:
                # A damaged built-in line - one ending in `$12` with no operand, say -
                # reached the user as a traceback. Naming where it is matters: the caller
                # is looking at a ROM, not at a program they wrote (found in review).
                sys.exit(f"{rom_path}: the built-in program at ${start:04X}, line "
                         f"{number}, will not decode: {e}")
            try:
                again = encode_body(text, tokens)
            except ValueError as e:
                # Decoding can succeed on bytes that will not go back - `&H` with no digits
                # after it, say. Same context as the decode failure above (found in review).
                sys.exit(f"{rom_path}: the built-in program at ${start:04X}, line "
                         f"{number}, decodes but will not re-encode: {e}")
            total_lines += 1
            total_bytes += len(body)
            total_escaped += escaped
            lines += 1
            if again != body:
                bad += 1
                ok = False
                if bad <= 2:
                    print(f"  [NG] ${start:04X} line {number}: changed on round trip")
                    print(f"       original   {body.hex(' ')}")
                    print(f"       re-encoded {again.hex(' ')}")
                    print(f"       as text    {text!r}")
            a += ln
        # ⚠️ Indexed, not `.get()`. A missing entry skipped the check in silence, which is
        # the same hole this check exists to close (found in review).
        if start not in BUILTIN_LINES:
            sys.exit(f"BUILTIN_LINES has no count for ${start:04X}, so that program would "
                     f"not be checked at all - the two tables in this file have drifted.")
        want_lines = BUILTIN_LINES[start]
        if lines != want_lines:
            sys.exit(f"{rom_path}: the built-in program at ${start:04X} has {lines} lines, "
                     f"not the {want_lines} this was measured against. A zero length byte "
                     f"ends the walk, so a damaged one shortens the program silently - "
                     f"this dump cannot be used as ground truth.")
        print(f"  ${start:04X}: {lines} lines" + ("" if not bad else f" / {bad} mismatched"))
    pct = 100.0 * (total_bytes - total_escaped) / max(total_bytes, 1)
    print(f"  {total_lines} lines / {total_bytes} bytes total. "
          f"{total_bytes - total_escaped} bytes understood without escaping ({pct:.1f}%)")
    print("verdict:", "every line round-tripped byte for byte" if ok else "mismatches found")
    return ok


def main():
    # `allow_abbrev=False`: argparse accepts `--ver v2` as `--version v2` by default, and
    # the check below - which asks whether the option was typed - looks for the full name
    # and misses it. Turning abbreviation off is the smaller change and removes a whole
    # class of "the parser accepted something the code does not know about".
    ap = argparse.ArgumentParser(allow_abbrev=False, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="the BASIC source text file")
    ap.add_argument("-o", "--output", help="the .sav to write")
    ap.add_argument("-V", "--version", choices=sorted(LAYOUTS), default="v2",
                    help="Family BASIC version (default v2 = V1.0/V2.0A/V2.1A)")
    ap.add_argument("--expanded", action="store_true",
                    help="for a ROM patched by fb-expand-basic-area.py (area ends at $7FFF)")
    ap.add_argument("--16k", dest="k16", action="store_true",
                    help="for the 16KB build from fb-mmc5-16k.py (area ends at $9FFF); "
                         "its .sav is 32768 bytes")
    ap.add_argument("--size", type=int, choices=SAV_SIZES, default=None,
                    help="output size. Default 8192 (32768 with --16k). 8192 for an "
                         "8KB MMC5 build; 32768 for the 16KB build and stock ROMs")
    ap.add_argument("--base", help="an existing .sav to build on (carries over names and so on)")
    ap.add_argument("--dump", action="store_true", help="print the generated bytes")
    ap.add_argument("--allow-long-strings", action="store_true",
                    help="convert string literals past the machine's %d-byte limit anyway. "
                         "A closed literal past it raises ?IL ERROR when the line runs "
                         "(measured); whether an unterminated one does has not been "
                         "measured, and is refused conservatively" % MAX_STRING)
    ap.add_argument("--selftest", metavar="ROM",
                    help="check the reserved-word list against a Family BASIC .nes, "
                         "and for V3 also decode and re-encode the four built-in programs "
                         "and demand not one byte differs (no source needed)")
    ap.add_argument("--tokens", metavar="ROM",
                    help="print the reserved words a dump uses and the numbers left over, "
                         "for picking one when adding a command")
    args = ap.parse_args()

    # `--selftest` and `--tokens` read a ROM and print; they write nothing and convert
    # nothing. Accepting the conversion arguments alongside them and ignoring them meant
    # `--selftest ROM -o out.sav` exited 0 with no `out.sav` anywhere - a success that says
    # a file was written when none was.
    # The two of them together ran only one and exited 0, so `--selftest A --tokens B`
    # reported success for a round-trip check it never performed.
    # `is not None`, not truthiness. `--selftest ""` - which is what an unset shell
    # variable expands to - read as "not given", so `--selftest "$A" --tokens "$B"` with
    # one of them empty ran the other and exited 0. An option that was
    # typed was typed, whatever its value; an empty ROM path is an error, not an absence.
    for flag, value in (("--selftest", args.selftest), ("--tokens", args.tokens),
                        ("-o", args.output), ("--base", args.base)):
        if value is not None and not value:
            ap.error(f"{flag} was given an empty name")
    if args.selftest is not None and args.tokens is not None:
        ap.error("--selftest and --tokens each read a ROM and print; run one at a time")
    for flag, value in (("--selftest", args.selftest), ("--tokens", args.tokens)):
        if value is None:
            continue
        # `-V` too: it picks the layout for a conversion, and neither of these converts.
        # Listing the arguments by hand is what left it out the first time; the rule is
        # every argument that only means something to the conversion path.
        # `is not None` for anything that takes a value, and `sys.argv` for `-V`, whose
        # default is a real string - comparing against the default cannot tell "not given"
        # from "given the same value", and an explicitly empty positional read as absent.
        # Both slipped through the version of this check that asked whether the value was
        # truthy - the same shape as the two before it.
        typed_v = any(a == "-V" or a.startswith("-V") or a == "--version"
                      or a.startswith("--version=") for a in sys.argv[1:])
        unused = [name for name, v in (("source", args.source is not None),
                                       ("--allow-long-strings", args.allow_long_strings),
                                       ("-o", args.output is not None),
                                       ("--base", args.base is not None),
                                       ("--size", args.size is not None),
                                       ("--expanded", args.expanded), ("--16k", args.k16),
                                       ("--dump", args.dump),
                                       ("-V", typed_v)) if v]
        if unused:
            ap.error(f"{flag} reads a ROM and prints; it does not convert anything, so "
                     f"{', '.join(unused)} would be ignored")

    if args.tokens is not None:
        sys.exit(0 if show_tokens(args.tokens) else 1)
    if args.selftest is not None:
        sys.exit(0 if selftest(args.selftest) else 1)
    if not args.source or not args.output:
        ap.error("source and -o are required unless --selftest is used")

    # --16k is only for the V3 16KB build. **Without checking the combination it would
    # quietly emit an 8KB .sav the 16KB build cannot read, or a .sav for a V2/16KB layout
    # that does not exist** (the 16KB build .sav is 32768 bytes).
    if args.k16:
        if args.version != "v3":
            sys.exit("--16k is V3 only (there is no 16KB build for V1/V2). Pass -V v3")
        if args.expanded:
            sys.exit("--16k and --expanded are mutually exclusive (they disagree on the end of area)")
        if args.size not in (None, 32768):
            sys.exit(f"a --16k .sav is 32768 bytes (--size {args.size} cannot be read by the 16KB build)")
        args.size = 32768
    elif args.size is None:
        args.size = 8192

    lay = LAYOUTS[args.version]
    if args.k16:
        top = TOP_16K
    elif args.expanded:
        top = TOP_EXPANDED
    else:
        top = lay["top"]
    prog_addr = lay["prog"]

    tokens = TOKENS_BY_VERSION[args.version]
    # Every fault build_program raises is a fault in the user's program - a missing line
    # number, a duplicate, lines out of order, a line or a literal too long. Uncaught they
    # arrived as a traceback with this file's own source in it. `fb-fds-file.py` already
    # catches the same exception for the same reason (found in its own review); the
    # rule had only ever been applied on that side.
    # ⚠️ Same reasoning as `fb-fds.py`: the output is opened for truncation, so naming an
    # input destroys it. `prog.bas -o prog.bas` exited successfully with the source
    # replaced by save data (found in review).
    if args.output and os.path.exists(args.output):
        for what, path in (("the source", args.source), ("--base", args.base)):
            if path and os.path.exists(path) and os.path.samefile(path, args.output):
                sys.exit(f"-o names {what} ({args.output}); this will not write over its "
                         f"own input")
    try:
        with open(args.source, encoding="utf-8") as fh:
            source = fh.read()
    except UnicodeDecodeError as e:
        # A `.bas` is text. Handing this one a disk image or a ROM is an easy slip, and
        # the codec's own words ("invalid start byte") do not say which mistake was made.
        # `UnicodeDecodeError` is a `ValueError`, so it was already refused rather than
        # thrown - this only changes the wording (raised in review; the traceback it claimed
        # does not happen).
        sys.exit(f"{args.source}: not text ({e.reason} at byte {e.start}) - a program "
                 f"source is what goes here, not a disk image or a ROM")
    try:
        program = build_program(source, tokens,
                                SMALL_DIGITS_BY_VERSION[args.version],
                                args.allow_long_strings)
    except ValueError as e:
        sys.exit(f"{args.source}: {e}")
    end_addr = prog_addr + len(program)                # the address AFTER the terminator
    capacity = top + 1 - prog_addr
    if len(program) > capacity:
        sys.exit(f"program does not fit in the area: {len(program)} bytes "
                 f"(${prog_addr:04X}-${top:04X} holds {capacity})")

    if args.base:
        sav = bytearray(open(args.base, "rb").read())
        if len(sav) < args.size:
            sys.exit(f"--base is smaller than the requested size: {len(sav)} < {args.size}")
        del sav[args.size:]
    else:
        sav = bytearray(args.size)

    off = prog_addr - WRAM_BASE
    if off + len(program) > len(sav):
        sys.exit(f"--size {args.size} does not leave room for the area")
    sav[off:off + len(program)] = program

    for addr, value in lay["sig"]:                     # boot signature
        sav[addr - WRAM_BASE] = value
    ep = lay["endptr"] - WRAM_BASE                     # end-of-program pointer
    sav[ep] = end_addr & 0xFF
    sav[ep + 1] = end_addr >> 8

    with open(args.output, "wb") as fh:
        fh.write(bytes(sav))

    print(f"{lay['name']} / area ${prog_addr:04X}-${top:04X}"
          f" ({capacity} bytes)")
    print(f"  program {len(program)} bytes -> ${prog_addr:04X}-${end_addr - 1:04X}"
          f" / {capacity - len(program)} bytes free")
    sigs = " ".join(f"${a:04X}=${v:02X}" for a, v in lay["sig"])
    print(f"  signature {sigs} / end pointer ${lay['endptr']:04X}=${end_addr:04X}")
    print(f"  wrote {args.size} bytes -> {args.output}")
    if args.dump:
        for i in range(0, len(program), 16):
            print("  " + program[i:i + 16].hex(" "))



def _run(fn):
    """Turn a filesystem refusal into a sentence, at the one place every path ends up.

    Every tool here takes paths from the command line, and until 2026-08-26 a missing or
    unreadable one arrived as a `FileNotFoundError` traceback with this file's own source
    in it - while every other bad input was refused in words (found in review).
    Catching it per `open()` would have meant the same rule at a dozen call sites; here it
    is one place per tool.

    ⚠️ **One place per tool, not one place** - this function is copied into each of them,
    which is the shape this project usually refuses. It is allowed here for a stated
    reason: every tool in `tools/` is a single file that runs on its own with nothing but
    the standard library, so there is nowhere shared to put it that does not make the
    tools depend on each other. What makes copies dangerous is holding a **fact** that can
    drift apart; this holds none - no address, no version, no size - so two copies can
    only ever differ in wording, not in what they decide.

    `BrokenPipeError` is deliberately not caught: `| head` closing the pipe is not a fault
    to report, and turning it into a message would put one on every truncated listing."""
    try:
        return fn()
    except BrokenPipeError:
        raise
    except OSError as e:
        where = f"{e.filename}: " if getattr(e, "filename", None) else ""
        sys.exit(f"{where}{e.strerror or e}")


if __name__ == "__main__":
    _run(main)
