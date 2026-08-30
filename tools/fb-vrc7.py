#!/usr/bin/env python3
"""Build the VRC7 (mapper 85) version: the same BASIC, with FM sound reachable from `POKE`.

  $ ./fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"
  $ ./fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"

  $ ./fb-vrc7.py --8k "Family BASIC (Japan) (Rev 2).nes" -o "V2.1A (VRC7 8KB).nes"

Two versions go in: **V3.0** (or the 8KB build `fb-expand-basic-area.py` makes from it) and
**V2.1A** (or its 4KB build). They need different work and cost different things, so each
has its own section below.

## What this buys

A VRC7 cartridge carries a 6-channel FM sound chip whose two ports sit in the address
space BASIC can already reach: `POKE &H9010,<register>` then `POKE &H9030,<value>`.
Nothing else about BASIC changes. (Hex, because V3's integer literals are signed 16-bit
and `36880` raises `?OV` - see `docs/reference/build-differences.md`.)

**On V3 it does not widen the free area.** VRC7's RAM window is `$6000-$7FFF`, the same
8KB `fb-expand-basic-area.py` already hands V3 on plain mapper 0. Run that first if you
want the 8KB; on V3 this tool keeps whatever area the input has.

**On V2.1A, `--8k` widens it here** - from the stock 2KB to the full `$6000-$7FFF`, the
same move the disk build makes, whose patch sites this tool reads out of `fb-fds.py`
rather than repeating. It is a flag rather than the default because the save layout moves
with it and `fb-basic-to-sav.py` cannot write that layout yet. Without the flag, V2.1A
keeps the area the input has, exactly as V3 does.

**A 16KB free area and FM are mutually exclusive** and always will be: the FM ports are
inside `$8000-$9FFF`, which the 16KB build turns into RAM. A cartridge has one mapper, so
the MMC5 16KB build and this one are different ROMs.

## The one hard problem: where the init code goes

VRC7 powers up with **nothing configured** - the three switchable PRG banks, all eight
CHR banks and the WRAM write-enable are undefined. So a few dozen bytes have to run before
the original reset handler (`$80BA` on V3, `$C400` on V2.1A), and they have to run from
`$E000-$FFFF`, the only window whose contents are known at power-on (it is fixed to the
last bank).

In both stock ROMs **that window is full**. What follows is how V3 makes room; V2.1A has
no built-in programs to move, and its own answer is under "The V2.1A build" further down,
in the section comment above `V21A_RESET`.

In V3's stock ROM: the four built-in programs run from `$D400` to `$FFF9` with no filler
anywhere, and the vectors sit on top of them. There is no room.

So the same move the MMC5 16KB build makes is made here: **each built-in program gets its
own 8KB bank, with the program in the upper half (`$D000-$DFFF`), and the `GAME` load
routine is rebuilt to swap that bank in.** Once the programs are read from their own
banks, the copies at `$E000-$FFF9` are dead, and the init code and the rebuilt loader go
there.

The lower half of each program bank duplicates `$C000-$CFFF`, so the swap only takes
`$D000-$DFFF` away - the half the program occupies - and `$C000-$CFFF` reads the same
either way.

That is precaution rather than a fix for a known hazard. V3's NMI handler is `$8971`,
installed by `$80CA` through the trampoline at `$00ED`, and `$8000-$9FFF` is never
swapped; the loader also turns NMI off across the copy. (`$CDA7` **is** the handler the
cartridge installs on V1/V2, which is what `fb-reach.py` and `fb-fds.py` mean by it. In
V3 those bytes are the middle of the reserved-word table, so it is not a reason for
anything here - on V2.1A it is the whole hinge, and the V2.1A section says why.)

## Memory map after power-on

The map below is the V3 build's. V2.1A stays at 32KB and four banks: 0/1/2 switched into
`$8000`/`$A000`/`$C000`, 3 fixed at `$E000`, and no bank-swapping loader at all.

| CPU address space | Contents (V3 build) |
|---|---|
| `$6000-$7FFF` | WRAM - the free area (4KB or 8KB, whatever the input declares) |
| `$8000-$9FFF` | ROM bank 4 = original `$8000-$9FFF`. **`$9010`/`$9030` are the FM ports** |
| `$A000-$BFFF` | ROM bank 5 = original `$A000-$BFFF` (the `GAME` entry is patched here) |
| `$C000-$DFFF` | ROM bank 6 = original `$C000-$DFFF` (title graphic still at `$D000`) |
| `$E000-$FFFF` | ROM bank 7 = original `$E000-$FFFF` + init + loader, fixed |
| banks 0-3 | one built-in program each, at `$D000`, swapped into `$C000-$DFFF` while loading |

**Every address the CPU can see is byte-for-byte the input's 32KB**, except in four
places: the init code and the rebuilt loader, both in dead built-in-program bytes at
`$E000-$FFF9`; the four-byte jump to the loader that replaces the stock call at `$AD96`;
and the reset and IRQ vectors, which now enter the init code. That is checked by
executing the ROM, not by reading it - see "What is checked" below, and `changed` in
`main()`, which is the same list in code.

## Writing into `$8000-$FFFF` is no longer harmless

On mapper 0 a store into ROM space did nothing, and the stock ROM contains no such store
(`fb-disasm.py --xref 8000-FFFF` reports zero among reachable instructions). On VRC7 the
same store would change a bank under the running code. That is the risk this build takes
on, and it is also what makes `POKE` able to reach the FM ports at all.

## Which VRC7 - A3 or A4?

Two boards exist: VRC7b decodes registers with A3 (`$x008`, submapper 1, **no sound**) and
VRC7a with A4 (`$x010`, submapper 2, sound). The header declares submapper 2, because FM
is the whole point. But the init code is written so that **it lands correctly under either
decoding**: the ambiguous registers (PRG select 1, the odd CHR selects) are written to
both aliases first, and the unambiguous one (`$x000`) last, which fixes up whichever of
the two got clobbered. Both readings are executed by the self-check below.

## What is checked before the file is written

(2 is the V3 build's; the V2.1A build has no rebuilt loader, and what stands in its place
is listed in the V2.1A section comment.)

1. **The map** - the ROM is executed from the reset vector on a model of the 6502 and of
   VRC7, with every unconfigured bank treated as poison (touching one fails on the spot),
   until it reaches the stock reset handler. Then all of `$8000-$FFFF` is compared against
   the authenticated input byte for byte, and WRAM must be writable. Both decodings.
   ⚠️ The baseline is the **input**, not the stock dump: the 8KB build of V3 and the 4KB
   build of V2.1A are supported inputs and legitimately differ from stock. What makes the
   input a known quantity is the authentication before it, not this comparison.
2. **The loader** - for every built-in program, the **stock** loader is executed on a
   model of the stock cartridge and the rebuilt loader on this build, and the bytes they
   leave in the program area are compared up to the end address the ROM itself records.
   A rebuilt loader that reads the wrong bank fails here rather than on hardware.
   Both are entered at `$AD96`, the stock call site, so the four-byte jump planted there
   is executed rather than assumed: entering the rebuilt loader at its own address
   instead would pass with no jump there at all (measured - three broken patches, one of
   them `JMP` turned into `NOP`, all built cleanly before this was fixed).

Everything the two loaders are compared on (the table addresses, the program count, the
copy limit, where the stock routine returns to) is **read out of the input ROM**, not
hard-coded, so a different dump fails loudly instead of being converted wrongly.
"""

import argparse
import hashlib
import os
import sys
import types

def sibling(name):
    """Load a tool next to this one, **from its source text**.

    ★Not `importlib`'s loader: that path serves Python's bytecode cache whenever the
    cache's recorded (mtime, size) still match the file, and a source that was replaced
    with one of the same size in the same second matches. Measured 2026-08-30, during the
    mutation testing this project requires: a site table in `fb-fds.py` was changed, the
    file was restored, and this tool went on building from the *replaced* table - clean
    working tree, wrong ROM (`a99d7012...` instead of `17add98d...`), every check green,
    because the ratchet was reading the same stale bytecode it was meant to police.
    ⚠️ The other tools here that import a sibling still go through `importlib` and can be
    fooled the same way.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    mod = types.ModuleType(name.replace("-", "_").removesuffix(".py"))
    mod.__file__ = path
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), mod.__dict__)      # noqa: S102 - our own tool
    return mod


# The tiny assembler lives in the MMC5 builder. Importing it keeps **one** copy: a second
# copy of an assembler in this file would be the same rule in two places, which in this
# repository has drifted within a day of being duplicated.
Asm = sibling("fb-mmc5-16k.py").Asm

BANK = 8192
MAPPER = 85
SUBMAPPER = 2                    # VRC7a: A4 decoding, sound present

# Registers (VRC7a addresses; the A3 aliases are $x008)
PRG_SELECT_0 = 0x8000            # $8000-$9FFF   (unambiguous)
PRG_SELECT_1 = (0x8008, 0x8010)  # $A000-$BFFF   (ambiguous: write both, see the docstring)
PRG_SELECT_2 = 0x9000            # $C000-$DFFF   (unambiguous)
CHR_SELECT = [0xA000, (0xA008, 0xA010), 0xB000, (0xB008, 0xB010),
              0xC000, (0xC008, 0xC010), 0xD000, (0xD008, 0xD010)]
MIRRORING = 0xE000               # bit7 WRAM enable / bit6 silence expansion sound / bits1-0 mirroring
IRQ_CONTROL = 0xF000             # unambiguous; 0 = IRQ off
SOUND_ADDR, SOUND_DATA = 0x9010, 0x9030      # documented here, never written by this build

# Bank numbers. Programs first, so the loader can write the program number straight into
# the bank register with no arithmetic.
BANK_8000, BANK_A000, BANK_C000, BANK_E000 = 4, 5, 6, 7
TOTAL_BANKS = 8

INIT_ORG = 0xE000                # both land in what was built-in-program data
LOADER_ORG = 0xE080
# Written down rather than measured off the assembler's output, for the same reason as the
# V2.1A pair below: a window measured on the thing it exempts grows with it, and a block
# that grew would be overwritten by the next one rather than reported. The digest pins the
# bytes as well as the count - `check_map` executes the init and checks the state it
# leaves, which says nothing about an instruction that changes no state the model keeps.
# ⚠️ Both are meant to change when `build_init` changes; that is the point. Update them
# deliberately, with the output MD5s in hand.
INIT_LEN, LOADER_LEN = 94, 81
INIT_SHA = "a3b7e1b4fe2d1821d6effb1a2b9160470a7a56d6b42b29baefe2dacf08699444"

# The stock `GAME` load routine. Everything that differs between dumps is a wildcard and
# is read out rather than assumed; the rest must match exactly or this is not a ROM whose
# loader this tool understands.
# The stock V3 PRG+CHR. `fb-relocate.py:76` pins this same digest for the same reason and
# records the measurement behind it: checking only structure lets an input through whose
# **data** was altered - there, one byte at `$8013` changed from `$AE` to `$AF` was
# accepted. The loader signature below has the same hole, so it gets the same fix.
STOCK_SHA256 = "c8c0b6c21bdda7503bab7592aea0f945a0259c18504bb241aafb1eabe65846f3"

# The only bytes `fb-expand-basic-area.py` changes in V3's PRG, as (stock, expanded)
# pairs. Normalising them away before the digest is taken is what lets this tool accept
# **both** the stock dump and the 8KB build - the two inputs it documents - and nothing
# else. A byte here that is neither value is a corrupted dump, not a wider area.
# The stock V3 header, byte for byte. Byte 10 is the **only** field allowed to differ, and
# only in step with the program-area constants below. Hashing PRG+CHR alone left every
# behaviour-affecting header field open: flipping byte 6 bit 0 built a ROM with the
# nametable arrangement inverted, and `check_map` passed it, because that check reads the
# arrangement out of the very header that was changed. Byte 7 says NES 2.0, which byte 10
# needs to mean anything at all; byte 8 would carry mapper bits 8-11 and byte 9 the high
# bits of both sizes, so the legacy fields do not settle either; byte 15 is the expansion
# device, and on this ROM it names the Family BASIC keyboard.
STOCK_HEADER = bytes.fromhex("4e45531a0201030800006000000000 23".replace(" ", ""))
NVRAM_BYTE = {"stock": 0x60, "expanded": 0x70}      # 4KB / 8KB, per the area state

AREA_SITES = {0x86A4: (0x6F, 0x7F),      # end of the program area
              0x97D8: (0x70, 0x80),      # the CLEAR ceiling
              0xB1BE: (0x6C, 0x7C),      # BGGET/BGPUT buffer, three sites
              0xB1CB: (0x6C, 0x7C),
              0xB20C: (0x6C, 0x7C)}

# What `read_loader()` must find, once the image above is pinned. These are not
# expectations about a parser - the input is fixed to two known ROMs, so they are facts
# about those ROMs, measured from them. Comparing them turns a silent regression in the
# parser into a stopped build.
#
# It needs to be a separate comparison because the rest of this file cannot see such a
# regression: every check derives its own window from the same values. Forcing `limit` to
# `$6D` built a ROM whose rebuilt loader stops at `$6D00` where the stock one runs to
# `$7000` - and every program ends by `$6CF8`, so the map check, both loader comparisons
# and the FCEUX test all passed while the output MD5 moved.
STOCK_LOADER_FACTS = {
    "end_table": 0x8003, "src_table": 0x800B, "count": 4,
    "limit": 0x70, "resume": 0x97A6,
    "sources": [0xD400, 0xDBFE, 0xE682, 0xF308],
    "ends": [0x6541, 0x6A8A, 0x6C8C, 0x6CF8],
}

LOADER_SITE = 0xAD96
STOCK_LOADER = bytes.fromhex(
    "a5 05 85 19"        # dest = $05/$06 (start of the program area)
    "a5 06 85 1a"
    "a5 f3 0a aa"        # X = program number * 2
    "bd 00 00 85 07"     # $07/$08 = end address, from a table
    "bd 00 00 85 08"
    "bd 00 00 85 1b"     # $1B/$1C = source address, from a second table
    "bd 00 00 85 1c"
    "a0 00"              # copy until the destination high byte reaches the limit
    "b1 1b 91 19"
    "e6 1b d0 02 e6 1c"
    "e6 19 d0 02 e6 1a"
    "a9 00"
    "c5 1a d0 ea"
    "4c 00 00")          # and resume
WILDCARDS = {13, 14, 18, 19, 23, 24, 28, 29, 51, 57, 58}


class Fault(Exception):
    """Touching something the hardware has not been told about yet."""


# --------------------------------------------------------------------------- input


def load(path):
    d = open(path, "rb").read()
    if d[:4] != b"NES\x1a":
        sys.exit(f"{path}: no iNES header")
    if len(d) < 16:
        sys.exit(f"{path}: an iNES header is 16 bytes, this file is {len(d)}")
    if d[6] & 0x04:
        sys.exit(f"{path}: has a trainer; not handled")
    prg_size, chr_size = d[4] * 16384, d[5] * 8192
    if len(d) != 16 + prg_size + chr_size:
        sys.exit(f"{path}: the file length disagrees with the header "
                 f"({len(d)} != 16 + {prg_size} + {chr_size})")
    mapper = (d[7] & 0xF0) | (d[6] >> 4)
    if mapper != 0:
        sys.exit(f"{path}: mapper {mapper}, expected 0 (feed this the stock ROM, or the "
                 f"output of fb-expand-basic-area.py)")
    if prg_size != 0x8000:
        sys.exit(f"{path}: PRG is {prg_size} bytes, expected 32768")
    if chr_size != 0x2000:
        sys.exit(f"{path}: CHR is {chr_size} bytes, expected 8192")
    return bytearray(d[:16]), bytes(d[16:16 + prg_size]), bytes(d[16 + prg_size:])


def check_stock(header, prg, chr_data):
    """Refuse anything but stock V3 or the 8KB build made from it.

    The structural checks - header, mapper, sizes, the `GAME` loader signature - all pass
    on a V3 dump whose data has been altered somewhere they do not look, and every later
    check in this file compares the output against **that same input**, so the damage is
    reproduced rather than caught. Pinning the whole image is the only thing that closes
    it, and it is what `fb-relocate.py` concluded after measuring the same failure.
    """
    image = bytearray(prg) + chr_data
    odd, seen = [], set()
    for addr, (stock, expanded) in AREA_SITES.items():
        got = image[addr - 0x8000]
        if got == expanded:
            image[addr - 0x8000] = stock          # an expanded area is allowed
            seen.add("expanded")
        elif got == stock:
            seen.add("stock")
        else:
            odd.append(f"${addr:04X}=${got:02X}")
    # **All five or none.** Normalising each site on its own would accept the mixtures as
    # well - a program area reaching $7FFF with the `CLEAR` ceiling still at $70, say,
    # which hashes clean and declares 8KB but cannot use it. `fb-expand-basic-area.py`
    # only ever writes the five together, so a mixture is damage, not a narrower area.
    if len(seen) > 1:
        odd.append("the five program-area constants are a mixture of stock and expanded, "
                   "which no build produces")
    if not odd:
        # The header has to match the same state. `fb-expand-basic-area.py --keep-header`
        # makes exactly the pair this rejects - a program area reaching `$7FFF` with the
        # header still declaring 4KB - and says so ("the expansion then has no effect on
        # real hardware").
        state = seen.pop()
        want = bytearray(STOCK_HEADER)
        want[10] = NVRAM_BYTE[state]
        if bytes(header) != bytes(want):
            bad = [f"byte{i}=${header[i]:02X} (expected ${want[i]:02X})"
                   for i in range(16) if header[i] != want[i]]
            sys.exit(f"the header is not stock V3's, with a {state} program area: "
                     + ", ".join(bad))
    got = hashlib.sha256(bytes(image)).hexdigest()
    if odd or got != STOCK_SHA256:
        why = ("the program-area constants hold values that are neither stock nor "
               f"expanded ({', '.join(odd)})") if odd else \
              f"sha256 {got[:16]}... != {STOCK_SHA256[:16]}..."
        sys.exit(f"this is not a stock V3 dump, nor the 8KB build made from one: {why}.\n"
                 f"       Only those two are supported. A dump that differs anywhere else "
                 f"would be converted faithfully, damage included.")


def read_loader(prg):
    """Read the addresses the stock `GAME` loader works from, out of the loader itself.

    Hard-coding them would convert a ROM this tool has never seen into a broken one:
    the tables sit at different addresses in other versions, and V1/V2 have a different
    routine altogether. Here a mismatch is an error.
    """
    got = prg[LOADER_SITE - 0x8000:LOADER_SITE - 0x8000 + len(STOCK_LOADER)]
    for i, (a, b) in enumerate(zip(got, STOCK_LOADER)):
        if i not in WILDCARDS and a != b:
            sys.exit(f"${LOADER_SITE + i:04X}: this is not the GAME loader this tool knows "
                     f"(${a:02X}, expected ${b:02X}). This tool builds from V3.0 and "
                     f"V2.1A; this dump is neither.")
    word = lambda i: got[i] | got[i + 1] << 8
    end_table, src_table = word(13), word(23)
    if word(18) != end_table + 1 or word(28) != src_table + 1:
        sys.exit("the loader reads its tables in a way this tool does not understand")
    count = (src_table - end_table) // 2
    if not 1 <= count <= 4 or end_table + 2 * count != src_table:
        sys.exit(f"derived a program count of {count} from the two tables "
                 f"(${end_table:04X}/${src_table:04X}); expected 1 to 4")
    off = end_table - 0x8000
    ends = [prg[off + 2 * n] | prg[off + 2 * n + 1] << 8 for n in range(count)]
    off = src_table - 0x8000
    sources = [prg[off + 2 * n] | prg[off + 2 * n + 1] << 8 for n in range(count)]
    return {"end_table": end_table, "src_table": src_table, "count": count,
            "ends": ends, "sources": sources, "limit": got[51], "resume": word(57)}


# --------------------------------------------------------------------------- code


def build_init(mirror_bits, reset_addr, org=INIT_ORG,
               banks=(BANK_8000, BANK_A000, BANK_C000)):
    """Configure VRC7 at power-on, then hand over to the stock reset handler.

    Order matters twice over:

      * the ambiguous registers are written through **both** aliases before the
        unambiguous one is written, so the result is right under A3 and A4 decoding alike
      * `$E000` (WRAM enable) is written last of the mapper setup, because everything
        BASIC does from `$80BA` on assumes it can write to `$6000-$7FFF`

    `org` and `banks` are parameters rather than module constants because the V2.1A build
    puts this at `$F000` and has only four banks, so its three switchable windows are
    0/1/2 rather than V3's 4/5/6. **The model would hide the difference** - `Vrc7Bus._bank`
    reduces the register value modulo the bank count, so 4/5/6 in a four-bank ROM comes
    out as 0/1/2 and every check passes. Passing the values in is what makes the two
    builds say what they mean; `check_map` then compares the model's raw `prg_banks`.
    """
    b_8000, b_a000, b_c000 = banks
    a = Asm(org)
    a.emit("SEI", "imp", note="VRC7's IRQ powers up undefined")

    def put(reg, value, note):
        a.emit("LDA", "imm", value)
        a.emit("STA", "abs", reg, note)

    # Pass 1: the registers whose address is read differently by the two boards. Each is
    # written through both aliases, so one of the two writes lands on the register next
    # door - always one whose correct value is written in pass 2.
    for alias in PRG_SELECT_1:
        put(alias, b_a000, f"$A000-$BFFF <- bank {b_a000} (${alias:04X} alias)")
    for n, reg in enumerate(CHR_SELECT):
        if isinstance(reg, tuple):
            for alias in reg:
                put(alias, n, f"CHR {n} (${alias:04X} alias)")
    # Pass 2: the unambiguous registers, which both boards read the same way. **These have
    # to come last**: they are what repairs whatever pass 1 hit by mistake.
    # $E000-$FFFF is fixed to the last bank, so there is no register for it and the code
    # running right now cannot be swapped out from under itself.
    put(PRG_SELECT_0, b_8000, f"$8000-$9FFF <- bank {b_8000}")
    put(PRG_SELECT_2, b_c000, f"$C000-$DFFF <- bank {b_c000}")
    for n, reg in enumerate(CHR_SELECT):
        if not isinstance(reg, tuple):
            put(reg, n, f"CHR {n}")
    put(IRQ_CONTROL, 0x00, "IRQ off")
    put(MIRRORING, 0x80 | mirror_bits,
        "WRAM writable (bit7) / expansion sound audible (bit6 clear) / "
        + ("vertical" if mirror_bits == 0 else "horizontal"))
    a.emit("JMP", "abs", reset_addr, "the stock reset handler")
    return a


def build_loader(info):
    """The `GAME n` load routine, called in place of the stock one at `$AD96`.

    Same copy loop as the stock routine, with two differences: the source is always
    `$D000` (the upper half of the program's own bank) and the bank is swapped in around
    the copy. NMI is held off while `$D000-$DFFF` is not itself.
    """
    a = Asm(LOADER_ORG)
    a.emit("LDA", "zp", 0x05, "destination = start of the program area")
    a.emit("STA", "zp", 0x19)
    a.emit("LDA", "zp", 0x06)
    a.emit("STA", "zp", 0x1A)
    a.emit("LDA", "zp", 0xF3, "program number")
    a.emit("ASL", "imp")
    a.emit("TAX", "imp")
    a.emit("LDA", "abx", info["end_table"], "end address, from the stock table")
    a.emit("STA", "zp", 0x07)
    a.emit("LDA", "abx", info["end_table"] + 1)
    a.emit("STA", "zp", 0x08)
    # **Why NMI has to go off**: BASIC's NMI handler uses `$19-$1C`, which are the very
    # pointers this copy walks. An NMI in the middle sends the source pointer into the
    # interpreter's own code - measured in FCEUX, where driving the stock loader with NMI
    # left on copied 4,047 bytes of garbage. It is not about where the handler lives.
    a.emit("LDA", "zp", 0x32, "NMI off while $D000-$DFFF is swapped out")
    a.emit("AND", "imm", 0x7F)
    a.emit("STA", "abs", 0x2000)
    a.emit("LDA", "zp", 0xF3, "program n lives in bank n")
    a.emit("STA", "abs", PRG_SELECT_2)
    a.emit("LDA", "imm", 0x00, "source = $D000")
    a.emit("STA", "zp", 0x1B)
    a.emit("LDA", "imm", 0xD0)
    a.emit("STA", "zp", 0x1C)
    a.emit("LDY", "imm", 0x00)
    loop = a.label("loop")
    a.emit("LDA", "izy", 0x1B)
    a.emit("STA", "izy", 0x19)
    a.emit("INC", "zp", 0x1B)
    a.emit("BNE", "rel", a.pc + 4)
    a.emit("INC", "zp", 0x1C)
    a.emit("INC", "zp", 0x19)
    a.emit("BNE", "rel", a.pc + 4)
    a.emit("INC", "zp", 0x1A)
    a.emit("LDA", "imm", info["limit"], "until the destination high byte reaches the stock limit")
    a.emit("CMP", "zp", 0x1A)
    a.emit("BNE", "rel", loop)
    a.emit("LDA", "imm", BANK_C000, "put $C000-$DFFF back")
    a.emit("STA", "abs", PRG_SELECT_2)
    # `$32` is BASIC's shadow of `$2000` and **does not carry bit 7**; BASIC adds it when
    # it re-enables NMI itself (`$BE14` is that routine, and this is it inlined). Writing
    # `$32` back unchanged leaves NMI off - black screen, no keyboard. That was measured
    # on hardware during the MMC5 build.
    #
    # So this is a save/restore, not an unconditional enable: BASIC does **not** bracket
    # the loader with its own `$BE0E`/`$BE14` (the `GAME` path calls `$8662`, which only
    # sets zero-page variables), so nothing else would put NMI back.
    a.emit("LDA", "zp", 0x32, "NMI back on (bit 7 is not in $32)")
    a.emit("ORA", "imm", 0x80)
    a.emit("STA", "abs", 0x2000)
    a.emit("JMP", "abs", info["resume"], "where the stock routine went next")
    return a


# --------------------------------------------------------------------------- models


class Cpu:
    """Enough 6502 to run the init code and either loader. Anything else stops the run."""

    def __init__(self, bus, pc):
        self.bus, self.pc = bus, pc
        self.a = self.x = self.y = 0
        self.z = self.n = False

    def _set(self, v):
        self.z, self.n = v == 0, bool(v & 0x80)
        return v

    def step(self):
        r, w = self.bus.read, self.bus.write
        op = r(self.pc)
        b1, b2 = r(self.pc + 1), r(self.pc + 2)
        abs_ = b1 | b2 << 8
        zp = lambda p: r(p) | r((p + 1) & 0xFF) << 8
        if op == 0x78:                                          # SEI
            self.pc += 1
        elif op == 0xA9:                                        # LDA #imm
            self.a = self._set(b1); self.pc += 2
        elif op == 0xA5:                                        # LDA zp
            self.a = self._set(r(b1)); self.pc += 2
        elif op == 0xAD:                                        # LDA abs
            self.a = self._set(r(abs_)); self.pc += 3
        elif op == 0xBD:                                        # LDA abs,X
            self.a = self._set(r((abs_ + self.x) & 0xFFFF)); self.pc += 3
        elif op == 0xB1:                                        # LDA (zp),Y
            self.a = self._set(r((zp(b1) + self.y) & 0xFFFF)); self.pc += 2
        elif op == 0x85:                                        # STA zp
            w(b1, self.a); self.pc += 2
        elif op == 0x8D:                                        # STA abs
            w(abs_, self.a); self.pc += 3
        elif op == 0x91:                                        # STA (zp),Y
            w((zp(b1) + self.y) & 0xFFFF, self.a); self.pc += 2
        elif op == 0xA0:                                        # LDY #imm
            self.y = self._set(b1); self.pc += 2
        elif op == 0xA2:                                        # LDX #imm
            self.x = self._set(b1); self.pc += 2
        elif op == 0xAA:                                        # TAX
            self.x = self._set(self.a); self.pc += 1
        elif op == 0x0A:                                        # ASL A
            self.a = self._set((self.a << 1) & 0xFF); self.pc += 1
        elif op == 0xC8:                                        # INY
            self.y = self._set((self.y + 1) & 0xFF); self.pc += 1
        elif op == 0xE6:                                        # INC zp
            w(b1, self._set((r(b1) + 1) & 0xFF)); self.pc += 2
        elif op == 0xC5:                                        # CMP zp
            self._set((self.a - r(b1)) & 0xFF); self.pc += 2
        elif op == 0xC9:                                        # CMP #imm
            self._set((self.a - b1) & 0xFF); self.pc += 2
        elif op == 0x29:                                        # AND #imm
            self.a = self._set(self.a & b1); self.pc += 2
        elif op == 0x09:                                        # ORA #imm
            self.a = self._set(self.a | b1); self.pc += 2
        elif op in (0xD0, 0xF0):                                # BNE / BEQ
            self.pc += 2
            if (op == 0xD0) != self.z:
                self.pc = (self.pc + (b1 - 256 if b1 > 127 else b1)) & 0xFFFF
        elif op == 0x4C:                                        # JMP abs
            self.pc = abs_
        else:
            raise Fault(f"${self.pc:04X}: opcode ${op:02X} is not modelled")

    def run_until(self, target, limit=2_000_000):
        for _ in range(limit):
            if self.pc == target:
                return
            self.step()
        raise Fault(f"${target:04X} was not reached in {limit} instructions")


class NromBus:
    """The stock cartridge: 32KB at `$8000`, WRAM at `$6000`, and stores into ROM do nothing."""

    def __init__(self, prg):
        self.prg, self.ram, self.wram = prg, bytearray(0x800), bytearray(0x2000)

    def read(self, addr):
        addr &= 0xFFFF
        if addr < 0x2000:
            return self.ram[addr & 0x7FF]
        if addr < 0x6000:
            return 0
        if addr < 0x8000:
            return self.wram[addr - 0x6000]
        return self.prg[addr - 0x8000]

    def write(self, addr, value):
        addr &= 0xFFFF
        if addr < 0x2000:
            self.ram[addr & 0x7FF] = value
        elif 0x6000 <= addr < 0x8000:
            self.wram[addr - 0x6000] = value


class Vrc7Bus:
    """This build on a VRC7, with everything unconfigured treated as poison.

    `decode` picks which address line selects registers: `a4` is VRC7a (the board with
    the sound chip), `a3` is VRC7b. The init code is meant to survive either, and the
    self-check runs both.
    """

    def __init__(self, prg, decode="a4"):
        self.prg, self.decode = prg, decode
        self.nbanks = len(prg) // BANK
        self.ram, self.wram = bytearray(0x800), bytearray(0x2000)
        self.prg_banks = [None, None, None]
        self.chr_banks = [None] * 8
        self.wram_enabled = False
        # Undefined at power-on, so the model starts it silenced: the init has to clear
        # bit 6 for this to come out true. Left unmodelled, a build with the FM output
        # muted passes every check here and only a human ear can tell (measured).
        self.sound_enabled = False
        self.mirroring = self.irq_control = None
        self.sound = []                      # (register, value) pairs, if any are written
        self.blocked_wram_writes = 0

    def _bank(self, slot, addr):
        v = self.prg_banks[slot]
        if v is None:
            reg = (PRG_SELECT_0, PRG_SELECT_1[1], PRG_SELECT_2)[slot]
            raise Fault(f"read ${addr:04X} while the bank register ${reg:04X} was still "
                        f"unset (undefined at power-on)")
        return v % self.nbanks

    def read(self, addr):
        addr &= 0xFFFF
        if addr < 0x2000:
            return self.ram[addr & 0x7FF]
        if addr < 0x6000:
            return 0
        if addr < 0x8000:
            if not self.wram_enabled:
                raise Fault(f"read ${addr:04X} while WRAM was still protected ($E000 bit 7)")
            return self.wram[addr - 0x6000]
        if addr >= 0xE000:
            return self.prg[(self.nbanks - 1) * BANK + (addr - 0xE000)]
        slot = (addr - 0x8000) // BANK
        return self.prg[self._bank(slot, addr) * BANK + (addr % BANK)]

    def write(self, addr, value):
        addr &= 0xFFFF
        if addr < 0x2000:
            self.ram[addr & 0x7FF] = value
            return
        if addr < 0x6000:
            return                                    # PPU / APU
        if addr < 0x8000:
            if self.wram_enabled:
                self.wram[addr - 0x6000] = value
            else:
                self.blocked_wram_writes += 1
            return
        group, alt = addr & 0xF000, bool(addr & (0x10 if self.decode == "a4" else 0x08))
        if group == 0x8000:
            self.prg_banks[1 if alt else 0] = value & 0x3F
        elif group == 0x9000:
            # Under A3 decoding `$9010` is **not** the sound port - it is PRG select 2.
            # That is the hardware's own answer to "what happens if this ends up on a
            # VRC7b board": `POKE &H9010` would switch a bank instead of making a sound.
            if alt:
                self.sound.append((addr, value))
            else:
                self.prg_banks[2] = value & 0x3F
        elif group in (0xA000, 0xB000, 0xC000, 0xD000):
            self.chr_banks[((group - 0xA000) >> 12) * 2 + (1 if alt else 0)] = value
        elif group == 0xE000:
            if not alt:
                self.mirroring = value & 3
                self.wram_enabled = bool(value & 0x80)
                self.sound_enabled = not value & 0x40      # bit 6 silences expansion sound
        elif group == 0xF000:
            if not alt:
                self.irq_control = value


# --------------------------------------------------------------------------- checks


def check_map(prg, orig_prg, reset_addr, decode, changed, ines_vertical, expect_banks=None):
    """Power on, run the init code, and compare what the CPU then sees with the input.

    `changed` is every address this build deliberately altered. Anything outside it that
    differs means a bank came out wrong, which is the failure this check exists for.
    """
    bus = Vrc7Bus(prg, decode)
    # Everything the model itself raises - an unset bank read, a poisoned window - says
    # which decoding it happened under. Without this the two iterations are
    # indistinguishable in the output, and a mutation that only breaks the A3 board
    # reports the same words as one that breaks both (measured while writing the suite:
    # dropping one alias fails only under A3, and said so nowhere).
    try:
        cpu = Cpu(bus, bus.read(0xFFFC) | bus.read(0xFFFD) << 8)
        cpu.run_until(reset_addr, limit=1000)
        unexpected = [a for a in range(0x8000, 0x10000)
                      if a not in changed and bus.read(a) != orig_prg[a - 0x8000]]
    except Fault as e:
        raise Fault(f"[{decode}] {e}") from None
    if unexpected:
        raise Fault(f"[{decode}] {len(unexpected)} bytes of $8000-$FFFF differ from the "
                    f"authenticated input, first at ${unexpected[0]:04X}")
    if not bus.wram_enabled:
        raise Fault(f"[{decode}] WRAM is still write-protected after the init code")
    # The one thing this build exists for: bit 6 of `$E000` silences VRC7's expansion
    # sound output, and nothing downstream would notice - the ROM boots,
    # BASIC runs, `POKE &H9010` still writes the register, and no sound comes out.
    if not bus.sound_enabled:
        raise Fault(f"[{decode}] $E000 bit 6 is set, which silences the FM output")
    # The **raw** register values, before `_bank` reduces them modulo the bank count.
    # Reading them back is the only way to tell 4/5/6 from 0/1/2 in a four-bank ROM: the
    # reduction makes both map the same way, so a map comparison cannot see the difference
    # The mutation this catches: passing `build_init` the V3 numbers 4/5/6 that were
    # constants here before they became arguments.
    if expect_banks is not None and bus.prg_banks != list(expect_banks):
        raise Fault(f"[{decode}] the PRG bank registers hold {bus.prg_banks}, "
                    f"expected {list(expect_banks)}")
    if bus.chr_banks != list(range(8)):
        raise Fault(f"[{decode}] CHR banks came out {bus.chr_banks}, expected 0-7")
    if bus.irq_control != 0:
        raise Fault(f"[{decode}] the IRQ control register was left at {bus.irq_control}")
    # Nothing on the boot screen depends on this - BASIC uses one nametable, and a build
    # with the arrangement inverted comes up pixel-identical in FCEUX (measured). So it
    # is checked here against what the header declares, or it is not checked at all.
    #
    # **The value the init code was told to write is deliberately not passed in.** Handing
    # a check the answer the code under test used means a wrong constant moves both at
    # once: with that version of this check, inverting the arrangement built cleanly.
    # `0 = vertical` is read off the register layout on the NESdev wiki's VRC7 page.
    if (bus.mirroring == 0) != ines_vertical:
        raise Fault(f"[{decode}] the nametable arrangement came out {bus.mirroring}, "
                    f"but the header declares "
                    f"{'vertical' if ines_vertical else 'horizontal'}")
    return bus


def check_loader(built, orig_prg, info, dest_start, decode):
    """Run both loaders for every built-in program and compare what lands in the area.

    The stock loader is executed on a model of the stock cartridge, the rebuilt one on
    this build. They are compared over exactly the bytes the ROM's own table calls the
    program: past that end address the stock routine copies whatever follows in the
    address space (for the last program, that runs off the end of the ROM and into RAM),
    and none of it is part of the program.

    **Both start at `LOADER_SITE`**, not at the address each routine happens to live at.
    On this build that means running the jump this tool planted at `$AD96`, so a wrong
    jump fails here. Starting the rebuilt loader at `LOADER_ORG` would hand the check the
    answer it exists to verify.
    """
    out = []
    for n in range(info["count"]):
        stock = NromBus(orig_prg)
        for zp, v in ((0x05, dest_start & 0xFF), (0x06, dest_start >> 8), (0xF3, n)):
            stock.write(zp, v)
        Cpu(stock, LOADER_SITE).run_until(info["resume"])

        bus = Vrc7Bus(built, decode)
        cpu = Cpu(bus, bus.read(0xFFFC) | bus.read(0xFFFD) << 8)
        cpu.run_until(info["reset"], limit=1000)       # the init code has to run first
        # What the machine actually had at $C000-$DFFF before the loader ran. Asking for
        # it back is the property; asking for `BANK_C000` would be asking the check to
        # agree with the constant `build_loader` emitted.
        was_mapped = bus.prg_banks[2]
        for zp, v in ((0x05, dest_start & 0xFF), (0x06, dest_start >> 8), (0xF3, n)):
            bus.write(zp, v)
        Cpu(bus, LOADER_SITE).run_until(info["resume"])   # through the planted jump

        # A loader that forgets to put the bank back leaves a built-in program where
        # $C000-$DFFF should be. Nothing in the copied bytes shows that, so it is asked
        # separately - the machine would only fall over later, somewhere else.
        if bus.prg_banks[2] != was_mapped:
            raise Fault(f"[{decode}] program {n}: the loader left $C000-$DFFF on bank "
                        f"{bus.prg_banks[2]}, not the bank {was_mapped} it found there")
        end = info["ends"][n]
        if end <= dest_start:
            raise Fault(f"program {n}: the end address ${end:04X} is not above the "
                        f"start of the area ${dest_start:04X}")
        length = end - dest_start + 1
        # The last program starts close enough to the end of the ROM that the stock copy
        # runs past $FFFF and reads RAM instead. Those bytes are **not reproducible** -
        # they are whatever BASIC last left in page zero - so they are excluded from the
        # comparison and counted instead of being quietly matched.
        wrapped = max(0, info["sources"][n] + length - 1 - 0xFFFF)
        cmp_len = length - wrapped
        off = dest_start - 0x6000
        a = stock.wram[off:off + cmp_len]
        b = bus.wram[off:off + cmp_len]
        if a != b:
            first = next(i for i in range(cmp_len) if a[i] != b[i])
            raise Fault(f"[{decode}] program {n}: the rebuilt loader differs from the stock "
                        f"one at ${dest_start + first:04X} (${a[first]:02X} vs ${b[first]:02X})")
        out.append((n, cmp_len, wrapped))
    return out


# --------------------------------------------------------------------------- V2.1A
#
# The same mapper swap, on the other ROM this repository builds from. Nothing here is a
# variation of the V3 path above: V2.1A has **no built-in BASIC programs**, so there is no
# `GAME` loader to rebuild and no program data to move into banks. What it
# has instead is a boot demo, and that is where the room for the init code comes from.
#
# ## Where the 94 bytes go
#
# `$E000-$FFFF` is the only window whose contents are known at power-on, and V2.1A fills
# all 8,192 bytes of it: **inside that window** the longest run of one repeated byte is 8,
# and the single gap is one alignment `$FF` at `$E6E8`. (The whole 32KB does have an
# 18-byte run, at `$C56D`, inside a data table and nowhere near this window - the two
# numbers are different measurements, which read as a contradiction once in review.)
#
# But `$F000-$FFF9` is reached **only from the boot demo** (the "COMPUTER / OPERATOR"
# conversation and fortune-telling program). Two facts make that actionable:
#
#   * the demo runs under the NMI handler `$CDA7`, and the **only** code that installs it
#     is `$C442`/`$C446`. Neither the 16-bit value `$CDA7` nor `$CDA6` (what an `RTS`
#     trick would push) appears anywhere in the PRG, and neither does `LDA #$CD` / `PHA`
#   * BASIC installs its own handler `$880F` at `$80AD`, so **the menu and everything
#     after it never used `$CDA7` anyway**
#
# So pointing those two immediates at `$880F` and skipping the demo's wait loop makes
# `$F000-$FFF9` unreachable, and the init goes there. Measured on a mapper-0 build with
# only those 8 bytes changed: the machine boots straight to `GAME BASIC` (`1--BASIC` /
# `2--BG GRAPHIC` / `3--END`) and `1` reaches `NS-HUBASIC V2.1A` / `1982 BYTES FREE`.
#
# **What is lost is the boot demo.** The title menu, BG GRAPHIC, the keyboard, `PLAY` and
# the cassette routines are all reached after `$80AD` and none of them goes through
# `$CDA7`. "And nothing else" is not claimed: what is claimed is bounded to the paths the
# tests traverse, each with its own note of what the check does not reach.
#
# ⚠️ This is not "the region is dead". The disassembler cannot follow indirect `JMP`s or
# `RTS` tricks, and `CALL`/`POKE` can send the CPU anywhere. The claim is bounded to the
# product paths the tests traverse.

V21A_RESET = 0xC400
V21A_INIT_ORG = 0xF000
V21A_BANKS = (0, 1, 2)                 # $8000 / $A000 / $C000; bank 3 is the fixed window
# ★**The check does not get handed the value under test.** `V21A_BANKS` is what
# `build_init` is told to emit; this is what the model's registers must read back as. They
# are the same numbers on purpose, but they are *separate constants*, so changing one and
# not the other fails - which is what makes the check able to see a wrong bank number at
# all. Passing `V21A_BANKS` to both sides made the "revert to 4/5/6" mutation pass
# (measured). Same rule as `docs/reference/mmc5-wram-banks.ja.md`.
V21A_EXPECT_BANKS = [0, 1, 2]
V21A_TOTAL_BANKS = 4                   # 32KB, unchanged from the cartridge

# The stock dump, pinned whole. Structure checks pass on a dump whose data was altered
# where they do not look, and every later check compares the output against *that same
# input* - so the damage would be reproduced rather than caught. Same reasoning as
# `STOCK_SHA256` above and `fb-relocate.py:76`.
V21A_SHA256 = "a646dcaeb5f114176446d7106816623c5f5918739a4c16d651c5715c9825b6e9"
V21A_BANNER = b"NS-HUBASIC V2.1A"      # $C358, once in the PRG - what BASIC prints on entry
V21A_HEADER = bytes.fromhex("4e45531a020103080000" + "50" + "0000000023")
V21A_NVRAM = {"stock": 0x50, "expanded": 0x60, "8k": 0x70}   # 2KB / 4KB / 8KB

# `fb-expand-basic-area.py` on a V1/V2 dump changes exactly these two PRG bytes and
# header byte 10 (measured). **Both land on the value the 8KB move wants**, because
# V1/V2's area starts at `$7000`, so its 4KB ceiling *is* `$7FFF`. That is why the
# `patched` set is smaller when the input is already expanded.
V21A_AREA_SITES = {0x8570: (0x77, 0x7F), 0x925A: (0x78, 0x80)}

# --- stage 1: stop the boot demo -------------------------------------------------------
# `$C442 LDA #$A7 / STA $EE` and `$C446 LDA #$CD / STA $EF` write `$00ED = JMP $CDA7`.
# Only the two immediates move; the stores stay where they are, so the instruction
# boundaries do not shift. ⚠️ Changing one and not the other gives `$88A7`, not `$CDA7`.
V21A_NMI_SITES = {0xC443: (0xA7, 0x0F), 0xC447: (0xCD, 0x88)}

# `$C44A LDA $60 / CMP #$01 / BNE $C45A` waits for the demo to set zero page `$60`.
# Nothing sets it once `$CDA7` is gone, so the loop would never end. Jumping straight to
# `$C450` takes the exit the ROM itself takes when the demo is finished: NMI off, then
# `JMP $8000` into `$80AD`, which draws the menu.
# ⚠️ **Do not "remove" this by writing NOPs** - six NOPs fall through to `$C450` as well,
# so that mutation is indistinguishable from the correct build.
V21A_LOOP_SITE = 0xC44A
V21A_LOOP_STOCK = bytes.fromhex("a560c901d00a")
V21A_LOOP_NEW = bytes.fromhex("4c50c4eaeaea")           # JMP $C450 + NOP NOP NOP

# ★**What the finished ROM must read back as, written out a second time.** The tables
# above are what the patcher is *told* to write; these are what the output is *checked*
# against, and they are separate constants on purpose - `V21A_NMI_SITES` and
# `V21A_LOOP_NEW` cannot be both the instruction and the expectation. Measured: with the
# check reading the same table, changing the loop's target from `$C450` to `$C45A` (into
# the demo's own loop, which nothing sets `$60` for any more) built a clean ROM that hangs
# at power-on. Same rule as `V21A_EXPECT_BANKS`.
V21A_EXPECT_CODE = {
    0xC442: bytes.fromhex("a90f85ee"),      # LDA #$0F / STA $EE   -> $00ED low  = $0F
    0xC446: bytes.fromhex("a98885ef"),      # LDA #$88 / STA $EF   -> $00ED high = $88
    # All six bytes, not just the `JMP`: the three `NOP`s are unreachable padding, but an
    # expectation that stops short of the bytes it replaced is an expectation with a gap,
    # and the gap is invisible from the correct build.
    0xC44A: bytes.fromhex("4c50c4eaeaea"),  # JMP $C450, the exit the demo itself takes
}

# `$F000-$FFF9` is what killing the boot demo frees, and the only place the init may go:
# everything below `$F000` is live on a cartridge (the reachability measurement is in
# the reachability was measured before this was relied on). Kept apart from
# `V21A_INIT_ORG` so that moving the origin is not also permission to move it - measured:
# with the placement derived from the origin alone, `V21A_INIT_ORG = 0xE000` wrote 94
# bytes over live code and built cleanly.
V21A_DEAD_START, V21A_DEAD_END = 0xF000, 0xFFFA         # end exclusive: the vectors follow
# The exclusion window `check_map` is handed comes from this, not from `len(init.code)`.
# A window measured on the thing it exempts grows with it: an init that emitted more than
# it should would widen its own exemption. 94 bytes, measured, and the build stops if the
# assembler produces anything else.
V21A_INIT_LEN = 94
V21A_INIT_SHA = "6727f5c16b051efe1b0da83ca6fdfd7c74b96b4bd60407d02e44aacefeb15aab"
V21A_EXPECT_VECTOR = bytes.fromhex("00f0")              # $F000, little-endian

# What `fb-fds.py` is expected to hold. The values are **read from that module**, never
# copied into this one; this table is the ratchet that stops the build if
# what came back is not what this build was written against. It is not a second source of
# truth - the patching uses the imported values - and its failure mode is a loud stop,
# which is the opposite of the silent pass that lesson is about.
V21A_FDS_FACTS = {
    # ★**The addresses, not just how many.** Recording the count alone let a site move to
    # any other byte that happens to hold `$70`: the ratchet passed, `patched` and the set
    # it is compared against both followed the moved address, and the build came out clean
    # with the real site untouched (measured: `$80DB` moved to `$8064`).
    "page_sites": [(0x80DB, "8d3a"), (0x80E0, "8d3b"), (0x80E7, "8d3c"), (0x80EC, "8d3d"),
                   (0x80FD, "ad3a"), (0x8104, "ad3b"), (0x810B, "ad3c"), (0x8110, "ad3d"),
                   (0x8118, "05a9"), (0x8210, "8d3a"), (0x8213, "8d3b"), (0x838D, "8d3e"),
                   (0x8390, "8d3f"), (0x8393, "8d40"), (0x83A5, "21a9")],
    "page_old": 0x70,
    "page_new": 0x60,
    "area_sites": {0x8570: (0x77, 0x7F), 0x925A: (0x78, 0x80)},
}
# Labels taken from `fb-fds.py`'s `PATCHES`. ⚠️ That list also holds `drop cartridge init`
# (`$C427`), which is a **disk-only** patch: `$CD94` is the APU/PPU init (`$4011` <- 0,
# `$2001` <- 6) plus three calls above `$E000`, and NOPping it on a cartridge would skip
# them. Selecting by label is what keeps it out.
V21A_FDS_LABELS = ("end of area", "CLEAR ceiling")


def fds_module():
    """The disk builder, read for its V2.1A patch sites (see `sibling` for why not import)."""
    return sibling("fb-fds.py")


def v21a_page_sites():
    """The 15 system-variable bytes, read out of `fb-fds.py` and checked against the ratchet.

    Each entry there is `(address, the two bytes in front)`: the two bytes **confirm** the
    site and the write is one byte at the address itself. Treating them as two bytes to
    change is wrong and would put unchanged bytes into `patched`, where the build would
    then reject its own correct output.
    """
    fds = fds_module()
    sites = [addr for addr, _ in fds.PROGRAM_PAGE_SITES]
    pinned = [(addr, before.hex()) for addr, before in fds.PROGRAM_PAGE_SITES]
    area = {}
    for label, addr, old, new in fds.PATCHES:
        if label not in V21A_FDS_LABELS:
            continue
        diff = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
        if len(diff) != 1:
            sys.exit(f"fb-fds.py's `{label}` changes {len(diff)} bytes; expected exactly 1")
        area[addr + diff[0]] = (old[diff[0]], new[diff[0]])
    got = {"page_sites": pinned, "page_old": fds.PROGRAM_PAGE_OLD,
           "page_new": fds.PROGRAM_PAGE_NEW, "area_sites": area}
    if got != V21A_FDS_FACTS:
        sys.exit("the V2.1A patch sites read out of fb-fds.py are not what this build was "
                 "written against:\n"
                 f"       read from fb-fds.py: {got}\n"
                 f"       recorded here:       {V21A_FDS_FACTS}\n"
                 "       Check whether the disk build's change applies to the VRC7 build "
                 "too; if it does, update the recorded values.")
    return sites, area


def is_v21a(prg):
    """Does this dump claim to be V2.1A? **Routing only - this authenticates nothing.**

    ★Routing must not be the same test as authentication. While it was (the whole-image
    sha256), a V2.1A dump with one byte damaged failed the hash, fell through to the V3
    path, and was rejected with "Only V3.0 is supported" - a message about the wrong
    version, for a ROM whose version this tool supports (measured). The banner is what the
    machine itself prints, it appears once, and it is inside the region `check_v21a_input`
    then authenticates whole, so routing on it cannot let a damaged dump through.
    """
    return V21A_BANNER in prg


def check_v21a_input(header, prg, chr_data):
    """Pin the input whole - PRG, CHR **and the 16-byte header**.

    Hashing PRG+CHR alone leaves every behaviour-affecting header field open; on the V3
    path a flipped nametable arrangement got through that way and `check_map` accepted it,
    because that check reads the arrangement out of the very header that was changed.
    """
    image = bytearray(prg) + chr_data
    seen, odd = set(), []
    for addr, (stock, expanded) in V21A_AREA_SITES.items():
        got = image[addr - 0x8000]
        if got == expanded:
            image[addr - 0x8000] = stock
            seen.add("expanded")
        elif got == stock:
            seen.add("stock")
        else:
            odd.append(f"${addr:04X}=${got:02X}")
    if len(seen) > 1:
        odd.append("the two program-area constants are a mixture of stock and expanded, "
                   "which no build produces")
    digest = hashlib.sha256(bytes(image)).hexdigest()
    if odd or digest != V21A_SHA256:
        why = f"({', '.join(odd)})" if odd else f"sha256 {digest[:16]}... != {V21A_SHA256[:16]}..."
        sys.exit(f"this is not a stock V2.1A dump, nor the 4KB build made from one: {why}")
    state = seen.pop()
    want = bytearray(V21A_HEADER)
    want[10] = V21A_NVRAM[state]
    if bytes(header) != bytes(want):
        bad = [f"byte{i}=${header[i]:02X} (expected ${want[i]:02X})"
               for i in range(16) if header[i] != want[i]]
        sys.exit(f"the header is not stock V2.1A's, with a {state} program area: "
                 + ", ".join(bad))
    return state


def check_no_cda7(built):
    """Refuse to write a ROM in which the demo's NMI handler could be installed again.

    Four shapes, because three of them would not show up as the address itself: an `RTS`
    trick pushes `target - 1`, and a handler can be pushed byte by byte.
    """
    for name, value in (("$CDA7", 0xCDA7), ("$CDA6 (an RTS trick pushes target-1)", 0xCDA6)):
        for i in range(len(built) - 1):
            if built[i] | built[i + 1] << 8 == value:
                sys.exit(f"the 16-bit value {name} appears at offset ${i:05X} of the output; "
                         f"the boot demo could be reached again")
    for pat, what in ((b"\xA9\xA7\x85\xEE", "LDA #$A7 / STA $EE"),
                      (b"\xA9\xCD\x85\xEF", "LDA #$CD / STA $EF"),
                      (b"\xA9\xCD\x48", "LDA #$CD / PHA")):
        if pat in built:
            sys.exit(f"`{what}` appears in the output; the boot demo's handler could be "
                     f"installed again")


# ★**The expected header, written out a second time as literals.** Deriving these from
# `MAPPER` / `SUBMAPPER` / `V21A_TOTAL_BANKS` / `V21A_NVRAM` - the constants that *write*
# the header - is what made the "submapper 2 -> 1" and "NVRAM declared 4KB" mutations both
# build cleanly (measured): the expectation moved with the value under test. Submapper 1 is
# VRC7b, the board **without** the sound chip, so that mutation silently produces the one
# ROM this tool has no reason to exist for. Same rule as `V21A_EXPECT_BANKS` above and
# `docs/reference/mmc5-wram-banks.ja.md`.
EXPECT_MAPPER_LOW, EXPECT_MAPPER_HIGH = 0x50, 0x50    # mapper 85 = $55, split over 6 and 7
EXPECT_NES20 = 0x08
EXPECT_SUBMAPPER_BYTE = 0x20                          # submapper 2 (VRC7a) in the high nibble
V21A_EXPECT_NVRAM = {"stock": 0x50, "expanded": 0x60, "8k": 0x70}
V21A_EXPECT_PRG_UNITS = 2                             # 32KB in 16KB units
V3_EXPECT_PRG_UNITS = 4                               # 64KB
V3_EXPECT_VECTOR = bytes.fromhex("00e0")              # $E000, little-endian


def check_header(out_header, in_header, prg_units, nvram, body_len):
    """Read the finished header back and compare each field with an independent expectation.

    The map check never looks at the header - it is handed the decoding as an argument and
    the header is written afterwards - so a wrong submapper or NVRAM declaration passes
    everything else. The one that matters most cannot be checked at all by machine: a ROM
    declared as VRC7b (submapper 1) has no sound.

    `nvram` is `None` when the build leaves byte 10 alone, which is what the V3 path and the
    default V2.1A build do; then the field is required to be unchanged rather than a value.
    """
    want = {4: prg_units,
            6: (in_header[6] & 0x0F) | EXPECT_MAPPER_LOW,
            7: (in_header[7] & 0x03) | EXPECT_MAPPER_HIGH | EXPECT_NES20,
            8: EXPECT_SUBMAPPER_BYTE, 9: 0x00,
            10: in_header[10] if nvram is None else nvram,
            15: in_header[15]}
    bad = [f"byte{i}=${out_header[i]:02X} (expected ${v:02X})"
           for i, v in want.items() if out_header[i] != v]
    # Independent of byte 4: what the file will actually carry.
    if body_len != prg_units * 16384:
        bad.append(f"the PRG body is {body_len} bytes, not the {prg_units * 16384} "
                   f"byte 4 declares")
    if (out_header[6] & 1) != (in_header[6] & 1):
        bad.append("the nametable arrangement was not carried over from the input")
    if bad:
        sys.exit("the output header is not what this build declares: " + ", ".join(bad))


def build_v21a(args, header, prg, chr_data):
    """The V2.1A path: stop the boot demo, optionally widen the area, put the init at $F000."""
    state = check_v21a_input(header, prg, chr_data)
    page_sites, area_sites = v21a_page_sites()
    fds = fds_module()

    out = bytearray(prg)
    patched = {}                    # address -> (stock byte, new byte); must all differ

    def put(addr, old, new):
        if out[addr - 0x8000] != old:
            sys.exit(f"${addr:04X}: expected ${old:02X}, found ${out[addr - 0x8000]:02X}")
        if old == new:
            sys.exit(f"${addr:04X}: the patch would not change anything (${old:02X})")
        out[addr - 0x8000] = new
        patched[addr] = (old, new)

    # --- stage 1 ------------------------------------------------------------
    for addr, (old, new) in V21A_NMI_SITES.items():
        put(addr, old, new)
    for i, (old, new) in enumerate(zip(V21A_LOOP_STOCK, V21A_LOOP_NEW)):
        put(V21A_LOOP_SITE + i, old, new)

    # --- stage 2 ------------------------------------------------------------
    if args.eight_k:
        for addr in page_sites:
            put(addr, fds.PROGRAM_PAGE_OLD, fds.PROGRAM_PAGE_NEW)
        for addr, (old, new) in sorted(area_sites.items()):
            # A 4KB input already holds the value the 8KB move wants; patching it again
            # would be a no-op, and a no-op in `patched` breaks the two-sided check.
            if out[addr - 0x8000] != new:
                put(addr, old, new)

    # --- stage 3 ------------------------------------------------------------
    mirror_bits = 0 if (header[6] & 1) else 1
    # Where the init hands over, taken from the input's own RESET vector rather than from
    # the constant that also assembles the `JMP`. The input is authenticated by now, so
    # this is an independent source for the same fact.
    stock_reset = prg[0xFFFC - 0x8000] | prg[0xFFFD - 0x8000] << 8
    if stock_reset != V21A_RESET:
        sys.exit(f"the input's RESET vector is ${stock_reset:04X}, and this build hands "
                 f"over to ${V21A_RESET:04X}")
    init = build_init(mirror_bits, V21A_RESET, org=V21A_INIT_ORG, banks=V21A_BANKS)
    if len(init.code) != V21A_INIT_LEN or \
            hashlib.sha256(init.code).hexdigest() != V21A_INIT_SHA:
        sys.exit(f"the init assembled to {len(init.code)} bytes / "
                 f"{hashlib.sha256(init.code).hexdigest()[:16]}...; this build is written "
                 f"against {V21A_INIT_LEN} / {V21A_INIT_SHA[:16]}...")
    if not V21A_DEAD_START <= V21A_INIT_ORG or \
            V21A_INIT_ORG + len(init.code) > V21A_DEAD_END:
        sys.exit(f"the init (${V21A_INIT_ORG:04X}+{len(init.code)}) is not inside "
                 f"${V21A_DEAD_START:04X}-${V21A_DEAD_END - 1:04X}, the region killing the "
                 f"boot demo frees; everything below it is live on a cartridge")
    out[V21A_INIT_ORG - 0x8000:V21A_INIT_ORG - 0x8000 + len(init.code)] = init.code
    for vec in (0xFFFC, 0xFFFE):
        out[vec - 0x8000:vec - 0x8000 + 2] = bytes([V21A_INIT_ORG & 0xFF, V21A_INIT_ORG >> 8])

    # ★**What the finished ROM must hold, byte by byte, written down rather than derived
    # from the patching.** Reading each site back against the `new` the patcher recorded
    # there is a check with no opinion of its own: it agrees with whatever was written.
    # Measured - passing `PROGRAM_PAGE_NEW + 1` at the call site put `$61` into all fifteen
    # system variables and built a clean ROM. So the expectation comes from the literals in
    # `V21A_EXPECT_CODE` and `V21A_FDS_FACTS`, the patching comes from what was read out of
    # `fb-fds.py`, and the ratchet is what holds those two together. It carries the
    # unchanged context bytes of each patched instruction too, so a patch that lands on the
    # wrong half of an instruction is visible.
    want_bytes = {}
    for addr, run in V21A_EXPECT_CODE.items():
        want_bytes.update({addr + i: b for i, b in enumerate(run)})
    if args.eight_k:
        for addr, _sig in V21A_FDS_FACTS["page_sites"]:
            want_bytes[addr] = V21A_FDS_FACTS["page_new"]
        for addr, (_old, new_b) in V21A_FDS_FACTS["area_sites"].items():
            want_bytes[addr] = new_b

    # ★**Which addresses should have been patched - derived from the expectation, not from
    # the tables that did the patching.** Two failures, one on each side:
    #
    #   * a site the loop skips is simply absent from `patched`, reads the same as the
    #     input, and every other check passes (measured: skipping one of the 15 page sites
    #     built a clean ROM)
    #   * a site *added* to the patch tables grows `patched` and `changed` together, so the
    #     map check exempts it and a byte anywhere in live PRG can be rewritten (measured:
    #     an extra entry for `$9000` built a clean ROM)
    #
    # Deriving the set from `want_bytes` closes both, because that table is written down
    # rather than assembled from the patcher's own inputs: what must change is exactly what
    # the expectation says the output holds and the authenticated input does not. It is
    # input-dependent for free - a 4KB input already holds the 8KB area values.
    want = {a for a, b in want_bytes.items() if prg[a - 0x8000] != b}
    if set(patched) != want:
        missing = sorted(want - set(patched))
        extra = sorted(set(patched) - want)
        sys.exit("the set of patched bytes is not what this input and these flags call for:\n"
                 + (f"       never patched: {[f'${a:04X}' for a in missing]}\n" if missing else "")
                 + (f"       unexpected:    {[f'${a:04X}' for a in extra]}\n" if extra else ""))

    # Both halves from the written-down side: the window from the pinned length, the patch
    # set from the expectation `patched` was just compared against. The gates above make
    # these equal to `len(init.code)` and `set(patched)` today - which is the point, and
    # also why taking them from there would be free to be wrong the day a gate moves.
    replaced = set(range(V21A_INIT_ORG, V21A_INIT_ORG + V21A_INIT_LEN)) | set(range(0xFFFC, 0x10000))
    changed = want | replaced
    built = bytes(out)

    # --- checks -------------------------------------------------------------
    print(f"read from {args.rom}")
    print(f"  V2.1A, {state} program area"
          + (f" -> 8KB ({len(page_sites)} page sites + {len(area_sites)} constants)"
             if args.eight_k else " (unchanged)"))

    wrong = [f"${a:04X}=${built[a - 0x8000]:02X} (expected ${b:02X})"
             for a, b in sorted(want_bytes.items()) if built[a - 0x8000] != b]
    if wrong:
        sys.exit("the output does not hold the bytes this build is written against: "
                 + ", ".join(wrong))
    # And the other side: a site that was never patched reads the same as the input, so it
    # is not "unexpected" to the map check - it is simply missing.
    for addr, (old, new) in patched.items():
        if new == old:
            sys.exit(f"${addr:04X}: the patch would not have changed anything")
    print(f"  {len(patched)} patched bytes, and the {len(want_bytes)} bytes around them "
          f"this build is written against, read back as expected")

    for decode in ("a4", "a3"):
        bus = check_map(built, prg, V21A_RESET, decode, changed, bool(header[6] & 1),
                        expect_banks=V21A_EXPECT_BANKS)
        print(f"  [{decode}] power-on -> ${V21A_RESET:04X}: $8000-$FFFF matches the input "
              f"outside the {len(changed)} changed bytes, WRAM writable, CHR 0-7, "
              f"PRG banks {V21A_EXPECT_BANKS}, nametables "
              + ("vertical" if bus.mirroring == 0 else f"arrangement {bus.mirroring}"))

    want_vec = V21A_EXPECT_VECTOR
    for name, vec in (("RESET", 0xFFFC), ("IRQ", 0xFFFE)):
        got = built[vec - 0x8000:vec - 0x8000 + 2]
        if got != want_vec:
            sys.exit(f"the {name} vector is {got.hex(' ')}, expected {want_vec.hex(' ')}")
    print(f"  RESET and IRQ both enter the init at ${V21A_INIT_ORG:04X}")
    check_no_cda7(built)
    print("  $CDA7 cannot be installed again (4 shapes checked)")

    # --- header -------------------------------------------------------------
    out_header = bytearray(header)
    out_header[4] = V21A_TOTAL_BANKS * BANK // 16384
    out_header[6] = (out_header[6] & 0x0F) | ((MAPPER & 0x0F) << 4)
    out_header[7] = (out_header[7] & 0x0F) | (MAPPER & 0xF0)
    out_header[7] = (out_header[7] & 0xF3) | 0x08
    out_header[8] = SUBMAPPER << 4
    out_header[9] = 0x00
    if args.eight_k:
        out_header[10] = V21A_NVRAM["8k"]
    check_header(out_header, header, V21A_EXPECT_PRG_UNITS,
                 V21A_EXPECT_NVRAM["8k"] if args.eight_k else None, len(built))

    nvram = 64 << (out_header[10] >> 4)
    print("built the VRC7 version of V2.1A")
    print(f"  free area {nvram // 1024}KB"
          f" / FM ports ${SOUND_ADDR:04X} and ${SOUND_DATA:04X}"
          f" = POKE &H{SOUND_ADDR:04X} / POKE &H{SOUND_DATA:04X}")
    print(f"  init ${V21A_INIT_ORG:04X} ({len(init.code)} bytes), in what the boot demo used")
    print(f"  mapper {MAPPER} submapper {SUBMAPPER} (VRC7a)"
          f" / PRG {len(built) // 1024}KB / CHR {len(chr_data) // 1024}KB")
    print("  ⚠️ the built-in boot demo (COMPUTER / OPERATOR) is gone; the title menu stays")

    if args.listing:
        print()
        for addr, raw, mnem, note in init.listing:
            print(f"  ${addr:04X}  {raw.hex(' '):8s}  {mnem:16s} {note}")

    if args.out:
        data = bytes(out_header) + built + chr_data
        open(args.out, "wb").write(data)
        print(f"\nwrote: {args.out} ({len(data)} bytes / MD5 {hashlib.md5(data).hexdigest()})")


# --------------------------------------------------------------------------- build


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", help="stock V3 or V2.1A, or the output of fb-expand-basic-area.py")
    ap.add_argument("-o", "--out")
    ap.add_argument("--listing", action="store_true", help="print the assembled code")
    ap.add_argument("--8k", dest="eight_k", action="store_true",
                    help="V2.1A only: widen the program area to 8KB ($6000-$7FFF). "
                         "⚠️ the .sav layout moves with it and fb-basic-to-sav.py cannot "
                         "write that layout yet")
    args = ap.parse_args()

    header, prg, chr_data = load(args.rom)
    # V2.1A first: it has no `GAME` loader, so `read_loader` below would reject it with a
    # message about V3 that does not describe what is wrong.
    if is_v21a(prg):
        return build_v21a(args, header, prg, chr_data)
    info = read_loader(prg)   # names the version in its message, so it goes first
    # After `read_loader`, so that a dump which is neither V3 nor V2.1A gets the message
    # about *that* rather than one about a flag. Measured: breaking the V2.1A digest made
    # this guard fire first and report "--8k is only for V2.1A", which says nothing about
    # the real problem. Which gate stops a bad input is part of what the gate is for.
    if args.eight_k:
        sys.exit("--8k is only for V2.1A; V3's area is widened by fb-expand-basic-area.py")
    check_stock(header, prg, chr_data)
    # Only meaningful after check_stock: it is what makes the input a known quantity.
    wrong = {k: (v, info[k]) for k, v in STOCK_LOADER_FACTS.items() if info[k] != v}
    if wrong:
        show = lambda v: (f"${v:04X}" if isinstance(v, int)
                          else "[" + ", ".join(f"${x:04X}" for x in v) + "]")
        sys.exit("the loader was read out of a pinned image but did not come out as that "
                 "image's known values - `read_loader` has regressed:\n"
                 + "\n".join(f"       {k}: expected {show(e)}, got {show(g)}"
                              for k, (e, g) in sorted(wrong.items())))
    reset_addr = prg[0xFFFC - 0x8000] | prg[0xFFFD - 0x8000] << 8
    info["reset"] = reset_addr
    if not 0x8000 <= reset_addr < 0xE000:
        sys.exit(f"the reset vector points at ${reset_addr:04X}; expected the interpreter")
    # iNES bit 0: 1 = vertical. VRC7's $E000: 0 = vertical, 1 = horizontal.
    mirror_bits = 0 if (header[6] & 1) else 1

    init = build_init(mirror_bits, reset_addr)
    loader = build_loader(info)
    if INIT_ORG + len(init.code) > LOADER_ORG:
        sys.exit(f"the init code ({len(init.code)} bytes) runs into the loader at ${LOADER_ORG:04X}")

    # --- banks --------------------------------------------------------------
    # Each program gets the upper half of its own bank; the lower half duplicates
    # $C000-$CFFF so that swapping it in only takes $D000-$DFFF away.
    c_low = prg[0xC000 - 0x8000:0xD000 - 0x8000]
    banks = []
    for src in info["sources"]:
        off = src - 0x8000
        payload = bytearray(prg[off:off + 0x1000])
        # The last program starts close enough to the end of the ROM that the stock copy
        # loop reads past $FFFF and wraps into RAM. Those bytes are past the end address
        # the ROM records, so they are never part of the program; they are filled here
        # rather than reproduced (RAM at that moment is not reproducible anyway).
        payload += b"\xFF" * (0x1000 - len(payload))
        banks.append(bytes(c_low) + bytes(payload))
    while len(banks) < BANK_8000:
        banks.append(b"\xFF" * BANK)                  # unused, if a ROM has fewer programs
    banks.append(prg[0x0000:0x2000])                  # bank 4: $8000-$9FFF
    resident_a = bytearray(prg[0x2000:0x4000])        # bank 5: $A000-$BFFF, patched below
    banks.append(resident_a)
    banks.append(prg[0x4000:0x6000])                  # bank 6: $C000-$DFFF
    bank7 = bytearray(prg[0x6000:0x8000])             # bank 7: $E000-$FFFF, fixed
    banks.append(bank7)                               # patched in place below

    # The stock loader is replaced by a jump to the rebuilt one. Everything from here to
    # its `JMP $97A6` is bypassed, which is why the rebuilt one has to end there too.
    off = LOADER_SITE - 0xA000
    resident_a[off:off + 4] = bytes([0x4C, LOADER_ORG & 0xFF, LOADER_ORG >> 8, 0xEA])

    def put7(addr, data):
        if addr < 0xE000 or addr + len(data) > 0xFFFA:
            sys.exit(f"${addr:04X}+{len(data)} does not fit in the fixed bank")
        bank7[addr - 0xE000:addr - 0xE000 + len(data)] = data

    # The two blocks are 128 bytes apart and the init is 94, so there are 34 bytes of
    # margin and nothing was saying so. `put7` only checks the bank's edges: an init that
    # grew past `LOADER_ORG` would be overwritten by the loader written next, silently
    # here and caught only later by `check_loader` executing the wreckage.
    if len(init.code) != INIT_LEN or hashlib.sha256(init.code).hexdigest() != INIT_SHA:
        sys.exit(f"the init assembled to {len(init.code)} bytes / "
                 f"{hashlib.sha256(init.code).hexdigest()[:16]}...; this build is written "
                 f"against {INIT_LEN} / {INIT_SHA[:16]}...")
    if len(loader.code) != LOADER_LEN:
        sys.exit(f"the rebuilt loader assembled to {len(loader.code)} bytes; this build is "
                 f"written against {LOADER_LEN}")
    if INIT_ORG + len(init.code) > LOADER_ORG:
        sys.exit(f"the init (${INIT_ORG:04X}+{len(init.code)}) runs into the rebuilt "
                 f"loader at ${LOADER_ORG:04X}")
    put7(INIT_ORG, init.code)
    put7(LOADER_ORG, loader.code)
    for vec in (0xFFFC, 0xFFFE):        # reset and IRQ both went to $80BA; both go to init
        bank7[vec - 0xE000:vec - 0xE000 + 2] = bytes([INIT_ORG & 0xFF, INIT_ORG >> 8])
    # Both read back against a literal. The model starts from `$FFFC`, so a missing IRQ
    # vector is executed by nothing here and exempted from the map check by `changed` -
    # measured: not writing it at all built a clean ROM. The V2.1A path has had this check
    # since it was written; this path had not.
    for name, vec in (("RESET", 0xFFFC), ("IRQ", 0xFFFE)):
        got = bytes(bank7[vec - 0xE000:vec - 0xE000 + 2])
        if got != V3_EXPECT_VECTOR:
            sys.exit(f"the {name} vector is {got.hex(' ')}, expected "
                     f"{V3_EXPECT_VECTOR.hex(' ')}")
    assert len(banks) == TOTAL_BANKS and all(len(b) == BANK for b in banks)
    built = b"".join(bytes(b) for b in banks)

    # Every address this build changed on purpose. The map check treats a difference
    # anywhere else as a wrong bank.
    changed = set(range(INIT_ORG, INIT_ORG + INIT_LEN))
    changed |= set(range(LOADER_ORG, LOADER_ORG + LOADER_LEN))
    changed |= set(range(LOADER_SITE, LOADER_SITE + 4))     # the jump to the rebuilt loader
    changed |= {0xFFFC, 0xFFFD, 0xFFFE, 0xFFFF}             # reset and IRQ point at init

    # --- checks -------------------------------------------------------------
    print(f"read from {args.rom}")
    print(f"  {info['count']} built-in programs at "
          + " ".join(f"${s:04X}" for s in info["sources"])
          + f", end addresses from ${info['end_table']:04X}")
    for decode in ("a4", "a3"):
        bus = check_map(built, prg, reset_addr, decode, changed, bool(header[6] & 1))
        print(f"  [{decode}] power-on -> ${reset_addr:04X}: $8000-$FFFF matches the input,"
              f" WRAM writable, CHR 0-7, nametables "
              + ("vertical" if bus.mirroring == 0 else f"arrangement {bus.mirroring}"))
    # The program area starts where WRAM does. The loader copies until the destination
    # high byte reaches the ROM's own limit, so every end address has to be in between;
    # if a version ever started the area higher, this stops the build.
    dest_start = 0x6000
    if not all(dest_start < e < info["limit"] << 8 for e in info["ends"]):
        sys.exit(f"the end addresses {[f'${e:04X}' for e in info['ends']]} are not inside "
                 f"${dest_start:04X}-${(info['limit'] << 8) - 1:04X}")
    for decode in ("a4", "a3"):
        for n, length, wrapped in check_loader(built, prg, info, dest_start, decode):
            note = "" if not wrapped else \
                (f" (+{wrapped} the stock loader read past the end of the ROM, "
                 f"out of RAM; $FF here)")
            print(f"  [{decode}] GAME {n}: {length} bytes identical to the stock loader{note}")

    # --- header -------------------------------------------------------------
    out_header = bytearray(header)
    out_header[4] = TOTAL_BANKS * BANK // 16384        # PRG 64KB
    out_header[6] = (out_header[6] & 0x0F) | ((MAPPER & 0x0F) << 4)
    out_header[7] = (out_header[7] & 0x0F) | (MAPPER & 0xF0)
    out_header[7] = (out_header[7] & 0xF3) | 0x08      # NES 2.0, so the submapper is read
    out_header[8] = SUBMAPPER << 4                     # VRC7a: the board with the sound chip
    out_header[9] = 0x00
    # The same read-back the V2.1A path does. This path had no header check at all, which
    # is the same hole one step further along: nothing here would have noticed a submapper
    # of 1 (VRC7b, no sound chip) either. Byte 10 is carried over untouched, so it is
    # checked as unchanged rather than against a value.
    check_header(out_header, header, V3_EXPECT_PRG_UNITS, None, len(built))
    out = bytes(out_header) + built + chr_data

    nvram = 0 if (out_header[10] >> 4) == 0 else 64 << (out_header[10] >> 4)
    print("built the VRC7 version")
    print(f"  free area {nvram // 1024}KB (unchanged from the input)"
          f" / FM ports ${SOUND_ADDR:04X} and ${SOUND_DATA:04X}"
          f" = POKE &H{SOUND_ADDR:04X} / POKE &H{SOUND_DATA:04X}")
    print(f"  init ${INIT_ORG:04X} ({len(init.code)} bytes)"
          f" / loader ${LOADER_ORG:04X} ({len(loader.code)} bytes)")
    print(f"  mapper {MAPPER} submapper {SUBMAPPER} (VRC7a)"
          f" / PRG {len(built) // 1024}KB / CHR {len(chr_data) // 1024}KB")

    if args.listing:
        for a in (init, loader):
            print()
            for addr, raw, mnem, note in a.listing:
                print(f"  ${addr:04X}  {raw.hex(' '):8s}  {mnem:16s} {note}")

    if args.out:
        open(args.out, "wb").write(out)
        print(f"\nwrote: {args.out} ({len(out)} bytes / MD5 {hashlib.md5(out).hexdigest()})")


if __name__ == "__main__":
    main()
