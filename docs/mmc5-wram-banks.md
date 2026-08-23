# MMC5 WRAM bank numbers cannot be hard-coded

[日本語](mmc5-wram-banks.ja.md)

**The same bank number lands in different places depending on the board or the
implementation.** Anyone trying to use more than 8KB of WRAM on MMC5 will hit this.

## What happens

On MMC5, the WRAM shown at `$6000-$7FFF` is selected by `$5113`, and the WRAM shown at
`$8000-$9FFF` by `$5114` (with bit 7 cleared). The NESdev wiki documents the bits of
these registers as `RAAA AaAA`.

**The problem is `a` (bit 2). It doubles as both "PRG-ROM A15" and "which WRAM chip's
/CE".** So the same number resolves differently depending on how many SRAM chips the
board actually carries.

| | `$5114 = $01` | `$5114 = $04` |
|---|---|---|
| **Real ETROM (16KB = 2 × 8KB) / EverDrive N8 PRO** | still chip 0 = **a mirror of `$6000-$7FFF`** | chip 1 = a distinct 8KB ✅ |
| **MiSTer NES core** | block 1 ✅ | block 4 — distinct, but outside the 32KB `.sav` (blocks 0-3) |
| **Real EWROM (32KB, one chip)** | the second 8KB ✅ | open bus |

MiSTer is linear simply because its NES core's `MMC5.sv` concatenates
`{prgsel[2], prgsel[1:0]}` and leaves it at that. **No commercial MMC5 game exercises
this path** (two 8KB SRAMs), so an implementation can be missing it and nobody notices.

## Why it bites

The nasty part is that **a mirror raises no error at all.**

On hardware where `$8000-$9FFF` mirrors `$6000-$7FFF`, BASIC still believes it has 16KB
and prints `16374 BYTES FREE`. **Everything works until the program exceeds 8KB, and
then it silently corrupts** — the upper half writes over the lower half.

## The fix — probe at boot

Stop guessing; try it during init (see `emit_probe` in `tools/fb-mmc5-16k.py`).

1. Write `$01` to `$5114`
2. Read back a marker placed in `$6000-$7FFF` and see whether it got clobbered
3. If it did, this is a **mirror** — switch to `$04`

Init code grows from 71 to 116 bytes.

### Implementation notes

- **On a hot start, the two bytes used for the probe hold the user's program.**
  They must be restored. Save them on the **stack** — that avoids having to guess at a
  free zero-page address
- **Do not hand the verifier the bank number you expect.** If you do, a wrong constant
  makes the check wrong in the same direction and it passes vacuously. Instead, execute
  the rule itself in the model: "pick the lowest number that is genuinely distinct" and
  "the touched bytes come back"
- **Prefer the lower number.** On MiSTer, `$04` is also distinct, but block 4 and above
  fall outside the 32KB `.sav` (blocks 0-3) and would never be saved

### ⚠️ Limitation

**On hardware that genuinely has only 8KB of WRAM, the probe finds nothing.**
Both `$01` and `$04` mirror, so neither choice yields 16KB — yet BASIC still proceeds as
if it had 16KB.

Handling that would require a further patch turning BASIC's "end of area" constant into a
RAM reference. (This project did not include it: both machines available here have 16KB
or more. It becomes necessary if you target 8KB-only hardware.)

## Sources

- NESdev wiki, [MMC5](https://www.nesdev.org/wiki/MMC5) — bit layout of `$5113`/`$5114` (`RAAA AaAA`)
- Measured on two machines: MiSTer FPGA NES core and EverDrive N8 PRO (2026-08-23)
