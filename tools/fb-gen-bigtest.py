#!/usr/bin/env python3
"""Generate a test program **large enough to actually fill the free area**, plus its answer.

  $ ./fb-gen-bigtest.py --bytes 12000 -o tests/basic/big.bas

## Why this is needed

The differential test asks "does it produce the same screen as a stock V3?" — but
**a stock V3 only has 4KB free**. So a program large enough to use memory above `$8000`
does not fit there and **has no counterpart to compare against**.

So the ground truth comes from somewhere else: **write a program whose answer can be
computed.** The same computation runs here in Python and is written to `.expect`, which
is then matched against the screen.

## What it exercises

- **Can a line placed across `$8000` be read?** Lines are laid out in address order, so
  a large enough program is guaranteed to straddle it
- **Can `GOSUB` reach a line past the boundary and return?** Line lookup walks from the
  start, so a break at the boundary shows up here
- **Do variables survive being allocated right after the program (i.e. in the upper half)?**

Values are kept in range with `MOD 997`. Family BASIC integers are signed 16-bit, and
**a bug in catching multiplication overflow has been reported** (micahcowan/fbdasm),
so nothing is allowed to overflow.
"""

import argparse
import sys

MOD = 997


def build(target_bytes):
    """Stack up lines until the target size is reached, computing the answer as we go."""
    lines, s, n = [], 0, 0
    lines.append((10, "S=0"))
    lines.append((20, "T=0"))
    ln = 100
    # A dozen or so bytes per line. Keep stacking until we reach the target
    while True:
        k = (n * 7 + 13) % 100
        lines.append((ln, f"S=(S+{k}) MOD {MOD}"))
        s = (s + k) % MOD
        n += 1
        ln += 10
        # Rough estimate from line number plus body. The exact size is measured
        # after conversion
        if sum(len(b) + 8 for _l, b in lines) >= target_bytes:
            break
    # **Jump to the very last line and come back** — exercises the line lookup for
    # lines placed above $8000
    sub = ln + 1000
    lines.append((ln, f"GOSUB {sub}"))
    ln += 10
    lines.append((ln, 'PRINT "S";S;"T";T'))
    ln += 10
    lines.append((ln, "END"))
    lines.append((sub, f"T=(S*2) MOD {MOD}"))
    t = (s * 2) % MOD
    lines.append((sub + 10, "RETURN"))
    text = "".join(f"{l} {b}\n" for l, b in lines)
    # How `PRINT "S";S;"T";T` actually renders. **A number is preceded by a space for
    # its sign, but no space is inserted right after a string literal** (measured on
    # hardware). Hence `S 780T 563`
    expect = f"S {s}T {t}"
    return text, expect, len(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bytes", type=int, default=12000, help="target program size")
    ap.add_argument("-o", "--output", required=True,
                    help="the .bas to write (.expect is written alongside it)")
    args = ap.parse_args()
    text, expect, count = build(args.bytes)
    open(args.output, "w").write(text)
    exp_path = args.output.rsplit(".", 1)[0] + ".expect"
    open(exp_path, "w").write(expect + "\n")
    print(f"wrote {count} lines -> {args.output}")
    print(f"answer {expect!r} -> {exp_path}")
    print("NOTE: check the real size by converting it with fb-basic-to-sav.py")


if __name__ == "__main__":
    main()
