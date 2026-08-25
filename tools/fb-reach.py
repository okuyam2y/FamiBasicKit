#!/usr/bin/env python3
"""Answer "can this address ever run on the disk build?" soundly enough to act on.

  $ ./fb-reach.py "Family BASIC (Japan) (Rev 2).nes" --target D3E0
  $ ./fb-reach.py "Family BASIC (Japan) (Rev 2).nes" --target D3E0 --cart-nmi

## Why this is not `fb-disasm.py --gaps`

`fb-disasm.py` reports what it could not reach. **Unreached is not dead** - it stops at
jump tables, so most of `$C000-$DFFF` comes back "unconfirmed" and nothing follows from it.
This asks the opposite question and tries to make the answer sound:

  * **`$E000-$FFFF` is not followed.** On a disk that is the BIOS; the cartridge code that
    lived there is gone, so anything only reachable through it cannot run.
  * **A jump table is opened only when an instruction that reads it has been reached.**
    Declaring a table the way `--table` does asserts its entries are live, which is exactly
    the question being asked. Opening tables unconditionally flipped this analysis's answer
    once, in the wrong direction.
  * **Table readers are found, not assumed.** Every `LDA/LDX/LDY abs,(X|Y)` pointing into a
    declared table counts. Missing one would wrongly declare a live table dead.
  * **The holes are counted.** Indirect jumps with no known table, and `RTS` tricks, are
    printed. While any remain unaccounted for the answer is not sound, and saying so is the
    point of the tool.
  * **The ROM is checked, not just its size.** Every address table below was read off one
    specific dump; `--target` refuses to run against a different one, even a same-size 32KB
    PRG, rather than silently applying this ROM's notes to different bytes.

## What it cannot decide

  * **`CALL`** (`$918F JMP ($0400)`) goes wherever the user's machine code says. So can
    `POKE`. Nothing static can bound that, and it is not what "dead code" means here.
  * **Self-modified branches.** Not possible for this ROM: the same bytes are ROM on the
    cartridge, so no path can depend on writing to `$8000-$DFFF`.

## Checking the tool against something known

Pass `--cart-nmi` to add `$CDA7`, the NMI handler the *cartridge* installs at `$C444`.
The disk build never installs it, because `fb-fds.py` replaces `$C436-$C46F`. With it the
built-in conversation program becomes reachable and `$D000-$DFFF` coverage goes from 2% to
36%; without it, neither. **An analysis that says "unreachable" for everything is useless;
this one changes its answer when the entry point does.**
"""

import argparse
import collections
import hashlib
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GONE = 0xE000                  # where the BIOS sits on a disk, so the cartridge code is gone

# `TABLE_RANGES`, `ENTRIES`, `INDIRECT_SITES`, `KNOWN_LIVE`, and `RTS_TRICK_SITES` below are
# all addresses read off one specific dump - none of them mean anything against a different
# ROM, even one the same 32KB size. `rom.base != 0x8000` alone does not catch that (found in
# review): matches `tools/fb-fds.py`'s `WANT_ROM_SHA1` - keep the two in sync.
WANT_ROM_SHA1 = "8e90d9a6a6090307a7e408d1c1704d09ba8f94fc"   # Family BASIC (Japan) (Rev 2), V2.1A

# Jump tables, as (first, one past last). Their readers are located, not written down.
TABLE_RANGES = [
    (0xC2C6, 0xC328),   # the command dispatch table
    (0xC328, 0xC358),   # the function dispatch table (24 entries; ASCII follows)
    (0xAF01, 0xAF25),   # $AC31 JMP ($0028)
    (0xB6A3, 0xB6C3),   # $B6A0 JMP ($0019)
    (0xB8D7, 0xB8F7),   # $B8CC JMP ($0019)
    (0x8841, 0x8865),   # $883E JMP ($001B)
    (0xA27D, 0xA299),   # $9D4B JMP ($0019)
    (0xCD43, 0xCD83),   # $CE7F JMP ($0002) - the built-in conversation program's
]

# What the disk build can start from. The reset and NMI vectors, the pieces `fb-fds.py`'s
# power-on routine jumps to, and the routines its SAVE/LOAD hooks call.
ENTRIES = [0xC400, 0x880F, 0xC470, 0x80F0, 0xAA82, 0xADA9, 0x80AD, 0xB5AF, 0x8131, 0xAF88,
           0x9869, 0x978F, 0x980C, 0x9839, 0xB3EB, 0xB3F1]
CART_NMI_HANDLER = 0xCDA7

# Which table each indirect jump goes through, read off the instructions that fill its
# zero-page pointer. Written down rather than guessed by proximity: an indirect jump that
# is not in here has to show up as unresolved, which is the whole point.
INDIRECT_SITES = {
    0x883E: (0x8841, 0x8865),   # LDA $8841,X / STA $1B ... JMP ($001B)
    0x958D: (0xC2C6, 0xC328),   # LDA $C2C6,X / STA $19 ... JMP ($0019)
    0x9D4B: (0xA27D, 0xA299),   # LDA $A27D,X pushed at $9D20, pulled into $19 at $9D39
    0xAC31: (0xAF01, 0xAF25),   # LDA $AF01,X / STA $28 ... JMP ($0028)
    0xB6A0: (0xB6A3, 0xB6C3),   # LDA $B6A3,X / STA $19 ... JMP ($0019)
    0xB8CC: (0xB8D7, 0xB8F7),   # LDA $B8D7,X / STA $19 ... JMP ($0019)
    0xCE7F: (0xCD43, 0xCD83),   # LDA $CD43,X / STA $02 ... JMP ($0002)
}
CALL_SITE = 0x918F              # JMP ($0400) - BASIC's CALL

# `RTS` tricks: a routine that pushes two bytes and falls into `RTS`, using the CPU stack as
# an indirect jump. Found by scanning, not assumed absent - see `find_rts_tricks()`. Each one
# that is actually reached has to be vetted here, or the answer is not sound, exactly like
# `INDIRECT_SITES` above.
#
# $9122 and $9171 are the only two in this ROM (confirmed by `find_rts_tricks()`: no reached
# path ending in a different `RTS` can reach either by pushing exactly two bytes without an
# intervening `PLA`/`PLP`/`TXS`/`JSR`/another `RTS`). Both consume the *same* two zero-page
# bytes ($5F/$60), pushed by the *same* setup code, by two different routes:
#
#   - `$9122` sits in the FOR-loop/expression-parser "unwind N stack frames and resume"
#     machinery: entry is guarded by a stack marker, popped and checked before the two bytes
#     this jumps to are ever pushed. Two sibling markers feed this same mechanism from
#     different callers: `$00/$FF` (pushed by `LDA #$00/PHA` then `LDA #$FF/PHA` at
#     `$90E8-$90ED`; popped and checked by `CMP #$FF` at `$9103` then an implicit
#     popped-zero check via `PLA`/`BNE` at `$9107` - the path traced below), and `$00/$FD`
#     (the literal bytes sit at `$9300` and `$9303`, and again at `$9382` and `$9385`, right
#     before the two `JMP $911C` sites two paragraphs down). `$00/$FD` matches
#     `FOR_STK_MARKER` in Micah Cowan's `fbdasm` symbol table (github.com/micahcowan/fbdasm,
#     `fb3.sym65`) - that table is for V3,
#     not this V2.1A ROM, so the name is corroborating (the FOR/GOSUB stack-marker idiom is
#     shared Nintendo interpreter code across versions), not the basis: both marker values
#     were read directly off this ROM's own disassembly, at the addresses above.
#   - `$9171` is the `RTS` at the end of a pointer-arithmetic routine ($914B-$9171, no `PHA`
#     anywhere in it) that is normally *called* - reached by plain fall-through right after a
#     `JSR $9172` elsewhere, in which case this `RTS` returns completely ordinarily, to
#     whatever a real call frame further up put on the stack. But `$90F0-$90F4` reaches the
#     same routine by pushing $60 then $5F and *jumping* (not calling) straight into its
#     middle at $914B: no call frame is pushed for that entry, so when execution falls through
#     the arithmetic to $9171's `RTS`, the two bytes on top of the stack are the ones just
#     pushed at $90F0/$90F3, and `RTS` "returns" to them instead. Same trick, same two bytes,
#     a different tail pressed into service as the landing site (found via `find_rts_tricks()`
#     itself, R3 - it did not surface with the narrower R1/R2 detector logic).
#
# The two bytes ($5F/$60) themselves are fed only by: (a) values relayed unchanged through an
# unwind elsewhere in the FOR/GOSUB/expression-parser subsystem ($9280, $930D - same idiom,
# same marker), or (b) a `JMP $911C` right after pushing a fresh marker frame ($9305, $9387 -
# same subsystem again), or (c) the `$90EE-$90F4` setup above, which loads the *current*
# $60/$5F (whatever an earlier, still-further-out frame in this same subsystem left there) and
# jumps into $914B. No site anywhere in the ROM loads a literal constant into $5F/$60 that
# forms an address in `$C400-$D3FF` (the built-in conversation program, where the disk build's
# dead message text lives): the only code that writes `$5F`/`$60` inside that range at all
# ($CEB1/$CF15/$CF1C/$D532) is itself part of the conversation program's own dispatch chain,
# which `--target` already proves unreachable on the disk build. So neither trick can hand
# control to the message block *through this specific mechanism*. That is a trace, not a proof
# the way the indirect-jump table check is a proof: it does not rule out some other write to
# $5F/$60 this scan missed, and `find_rts_tricks()` still only recognizes the "exactly two
# `PHA`, no stack-affecting instruction or subroutine call in between" shape - a trick built
# some other way (e.g. balanced `PHA`/`PLA` pairs that leave two net-new bytes on the stack)
# would not surface at all, the same silent-omission risk three review rounds have now found
# in this function. If either tool or ROM changes, re-run `--target` and re-check this note
# before trusting it again.
RTS_TRICK_SITES = {
    0x9122: "FOR-loop/expression-parser unwind (marker $00/$FD or $00/$FF) - see comment above",
    0x9171: "pointer-arithmetic routine's RTS, pressed into the same trick via a JMP (not JSR) "
            "entry at $90F4 - see comment above",
}

# Where the walk ran into a byte that is not an opcode and stopped. That happens when it
# walks off code into data, which is expected in a ROM with tables interleaved - but "I
# stopped looking here" and "I looked and there is nothing" are different facts, and this
# tool's whole job is to keep them apart. Every one has to be vetted here, exactly like
# `INDIRECT_SITES` and `RTS_TRICK_SITES`; anything else fails loud.
#
# Direction of error, for the record: each of these *adds* a place the walk gave up, so the
# risk is under-approximating what is reachable - which is the direction that could wrongly
# call the message block dead. Hence vetting them rather than ignoring them. All four below
# were traced and are data, not truncated code paths (checked in review).
DATA_STOPS = {
    0xC128: "the V2 reserved-word table's first entry ($80 'GOTO') - fallen into from the "
            "code above it. The table runs $C128-$C2C5, measured off this dump",
    0xC990: "data after the end of the code block above it - nothing branches or jumps here",
    0xA9AB: "the operand byte of `LDA #$3F` at $A9AA. Jump-table entry $B6BF points one byte "
            "into that instruction, so this entry point is spurious; landing mid-instruction "
            "and stopping is the walk declining a bad address, not losing a real path",
    0xCD45: "inside the conversation program's own jump table ($CD43-$CD82) - an entry "
            "pointing into the table itself, so spurious the same way $A9AB is. "
            "Only reached with --cart-nmi",
}

# Addresses that demonstrably run, so a run that misses one is not to be believed.
KNOWN_LIVE = [(0xB5AF, "BG GRAPHIC (menu choice 2)"), (0x80AD, "the title menu"),
              (0x978C, "cassette SAVE"), (0x97FC, "cassette LOAD"),
              (0xAF88, "the keyboard read"), (0x9EE5, "the function dispatch")]


def load_disasm():
    spec = importlib.util.spec_from_file_location(
        "fbdis", os.path.join(HERE, "fb-disasm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--target", action="append", default=[],
                    help="an address to ask about, in hex (repeatable)")
    ap.add_argument("--cart-nmi", action="store_true",
                    help="also start from $CDA7, which only the cartridge installs")
    args = ap.parse_args()

    fbdis = load_disasm()
    rom = fbdis.Rom(args.rom)
    if rom.base != 0x8000:
        sys.exit("this wants a 32KB PRG")
    raw = open(args.rom, "rb").read()
    body_sha1 = hashlib.sha1(raw[16:16 + raw[4] * 16384 + raw[5] * 8192]).hexdigest()
    if body_sha1 != WANT_ROM_SHA1:
        sys.exit(f"{args.rom}: body SHA-1 {body_sha1} is not the V2.1A dump every address "
                  f"table in this file was read off (want {WANT_ROM_SHA1}) - the analysis "
                  f"below would be applying this ROM's notes to a different ROM's bytes")

    def B(a):
        return rom.byte(a)

    def W(a):
        return rom.word(a)

    def find_rts_tricks(reached, pred):
        """Every reached `RTS` that some path of reached instructions can reach by pushing
        exactly two bytes - no intervening `PLA`/`PLP`/`TXS`, `JSR`, or another
        `RTS`/`RTI`/`BRK` - right before it. That is the shape of an `RTS` trick: the two
        pushed bytes become the jump target.

        A blocklist of what stops the walk, not an allowlist of what continues it: an
        allowlist has to name every instruction that cannot disturb the two pushed bytes, and
        missing one silently drops the site instead of flagging it - the same failure this
        function exists to close (an allowlist version of this shipped and missed
        `PHA; ASL; PHA; RTS`, found in review).

        A plain `JMP` or a branch does not touch the stack at all, so it stays off the
        blocklist and the walk crosses it via `pred` - unlike a first version of this
        blocklist, which put every branch and `JMP` on it (copied from "instructions that
        change the answer" without checking each one actually touches the stack) and so
        could not see a two-`PHA` run split across a jump or branch, silently (found in
        review). `pred` records every edge the recursive descent found - fall-through
        *and* branch/`JMP`/`JSR` targets - not just fall-through, so a two-`PHA` run reached
        only by a jump into its tail is not missed either (found in review). Table-driven
        indirect jumps are not followed backward past their entry point; those already have
        to be vetted separately, in `INDIRECT_SITES` or as unresolved - confirmed harmless
        for this ROM's shape in review (the only way in is a plain indirect `JMP`, and
        that already fails loud on its own if unaccounted for).

        Bounded to `LIMIT` new *states* per `RTS` and a shared `seen` set per query (so a
        diamond in the control-flow graph is not walked twice) - a genuinely unbounded walk
        of a ~7000-instruction reached set for every `RTS` is not needed for a ROM this size.
        A state is `(address, pushes-so-far)`, not just `address`: an earlier version
        deduplicated on address alone, so a diamond where one arm reaches a node having
        pushed 0 bytes and another arm reaches the *same* node having pushed 1 could silently
        drop whichever arm's state lost the race to be recorded first - exactly the
        silent-omission failure mode this function exists to close, in the function meant to
        close it (found in review).

        Returns `(tricks, gave_up)`, not one merged list: exhausting the budget without
        finding a two-`PHA` run is not the same fact as never finding one, and merging them
        made the answer wrong in a specific way - an `RTS` that happened to already be in
        `RTS_TRICK_SITES` (like `$9122` or `$9171`) would be printed as vetted even on a run
        where the search gave up rather than actually confirmed the shape does not occur,
        because both cases set the same `found` flag (found in review). Every address in
        `gave_up` has to be treated as unresolved regardless of `RTS_TRICK_SITES` membership.

        Breadth-first, so the *shortest* route to a two-`PHA` run is the one found and the
        budget is spent on near neighbours rather than on one deep wander - with a bounded
        search the traversal order decides how often the bound is hit at all. Measured on
        this ROM: the greediest `RTS` uses 132 states of the 4000, so the bound is nowhere
        near binding today; breadth-first is what keeps that true if the ROM or the walk
        changes. A depth-first version shipped first, described in its own commit message as
        breadth-first (found in review - the description was wrong, not the safety: a
        bound hit lands in `gave_up`, which fails loud)."""
        UNSAFE = {"JSR", "RTS", "RTI", "BRK", "PLA", "PLP", "PHP", "TXS"}
        LIMIT = 4000
        tricks, gave_up = [], []
        for r in reached:
            if B(r) != 0x60:  # RTS
                continue
            seen = {(r, 0)}
            frontier = collections.deque([(r, 0)])
            found = exhausted = False
            budget = LIMIT
            while frontier:
                a, pushes = frontier.popleft()
                stop = False
                for p in pred.get(a, ()):
                    name = fbdis.OPCODES[B(p)][0]
                    if name in UNSAFE:
                        continue
                    new_pushes = pushes + (1 if name == "PHA" else 0)
                    if new_pushes == 2:
                        found = stop = True
                        break
                    state = (p, new_pushes)
                    if state in seen:
                        continue
                    if budget <= 0:
                        exhausted = stop = True
                        break
                    budget -= 1
                    seen.add(state)
                    frontier.append(state)
                if stop:
                    break
            if found:
                tricks.append(r)
            elif exhausted:
                gave_up.append(r)
        return tricks, gave_up

    def readers_of(lo, hi):
        """Every absolute load that points into the table. Missing one would let a live
        table be called dead, so the whole ROM is scanned rather than trusting a note."""
        out = []
        for a in range(0x8000, 0xFFFE):
            if B(a) in (0xAD, 0xBD, 0xB9, 0xAE, 0xBE, 0xAC, 0xBC) and lo - 1 <= W(a + 1) < hi:
                out.append(a)
        return out

    tables = [(lo, hi, readers_of(lo, hi)) for lo, hi in TABLE_RANGES]
    entries = list(ENTRIES) + ([CART_NMI_HANDLER] if args.cart_nmi else [])

    code, indirect, opened = set(), [], set()
    data_stops = set()                     # where the walk hit a non-opcode and stopped
    pred = collections.defaultdict(list)   # instruction address -> every reached predecessor
    work = list(entries)
    while True:
        while work:
            a = work.pop()
            while 0x8000 <= a < GONE and a not in code:
                op = B(a)
                if op not in fbdis.OPCODES:
                    data_stops.add((a, op))
                    break
                name, size, mode = fbdis.OPCODES[op]
                code.add(a)
                if mode in ("abs", "ind"):
                    t = W(a + 1)
                elif mode == "rel":
                    o = B(a + 1)
                    t = a + 2 + (o - 256 if o > 127 else o)
                else:
                    t = None
                takes_branch = (name == "JSR" or (name == "JMP" and mode == "abs")
                                or name in fbdis.BRANCHES)
                if takes_branch and t is not None and 0x8000 <= t < GONE:
                    work.append(t)
                    pred[t].append(a)
                if name == "JMP" and mode == "ind":
                    indirect.append((a, t))
                if name in fbdis.TERMINAL:
                    break
                pred[a + size].append(a)
                a += size
        grew = False
        for lo, hi, readers in tables:
            if (lo, hi) not in opened and any(r in code for r in readers):
                opened.add((lo, hi))
                grew = True
                for x in range(lo, hi, 2):
                    if 0x8000 <= W(x) < GONE:
                        work.append(W(x))
        if not grew:
            break

    print(f"reached {len(code)} instructions / {len(opened)} of {len(tables)} tables opened"
          f"{'  (with the cartridge NMI handler)' if args.cart_nmi else ''}")
    print()
    print("jump tables")
    for lo, hi, readers in tables:
        hit = [r for r in readers if r in code]
        where = " ".join(f"${r:04X}" for r in readers) or "no reader found"
        print(f"  ${lo:04X}-${hi - 1:04X}  read by {where}"
              f"   {'opened' if hit else 'NOT opened: no reader is reachable'}")

    print()
    print("indirect jumps (each must have a table, or the answer is not sound)")
    unresolved = 0
    for a, t in sorted(set(indirect)):
        if a == CALL_SITE:
            note = "CALL - wherever the user's machine code says, out of scope"
        elif a in INDIRECT_SITES:
            lo, hi = INDIRECT_SITES[a]
            note = f"goes through ${lo:04X}-${hi - 1:04X}"
        else:
            note = "*** UNRESOLVED - the answer below is not sound ***"
            unresolved += 1
        print(f"  ${a:04X} -> (${t:04X})   {note}")
    if not indirect:
        print("  none")

    print()
    print("RTS tricks (each must be vetted, or the answer is not sound)")
    rts_tricks, rts_gave_up = find_rts_tricks(code, pred)
    for r in sorted(rts_tricks):
        if r in RTS_TRICK_SITES:
            note = RTS_TRICK_SITES[r]
        else:
            note = "*** UNRESOLVED - the answer below is not sound ***"
            unresolved += 1
        print(f"  ${r:04X}   {note}")
    for r in sorted(rts_gave_up):
        # Always unresolved, even if r is in RTS_TRICK_SITES: the search gave up on budget,
        # it did not confirm the two-PHA shape does not occur, so that vetting does not apply.
        print(f"  ${r:04X}   *** UNRESOLVED (search gave up) - the answer below is not sound ***")
        unresolved += 1
    if not rts_tricks and not rts_gave_up:
        print("  none")

    print()
    print("walked into data and stopped (each must be vetted, or the answer is not sound)")
    for a, op in sorted(data_stops):
        if a in DATA_STOPS:
            note = DATA_STOPS[a]
        else:
            note = "*** UNRESOLVED - the answer below is not sound ***"
            unresolved += 1
        print(f"  ${a:04X} (${op:02X})   {note}")
    if not data_stops:
        print("  none")

    print()
    print("sanity: things that demonstrably run")
    bad = unresolved > 0
    for a, what in KNOWN_LIVE:
        ok = a in code
        bad = bad or not ok
        print(f"  ${a:04X} {what:<28} {'reached' if ok else '*** NOT reached - do not '
                                                            'believe this run ***'}")

    if args.target:
        print()
        print("asked about")
        for t in args.target:
            a = int(t, 16)
            print(f"  ${a:04X}   {'REACHABLE' if a in code else 'not reachable'}")

    per4k = collections.Counter(a >> 12 for a in code)
    print()
    print("coverage " + " ".join(f"${p:X}xxx {per4k.get(p, 0) * 100 // 4096}%"
                                 for p in range(0x8, 0xE)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
