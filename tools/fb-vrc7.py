#!/usr/bin/env python3
"""Build the VRC7 (mapper 85) version: the same BASIC, with FM sound reachable from `POKE`.

  $ ./fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"
  $ ./fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"

## What this buys

A VRC7 cartridge carries a 6-channel FM sound chip whose two ports sit in the address
space BASIC can already reach: `POKE &H9010,<register>` then `POKE &H9030,<value>`.
Nothing else about BASIC changes. (Hex, because V3's integer literals are signed 16-bit
and `36880` raises `?OV` - see `docs/reference/build-differences.md`.)

**It does not widen the free area.** VRC7's RAM window is `$6000-$7FFF`, the same 8KB
`fb-expand-basic-area.py` already hands V3 on plain mapper 0. Run that first if you want
the 8KB; this tool keeps whatever area the input has.

**A 16KB free area and FM are mutually exclusive** and always will be: the FM ports are
inside `$8000-$9FFF`, which the 16KB build turns into RAM. A cartridge has one mapper, so
the MMC5 16KB build and this one are different ROMs.

## The one hard problem: where the init code goes

VRC7 powers up with **nothing configured** - the three switchable PRG banks, all eight
CHR banks and the WRAM write-enable are undefined. So a few dozen bytes have to run before
the original reset handler (`$80BA`), and they have to run from `$E000-$FFFF`, the only
window whose contents are known at power-on (it is fixed to the last bank).

In the stock ROM **that window is full**: the four built-in programs run from `$D400` to
`$FFF9` with no filler anywhere, and the vectors sit on top of them. There is no room.

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
V3 - the only version this tool accepts - those bytes are the middle of the reserved-word
table, so it is not a reason for anything here.)

## Memory map after power-on

| CPU address space | Contents |
|---|---|
| `$6000-$7FFF` | WRAM - the free area (4KB or 8KB, whatever the input declares) |
| `$8000-$9FFF` | ROM bank 4 = original `$8000-$9FFF`. **`$9010`/`$9030` are the FM ports** |
| `$A000-$BFFF` | ROM bank 5 = original `$A000-$BFFF` (the `GAME` entry is patched here) |
| `$C000-$DFFF` | ROM bank 6 = original `$C000-$DFFF` (title graphic still at `$D000`) |
| `$E000-$FFFF` | ROM bank 7 = original `$E000-$FFFF` + init + loader, fixed |
| banks 0-3 | one built-in program each, at `$D000`, swapped into `$C000-$DFFF` while loading |

**Every address the CPU can see is byte-for-byte the stock 32KB**, except in four
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

1. **The map** - the ROM is executed from the reset vector on a model of the 6502 and of
   VRC7, with every unconfigured bank treated as poison (touching one fails on the spot),
   until it reaches the stock reset handler. Then all of `$8000-$FFFF` is compared against
   the stock 32KB byte for byte, and WRAM must be writable. Run for both decodings.
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
import importlib.util
import os
import sys

# The tiny assembler lives in the MMC5 builder. Importing it keeps **one** copy: a second
# copy of an assembler in this file would be the same rule in two places, which in this
# repository has drifted within a day of being duplicated.
_spec = importlib.util.spec_from_file_location(
    "fb_mmc5_16k", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fb-mmc5-16k.py"))
_fb16 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fb16)
Asm = _fb16.Asm

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
                     f"(${a:02X}, expected ${b:02X}). Only V3.0 is supported.")
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


def build_init(mirror_bits, reset_addr):
    """Configure VRC7 at power-on, then hand over to the stock reset handler.

    Order matters twice over:

      * the ambiguous registers are written through **both** aliases before the
        unambiguous one is written, so the result is right under A3 and A4 decoding alike
      * `$E000` (WRAM enable) is written last of the mapper setup, because everything
        BASIC does from `$80BA` on assumes it can write to `$6000-$7FFF`
    """
    a = Asm(INIT_ORG)
    a.emit("SEI", "imp", note="VRC7's IRQ powers up undefined")

    def put(reg, value, note):
        a.emit("LDA", "imm", value)
        a.emit("STA", "abs", reg, note)

    # Pass 1: the registers whose address is read differently by the two boards. Each is
    # written through both aliases, so one of the two writes lands on the register next
    # door - always one whose correct value is written in pass 2.
    for alias in PRG_SELECT_1:
        put(alias, BANK_A000, f"$A000-$BFFF <- bank {BANK_A000} (${alias:04X} alias)")
    for n, reg in enumerate(CHR_SELECT):
        if isinstance(reg, tuple):
            for alias in reg:
                put(alias, n, f"CHR {n} (${alias:04X} alias)")
    # Pass 2: the unambiguous registers, which both boards read the same way. **These have
    # to come last**: they are what repairs whatever pass 1 hit by mistake.
    # $E000-$FFFF is fixed to the last bank, so there is no register for it and the code
    # running right now cannot be swapped out from under itself.
    put(PRG_SELECT_0, BANK_8000, f"$8000-$9FFF <- bank {BANK_8000}")
    put(PRG_SELECT_2, BANK_C000, f"$C000-$DFFF <- bank {BANK_C000}")
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
        elif group == 0xF000:
            if not alt:
                self.irq_control = value


# --------------------------------------------------------------------------- checks


def check_map(prg, orig_prg, reset_addr, decode, changed, ines_vertical):
    """Power on, run the init code, and compare what the CPU then sees with the stock ROM.

    `changed` is every address this build deliberately altered. Anything outside it that
    differs means a bank came out wrong, which is the failure this check exists for.
    """
    bus = Vrc7Bus(prg, decode)
    cpu = Cpu(bus, bus.read(0xFFFC) | bus.read(0xFFFD) << 8)
    cpu.run_until(reset_addr, limit=1000)
    unexpected = [a for a in range(0x8000, 0x10000)
                  if a not in changed and bus.read(a) != orig_prg[a - 0x8000]]
    if unexpected:
        raise Fault(f"[{decode}] {len(unexpected)} bytes of $8000-$FFFF differ from the stock "
                    f"ROM, first at ${unexpected[0]:04X}")
    if not bus.wram_enabled:
        raise Fault(f"[{decode}] WRAM is still write-protected after the init code")
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


# --------------------------------------------------------------------------- build


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom", help="stock V3, or the output of fb-expand-basic-area.py")
    ap.add_argument("-o", "--out")
    ap.add_argument("--listing", action="store_true", help="print the assembled code")
    args = ap.parse_args()

    header, prg, chr_data = load(args.rom)
    info = read_loader(prg)   # names the version in its message, so it goes first
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

    put7(INIT_ORG, init.code)
    put7(LOADER_ORG, loader.code)
    for vec in (0xFFFC, 0xFFFE):        # reset and IRQ both went to $80BA; both go to init
        bank7[vec - 0xE000:vec - 0xE000 + 2] = bytes([INIT_ORG & 0xFF, INIT_ORG >> 8])
    assert len(banks) == TOTAL_BANKS and all(len(b) == BANK for b in banks)
    built = b"".join(bytes(b) for b in banks)

    # Every address this build changed on purpose. The map check treats a difference
    # anywhere else as a wrong bank.
    changed = set(range(INIT_ORG, INIT_ORG + len(init.code)))
    changed |= set(range(LOADER_ORG, LOADER_ORG + len(loader.code)))
    changed |= set(range(LOADER_SITE, LOADER_SITE + 4))     # the jump to the rebuilt loader
    changed |= {0xFFFC, 0xFFFD, 0xFFFE, 0xFFFF}             # reset and IRQ point at init

    # --- checks -------------------------------------------------------------
    print(f"read from {args.rom}")
    print(f"  {info['count']} built-in programs at "
          + " ".join(f"${s:04X}" for s in info["sources"])
          + f", end addresses from ${info['end_table']:04X}")
    for decode in ("a4", "a3"):
        bus = check_map(built, prg, reset_addr, decode, changed, bool(header[6] & 1))
        print(f"  [{decode}] power-on -> ${reset_addr:04X}: $8000-$FFFF matches the stock ROM,"
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
