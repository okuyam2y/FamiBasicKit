#!/usr/bin/env python3
"""Relocate the V3 BASIC interpreter at `$8000-$9FFF` to a different address.

  $ ./fb-relocate.py "Family BASIC V3 (Japan).nes" -o "V3 (canary).nes"

## Why relocate at all

Making the free area 16KB (`$6000-$9FFF`) requires turning `$8000-$9FFF` into RAM, which
evicts the 8KB interpreter living there. **The only space that can be freed is where the
four built-in programs sit at `$D400-$FFF9`** (a music demo and three games).

## What has to be fixed (full counts)

| Kind | Count | How they are found |
|---|---|---|
| Absolute `JSR`/`JMP`/data references | 860 | operands of disassembled instructions |
| Relative branches | 479 | **not fixed** (the range is extended so targets move too) |
| Jump-table entries | 4 tables | the `LDA tbl,X / STA $19 / LDA tbl+1,X / STA $1A / JMP ($0019)` shape |
| Addresses built from two immediates | 2 | `LDA #<lo / STA zp / LDA #>hi / STA zp+1` |
| Reset / IRQ vectors | 2 | `$FFFC` / `$FFFE` |

(Counts measured on the actual V3. The number really fixed is printed at run time — this
table explains the reasoning; decisions are made on the runtime output.)

**Jump tables were told apart from data tables by the instructions that read them**
(`docs/background/relocation-notes.md`: "a byte-pattern match is a hypothesis, not a meaning").
`$8013` (sound periods), `$B37F` (palettes), `$B44A` (function-key strings) and `$C787`
(16-bit addends) are **data** whose values merely happen to look like `$80xx-$9Fxx`, so
they are left alone.

## Verification

The output is disassembled again and checked until "no reference points into
`$8000-$9FFF`" and "the instruction boundaries of the moved 8KB match the original 1:1".
**Nothing is written unless it passes.**
"""

import argparse
import hashlib
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "fb_disasm", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fb-disasm.py"))
fbd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbd)

BLOCK_LO, BLOCK_HI = 0x8000, 0x9FFF
BLOCK_SIZE = BLOCK_HI - BLOCK_LO + 1

# --- Disassembly entry points -------------------------------------------------
# Places that are genuinely instructions but are not reachable from a vector; the
# reasoning for each is recorded below.
# EXPECTED_ORPHANS are the entry points the orphan sweep (seed_from_orphans) should
# find. **This set was confirmed to be instructions on the real ROM**, so any change
# in it means the input is not what we expect (a different ROM or version) and we stop.
# All four are "a JSR/JMP left in a region we could not reach"; leaving them unfixed
# crashes programs run under GAME (measured on hardware).
# EXPECTED_BAD are the places where disassembly stops on an undefined opcode. They
# happen because **a table entry points at data**, and this is the set a genuine ROM
# produces. Merely printing the count would not do: with an unexpected input a whole
# path can vanish from the original and the output alike, leaving the instruction-
# boundary comparison vacuous.
# EXPECTED_VECTORS are the three vectors of the original ROM. NMI points into RAM
# ($00ED), so it never enters `fixes` and no check touches it. It therefore has to be
# **confirmed at input time**: without this, an input whose NMI was changed to $80BA
# passes every check, and in the final ROM the NMI jumps into $8000-$9FFF, which is
# now WRAM
EXPECTED_VECTORS = {0xFFFA: 0x00ED, 0xFFFC: 0x80BA, 0xFFFE: 0x80BA}

# The PRG+CHR of the original ROM itself. Checking only structure and vectors lets an
# input through whose **data** was altered (a sound-period table, say) — measured: a pair
# with `$8013` changed from `$AE` to `$AF` was accepted. This tool is for a stock V3 only,
# so pinning the whole thing is the right call
SOURCE_SHA256 = "c8c0b6c21bdda7503bab7592aea0f945a0259c18504bb241aafb1eabe65846f3"

# The set of "what to fix and what its original value was". **Ground truth independent of
# the converter's own configuration (CODE_TABLES and friends).** Without it, dropping a
# single table from the configuration makes the checks vacuous (measured: removing `$8250`
# left seven stale references at $D250 while every check still passed).
# **Immediate pointers are included too** — without them, dropping one entry from
# `IMMEDIATE_POINTERS` leaves a stale high byte at the destination and everything passes
EXPECTED_FIXES_SHA256 = "d30efa4042b8877ce45eec7bd828bf07547e440e319729671492d516d4d215f4"

EXPECTED_BAD = {
    0xC226, 0xC234, 0xC235, 0xC23B, 0xC242,      # inside strings the $C250 table points at
    0xCFAE, 0xCFBE, 0xCFCE, 0xCFDE, 0xCFEE,      # past the end of the $CF41 table
}

EXPECTED_ORPHANS = {
    0x8916: "JSR $908C (right after the JMP at $8913; only reached from elsewhere)",
    0xABAC: "JMP $8491 (right after the RTS at $ABAB)",
    0xAFF6: "JMP $8494 (right after the RTS at $AFF5)",
    0xAFF9: "JMP $8491",
}

MANUAL_ENTRIES = {
    0x8000: "the JMP $80BA at the start of ROM (vectors point straight at $80BA, so it is never reached)",
    0x8971: "body of the NMI handler; $80D0 puts $8971 into $EE/$EF and copies it to RAM",
    0x90F4: "a small JSR $912D / JSR $8DF2 / RTS routine; the caller is unidentified but it is code",
}

# --- Address tables (entries are code addresses) -------------------------------
# (start, entry count, what the entries point at, evidence for the count)
CODE_TABLES = [
    (0x89A3, 15, "code", "$8996 jumps through JMP ($001B); entry 16 stops being a ROM address"),
    (0xA86E, 6, "code", "CMP #$F9 / SBC #$F3 at $A315 -> the 6 tokens $F3-$F8"),
    (0xB8BE, 32, "code", "CMP #$20 / BCS just before $B58F -> subscripts 0-31"),
    (0xC0F3, 6, "code", "$49 just before $C0E6 is capped by CMP #$05"),
    (0xC250, 6, "code", "$C295; entry 7 stops being a ROM address"),
    (0xC33D, 8, "code", "CPX #$08 just before $C328"),
    (0xCEBF, 68, "code", "SBC #$80 / CMP #$44 / BCS at $8432 -> tokens $80-$C3"),
    (0xCF41, 37, "code", "SBC #$CA / CMP #$EF at $A4CE -> tokens $CA-$EE"),
    (0xCFA4, 2, "code", "CMP #$01 just before $AF5E"),
    (0xCFA8, 3, "code", "CMP #$03 just before $AF81"),
    # A table whose entries point at message strings. They are not code, so they are not
    # used as disassembly entry points, but **they are addresses and must be fixed**
    # ($821C-$824F holds strings like " ON" and " OFF").
    (0x8250, 7, "data", "$820F loads $52/$53 and does JMP $895A (print string); entry 8 is not a ROM address"),
]

# --- Addresses assembled from two immediates -----------------------------------
# (address of the high-byte immediate, the address it builds, evidence)
IMMEDIATE_POINTERS = [
    (0x80D1, 0x8971, "$80D0: LDA #$71 / STA $EE / LDA #$89 / STA $EF -> $EE/$EF = $8971"),
    (0x816C, 0x817B, "$816B: LDA #$7B / STA $52 / LDA #$81 / STA $53 -> $52/$53 = $817B"),
]

# Things that look like addresses but **are not** (the reasons for leaving them alone).
NOT_POINTERS = """
  $8337 LDA #$89 -> $0D   ; $0D/$0E = $BB89; #$89 is the LOW byte
  $934C LDA #$8B -> $99   ; a token value, later compared with CMP #$95
  $A9CE LDA #$83 -> $2C   ; $2C/$2D = $0383 (RAM)
  $A289 LDA #$80 -> $21   ; $20/$21 is never used indirectly, so it is just a variable
  $BE05 LDA #$80 -> $AF   ; $AD-$B0 are the two pairs $0500 / $0580
  $BC80 LDA #$80 -> $74   ; DEC $74 follows; a counter
  $A1A0 / $A1D3           ; values written to the $4000 range (sound)
  ORA/AND #$80, various   ; bit manipulation
  CMP #$8x, various       ; token comparisons
"""


# The range that holds code. Outside it ($D000-$FFFF) is the text of the built-in
# programs, so a `20`/`4C` there is not an instruction (those byte values occur naturally
# in BASIC text).
CODE_AREA = (0x8000, 0xCFFF)


def seed_from_orphans(rom, tr):
    """Pick up references from unreached fragments that are **definitely instructions**.

    Recursive descent only reaches what the vectors and the address tables lead to. In
    practice there are small fragments — right after an `RTS`, for instance — reachable
    only through a path we never followed. If one holds a `JSR`/`JMP <inside the block>`,
    **it gets missed and breaks silently.**

    The criteria: not yet reached as an instruction, AND its target lands exactly on
    **an instruction boundary we did reach**. Data rarely satisfies both at once.
    This actually turned up four (`$8916` / `$ABAC` / `$AFF6` / `$AFF9`) — leaving those
    four unfixed on real hardware crashed programs run under `GAME`.
    """
    starts = set(tr.code)
    found = []
    for addr in range(CODE_AREA[0], CODE_AREA[1] - 2):
        if addr in tr.covered:
            continue
        op = rom.byte(addr)
        if op not in (0x20, 0x4C):
            continue
        target = rom.word(addr + 1)
        if BLOCK_LO <= target <= BLOCK_HI and target in starts:
            found.append(addr)
    return found


def trace(rom, verbose=False):
    """Descend from the entry points and tables, repeating until no fragment is left."""
    tr = fbd.Trace(rom)
    for vec in (0xFFFC, 0xFFFA, 0xFFFE):
        tr.add_entry(rom.word(vec), "vector")
    for addr, why in MANUAL_ENTRIES.items():
        tr.add_entry(addr, why)
    for base, count, kind, _why in CODE_TABLES:
        if kind != "code":
            continue
        for i in range(count):
            tr.add_entry(rom.word(base + 2 * i), f"table ${base:04X}[{i}]")
    tr.run()

    all_orphans = []
    for round_no in range(1, 9):
        orphans = seed_from_orphans(rom, tr)
        if not orphans:
            break
        all_orphans += orphans
        if verbose:
            print(f"  orphan sweep {round_no}: {len(orphans)} found " +
                  " ".join(f"${a:04X}" for a in orphans))
        for addr in orphans:
            tr.add_entry(addr, "orphan")
            tr.entries.pop(addr, None)
        tr2 = fbd.Trace(rom)
        tr2.entries = dict(tr.entries)
        for addr in orphans:
            tr2.add_entry(addr, "orphan")
        tr2.run()
        tr = tr2

    if set(tr.bad) != EXPECTED_BAD:
        extra = sorted(set(tr.bad) - EXPECTED_BAD)
        missing = sorted(EXPECTED_BAD - set(tr.bad))
        sys.exit("the places that stop on an undefined opcode differ from expectation "
                 "(the input ROM may be a different one): "
                 f"extra {[f'${a:04X}' for a in extra[:6]]} / "
                 f"missing {[f'${a:04X}' for a in missing[:6]]}")
    if set(all_orphans) != set(EXPECTED_ORPHANS):
        extra = sorted(set(all_orphans) - set(EXPECTED_ORPHANS))
        missing = sorted(set(EXPECTED_ORPHANS) - set(all_orphans))
        def head(xs):
            shown = " ".join(f"${a:04X}" for a in xs[:6])
            return f"{len(xs)} ({shown}{' ...' if len(xs) > 6 else ''})" if xs else "0"
        sys.exit("the orphan entry points differ from expectation (the input ROM may be "
                 f"a different one): extra {head(extra)} / missing {head(missing)}")
    return tr


def check_source_rom(raw, label):
    """Confirm that **the V3 ROM being relocated** is what we expect.

    WARNING: `fb-mmc5-16k.py` must call this same function. Splitting the check into two
    paths creates a way in through only one of them (measured: `fb-mmc5-16k.py` used to
    call `trace()`/`relocate()` directly, so a ROM with a rewritten NMI passed through
    that path alone).
    """
    if raw[:4] != b"NES\x1a":
        sys.exit(f"{label}: no iNES header")
    if raw[6] & 0x04:
        sys.exit(f"{label}: ROMs with a trainer are not supported (everything after byte 16 shifts)")
    if raw[4] != 2:
        sys.exit(f"{label}: PRG is not 32KB ({raw[4] * 16}KB)")
    if raw[5] != 1:
        sys.exit(f"{label}: CHR is not 8KB ({raw[5] * 8}KB)")
    if raw[8] & 0x0F:
        # NES 2.0 keeps the high bits of the mapper number in the low nibble of byte 8.
        # Ignoring it would let a mapper-256 ROM through as "mapper 0"
        sys.exit(f"{label}: the NES 2.0 mapper high bits (byte8 = ${raw[8]:02X}) are not supported")
    if raw[9]:
        # NES 2.0 keeps the high bits of the PRG/CHR size in byte 9. Without this, an
        # input whose header claims a huge PRG but holds 32KB passes the length check
        sys.exit(f"{label}: the NES 2.0 size high bits (byte9 = ${raw[9]:02X}) are not supported")
    mapper = (raw[6] >> 4) | (raw[7] & 0xF0)
    if mapper != 0:
        sys.exit(f"{label}: mapper is not 0/NROM ({mapper}); this tool targets a stock V3 ROM")
    want_len = 16 + 0x8000 + 0x2000
    if len(raw) != want_len:
        sys.exit(f"{label}: file length disagrees with the header ({len(raw)} != {want_len})")
    for vec, want in sorted(EXPECTED_VECTORS.items()):
        got = raw[16 + vec - 0x8000] | (raw[16 + vec + 1 - 0x8000] << 8)
        if got != want:
            sys.exit(f"{label}: vector ${vec:04X} differs from expectation (${got:04X} != ${want:04X})")
    got = hashlib.sha256(raw[16:16 + 0x8000 + 0x2000]).hexdigest()
    if got != SOURCE_SHA256:
        sys.exit(f"{label}: PRG+CHR does not match a stock V3 "
                 f"(sha256 {got[:16]}... != {SOURCE_SHA256[:16]}...)")


def overlapping(tr, addr):
    """Is this instruction's operand **also the first byte of another instruction**?

    On the 6502 there is an idiom where `BIT abs` (`2C`) swallows two operand bytes, and
    a branch landing one byte later **executes those two bytes as a different
    instruction**. From the real ROM:

        $963E  BEQ $9643      ; branching goes to $9643
        $9642  BIT $9785      ; falling through skips 85 97 (2C 85 97)
        $9643  STA $97        ; arriving by branch runs 85 97 as STA $97

    That operand is **an instruction as well as an address**, so it must not get +delta.
    A version that did rewrite it turned `STA $97` into `STA $E7`, corrupting a different
    zero-page variable (measured on a ROM already running on hardware).
    Because both the reference value and the instruction boundaries come out as expected,
    every other check passes.
    """
    size = tr.code[addr][1]
    return any(addr + k in tr.code for k in range(1, size))


def collect_fixes(rom, tr, last):
    """Collect every place holding a 16-bit value that is an address inside the block.

    Returns {address where the value sits: original value}. Values are little-endian pairs.
    """
    fixes = {}

    # 1. Instruction operands. **The yardstick is the range being moved** (through
    #    $A00A). The range was extended for branches leaving the block, so absolute
    #    references into it must be fixed too, or the moved copy keeps pointing back at
    #    the original location and is not self-contained
    skipped = []
    for addr, name, mode, target in tr.refs:
        if mode in fbd.ABS16 and BLOCK_LO <= target <= last:
            if overlapping(tr, addr):
                skipped.append((addr, name, target))   # overlapping instruction; leave alone
                continue
            fixes[addr + 1] = target

    # 2. Address-table entries
    for base, count, _kind, _why in CODE_TABLES:
        for i in range(count):
            at = base + 2 * i
            value = rom.word(at)
            if BLOCK_LO <= value <= last:
                fixes[at] = value

    # 3. Vectors
    for vec in (0xFFFC, 0xFFFE):
        if BLOCK_LO <= rom.word(vec) <= last:
            fixes[vec] = rom.word(vec)

    return fixes, skipped


def boundary(tr):
    """Extend the end of the moved range until **moving it wholesale changes nothing**.

    Leaving relative branches alone is only safe when their targets move along with them.
    The real ROM overflows the block in two ways, both solved by extending the range
    (with a bridge back to what followed):

      1. **An instruction straddles the boundary** — `JSR $908C` at `$9FFF` has its
         operand at `$A000-$A001`
      2. **A relative branch inside the block targets outside it** — `$9FE1 BEQ $A005`,
         `$9FF9 BEQ $A005` and `$9FFD BNE $A009`. **Moving without extending shifts the
         targets.** A version that missed this had `$EFE1`/`$EFF9` land on the first byte
         of the MMC5 init code (`SEI`) and `$EFFD` in the middle of an instruction
         (`BRK`), so exercising that BASIC path **silently restarted BASIC** (measured on
         a ROM running on hardware)

    Extending pulls new instructions into the range, which may branch out again, so
    **repeat until it stops growing**.
    """
    last = BLOCK_HI
    for _ in range(64):
        grown = last
        for addr, (name, size, mode, operand) in tr.code.items():
            if addr <= grown < addr + size - 1:          # straddles an instruction
                grown = max(grown, addr + size - 1)
        for addr, (name, size, mode, operand) in tr.code.items():
            if mode == "rel" and BLOCK_LO <= addr <= grown and operand > grown:
                tsize = tr.code[operand][1] if operand in tr.code else 1
                grown = max(grown, operand + tsize - 1)  # out to the end of the target
        if grown == last:
            break
        last = grown
    else:
        sys.exit("the range to move does not converge (a branch target is too far)")
    return last, last + 1


def relocate(rom, tr, dest, canary):
    """Build the new 32KB image."""
    delta = dest - BLOCK_LO
    # WARNING: for addresses assembled from two immediates **only the high byte is
    # fixed** (the position of the low byte is not recorded). So the relocation delta must
    # land on a page boundary (low byte 0). Example: moving by +$5001 would make $8971
    # become $D971 where it should be $D972 — and the 16-bit operand checks would not
    # catch it.
    if delta & 0xFF:
        sys.exit(f"relocation delta ${delta:04X} does not have a zero low byte. "
                 f"Addresses assembled from immediates would break, so move ${BLOCK_LO:04X} "
                 f"to a page boundary ($xx00)")
    new = bytearray(rom.view)          # $8000-$FFFF
    last, fallthrough = boundary(tr)

    def put(addr, value):
        new[addr - 0x8000] = value & 0xFF

    # Move the block (including the overhang of the instruction straddling the boundary)
    for i in range(last - BLOCK_LO + 1):
        new[dest - 0x8000 + i] = rom.byte(BLOCK_LO + i)
    # Bridge to what execution used to fall through into (JMP <original continuation>)
    bridge = dest + (last - BLOCK_LO) + 1
    put(bridge, 0x4C)
    put(bridge + 1, fallthrough & 0xFF)
    put(bridge + 2, fallthrough >> 8)
    # Fill the vacated place with the canary (the overhang past $A000 stays as it was;
    # nothing looks at it)
    for i in range(BLOCK_SIZE):
        new[BLOCK_LO - 0x8000 + i] = canary

    fixes, skipped = collect_fixes(rom, tr, last)
    touched = {bridge, bridge + 1, bridge + 2}      # places written on purpose
    for at, value in sorted(fixes.items()):
        # If the place holding the 16-bit value moved as well, fix it at its new home.
        # The test uses the range that was moved (through $A001): the operand of the
        # instruction straddling the boundary sits at $A000-$A001 and travelled along
        # with $9FFF
        where = at + delta if BLOCK_LO <= at <= last else at
        put(where, (value + delta) & 0xFF)
        put(where + 1, ((value + delta) >> 8) & 0xFF)
        touched |= {where, where + 1}

    # For addresses assembled from two immediates, only the high byte gets the delta
    for at, value, _why in IMMEDIATE_POINTERS:
        where = at + delta if BLOCK_LO <= at <= last else at
        assert new[where - 0x8000] == (value >> 8), \
            f"the immediate at ${at:04X} differs from expectation ({new[where - 0x8000]:02X} != {value >> 8:02X})"
        new[where - 0x8000] = ((value + delta) >> 8) & 0xFF
        touched.add(where)

    return bytes(new), fixes, delta, last, bridge, touched, skipped


def unintended_writes(rom, new, dest, delta, last, bridge, touched):
    """Check that **nothing outside the intended places changed**, without disassembly.

    Checks (a) and (b) use the same `Trace` the conversion did, so a shared blind spot
    would let both pass together. This check does not depend on disassembly at all — it
    compares the original image with the produced image directly — so it does not share
    their failure mode.
    """
    bad = []
    span = last - BLOCK_LO
    for addr in range(0x8000, 0x10000):
        got = new[addr - 0x8000]
        if BLOCK_LO <= addr <= BLOCK_HI:
            continue                                   # filled with the canary
        if bridge <= addr <= bridge + 2:
            continue                                   # the bridge we built
        want = rom.byte(addr - delta) if dest <= addr <= dest + span else rom.byte(addr)
        if got != want and addr not in touched:
            bad.append((addr, want, got))
    return bad


def verify(original, new, dest, delta, last, orphans, fixes, skipped):
    """Disassemble the output again and check it. Anything wrong is reported."""
    problems = []

    tmp = fbd.Rom.__new__(fbd.Rom)
    tmp.prg = new
    tmp.base = 0x8000
    tmp.view = new
    tr2 = fbd.Trace(tmp)
    for vec in (0xFFFC, 0xFFFA, 0xFFFE):
        tr2.add_entry(tmp.word(vec), "vector")
    for addr in list(MANUAL_ENTRIES) + orphans:
        tr2.add_entry(addr + delta if BLOCK_LO <= addr <= last else addr, "manual")
    for base, count, kind, _why in CODE_TABLES:
        if kind != "code":
            continue
        at = base + delta if BLOCK_LO <= base <= last else base   # moved range runs to $A001
        for i in range(count):
            tr2.add_entry(tmp.word(at + 2 * i), "tbl")
    tr2.run()

    # Overlapping instructions are **correctly left alone**, so exclude them from (a-1)/(a-2)
    skipped_at = {(a + delta if BLOCK_LO <= a <= last else a) for a, _n, _t in skipped}

    # (a-1) not a single reference still points into the original block
    left = [r for r in tr2.refs if BLOCK_LO <= r[3] <= BLOCK_HI and r[0] not in skipped_at]
    if left:
        problems.append(f"{len(left)} references still point into $8000-$9FFF: " +
                        " ".join(f"${a:04X}->{t:04X}" for a, _, _, t in left[:8]))

    # (a-2) references into the destination number **exactly as many** as those that
    # pointed into the original block. A zero-count test alone would let a bad rewrite
    # that sends them to neither address (e.g. $0000) slip through.
    span = last - BLOCK_LO
    # Count against **the range being moved** (through $A00A). Branches leaving the
    # block became part of the move, so counting only to $9FFF undercounts the source
    before = sum(1 for r in original.refs if BLOCK_LO <= r[3] <= last) - len(skipped)
    after = sum(1 for r in tr2.refs if dest <= r[3] <= dest + span)
    if before != after:
        problems.append(f"reference counts into the destination disagree ({before} before -> {after} after)")

    # (a-2b) **Compare every value individually.** A count alone lets a wrong target
    # somewhere else in $Dxxx pass all three of count, instruction boundaries and
    # "nothing left in the old range". The inputs to this comparison are the original
    # disassembly and the bytes of the produced image — never the rewriter's own records.
    wrong = []
    for addr, _name, mode, target in original.refs:
        if mode not in fbd.ABS16 or not (BLOCK_LO <= target <= last):
            continue
        if overlapping(original, addr):                # correctly left alone
            where = addr + delta if BLOCK_LO <= addr <= last else addr
            size = original.code[addr][1]
            if any(tmp.byte(where + k) != original.rom.byte(addr + k) for k in range(size)):
                problems.append(f"bytes of the overlapping instruction at ${addr:04X} changed (they must not)")
            continue
        at = addr + 1
        where = at + delta if BLOCK_LO <= at <= last else at
        if tmp.word(where) != target + delta:
            wrong.append((addr, target, tmp.word(where)))
    if wrong:
        problems.append(f"{len(wrong)} references were rewritten to the wrong target: " +
                        " ".join(f"${a:04X}(${t:04X}->${g:04X})" for a, t, g in wrong[:6]))

    # (a-0) **Every place we decided to fix really is fixed.**
    # Without this, a place we forgot looks to `unintended_writes` like a place we never
    # touched (the original value, so no diff), and the other checks sail past too.
    # The **reset/IRQ vectors** fell into exactly this hole: leaving `$FFFC` as `$80BA`
    # passed every check, and a ROM that would not boot was reported as verified.
    # Vectors are neither instruction operands nor table entries, so (a-2b)/(a-3) miss them.
    unapplied = []
    for at, value in sorted(fixes.items()):
        where = at + delta if BLOCK_LO <= at <= last else at
        if tmp.word(where) != value + delta:
            unapplied.append((at, value, tmp.word(where)))
    if unapplied:
        problems.append(f"{len(unapplied)} places we decided to fix were not fixed: " +
                        " ".join(f"${a:04X}(${v:04X}->${g:04X})" for a, v, g in unapplied[:6]))

    # (a-0a) **Check the set of "what to fix" itself, independently of the converter's
    # configuration.** (a-0) treats `fixes` as ground truth, so dropping an entry from the
    # configuration (`CODE_TABLES` and friends) removes it from `fixes` as well and every
    # other check passes in unison.
    blob = b"".join(f"{a:04X}:{v:04X};".encode() for a, v in sorted(fixes.items()))
    blob += b"|IMM|" + b"".join(f"{a:04X}:{v:04X};".encode()
                                for a, v, _why in IMMEDIATE_POINTERS)
    got = hashlib.sha256(blob).hexdigest()
    if got != EXPECTED_FIXES_SHA256:
        problems.append(f"the set of places to fix differs from expectation "
                        f"({len(fixes)} 16-bit + {len(IMMEDIATE_POINTERS)} immediates, "
                        f"sha256 {got[:16]}... != {EXPECTED_FIXES_SHA256[:16]}...)")

    # (a-0b) **Check the vectors independently of `fixes`.** (a-0) only looks at places
    # we decided to fix, so anything missed by the collection itself passes. Even with a
    # vector left pointing at the old address, `MANUAL_ENTRIES` still finds the code and
    # the instruction-boundary comparison passes.
    for vec in (0xFFFA, 0xFFFC, 0xFFFE):
        was = original.rom.word(vec)
        want = was + delta if BLOCK_LO <= was <= last else was
        if tmp.word(vec) != want:
            problems.append(f"vector ${vec:04X} is wrong "
                            f"(${tmp.word(vec):04X}, expected ${want:04X})")

    # (a-0c) **Check the bridge independently** (the JMP from the end of the moved range
    # back to the original continuation). `unintended_writes` excludes the bridge
    # unconditionally, so changing its target by one byte passed every check
    # (measured: `JMP $A00B` became `JMP $A00E`).
    bridge_at = dest + (last - BLOCK_LO) + 1
    want_bridge = bytes([0x4C, (last + 1) & 0xFF, (last + 1) >> 8])
    got_bridge = bytes(tmp.byte(bridge_at + k) for k in range(3))
    if got_bridge != want_bridge:
        problems.append(f"the bridge at ${bridge_at:04X} is wrong "
                        f"({got_bridge.hex(' ')}, expected {want_bridge.hex(' ')})")

    # (a-3) Table entries and immediate pointers really did get +delta.
    # (a-1)/(a-2) look at **instruction operands only** (Trace.refs is populated by
    # Trace.run in fb-disasm.py). A missed table entry or immediate keeps its original
    # bytes, so `unintended_writes` sees it as a place never touched and it slips past.
    for base, count, _kind, _why in CODE_TABLES:
        at0 = base + delta if BLOCK_LO <= base <= last else base
        for i in range(count):
            was = original.rom.word(base + 2 * i)
            now = tmp.word(at0 + 2 * i)
            want = was + delta if BLOCK_LO <= was <= last else was
            if now != want:
                problems.append(f"table ${base:04X}[{i}] was not fixed "
                                f"(${was:04X} -> ${now:04X}, expected ${want:04X})")
    for at, value, _why in IMMEDIATE_POINTERS:
        where = at + delta if BLOCK_LO <= at <= last else at
        if tmp.byte(where) != ((value + delta) >> 8) & 0xFF:
            problems.append(f"the high byte of immediate pointer ${at:04X} was not fixed "
                            f"(${tmp.byte(where):02X}, expected ${((value + delta) >> 8) & 0xFF:02X})")

    # (b) instruction boundaries in the moved 8KB match the original 1:1
    orig_starts = {a for a in original.code if BLOCK_LO <= a <= last}
    new_starts = {a - delta for a in tr2.code if dest <= a <= dest + (last - BLOCK_LO)}
    if orig_starts != new_starts:
        only_old = sorted(orig_starts - new_starts)[:6]
        only_new = sorted(new_starts - orig_starts)[:6]
        problems.append(
            f"instruction boundaries disagree ({len(orig_starts - new_starts)} only in the "
            f"original {[f'${a:04X}' for a in only_old]} / "
            f"{len(new_starts - orig_starts)} only in the output "
            f"{[f'${a:04X}' for a in only_new]})")

    return problems, tr2


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("-o", "--out", help="the .nes to write")
    ap.add_argument("--dest", default="D000", help="start of the destination (default D000)")
    ap.add_argument("--canary", default="02",
                    help="byte to fill the vacated $8000-$9FFF with "
                         "(default 02, an opcode that jams the CPU if executed)")
    args = ap.parse_args()

    raw = open(args.rom, "rb").read()
    check_source_rom(raw, args.rom)
    rom = fbd.Rom(args.rom)
    dest = int(args.dest, 16)
    canary = int(args.canary, 16)

    tr = trace(rom, verbose=True)
    print(f"disassembly: {len(tr.code)} instructions / {len(tr.covered)} bytes covered"
          f" ({100 * len(tr.covered) / 0x8000:.1f}%) / {len(tr.bad)} undefined opcodes")
    inblock = sum(1 for a in tr.covered if BLOCK_LO <= a <= BLOCK_HI)
    print(f"  bytes in $8000-$9FFF confirmed to be instructions: {inblock}/{BLOCK_SIZE}"
          f" ({100 * inblock / BLOCK_SIZE:.1f}%)")

    new, fixes, delta, last, bridge, touched, skipped = relocate(rom, tr, dest, canary)
    print(f"\nrelocated: $8000-${last:04X} -> ${dest:04X}-${dest + last - BLOCK_LO:04X} (+${delta:04X})")
    if last != BLOCK_HI:
        print(f"  moved {last - BLOCK_HI} bytes of overhang for instructions crossing the boundary")
        print(f"  JMP ${(new[bridge - 0x8000 + 1] | new[bridge - 0x8000 + 2] << 8):04X} at "
              f"${bridge:04X} (bridge to what execution fell through into)")
    print(f"  16-bit values fixed: {len(fixes)} + {len(IMMEDIATE_POINTERS)} immediates")
    if skipped:
        print(f"  references LEFT ALONE because they are overlapping instructions: "
              f"{len(skipped)} " +
              " ".join(f"${a:04X}({n} ${t:04X})" for a, n, t in skipped))

    orphans = [a for a, why in tr.entries.items() if why == "orphan"]
    problems, _ = verify(tr, new, dest, delta, last, orphans, fixes, skipped)
    if problems:
        print("\nverification failed:")
        for p in problems:
            print(f"  x {p}")
        sys.exit(1)
    stray = unintended_writes(rom, new, dest, delta, last, bridge, touched)
    if stray:
        print("\nverification failed:")
        print(f"  x {len(stray)} unintended writes: " +
              " ".join(f"${a:04X}({w:02X}->{g:02X})" for a, w, g in stray[:8]))
        sys.exit(1)
    print(f"  ok: all {len(fixes)} places we decided to fix (vectors included) are fixed")
    print("  ok: no reference points into $8000-$9FFF, and the counts match before/after")
    print("  ok: instruction boundaries in the moved 8KB match the original 1:1")
    print("  ok: table entries and immediate pointers received +delta too")
    print(f"  ok: not one byte outside the intended {len(touched)} places changed "
          "(an independent check that does not rely on disassembly)")

    if args.out:
        header = bytearray(raw[:16])
        chr_data = raw[16 + 0x8000:]              # length already checked against the header
        out = bytes(header) + new + chr_data
        open(args.out, "wb").write(out)
        print(f"\nwrote: {args.out} ({len(out)} bytes / "
              f"MD5 {hashlib.md5(out).hexdigest()})")


if __name__ == "__main__":
    main()
