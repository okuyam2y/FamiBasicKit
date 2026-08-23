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
The reserved-word table is re-read from `$CCAB` in the ROM and cross-checked too.
```

A `00` terminator follows the last line. The end pointer addresses **the byte after it**.

The token table differs by version: 87 words from `$C120-$C2BF` for the V2 series (Rev 2),
**109 words from `$CCAB-$CEBD` for V3** (`TR` `GAME` `SCR$` `INSTR` `RESUME` and 18 more
are added in V3). Using the old 87-word table on V3 turns every added word into individual
ASCII characters and breaks it silently.
"""

import argparse
import re
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
# `$CCAB-$CEBD`.** The old 87-word table came from V2.1A and was missing the 22 words V3
# adds (`TR` `FIND` `GAME` `BGTOOL` `AUTO` `DELETE` `RENUM` `FILTER` `CLICK` `SCREEN`
# `BACKUP` `ERROR` `RESUME` `BGPUT` `BGGET` `CAN` `SCR$` `INSTR` `CRASH` `ERR` `ERL`
# `VCT`). Converting without them turns those words into individual ASCII characters and
# breaks the program silently.
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


def encode_body(text, tokens, small_digits=True):
    """Encode a line body into a token stream.

    WARNING: **numbers have three storage forms** (see the notes near `SMALL_MAX`).
    A version that wrote everything as `$12 lo hi` made any program containing
    `GOTO`/`GOSUB` fail with `?SN ERROR` (found on hardware). Line numbers must be
    `$0B lo hi`.
    """
    out = bytearray()
    i = 0
    in_name = False          # are we mid-identifier? (keeps `X0` from becoming X plus 0)
    expect_ref = False       # is the next number a line number? (survives spaces and `,`)
    while i < len(text):
        ch = text[i]

        if text.startswith("\\x", i) and len(text) >= i + 4:   # escape for a raw byte
            out.append(int(text[i + 2:i + 4], 16))
            i += 4
            in_name = False
            continue

        if ch == '"':                                  # strings are kept raw
            j = text.find('"', i + 1)
            if j < 0:
                raise ValueError(f'unterminated quote: {text}')
            out += encode_raw(text[i:j + 1])
            i = j + 1
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
                if word in RAW_AFTER:
                    out += encode_raw(text[i:])
                    return bytes(out)
                break
        else:
            if ch == "'":                              # shorthand for REM; raw from here on
                out += encode_raw(text[i:])
                return bytes(out)
            out += ch.upper().encode("ascii")          # spaces, punctuation, identifiers
            in_name = ch.isalpha()
            if ch not in " ,":                         # spaces and `,` continue a line-number list
                expect_ref = False
            i += 1

    return bytes(out)


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

    while i < len(data):
        b = data[i]
        if b == 0x22:                                   # string
            j = i + 1
            while j < len(data) and data[j] != 0x22:
                j += 1
            out.append('"' + "".join(raw(x) for x in data[i + 1:j]) + '"')
            i = j + 1
            continue
        if b in (0x0B, 0x11, 0x12):
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
                return "".join(out), escaped
            continue
        if b == 0x27:                                   # shorthand for REM
            out.append("'" + "".join(raw(x) for x in data[i + 1:]))
            return "".join(out), escaped
        out.append(raw(b))
        i += 1
    return "".join(out), escaped


def encode_line(number, body_text, tokens, small_digits):
    body = encode_body(body_text, tokens, small_digits)
    line = bytes([number & 0xFF, number >> 8]) + body + b"\x00"
    # The line length is one leading byte, so 255 is the maximum. Past that, bytes()
    # raises an opaque "bytes must be in range(0, 256)"
    if len(line) + 1 > 255:
        raise ValueError(f"line {number} is too long ({len(line) + 1} bytes, limit 255)")
    return bytes([len(line) + 1]) + line


def build_program(source, tokens, small_digits):
    out = bytearray()
    lineno_seen = set()
    prev = -1
    for raw in source.splitlines():
        raw = raw.rstrip()
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
        out += encode_line(number, m.group(2), tokens, small_digits)
    out += b"\x00"                                     # end of program
    return bytes(out)


# Where the four built-in programs start in a stock V3 (32KB PRG). Same values as in
# `fb-mmc5-16k.py`
BUILTIN_PROGRAMS = (0xD400, 0xDBFE, 0xE682, 0xF308)


def selftest(rom_path):
    """**Test the converter against the ROM built-in programs as ground truth.**

    Using my (or the tool's) understanding as ground truth lets a wrong understanding
    round-trip and pass. So the ground truth is the real byte sequence: decode, re-encode,
    and demand **not one byte differs**. Bytes escaped as `\\xNN` were not understood, so
    they are counted and reported (a high count makes "it passed" mean little).
    """
    raw = open(rom_path, "rb").read()
    if raw[:4] != b"NES\x1a" or raw[4] != 2:
        sys.exit(f"{rom_path}: not a stock V3 (iNES with a 32KB PRG)")
    prg = raw[16:16 + 0x8000]

    def cpu(a):
        return prg[a - 0x8000]

    # Re-read the reserved-word table from the ROM and cross-check it against the
    # built-in TOKENS_V3. **A skewed table would round-trip unnoticed**, so pin it here
    i = prg.find(b"\x80GOTO")
    if i < 0:
        sys.exit(f"{rom_path}: reserved-word table not found")
    a = 0x8000 + i
    from_rom = []
    while cpu(a) >= 0x80:
        tok = cpu(a)
        j, word = a + 1, ""
        while cpu(j) < 0x80 and 0x20 <= cpu(j) < 0x7F:
            word += chr(cpu(j))
            j += 1
        if not word:
            break
        from_rom.append((word, tok))
        a = j
    if sorted(from_rom) != sorted(TOKENS_V3):
        only_rom = sorted(set(from_rom) - set(TOKENS_V3))
        only_tbl = sorted(set(TOKENS_V3) - set(from_rom))
        sys.exit(f"reserved-word table disagrees with the ROM. ROM only: {only_rom} / table only: {only_tbl}")
    print(f"  reserved-word table: matches all {len(from_rom)} words in the ROM")

    tokens = TOKENS_BY_VERSION["v3"]
    ok = True
    total_lines = total_bytes = total_escaped = 0
    for start in BUILTIN_PROGRAMS:
        a = start
        lines = bad = 0
        while True:
            ln = cpu(a)
            if ln == 0:
                break
            number = cpu(a + 1) | (cpu(a + 2) << 8)
            body = bytes(cpu(x) for x in range(a + 3, a + ln - 1))
            text, escaped = decode_body(body, tokens)
            again = encode_body(text, tokens)
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
        print(f"  ${start:04X}: {lines} lines" + ("" if not bad else f" / {bad} mismatched"))
    pct = 100.0 * (total_bytes - total_escaped) / max(total_bytes, 1)
    print(f"  {total_lines} lines / {total_bytes} bytes total. "
          f"{total_bytes - total_escaped} bytes understood without escaping ({pct:.1f}%)")
    print("verdict:", "every line round-tripped byte for byte" if ok else "mismatches found")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
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
    ap.add_argument("--selftest", metavar="ROM",
                    help="given a stock V3 .nes, decode and re-encode the four built-in "
                         "programs and check that not one byte differs (no source needed)")
    args = ap.parse_args()

    if args.selftest:
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
    program = build_program(open(args.source, encoding="utf-8").read(), tokens,
                            SMALL_DIGITS_BY_VERSION[args.version])
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

    open(args.output, "wb").write(bytes(sav))

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


if __name__ == "__main__":
    main()
