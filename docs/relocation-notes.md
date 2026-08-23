# Traps hit while relocating 8KB of code

[日本語](relocation-notes.ja.md)

Notes for anyone attempting the same thing. **Multi-model review turned up two real
defects in a ROM that was already running on hardware.** Both produced output that looked
correct, passed every static check, and did not reproduce during ordinary use.

## 1. "Self-contained" fails in two separate ways

Leaving relative branches alone is only safe **when their targets are inside the block
being moved.**

### Branches that leave the block (3 of them)

V3's actual ROM has three branches near the end of the block that leave it
(`$9FE1` / `$9FF9` / `$9FFD` targeting `$A005` / `$A009`). After the move they pointed at
**completely different code** — the start of the MMC5 init routine, and the middle of an
instruction.

**Fix**: **extend the range being moved** so it covers those targets. Extending pulls new
branches into the range, so **repeat until nothing new appears.** The result: the range is
`$8000-$A00A`, not `$8000-$A001` (the bridge sits at `$F00B-$F00D`).

So **the range being moved is not a constant.** `fb-mmc5-16k.py` does not hard-code it;
it cross-checks against the value `fb-relocate.py` re-derives. In an earlier version that
did hard-code it, the interpreter grew, the guard missed it, and **the init code
overwrote the interpreter.**

### Overlapping instructions (1 of them)

There is an idiom where `BIT abs` (`2C lo hi`) swallows two operand bytes, and a branch
into the second byte **executes those same two bytes as a different instruction.**

In V3 this is `$9642 BIT $9785` (`2C 85 97`): branch to `$9643` and the same two bytes run
as `STA $97`. **That operand is an address and an instruction at the same time, so it must
not be relocated as an address** — rewriting it turns it into `STA $E7`, and the branch
path then corrupts a different variable.

Across the whole ROM there are 16 overlaps; one of them mattered.

**Procedure**: before moving anything, count two things.

- **(a) relative branches inside the block whose target is outside it**
- **(b) references whose operand bytes are also the first byte of another instruction**

If both are zero, the block is self-contained. If not, extend the range or exclude that
reference from rewriting.

**Why static checks miss this**: in both cases **the reference values and the instruction
boundaries all come out exactly as expected.**

(Applies to variable-length instruction sets where execution can start mid-instruction —
6502, x86. On fixed-length, alignment-required sets such as ARM64, (b) cannot occur.)

## 2. A byte-pattern match is a hypothesis, not a meaning

A sequence found by searching a binary is **a sequence of bytes that looks like something**,
not evidence of **what that code does**. Read around it before rewriting.

A real example: three sites matched "looks like a comparison against the end of the area"
(`LDA #$70 / CMP / BNE`), and one was rewritten. Disassembling and reading it showed it
was not a bounds check at all — it was **the termination condition of a loop copying data
from ROM to fill the area.** Extending the end made the source pointer **read past the end
of ROM**, where address wraparound could reach registers with side effects (PPU).

**Procedure**: find a candidate → **disassemble and read around that address** → only
rewrite once you can state in words what the code does. **If you cannot put it into words,
do not rewrite it.**

`tools/fb-disasm.py` exists for this. It descends recursively from `RESET` / `NMI` / `IRQ`
and treats **only instructions actually reachable from a vector** as instructions.
Regions it could not reach are reported as **"unconfirmed", not "data".**

## 3. Telling jump tables from data tables

Not every 16-bit value that looks like `$80xx-$9Fxx` is an address that needs moving.
**Decide by the instructions that read the table.**

The jump-table shape (feeding an indirect `JMP`):

```
LDA tbl,X / STA $19 / LDA tbl+1,X / STA $1A / JMP ($0019)
```

Data tables in V3 that must **not** be touched (their values merely happen to look like
`$80xx-$9Fxx`):

| Address | Contents |
|---|---|
| `$8013` | sound periods |
| `$B37F` | palettes |
| `$B44A` | function-key strings |
| `$C787` | 16-bit addends |

`--find-tables` in `fb-disasm.py` only **proposes** runs whose every entry is an in-ROM
address. The call is settled by reading the instructions that follow.

## 4. Total references fixed (measured on V3)

| Kind | Count | How they were found |
|---|---|---|
| Absolute `JSR`/`JMP`/data references | 860 | operands of disassembled instructions |
| Relative branches | 479 | **left alone** (range extended so targets move too) |
| Jump-table entries | 4 tables | the shape above |
| Addresses built from two immediates | 2 | `LDA #<lo / STA zp / LDA #>hi / STA zp+1` |
| Reset / IRQ vectors | 2 | `$FFFC` / `$FFFE` |

This table is for explaining the reasoning. **What decisions are actually made on is the
runtime output** — `fb-relocate.py` prints the number it really fixed on every run.

## 5. How to write the checks

- **Check "did the intended place change", not "how many places changed."**
  A count-based check **passes just as happily when you corrupt the same number of wrong
  places**
- **Never hand a check the answer you expect.** If you do, a wrong constant makes the
  check wrong in the same direction and it passes vacuously
  (→ the bank-number probe in [mmc5-wram-banks.md](mmc5-wram-banks.md))
- **Pin the input.** `fb-relocate.py` refuses to run unless the input is a stock V3
  (PRG+CHR SHA-256, all three vectors, positions of undefined opcodes, entry points of
  code fragments)
- **Test for "same screen as the stock ROM", not "it ran."**
  Relocation defects surface as rarely taken paths breaking silently
