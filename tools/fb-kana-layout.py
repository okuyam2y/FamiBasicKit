#!/usr/bin/env python3
"""Lay Family BASIC's kana out the way a JIS keyboard is printed.

  $ ./fb-kana-layout.py "Family BASIC (Japan) (Rev 2).nes" -o "V2.1A (JIS kana).nes"
  $ ./fb-kana-layout.py rom.nes --keyboard mister-jis -o out.nes
  $ ./fb-kana-layout.py rom.nes --dump          # show the layout, write nothing
  $ ./fb-kana-layout.py rom.nes --selftest

## What is going on

Family BASIC puts the kana on the keyboard in **gojuon order**: the left half of the
keyboard runs A-KA-SA-TA down the rows, the right half NA-HA-MA-YA. That is what the
HVC-007 keyboard has printed on its keytops. Plug a JIS 109 keyboard into a Famicom core
instead and **the printed kana mean nothing** - the key printed `ち` types `SA`.

This rewrites the ROM's kana tables so the printed kana are the ones you get.

## Five tables, and only three of them matter

The ROM decodes a keypress through five 72-byte tables, indexed by **keycode**, laid end
to end:

| Table | What it is used for |
|---|---|
| `tbl_KeyMap` | plain |
| `tbl_ShiftedKeyMap` | Shift held |
| `tbl_KanaKeyMap` | kana mode |
| `tbl_ShiftedKanaKeyMap` | kana mode, Shift held |
| `tbl_GrphKeyMap` | GRPH held - dakuten kana, and the graphics characters |

**The kana tables are wholly separate from the ASCII ones.** Only the last three are
touched here, so letters, digits and symbols keep working exactly as they did. (Remapping
in the core instead would move the ASCII layout with the kana, which is why this belongs
in the ROM.)

The tables are found by searching for the untouched `tbl_KeyMap`, never by a written-down
address, and the 360-byte block is checked against a known digest before anything is
written. All four cartridge dumps - V1.0, V2.0A, V2.1A, V3.0 - carry a **byte-identical**
block, so one tool covers every version. So do the MMC5 build and the disk build; a `.fds`
image is patched in place, and since that format stores no CRCs, nothing else has to be
recomputed.

⚠️ Keycode 0 is `STOP` and keycode 1 is the `¥` key: the tables start **four entries
before** the `]` key, not at it. Reading them from the `]` key loses `¥` - which in kana
mode is `RU` - and makes every count come out four short.

## What cannot be done: `゛` and `゜` as keys

JIS kana puts dakuten on `@` and handakuten on `[`. Family BASIC has **no standalone `゛`
or `゜` character** - the font has no such tile. Dakuten kana exist only as single
precomposed characters (`GA` is one character, not `KA` plus a mark), reached with GRPH.

∴ **dakuten stays on GRPH+key** and handakuten on Shift+key, following the layout. The
`@` and `[` keys are left with no kana on them.

`ー` is not affected by this: it is character `$2D`, the same mid-height bar the ASCII
tables use for `-`, and it moves with the layout like any other kana.

**`・` is missing too**, so Shift+`/` is left empty. The font has no centred dot: the one
tile that looked like one, `$CD`, turns out to be two scattered dots - a texture, not a
character.

⚠️ Read **both** CHR bit planes when judging a tile. 86 of the 256 carry ink in plane 1
that plane 0 does not have, `$B8` (a top rule) and `$CD` among them, so a plane-0 scan
calls them blank.

## One seat short, and why the keyboard is an option

A JIS kana layout needs 46 unshifted characters (45 kana plus `ー`). How many of them the
machine can actually receive depends on **which Family BASIC key each JIS keytop reaches**,
and that is a property of the core, not of the ROM:

| `--keyboard` | What it assumes |
|---|---|
| `mister-stock` | the stock MiSTer NES core, measured on hardware. `¥` and `ろ` reach nothing; `@`, `[`, `]` land one seat over |
| `mister-jis` | a core patched to read JIS key positions, `ろ` included. **Every keytop types what it says** |
| `hvc007` | the real Family BASIC keyboard. Its keytops are already gojuon, so this leaves the ROM alone |

Under `mister-stock` there are **45 reachable seats for 46 characters**, so one character
has to go somewhere unprinted or be dropped, and `--place` decides which. Under
`mister-jis` nothing is left over. Whatever is still homeless is reported rather than
silently dropped.

**Assumption**: the `mister-stock` table below is one session's hardware measurement of one
core build. If the core's key handling changes, that profile is what goes stale - the rest
of the tool does not depend on it.
"""

import argparse
import hashlib
import sys

# ---------------------------------------------------------------- the key matrix

# Keycode -> the key's name on the HVC-007. From the label table and commentary of
# [micahcowan/fbdasm](https://github.com/micahcowan/fbdasm), an annotated disassembly of
# V3 (`tbl_KeyMap`, keycode = 8 * matrix byte + bit). Confirmed against every dump: the
# ASCII table read at these positions spells the keyboard out.
KEYCODES = [
    "STOP", "¥",   "RSHIFT", "KANA",  "]",   "[",   "RET", "F8",
    "^",    "-",   "/",      "_",     ";",   ":",   "@",   "F7",
    "0",    "P",   ",",      ".",     "K",   "L",   "O",   "F6",
    "8",    "9",   "N",      "M",     "J",   "U",   "I",   "F5",
    "6",    "7",   "V",      "B",     "H",   "G",   "Y",   "F4",
    "4",    "5",   "C",      "F",     "D",   "R",   "T",   "F3",
    "3",    "E",   "Z",      "X",     "A",   "S",   "W",   "F2",
    "2",    "1",   "GRPH",   "LSHIFT","CTRL","Q",   "ESC", "F1",
    "INS",  "DEL", "SPACE",  "DOWN",  "LEFT","RIGHT","UP", "CLR",
]
SEAT = {name: i for i, name in enumerate(KEYCODES)}

# The seats that carry a printable character. Everything else - function keys, arrows,
# modifiers, RET, SPACE - is left exactly as found.
KANA_SEATS = (
    list("1234567890") + ["-", "^", "¥"]
    + list("QWERTYUIOP") + ["@", "["]
    + list("ASDFGHJKL") + [";", ":", "]"]
    + list("ZXCVBNM") + [",", ".", "/", "_"]
)

# ⚠️ One byte inside these tables is not a key at all.
#
# In direct mode - a command typed at the `OK` prompt rather than run from a program -
# BASIC sets its "pointer to the next program line" (`zpRunTxtNxtLnk`, zero page `$0D/$0E`)
# to **a fixed ROM address that happens to hold `$00`**, so that finishing the typed line
# reads a null and stops. That address lands **inside the key tables**:
#
# | Version | address it points at | which entry |
# |---|---|---|
# | V1.0 | `$B109` | `tbl_ShiftedKeyMap`, keycode 16 |
# | V2.0A / V2.1A | `$B144` | `tbl_ShiftedKeyMap`, keycode 16 |
# | V3.0 | `$BB89` | `tbl_ShiftedKanaKeyMap`, keycode 8 |
#
# Put a non-zero byte there and every command that **completes normally** - `PRINT`, `LIST`
# - runs, prints what it should, and then walks into the key tables as though they were the
# next program line: `?SN ERROR IN <a line number that is not in the program>`. `RUN` and a
# command that errors out do not come back through that path, so they look fine, which is
# what makes it easy to miss.
#
# Found on hardware by the mister-fpga session, 2026-08-26, bisected to one byte; explained
# here afterwards, from the code that builds the pointer. **The entry differs by version**,
# so it is read out of each ROM rather than written down - `find_sentinel` searches for the
# instructions that build the constant. On V1/V2 it lands in the Shift table, which this
# tool never writes, so it costs nothing there; on V3 it costs `Shift`+`^`.
SENTINEL_ZP = 0x0D          # zpRunTxtNxtLnk

TABLE_LEN = 72
PLAIN, SHIFT, KANA, KANA_SHIFT, GRPH = range(5)
TABLE_NAMES = ["tbl_KeyMap", "tbl_ShiftedKeyMap", "tbl_KanaKeyMap",
               "tbl_ShiftedKanaKeyMap", "tbl_GrphKeyMap"]

# The first twelve entries of an untouched `tbl_KeyMap`: STOP ¥ RSHIFT KANA ] [ RET F8 ^ - / _
SIGNATURE = bytes([0x03, 0x5C, 0x00, 0xFF, 0xB3, 0xB2, 0x0D, 0xF7, 0x5E, 0x2D, 0x2F, 0x00])
# MD5 of the whole 360-byte block, identical in V1.0 / V2.0A / V2.1A / V3.0.
STOCK_MD5 = "bae6a9a2a674d36d9c579bb7bcf60e92"

# ---------------------------------------------------------------- the character set

# Character code -> character. Read off the ROM's own font (CHR bank 1, code = tile index)
# and cross-checked against two independent statements of the same codes: `$91` is small
# `ェ` (`docs/reference/token-numbering.md`) and `$95` small `ョ` / `$A3` `ヅ` (the BUGS
# notes of [micahcowan/fbdasm](https://github.com/micahcowan/fbdasm)).
_KANA46 = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワンヲ"
_SMALL = "ァィゥェォャュョッ"
_DAKUTEN = "ガギグゲゴザジズゼゾダヂヅデドバビブベボ"
_HANDAKUTEN = "パピプペポ"

CODE = {}
for _i, _c in enumerate(_KANA46):
    CODE[_c] = 0x60 + _i
for _i, _c in enumerate(_SMALL):
    CODE[_c] = 0x8E + _i
for _i, _c in enumerate(_DAKUTEN):
    CODE[_c] = 0x97 + _i
for _i, _c in enumerate(_HANDAKUTEN):
    CODE[_c] = 0xAB + _i
CODE["ー"] = 0x2D      # the same mid bar the ASCII table uses for `-`
CODE["、"] = 0x2C      # the same low stroke the ASCII table uses for `,`
CODE["。"] = 0xB1
CODE["「"] = 0x5B
CODE["」"] = 0x5D
CHAR = {v: k for k, v in CODE.items()}

# Which base kana have a precomposed voiced / half-voiced form.
VOICED = dict(zip("カキクケコサシスセソタチツテトハヒフヘホ", _DAKUTEN))
HALF_VOICED = dict(zip("ハヒフヘホ", _HANDAKUTEN))

# Codes that count as "a character this tool owns". Anything else sitting in a kana seat
# (a graphics character on a digit, say) is preserved.
def _owned(code):
    return code in CHAR


# ---------------------------------------------------------------- layouts

# JIS X 6002 kana, written in katakana because that is all Family BASIC has.
# keytop -> (plain, shifted).  `None` means "nothing on this key".
JIS = {
    "1": ("ヌ", None), "2": ("フ", "プ"), "3": ("ア", "ァ"), "4": ("ウ", "ゥ"),
    "5": ("エ", "ェ"), "6": ("オ", "ォ"), "7": ("ヤ", "ャ"), "8": ("ユ", "ュ"),
    "9": ("ヨ", "ョ"), "0": ("ワ", "ヲ"), "-": ("ホ", "ポ"), "^": ("ヘ", "ペ"),
    "¥": ("ー", None),
    "Q": ("タ", None), "W": ("テ", None), "E": ("イ", "ィ"), "R": ("ス", None),
    "T": ("カ", None), "Y": ("ン", None), "U": ("ナ", None), "I": ("ニ", None),
    "O": ("ラ", None), "P": ("セ", None),
    "@": (None, None),          # `゛` - the font has no such character
    "[": (None, "「"),          # `゜` - likewise
    "A": ("チ", None), "S": ("ト", None), "D": ("シ", None), "F": ("ハ", "パ"),
    "G": ("キ", None), "H": ("ク", None), "J": ("マ", None), "K": ("ノ", None),
    "L": ("リ", None), ";": ("レ", None), ":": ("ケ", None), "]": ("ム", "」"),
    "Z": ("ツ", "ッ"), "X": ("サ", None), "C": ("ソ", None), "V": ("ヒ", "ピ"),
    "B": ("コ", None), "N": ("ミ", None), "M": ("モ", None),
    ",": ("ネ", "、"), ".": ("ル", "。"), "/": ("メ", None),   # JIS `・` - see below
    "ろ": ("ロ", None),
}
# JIS leaves the half-voiced kana nowhere, so they keep Family BASIC's own convention:
# Shift plus the base kana. Those Shift slots (2 ^ - F V) are unused by JIS kana.

LAYOUTS = {"jis": JIS, "gojuon": None}   # `gojuon` = leave the ROM's own layout alone

# ---------------------------------------------------------------- keyboards

# keytop -> the Family BASIC seat it reaches. `None` = reaches nothing.
_IDENTITY = {k: k for k in list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
             + ["-", "^", ";", ":", ",", ".", "/"]}

KEYBOARDS = {
    # Measured on hardware by the mister-fpga session, 2026-08-26, with a JIS 109
    # keyboard on the stock MiSTer NES core.
    "mister-stock": dict(_IDENTITY, **{
        "@": "/",        # ⚠️ collides with the `/` keytop, which also lands here
        "[": "]",
        "]": "¥",
        "¥": None,       # reaches nothing
        "ろ": None,      # reaches nothing
        "半角/全角": "_",   # Shift only
    }),
    # A core taught to read JIS key positions. Family BASIC has no `ろ` key of its own,
    # so the core sends that key to the `_` seat - which is right, not a workaround: the
    # JIS keytop reads `＼ _ ろ`, and `_` is exactly what that seat types under Shift.
    # Every JIS keytop then types what it says. Mapping supplied by the mister-fpga
    # session from the core patch it built, 2026-08-26.
    "mister-jis": dict(_IDENTITY, **{
        "@": "@", "[": "[", "]": "]", "¥": "¥",
        "ろ": "_",
        "半角/全角": "_",   # lands on the same seat; harmless, JIS puts no kana there
    }),
    # The real thing. Its keytops are gojuon already.
    "hvc007": None,
}

# What the profile costs the user, beyond what the tables can say.
KEYBOARD_NOTES = {
    "mister-stock": [
        "the `@` key reaches the `/` seat, so it types whatever `/` types",
        "the `[` key reaches the `]` seat and the `]` key reaches the `¥` seat: "
        "kana printed on `[` and `]` come out one key to the left of where they look",
        "the `¥` and `ろ` keys reach nothing at all",
    ],
    "mister-jis": [
        "the `ろ` key arrives at the `_` seat (keycode 11), which is where the core sends "
        "it; the `[` seat is left empty because Family BASIC has no `゜`",
    ],
    "hvc007": [],
}

# Where the leftovers go when their own key cannot reach a seat. `S+` prefixes a Shift
# slot. Overridable with `--place`.
DEFAULT_PLACES = {
    "mister-stock": {"ー": "]", "ロ": "S+;", "ペ": "S+:"},
    "mister-jis": {"ペ": "["},
    "hvc007": {},
}


# ---------------------------------------------------------------- locating the tables

def find_block(rom):
    """Return (file offset, where) of `tbl_KeyMap`. Searched for, never assumed.

    Cartridge dumps and disk images are both accepted. `where` is a CPU address only for
    a 32KB PRG, which is the whole unbanked address space; a banked build (the MMC5 one)
    and a disk image have no single CPU address for a file offset, so they are named by
    offset. The patch is done by offset either way.

    ⚠️ A disk image has to be patched **after** `fb-fds.py` builds it: that tool looks its
    input up by SHA-1 and refuses a dump it does not recognise, a patched one included.
    The tables come out byte-identical either way - the disk carries the same code.
    """
    if rom[:4] == b"NES\x1a":
        kind, start, span = "cart", 16, rom[4] * 16384
    elif rom[:4] == b"FDS\x1a" or rom[1:15] == b"*NINTENDO-HVC*":
        kind, start, span = "disk", 0, len(rom)
    else:
        sys.exit("not an iNES dump or an FDS disk image.")
    body = rom[start:start + span]
    hits = [i for i in range(len(body) - len(SIGNATURE) + 1)
            if body[i:i + len(SIGNATURE)] == SIGNATURE]
    if len(hits) != 1:
        sys.exit(f"expected exactly one key table, found {len(hits)}. "
                 "Is this a Family BASIC dump?")
    if kind == "cart" and span <= 32768:
        where = f"${0x10000 - span + hits[0]:04X}"
    elif kind == "cart":
        where = f"PRG offset ${hits[0]:05X} (banked; no single CPU address)"
    else:
        where = f"disk offset ${hits[0]:05X}"
    return start + hits[0], where


def find_sentinel(rom, start, span, block_off):
    """Where BASIC's direct-mode "next line" pointer points, if it points into the tables.

    Searched for as the instructions that build it - `LDA #lo / STA $0D / LDA #hi / STA $0E`
    - rather than written down, because the address differs between versions. Returns
    (table index, keycode) or None if the constant lands outside the tables.
    """
    body = rom[start:start + span]
    base_cpu = table_base_cpu(body, block_off - start)
    hits = set()
    for i in range(len(body) - 7):
        if (body[i] == 0xA9 and body[i + 2] == 0x85 and body[i + 3] == SENTINEL_ZP
                and body[i + 4] == 0xA9 and body[i + 6] == 0x85
                and body[i + 7] == SENTINEL_ZP + 1):
            hits.add(body[i + 1] | (body[i + 5] << 8))
    if len(hits) != 1:
        sys.exit(f"expected exactly one direct-mode line pointer, found {len(hits)}. "
                 "Refusing to patch a ROM whose command path is not the one this knows.")
    ptr = hits.pop()
    if base_cpu is None:
        sys.exit("could not place the key tables in the CPU address space, so the "
                 "direct-mode line pointer cannot be checked. Refusing to patch blind.")
    # The tables' CPU address and their file offset give the one delta that maps between
    # them, whatever the container is - cartridge, banked build or disk image.
    delta = (ptr - base_cpu)
    if not 0 <= delta < TABLE_LEN * 5:
        return None          # points somewhere harmless
    return delta // TABLE_LEN, delta % TABLE_LEN


def table_base_cpu(body, block_off):
    """The CPU address the key tables live at, read out of the code that indexes them.

    The five `LDA table,X` loads carry the five base addresses, and they are 72 apart. That
    spacing is the fingerprint: find the value whose four successors are all present as
    `LDA abs,X` operands. This works for a disk image and a banked build too, where the
    file offset alone says nothing about where the bytes are addressed from.
    """
    operands = set()
    for i in range(len(body) - 2):
        if body[i] == 0xBD:
            operands.add(body[i + 1] | (body[i + 2] << 8))
    bases = [b for b in operands
             if all(b + n * TABLE_LEN in operands for n in range(1, 5))]
    if len(bases) != 1:
        return None
    return bases[0]


def sentinel_set(rom, off):
    """`find_sentinel` as a set of (table, keycode), empty when it lands harmlessly."""
    span = rom[4] * 16384 if rom[:4] == b"NES\x1a" else len(rom) - (16 if rom[:4] == b"FDS\x1a" else 0)
    start = 16 if rom[:4] in (b"NES\x1a", b"FDS\x1a") else 0
    hit = find_sentinel(rom, start, span, off)
    return {hit} if hit else set()


def read_tables(rom, off):
    return [bytearray(rom[off + t * TABLE_LEN: off + (t + 1) * TABLE_LEN]) for t in range(5)]


# ---------------------------------------------------------------- building the layout

class Plan:
    def __init__(self, noop=False):
        self.noop = noop     # `gojuon`, or a keyboard whose keytops are already gojuon
        self.plain = {}      # seat -> character
        self.shift = {}
        self.homeless = []   # (character, why)
        self.notes = []


def build(layout_name, keyboard_name, places, reserved=()):
    """Compose layout (keytop -> kana) with keyboard (keytop -> seat)."""
    layout = LAYOUTS[layout_name]
    reach = KEYBOARDS[keyboard_name]
    if layout is None or reach is None:
        return Plan(noop=True)   # the ROM's own layout is already what is being asked for
    plan = Plan()

    for keytop, (plain, shifted) in sorted(layout.items()):
        seat = reach.get(keytop, keytop if keytop in SEAT else None)
        for char, table in ((plain, plan.plain), (shifted, plan.shift)):
            if char is None:
                continue
            if seat is None:
                plan.homeless.append((char, f"the `{keytop}` key reaches no seat"))
                continue
            layer = KANA_SHIFT if table is plan.shift else KANA
            if (layer, SEAT[seat]) in reserved:
                plan.homeless.append(
                    (char, f"BASIC's direct-mode line pointer points at that byte; "
                           f"writing it breaks every command that completes normally"))
                continue
            if seat in table and table[seat] != char:
                plan.notes.append(
                    f"`{keytop}` and another key both reach the `{seat}` seat; "
                    f"`{table[seat]}` keeps it, `{char}` is dropped")
                continue
            table[seat] = char

    # JIS leaves the half-voiced kana nowhere, so they keep Family BASIC's own convention:
    # Shift plus the base kana. Derived here rather than when writing the tables, so that
    # `--place` below sees - and can move - the finished layout.
    for seat, plain in list(plan.plain.items()):
        half = HALF_VOICED.get(plain)
        if half and seat not in plan.shift:
            if (KANA_SHIFT, SEAT[seat]) in reserved:
                plan.homeless.append(
                    (half, "BASIC's direct-mode line pointer points at that byte; "
                           "writing it breaks every command that completes normally"))
            else:
                plan.shift[seat] = half

    # `--place` moves a character, whether or not the layout had a seat for it. Moving
    # rather than only filling gaps is what lets one layout hold across versions: the seat
    # a character has to leave differs by version (see `find_sentinel`), and a keyboard
    # people have to relearn per build is worse than a seat left empty.
    homeless = dict(plan.homeless)
    for char, where in places.items():
        if where.startswith("S+"):
            table, seat, shown = plan.shift, where[2:], f"Shift+`{where[2:]}`"
        else:
            table, seat, shown = plan.plain, where, f"`{where}`"
        if seat not in SEAT:
            sys.exit(f"--place {char}={where}: no such key as `{seat}`")
        was = None
        for t in (plan.plain, plan.shift):
            for s in [s for s, c in t.items() if c == char]:
                was = s
                del t[s]
        if seat in table and table[seat] != char:
            sys.exit(f"--place {char}={where}: the `{seat}` seat already holds "
                     f"`{table[seat]}`")
        table[seat] = char
        if char in homeless:
            plan.notes.append(f"`{char}` has no key of its own here; put on {shown}")
            del homeless[char]
        else:
            plan.notes.append(f"`{char}` moved from `{was}` to {shown}"
                              if was else f"`{char}` put on {shown}")
    plan.homeless = list(homeless.items())
    return plan


def apply(tables, plan, reserved=()):
    """Write the plan into the kana, kana+Shift and GRPH tables. Returns a new list."""
    if plan.noop:
        return [bytearray(t) for t in tables]
    out = [bytearray(t) for t in tables]
    for seat in KANA_SEATS:
        k = SEAT[seat]
        plain = plan.plain.get(seat)
        shifted = plan.shift.get(seat)

        reserved_shift = (KANA_SHIFT, k) in reserved
        if (KANA, k) not in reserved:
            out[KANA][k] = CODE[plain] if plain else 0x00
        if not reserved_shift:
            out[KANA_SHIFT][k] = CODE[shifted] if shifted else 0x00

        # GRPH carries the voiced kana - it has to follow the base kana around. Where the
        # new kana has no voiced form, anything the ROM already had that this tool does
        # not own (a graphics character) is left alone.
        voiced = VOICED.get(plain) if plain else None
        if (GRPH, k) in reserved:
            pass
        elif voiced:
            out[GRPH][k] = CODE[voiced]
        elif _owned(tables[GRPH][k]):
            out[GRPH][k] = 0x00
    return out


# ---------------------------------------------------------------- reporting

def render(tables, title):
    rows = [f"--- {title} ---",
            f"{'key':>6} | {'kana':>4} | {'+Shift':>6} | {'+GRPH':>5}"]
    rows.append("-" * 34)
    for seat in KANA_SEATS:
        k = SEAT[seat]
        def show(t):
            b = tables[t][k]
            return CHAR.get(b, "·" if b == 0 else f"<{b:02X}>")
        rows.append(f"{seat:>6} | {show(KANA):>4} | {show(KANA_SHIFT):>6} | {show(GRPH):>5}")
    return "\n".join(rows)


# ---------------------------------------------------------------- self test

def selftest(path):
    rom = bytearray(open(path, "rb").read())
    off, addr = find_block(rom)
    stock = read_tables(rom, off)
    reserved = sentinel_set(rom, off)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
        if not cond:
            ok = False

    print(f"tables at {addr} (file offset {off})")
    check("block matches the known stock digest", _stock_md5(rom, off) == STOCK_MD5)

    # The gojuon layout must be a byte-exact no-op.
    if reserved:
        t, k = next(iter(reserved))
        print(f"  direct-mode line pointer lands in {TABLE_NAMES[t]} keycode {k} "
              f"(the `{KEYCODES[k]}` key; stock value {stock[t][k]:#04x})")
        check("the byte it points at is $00 in this dump", stock[t][k] == 0x00)

    plan = build("gojuon", "mister-stock", {})
    check("`--layout gojuon` changes nothing",
          apply(stock, plan, reserved) == stock)

    for kb in ("mister-stock", "mister-jis"):
        plan = build("jis", kb, DEFAULT_PLACES[kb], reserved)
        new = apply(stock, plan, reserved)
        # The ASCII tables must be untouched: this is the whole point of doing it in ROM.
        check(f"{kb}: plain and Shift tables untouched",
              new[PLAIN] == stock[PLAIN] and new[SHIFT] == stock[SHIFT])
        # Nothing outside a kana seat may move.
        moved = [KEYCODES[k] for t in (KANA, KANA_SHIFT, GRPH) for k in range(TABLE_LEN)
                 if new[t][k] != stock[t][k] and KEYCODES[k] not in KANA_SEATS]
        check(f"{kb}: only kana seats written", not moved, str(moved))
        # Every character the plan placed must be readable back out of the tables.
        back = {}
        for seat in KANA_SEATS:
            for t, d in ((KANA, plan.plain), (KANA_SHIFT, plan.shift)):
                b = new[t][SEAT[seat]]
                if b and b in CHAR:
                    back.setdefault(t, {})[seat] = CHAR[b]
        check(f"{kb}: plain layer reads back", back.get(KANA, {}) == plan.plain)
        # The half-voiced kana are added on top of the plan, so compare the plan's own
        # Shift entries as a subset.
        shift_back = back.get(KANA_SHIFT, {})
        check(f"{kb}: Shift layer reads back",
              all(shift_back.get(s) == c for s, c in plan.shift.items()))
        # Every base kana must be typeable somewhere, or reported.
        placed = set(plan.plain.values()) | set(plan.shift.values())
        missing = [c for c in _KANA46 if c not in placed]
        reported = [c for c, _ in plan.homeless]
        check(f"{kb}: every missing kana is reported",
              sorted(missing) == sorted(reported), f"missing={missing} reported={reported}")
        # The reserved seats must come out exactly as the stock ROM had them.
        moved = [f"table {t} keycode {k}" for t, k in reserved if new[t][k] != stock[t][k]]
        check(f"{kb}: the direct-mode pointer's byte is left alone", not moved, str(moved))
        # A graphics character sitting in a kana seat is not this tool's to move.
        kept = [KEYCODES[SEAT[s]] for s in KANA_SEATS
                if not _owned(stock[GRPH][SEAT[s]]) and stock[GRPH][SEAT[s]] != 0
                and new[GRPH][SEAT[s]] != stock[GRPH][SEAT[s]]
                and not VOICED.get(plan.plain.get(s))]
        check(f"{kb}: graphics characters left where they were", not kept, str(kept))
        # Voiced kana must all be reachable through GRPH.
        grph = {CHAR[new[GRPH][SEAT[s]]] for s in KANA_SEATS if new[GRPH][SEAT[s]] in CHAR}
        lost = [v for base, v in VOICED.items() if base in placed and v not in grph]
        check(f"{kb}: voiced kana follow their base kana", not lost, str(lost))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def _stock_md5(rom, off):
    return hashlib.md5(bytes(rom[off:off + TABLE_LEN * 5])).hexdigest()


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rom")
    ap.add_argument("-o", "--out")
    ap.add_argument("--layout", choices=sorted(LAYOUTS), default="jis")
    ap.add_argument("--keyboard", choices=sorted(KEYBOARDS), default="mister-stock")
    ap.add_argument("--place", action="append", default=[], metavar="CHAR=KEY",
                    help="put CHAR on KEY (prefix `S+` for the Shift layer), "
                         "for kana whose own key reaches no seat")
    ap.add_argument("--dump", action="store_true", help="print the layout, write nothing")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="patch even if the tables are not the known stock ones")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args.rom)

    rom = bytearray(open(args.rom, "rb").read())
    off, addr = find_block(rom)
    stock = read_tables(rom, off)
    digest = _stock_md5(rom, off)
    if digest != STOCK_MD5 and not args.force:
        sys.exit(f"the key tables at {addr} are not the stock ones "
                 f"(md5 {digest}). Patch an unmodified dump, or pass --force.")

    places = dict(DEFAULT_PLACES.get(args.keyboard, {}))
    for spec in args.place:
        if "=" not in spec:
            sys.exit(f"--place wants CHAR=KEY, got `{spec}`")
        char, where = spec.split("=", 1)
        places[char] = where

    reserved = sentinel_set(rom, off)
    plan = build(args.layout, args.keyboard, places, reserved)
    new = apply(stock, plan, reserved)
    if plan.noop:
        print(f"`--layout {args.layout} --keyboard {args.keyboard}`: "
              "the ROM's own gojuon layout is what this asks for. Nothing to change.")

    print(render(new, f"{args.layout} on {args.keyboard}  (tables at {addr})"))
    for t, k in reserved:
        print(f"note: BASIC's direct-mode line pointer points at {TABLE_NAMES[t]} "
              f"keycode {k} (the `{KEYCODES[k]}` key); left at $00")
    if not plan.noop:
        for note in KEYBOARD_NOTES.get(args.keyboard, []):
            print(f"note: {note}")
    for note in plan.notes:
        print(f"note: {note}")
    for char, why in plan.homeless:
        print(f"⚠️  `{char}` cannot be typed: {why}. Give it a seat with "
              f"--place {char}=<key>")
    changed = sum(1 for t in range(5) for k in range(TABLE_LEN) if new[t][k] != stock[t][k])
    print(f"{changed} bytes change.")

    if args.dump:
        return 0
    if not args.out:
        sys.exit("give -o to write the patched ROM (or --dump to just look)")
    for t in range(5):
        rom[off + t * TABLE_LEN: off + (t + 1) * TABLE_LEN] = new[t]
    open(args.out, "wb").write(bytes(rom))
    print(f"wrote {args.out}  md5 {hashlib.md5(bytes(rom)).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
