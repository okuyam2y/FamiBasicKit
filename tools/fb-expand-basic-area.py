#!/usr/bin/env python3
"""Widen Family BASIC's program area to fill WRAM (up to `$7FFF`).

  $ ./fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

## What is going on

The amount of RAM BASIC can use is decided by **a constant burned into the ROM, not by
how much RAM is present**. Even when a core or a flash cart provides the full 8KB at
`$6000-$7FFF`, BASIC only ever touches its hard-coded range.

| Version | Stock area | Expanded |
|---|---|---|
| V1.0 / V2.0A / V2.1A | `$7000-$77FF` (2KB) | `$7000-$7FFF` (4KB) |
| V3.0 | `$6000-$6FFF` (4KB) | `$6000-$7FFF` (8KB) |

V1/V2 **start** the area at `$7000`, so widening without moving the start caps out at
4KB. V3 starts at `$6000` and reaches 8KB (**V3 is the roomiest**).

## What changes: 3 bytes for V1/V2, 6 for V3

1. **Boot-time init** — the constant that loads "end of area" into zero page `$03/$04`.
   That alone widens the area, `BYTES FREE` display included
2. **The `CLEAR` argument check** — a hard-coded "error if at or above this address"
   ceiling. It is a *separate* constant: raising the one above does not raise this one,
   and `CLEAR` keeps refusing the new space until it is raised too
3. **The `BGGET`/`BGPUT` buffer (V3 only, 3 bytes)** — one 1KB screen buffer pinned to
   the top of the area by three immediates. Also separate constants: leave them and the
   buffer stays in what is now the middle of the user's program, and `BGGET` errors out
   as soon as the program passes it
4. **Byte 10 of the NES 2.0 header** — the PRG-NVRAM size. **Without this the PRG
   changes do nothing on real hardware**, because the machine only provides as much RAM
   as the header declares. The declared size is doubled (`5`→`6` = 2KB→4KB for V1/V2,
   `6`→`7` = 4KB→8KB for V3). Pass `--keep-header` to skip it

⚠️ **Leave the loop near `$ADC8` (V3) that copies a built-in program from ROM to fill the
area alone.** Extending its end address makes the source pointer read past the end of ROM.
The end of the area is tracked through `$03/$04`, so this loop never needs to change.

## "Last address" means different things per version

V2.0A / V2.1A / V3.0 store **the address of the last byte** in `$03/$04` (`$77FF` and so
on), but **V1.0 alone stores "one past the last"** (`$7800`). Tell them apart by whether
`LDA #$FF / STA $03` precedes it: the former becomes `$7FFF`, the latter `$8000`.
"""

import argparse
import hashlib
import re
import sys

# The sequence that sets "end of area" at boot.
# Matching on `LDA #$04` alone also catches unrelated uses of $03/$04, so the pattern
# includes the preceding
#   LDA #$03 / STA $56 [/ STA $E3]   (only V3 has the STA $E3)
# to make it unique.
PAT_TOP = re.compile(rb"\xA9\x03\x85\x56(?:\x85.)?\xA9(.)\x85\x04", re.S)
# The CLEAR argument check: LDA $0401 / CMP #>limit / BCS <error>
PAT_CLEAR = re.compile(rb"\xAD\x01\x04\xC9(.)\xB0", re.S)
# Marker for the "address of the last byte" convention: LDA #$FF / STA $03
MARK_INCLUSIVE = b"\xA9\xFF\x85\x03"

# `BGGET` and `BGPUT` share one 1KB screen buffer that sits at the **top of the area**:
# `$6C00-$6FFF`, the last kilobyte of the unexpanded `$6000-$6FFF`. Widening the area does
# not move it, so it ends up in the middle of the user's program. `BGGET` then refuses with
# `?OM ERROR` as soon as the program passes `$6C00`, however much room is really left.
# Confirmed on hardware 2026-08-23: a 3,529-byte program on the 16KB build printed
# `12846 BYTES FREE` and then `?OM ERROR IN 10`.
#
# Three immediates hold the high byte (the low byte is `$00` in all three). The two
# patterns pin whole routine prologues rather than the immediates alone, so a match
# cannot land on an unrelated `#$6C`:
#   BGGET  JSR Clear / LDA $20 / CMP #>buf / BCS err / LDA #$0C / JSR nmi
#          / LDA #$00 / STA $19 / LDA #>buf / STA $1A
#   BGPUT  LDA $6004 / BEQ err / LDA #$0C / JSR nmi / LDA #>buf / STA $1A
#          / LDA #$00 / STA $19
# **V3 only** - V1/V2 have neither command, and neither pattern matches there.
PAT_BGGET = re.compile(
    rb"\x20..\xA5\x20\xC9(.)\xB0.\xA9\x0C\x20..\xA9\x00\x85\x19\xA9(.)\x85\x1A", re.S)
PAT_BGPUT = re.compile(
    rb"\xAD\x04\x60\xF0.\xA9\x0C\x20..\xA9(.)\x85\x1A\xA9\x00\x85\x19", re.S)
BG_PAGE_OLD = 0x6C               # $6C00, the last 1KB of the unexpanded area

WANT_TOP_INCLUSIVE = 0x7F        # $7FFF
WANT_TOP_EXCLUSIVE = 0x80        # $8000 (one past the last)
WANT_CLEAR_LIMIT = 0x80          # CLEAR becomes "error at or above $8000"
WANT_BG_PAGE = 0x7C              # $7C00-$7FFF, the last 1KB of the widened area

# NES 2.0 header byte 10: the low nibble is volatile PRG-RAM, the high nibble PRG-NVRAM.
# The size is 64 << nibble bytes, so 5=2KB, 6=4KB, 7=8KB, 8=16KB.
NVRAM_NAMES = {0: "none", 5: "2KB", 6: "4KB", 7: "8KB", 8: "16KB"}


def load(path):
    d = open(path, "rb").read()
    if d[:4] != b"NES\x1a":
        raise ValueError(f"{path}: not an iNES header")
    # The magic matching does not mean the rest of the header is there, and `h[6]`/`h[4]`
    # below index into it - a short file would raise IndexError past the ValueError handling
    if len(d) < 16:
        raise ValueError(f"{path}: an iNES header is 16 bytes, this file is {len(d)}")
    h = d[:16]
    off = 16 + (512 if h[6] & 0x04 else 0)
    prg_size = h[4] * 16384
    return d, off, prg_size


def version_string(prg):
    m = re.search(rb"NS-HUBASIC V\d\.\d[A-Z]?", prg)
    return m.group(0).decode() if m else "(no version string found)"


def find_one(pattern, prg, what):
    hits = list(pattern.finditer(prg))
    if len(hits) != 1:
        raise ValueError(f"{what}: not uniquely found ({len(hits)} matches)")
    return hits[0]


def expand(path, out_path, keep_header=False):
    data, body_off, prg_size = load(path)
    prg = data[body_off:body_off + prg_size]
    # Only look at the original 32KB (banks added by an MMC5 conversion are not
    # BASIC's own code)
    scan = prg[:32768]

    print(f"## input: {path}")
    print(f"  {version_string(scan)} / MD5 {hashlib.md5(data).hexdigest()}")

    top = find_one(PAT_TOP, scan, 'the boot-time "end of area"')
    clr = find_one(PAT_CLEAR, scan, "the CLEAR ceiling check")

    top_off = top.end() - 3                   # the immediate (the <hi> in ... A9 <hi> 85 04)
    clr_off = clr.start() + 4
    top_old = scan[top_off]
    clr_old = scan[clr_off]

    # Confirm we grabbed the intended byte (the middle of an A9 <hi> 85 04 sequence)
    if not (scan[top_off - 1] == 0xA9 and scan[top_off + 1] == 0x85
            and scan[top_off + 2] == 0x04):
        raise ValueError(f"the immediate is not where expected (${0x8000 + top_off:04X})")
    if not 0x60 <= top_old <= 0x7F:
        raise ValueError(f"unexpected value for end of area: ${top_old:02X}")
    if not (scan[clr_off - 1] == 0xC9 and scan[clr_off + 1] == 0xB0):
        raise ValueError(f"the CLEAR immediate is not where expected (${0x8000 + clr_off:04X})")

    before = scan[max(0, top.start() - 8):top.start()]
    inclusive = MARK_INCLUSIVE in before
    top_new = WANT_TOP_INCLUSIVE if inclusive else WANT_TOP_EXCLUSIVE

    kind = "address of last byte" if inclusive else "one past the last"
    print()
    print(f"  end of area   ${0x8000 + top_off - 1:04X}  LDA #${top_old:02X} -> #${top_new:02X}"
          f"   ({kind})")
    print(f"  CLEAR ceiling ${0x8000 + clr.start() + 3:04X}  CMP #${clr_old:02X} -> "
          f"#${WANT_CLEAR_LIMIT:02X}")

    if top_new == top_old:
        raise ValueError("already expanded (nothing to change)")

    out = bytearray(data)
    out[body_off + top_off] = top_new
    out[body_off + clr_off] = WANT_CLEAR_LIMIT
    changed = {body_off + top_off, body_off + clr_off}

    # The BGGET/BGPUT buffer follows the top of the area. Only V3 has those two
    # commands, and **which version this is gets decided structurally**, from the
    # "end of area" constant already validated above — not from the printable version
    # string. The string is not load-bearing anywhere else, so a ROM whose copy of it
    # is damaged would otherwise skip this patch without a word and ship a build whose
    # widened program area overlaps the buffer.
    # V1/V2 put the area at `$7000` and V3 at `$6000`, so the top lands in a different
    # page (measured on the four dumps: V1.0 `$78`, V2.0A/V2.1A `$77`, V3.0 `$6F`).
    is_v3 = top_old < 0x70
    bgget = list(PAT_BGGET.finditer(scan))
    bgput = list(PAT_BGPUT.finditer(scan))
    # Compare against the expected count, not against a boolean. Writing this as
    # `is_v3 != (len(bgget) == 1 and len(bgput) == 1)` let a non-V3 image with a
    # partial match through: one BGGET and no BGPUT makes the right side false,
    # which equals `is_v3`, so nothing fired even though the message promises
    # "expected none".
    want = 1 if is_v3 else 0
    if len(bgget) != want or len(bgput) != want:
        raise ValueError(
            f"BGGET/BGPUT: the area top is ${top_old:02X}xx, so this "
            f"{'is' if is_v3 else 'is not'} V3, but BGGET matched {len(bgget)} time(s) "
            f"and BGPUT {len(bgput)} time(s) (expected {want} each). "
            f"Version string: {version_string(scan)}")
    if is_v3:
        for m in (bgget[0], bgput[0]):
            for g in range(1, m.re.groups + 1):
                off = m.start(g)
                if scan[off] != BG_PAGE_OLD:
                    raise ValueError(f"${0x8000 + off:04X}: the BGGET/BGPUT buffer is "
                                     f"${scan[off]:02X}00, expected ${BG_PAGE_OLD:02X}00")
                print(f"  BG buffer     ${0x8000 + off - 1:04X}  "
                      f"#${BG_PAGE_OLD:02X} -> #${WANT_BG_PAGE:02X}"
                      f"   (BGGET/BGPUT, ${WANT_BG_PAGE:02X}00-${WANT_BG_PAGE + 3:02X}FF)")
                out[body_off + off] = WANT_BG_PAGE
                changed.add(body_off + off)

    # The header declaration. Without this the machine never provides the extra RAM,
    # so the PRG changes above have no visible effect.
    if not keep_header:
        if data[7] & 0x0C != 0x08:
            raise ValueError("not a NES 2.0 header, so byte 10 is not the NVRAM size. "
                             "Add the header first, or pass --keep-header")
        nv_old = data[10] >> 4
        if nv_old not in (5, 6):
            raise ValueError(f"unexpected PRG-NVRAM declaration (nibble {nv_old}). "
                             "Expected 5 (2KB) or 6 (4KB)")
        nv_new = nv_old + 1
        out[10] = (data[10] & 0x0F) | (nv_new << 4)
        changed.add(10)
        print(f"  header byte10 ${data[10]:02X} -> ${out[10]:02X}          "
              f"(PRG-NVRAM {NVRAM_NAMES[nv_old]} -> {NVRAM_NAMES[nv_new]})")
    else:
        print("  header byte10 left alone (--keep-header). "
              "NOTE: the machine will not provide the extra RAM")

    # Verify: are exactly the intended bytes the ones that changed?
    diff = [i for i, (a, b) in enumerate(zip(data, out)) if a != b]
    expect = sorted(changed)
    if diff != expect or len(out) != len(data):
        raise ValueError(f"unexpected diff: {diff} != {expect}")

    open(out_path, "wb").write(bytes(out))
    print()
    print(f"## output: {out_path}")
    print(f"  MD5 {hashlib.md5(bytes(out)).hexdigest()} / {len(diff)} bytes changed")
    return top_old, top_new


def main():
    ap = argparse.ArgumentParser(
        description="Widen BASIC's program area to fill WRAM")
    ap.add_argument("input")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--keep-header", action="store_true",
                    help="do not raise the PRG-NVRAM size in the header "
                         "(the expansion then has no effect on real hardware)")
    args = ap.parse_args()
    try:
        expand(args.input, args.output, keep_header=args.keep_header)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
