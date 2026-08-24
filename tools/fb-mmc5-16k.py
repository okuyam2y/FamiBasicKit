#!/usr/bin/env python3
"""Build the 16KB-free-area MMC5 version from an already relocated V3.

  $ ./fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
  $ ./fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
        -o "Family BASIC V3.0 (MMC5 16KB).nes"

## What this does

A 16KB free area requires all of `$6000-$9FFF` to be RAM. The interpreter that lived at
`$8000-$9FFF` has already been moved to `$D000` and up by `fb-relocate.py`, so what
happens here is **rearranging the rest of the ROM into MMC5 banks and rebuilding the load
path for the built-in programs**.

## Memory map (after power-on)

| CPU address space | Contents |
|---|---|
| `$6000-$7FFF` | WRAM block 0 — lower half of the free area |
| `$8000-$9FFF` | WRAM block 1 — upper half (**this is what makes 16KB possible**) |
| `$A000-$BFFF` | ROM bank 5 — always resident, **patched in place** (`put_a`) |
| `$C000-$DFFF` | ROM bank 6 (original `$C000-$CFFF` + first half of the relocated interpreter) |
| `$E000-$FFFF` | ROM bank 7 (second half + title graphic + init + loader + vectors) |

**The moved range is not a constant** (it extends out to the targets of branches leaving
the block; on the real ROM `$8000-$A00A` -> `$D000-$F00A`, bridge at `$F00B-$F00D`).
So the placement of the init code, the graphic and the loader is checked against values
`check_inputs()` re-derives — in a version that hard-coded this, the interpreter grew, the
guard missed it, and the init code overwrote the interpreter.

## How the four built-in programs are placed

**One 8KB bank each**, with the program in the upper half (`$D000-$DFFF`).
The load loop keeps reading until the destination reaches `$7000` (at most 4,090 bytes),
and 4,090 < 4,096, so **it never runs off the end of the bank**: no cross-bank handling.

The lower half of each bank duplicates `$C000-$CFFF`. Since `$5116` swaps in 8KB units,
duplicating it means **only `$D000-$DFFF` disappears during the swap**.

WARNING: while the swap is in effect, the body of the NMI handler (`$D971` after
relocation) is gone. NMI is therefore disabled for the duration of the load (`$32` is
BASIC's shadow copy of `$2000`).

## Why `$5117` never has to be written at power-on

MMC5's `$5117` powers up as `$FF` — the last bank of ROM. **The init code and the vectors
were placed in that last bank (bank 7)**, so the right thing is visible from the instant
power comes up. That sidesteps the classic MMC5 trap entirely: "the moment you write
`$5117`, the bank holding the code you are currently executing is swapped out."
"""

import argparse
import hashlib
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "fb_relocate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fb-relocate.py"))
fbr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbr)

BANK = 0x2000
PROGRAMS = [(0xD400, 0), (0xDBFE, 1), (0xE682, 2), (0xF308, 3)]   # where they sit in the original
BG_SRC = 0xD000            # the title graphic in the original ROM (1KB)
BG_DEST = 0xF100           # where it is moved to (bank 7, always mapped)
INIT_ORG = 0xF010          # init code (after the relocated body and bridge; check_inputs
                           # verifies there is no overlap)
LOADER_ORG = 0xF500        # the rebuilt GAME loader
# In the relocated image everything from `$D000` up is overwritten by the interpreter, so
# the byte-for-byte comparison against the re-derived image **cannot detect a difference
# in this range of the original ROM**. Yet the title graphic and the built-in programs are
# taken from exactly here, so this range gets its own hash check.
ORIG_DATA_RANGE = (0xD000, 0xFFF9)
ORIG_DATA_SHA256 = "52830b82ccd86496121f5098dd8d3954a62908bb96bbc9e407a9ff604704bbc3"

NORMAL_C_BANK = 6
RESIDENT_A_BANK = 5
RESIDENT_E_BANK = 7

# --- Bank number that puts a second WRAM block at $8000-$9FFF ------------------
# **The right answer differs between machines.** In an MMC5 bank number, bit 2 doubles as
# both "A15" and "which RAM chip's /CE" (NESdev wiki), so its meaning depends on the board:
#
#   * ETROM (16KB = two 8KB chips): 0-3 are chip 0, 4-7 are chip 1. The chips are 8KB, so
#     A13/A14 have nowhere to go and **$01 becomes a mirror of $6000-$7FFF**
#   * MiSTer NES core: plain `{prgsel[2], prgsel[1:0]}` concatenation. **$01 is block 2**
#
# Measured: an EverDrive N8 PRO follows the ETROM reading — $01 mirrors, $04 separates.
# MiSTer separates on $01, and on $04 as well, but $04 risks **falling outside the range
# that gets saved**. So neither value can be hard-coded: write and read back at boot to
# choose (the probe in build_init).
RAM_BANK_LINEAR = 0x01       # second block on a linear implementation (MiSTer)
RAM_BANK_CHIP1 = 0x04        # on an implementation using bit 2 as chip select (ETROM / N8 PRO)
PROBE_LO = 0x7E00            # address used for the probe, in the lower block
PROBE_HI = 0x9E00            # the same offset within the 8KB, in the upper block
PROBE_MARK_LO = 0x5A
PROBE_MARK_HI = 0xA5


class Asm:
    """A tiny assembler covering only the instructions needed. Keeps a listing."""

    # Instruction sizes, used to measure forward branches. Kept separately because they
    # cannot be derived from the OPS table
    SIZES = {"imp": 1, "imm": 2, "zp": 2, "izy": 2, "rel": 2, "abs": 3, "abx": 3}

    OPS = {
        ("SEI", "imp"): 0x78, ("ASL", "imp"): 0x0A, ("TAX", "imp"): 0xAA,
        ("INY", "imp"): 0xC8, ("LDY", "imm"): 0xA0, ("LDA", "imm"): 0xA9,
        ("LDA", "zp"): 0xA5, ("LDA", "abs"): 0xAD, ("LDA", "abx"): 0xBD,
        ("LDA", "izy"): 0xB1, ("STA", "zp"): 0x85, ("STA", "abs"): 0x8D,
        ("STA", "izy"): 0x91, ("INC", "zp"): 0xE6, ("CMP", "zp"): 0xC5,
        ("AND", "imm"): 0x29, ("ORA", "imm"): 0x09, ("JMP", "abs"): 0x4C,
        ("BNE", "rel"): 0xD0,
        # Added to probe the WRAM bank number at boot (see the probe below)
        ("CMP", "imm"): 0xC9, ("BEQ", "rel"): 0xF0,
        ("PHA", "imp"): 0x48, ("PLA", "imp"): 0x68,
    }

    def __init__(self, org):
        self.org, self.code, self.listing, self.labels = org, bytearray(), [], {}

    @property
    def pc(self):
        return self.org + len(self.code)

    def label(self, name):
        self.labels[name] = self.pc
        return self.pc

    def emit(self, mnem, mode, operand=None, note=""):
        op = self.OPS[(mnem, mode)]
        raw = [op]
        if mode in ("imm", "zp", "izy"):
            raw.append(operand & 0xFF)
        elif mode == "rel":
            off = operand - (self.pc + 2)
            assert -128 <= off <= 127, f"branch out of range ({off})"
            raw.append(off & 0xFF)
        elif mode in ("abs", "abx"):
            raw += [operand & 0xFF, operand >> 8]
        text = "" if operand is None else {
            "imp": "", "imm": f"#${operand:02X}", "zp": f"${operand:02X}",
            "izy": f"(${operand:02X}),Y", "rel": f"${operand:04X}",
            "abs": f"${operand:04X}", "abx": f"${operand:04X},X"}[mode]
        self.listing.append((self.pc, bytes(raw), f"{mnem} {text}".strip(), note))
        self.code += bytes(raw)


def check_inputs(rel, orig, orig_path, delta):
    """Checks that stop **an unexpected input from silently producing a broken ROM**.

    Without them, all of the following pass without an error (all measured):

      * a different ROM passed as `--original` -> a ROM whose graphic and built-in
        programs alone come from other data
      * a `--delta` different from the one used to relocate -> the init code is written
        over the relocated interpreter
      * a ROM with a trainer, or a different PRG size -> everything read from byte 16 on
        is shifted
    """
    for name, data in (("--relocated", rel), ("--original", orig)):
        if data[:4] != b"NES\x1a":
            sys.exit(f"{name}: no iNES header")
        # Matching the magic says nothing about the other 12 header bytes being there, and
        # every check below indexes into them - a short file would raise IndexError instead
        # of the one-line exit every other malformed input gets
        if len(data) < 16:
            sys.exit(f"{name}: an iNES header is 16 bytes, this file is {len(data)}")
        if data[6] & 0x04:
            sys.exit(f"{name}: ROMs with a trainer are not supported")
        if data[4] != 2:
            sys.exit(f"{name}: PRG is not 32KB ({data[4] * 16}KB)")
        if ((data[7] >> 2) & 3) != 2:
            # Byte 10 (NVRAM) only means anything in NES 2.0. If the original is 1.0,
            # the 16KB declaration is silently ignored
            sys.exit(f"{name}: not NES 2.0 (byte7 = ${data[7]:02X})")
        # The mapper number and ROM size are not determined by the low bits of byte 6/7
        # alone. Carrying the high bits over from the input would produce a ROM on some
        # other mapper while we believed it was mapper 5
        if data[7] & 0xF0:
            sys.exit(f"{name}: the high bits of the mapper number are not 0 (byte7 = ${data[7]:02X})")
        if data[8]:
            sys.exit(f"{name}: the NES 2.0 mapper-high/submapper byte (byte8) is not 0")
        if data[9]:
            sys.exit(f"{name}: the high bits of the PRG/CHR size (byte9) are not 0")
    # `fb-relocate.py` copies the header without changing a byte, so a difference here
    # means the two inputs do not correspond. Without comparing headers, clearing bit 0 of
    # byte 6 in `--relocated` alone silently yields **a ROM with different mirroring**
    if bytes(rel[:16]) != bytes(orig[:16]):
        diff = [i for i in range(16) if rel[i] != orig[i]]
        sys.exit(f"the two inputs have different headers (differing bytes {diff})")
    for name, data in (("--relocated", rel), ("--original", orig)):
        want_len = 16 + data[4] * 16384 + data[5] * 8192
        if len(data) != want_len:
            sys.exit(f"{name}: file length disagrees with the header "
                     f"({len(data)} != {want_len})")
    # Relocation does not change a single CHR byte, so a difference here means the
    # inputs do not correspond
    if bytes(rel[16 + 0x8000:]) != bytes(orig[16 + 0x8000:]):
        sys.exit("CHR differs between the inputs (--relocated did not come from --original)")

    # The source ROM is checked with **the same function fb-relocate.py uses**.
    # Writing the check twice creates a way in through only one of them
    fbr.check_source_rom(bytes(orig), "--original")

    rel_prg = rel[16:16 + 0x8000]
    if len(set(rel_prg[0:0x2000])) != 1:
        sys.exit("$8000-$9FFF in --relocated is not filled with the canary "
                 "(it is not the output of fb-relocate.py)")

    # Do **not** infer the correspondence from partial matches. Redo the relocation from
    # --original and require that it matches --relocated byte for byte. That rejects
    # "a different ROM with the same tables", "an input corrupted elsewhere in the body"
    # and "a different delta" all at once — a structural answer that avoids piling up
    # more partial-match checks.
    rom = fbr.fbd.Rom(orig_path)
    tr = fbr.trace(rom)
    expect, _fixes, _d, last, bridge, _touched, _skipped = fbr.relocate(
        rom, tr, 0x8000 + delta, rel_prg[0])
    if bytes(expect) != bytes(rel_prg):
        diff = [a for a in range(0x8000) if expect[a] != rel_prg[a]]
        sys.exit(f"--relocated does not match what --original produces "
                 f"({len(diff)} differing bytes, first at ${0x8000 + diff[0]:04X}). "
                 f"Either a different ROM, or --delta ${delta:04X} differs from the "
                 f"one used to relocate")

    lo, hi = ORIG_DATA_RANGE
    seg = bytes(orig[16 + lo - 0x8000:16 + hi + 1 - 0x8000])
    got = hashlib.sha256(seg).hexdigest()
    if got != ORIG_DATA_SHA256:
        sys.exit(f"${lo:04X}-${hi:04X} of --original (title graphic and built-in "
                 f"programs) differs from expectation "
                 f"(sha256 {got[:16]}... != {ORIG_DATA_SHA256[:16]}...). "
                 f"This range is overwritten in the relocated image, which is why it is "
                 f"checked separately")

    # Nothing may overlap the relocated interpreter.
    # WARNING: **do not hard-code the length moved** — the range can grow because of
    # branches leaving the block (measured: $A001 -> $A00A). Derive it from the
    # re-derived `last` and the bridge position. A version that hard-coded this treated
    # $F005 as free while the body reached $F00A, and missed the init code overwriting it
    first_free = bridge + 3
    for name, start, size in (("init code", INIT_ORG, 0x100),
                              ("title graphic", BG_DEST, 0x400),
                              ("loader", LOADER_ORG, 0x100)):
        if start < first_free:
            sys.exit(f"placement of the {name} at ${start:04X} overlaps the relocated "
                     f"body (through ${last + delta:04X}) plus its bridge "
                     f"(${bridge:04X}-${bridge + 2:04X}). Check --delta")
        if start + size > 0xFFFA:
            sys.exit(f"placement of the {name} at ${start:04X} runs into the vectors")


def emit_probe(a):
    """Pick a bank number that makes `$8000-$9FFF` **a different RAM** from `$6000-$7FFF`.

    Just write and read back. Set `$01`, write to the upper block, and if the marker in
    the lower block survives it is a separate block. If it was clobbered it is a mirror,
    so switch to `$04` (bit 2 = chip select).

    WARNING: **on a hot start the two bytes used for the probe hold the user's program.**
    Everything touched must be restored. They are saved on the stack (`$0100-$01FF`) —
    that avoids having to guess at a free zero-page address. This runs before BASIC's
    reset handler so the stack pointer is undefined, but as long as pushes and pulls
    balance, that does not matter.

    WARNING: **on a machine where neither number separates** (WRAM really is only 8KB)
    it proceeds with `$04` and BASIC runs believing it has 16KB, breaking silently once a
    program passes 8KB. Handling that would require rewriting BASIC's own "end of area"
    constant into a RAM reference, which is beyond this function (not implemented).
    """
    a.emit("LDA", "abs", PROBE_LO, "save the original value from the lower block")
    a.emit("PHA", "imp")
    a.emit("LDA", "imm", RAM_BANK_LINEAR)
    a.emit("STA", "abs", 0x5114, "try the linear reading first")
    a.emit("LDA", "abs", PROBE_HI, "save the original value from the upper block")
    a.emit("PHA", "imp")
    a.emit("LDA", "imm", PROBE_MARK_LO)
    a.emit("STA", "abs", PROBE_LO, "put a marker in the lower block")
    a.emit("LDA", "imm", PROBE_MARK_HI)
    a.emit("STA", "abs", PROBE_HI, "write a different value in the upper block")
    a.emit("LDA", "abs", PROBE_LO)
    a.emit("CMP", "imm", PROBE_MARK_LO, "marker intact means separate blocks")

    # Cleanup taken only when it turned out to be a mirror.
    # **Measure its length first, then emit the forward branch**
    fixup = [
        ("PLA", "imp", None, "the saved \"upper value\" was really the lower one; discard it"),
        ("LDA", "imm", RAM_BANK_CHIP1, ""),
        ("STA", "abs", 0x5114, "switch to the chip-select reading of bit 2"),
        ("LDA", "abs", PROBE_HI, "re-save the original value at the new mapping"),
        ("PHA", "imp", None, ""),
    ]
    span = sum(Asm.SIZES[mode] for _m, mode, _o, _c in fixup)
    a.emit("BEQ", "rel", a.pc + 2 + span, "separate blocks: no cleanup needed")
    for mnem, mode, operand, note in fixup:
        a.emit(mnem, mode, operand, note)

    a.emit("PLA", "imp", None, "restore the upper block")
    a.emit("STA", "abs", PROBE_HI)
    a.emit("PLA", "imp", None, "restore the lower block")
    a.emit("STA", "abs", PROBE_LO)


WRAM_MODELS = {
    # bank number -> 8KB block number. **Differs per implementation** (see RAM_BANK_*)
    "linear": lambda v: v & 0x07,          # MiSTer NES core
    "chip": lambda v: (v >> 2) & 1,        # real ETROM / EverDrive N8 PRO (two 8KB chips)
    "only8k": lambda v: 0,                 # a machine with only 8KB of WRAM
}


def simulate_init(init, model):
    """**Actually execute** the assembled init code and confirm the probe does its job.

    Only two machines are available here, and one of them (a real ETROM) is not on hand.
    So it runs on a model whose reading of bank numbers can be swapped out.

    WARNING: **do not hand it the bank number you expect.** Using `RAM_BANK_*` as the
    expected value makes the check move along with a wrong constant and pass vacuously
    (measured: changing `RAM_BANK_CHIP1` from `$04` to `$01` still passed). So what gets
    checked is not the number but the **properties**:

      * if the machine can offer two blocks, writing to `$8000-$9FFF` must leave
        `$6000-$7FFF` untouched
      * the bytes used for the probe must be restored, and the stack must balance

    Whether the machine can offer two blocks is derived from the model itself
    (never from `RAM_BANK_*`).
    """
    to_block = WRAM_MODELS[model]
    blocks = {i: bytearray(0x2000) for i in range(8)}
    for i, blk in blocks.items():
        blk[PROBE_LO - 0x6000] = 0x40 + i      # a pre-existing value (hot-start case)
    regs, sp, stack = {}, 0xFD, bytearray(0x100)
    a, zflag, pc = 0, False, init.org
    code = init.code

    def block_at(cpu_addr):
        if 0x6000 <= cpu_addr < 0x8000:
            return blocks[to_block(regs.get(0x5113, 0))], cpu_addr - 0x6000
        if not 0x8000 <= cpu_addr < 0xA000:
            raise AssertionError(f"${cpu_addr:04X}: touched something outside WRAM")
        v = regs.get(0x5114)
        if v is None or v & 0x80:
            raise AssertionError(f"${cpu_addr:04X} was touched but $5114 is not set to RAM")
        return blocks[to_block(v)], cpu_addr - 0x8000

    for _ in range(500):
        off = pc - init.org
        if not 0 <= off < len(code):
            raise AssertionError(f"execution left the init code (${pc:04X})")
        op = code[off]
        if op == 0x78:                                        # SEI
            pc += 1
        elif op == 0xA9:                                      # LDA #imm
            a = code[off + 1]; zflag = (a == 0); pc += 2
        elif op == 0xAD:                                      # LDA abs
            addr = code[off + 1] | (code[off + 2] << 8)
            blk, i = block_at(addr); a = blk[i]; zflag = (a == 0); pc += 3
        elif op == 0x8D:                                      # STA abs
            addr = code[off + 1] | (code[off + 2] << 8)
            if 0x5000 <= addr < 0x6000:
                regs[addr] = a
            else:
                blk, i = block_at(addr); blk[i] = a
            pc += 3
        elif op == 0x48:                                      # PHA
            stack[sp] = a; sp = (sp - 1) & 0xFF; pc += 1
        elif op == 0x68:                                      # PLA
            sp = (sp + 1) & 0xFF; a = stack[sp]; zflag = (a == 0); pc += 1
        elif op == 0xC9:                                      # CMP #imm
            zflag = (a == code[off + 1]); pc += 2
        elif op == 0xF0:                                      # BEQ rel
            d = code[off + 1]; pc += 2
            if zflag:
                pc += d - 256 if d >= 0x80 else d
        elif op == 0x4C:                                      # JMP abs (to the real reset handler)
            break
        else:
            raise AssertionError(f"${pc:04X}: the model does not know opcode ${op:02X}. "
                                 f"Extend the model whenever the init code grows")
    else:
        raise AssertionError("the init code never terminated")

    got = regs.get(0x5114)
    if got is None or got & 0x80:
        raise AssertionError(f"[{model}] $5114 is not set to RAM after init")
    if sp != 0xFD:
        raise AssertionError(f"[{model}] the stack does not balance (SP ${sp:02X})")
    for i, blk in blocks.items():
        if blk[PROBE_LO - 0x6000] != 0x40 + i:
            raise AssertionError(f"[{model}] the probe byte in block {i} was not restored "
                                 f"(${blk[PROBE_LO - 0x6000]:02X} != ${0x40 + i:02X})")

    # The heart of it: **look at the outcome of the choice, not the number chosen.**
    # Whether this machine can offer two blocks is derived from the model itself
    base = to_block(regs.get(0x5113, 0))
    candidates = [v for v in range(8) if to_block(v) != base]
    two_blocks = bool(candidates)
    # "Any number that separates" is not good enough: pick **the lowest one that does**.
    # A larger number lands on block 4 or above on MiSTer, outside the `.sav`
    # (32KB = blocks 0-3), and would never be saved. This expectation is derived from the
    # model too, never from RAM_BANK_* (which would move along with a wrong constant)
    if two_blocks and got != min(candidates):
        raise AssertionError(f"[{model}] chose $5114 = ${got:02X}, but the lowest number "
                             f"that separates is ${min(candidates):02X}")
    lo_blk, lo_i = blocks[to_block(regs.get(0x5113, 0))], PROBE_LO - 0x6000
    hi_blk, hi_i = blocks[to_block(got)], PROBE_HI - 0x8000
    before = lo_blk[lo_i]
    hi_blk[hi_i] = before ^ 0xFF
    separated = (lo_blk[lo_i] == before)
    hi_blk[hi_i] = 0x40 + to_block(got)                      # restore what we touched
    if two_blocks and not separated:
        raise AssertionError(f"[{model}] chose $5114 = ${got:02X}, yet $8000-$9FFF is "
                             f"still a mirror of $6000-$7FFF")
    if not two_blocks and separated:
        raise AssertionError(f"[{model}] blocks separated on a machine that cannot offer two (model error)")
    return got, separated


def build_init(mirror_value, reset_addr):
    """Configure MMC5 at power-on, then hand over to the relocated reset handler.

    `$5114` (`$8000-$9FFF`) **is the one value that is not hard-coded**: the right answer
    differs between machines, so `emit_probe` decides it after write access is enabled.
    """
    a = Asm(INIT_ORG)
    a.emit("SEI", "imp", note="disable interrupts (MMC5 IRQ powers up undefined)")
    # The probe is meaningless before WRAM writes are enabled ($5102/$5103), so the
    # register writes are split into two lists rather than one
    before = [
        (0x5100, 0x03, "PRG mode 3 (4 x 8KB)"),
        (0x5101, 0x00, "CHR mode 0 (1 x 8KB)"),
        (0x5104, 0x00, "ExRAM mode 0"),
        (0x5113, 0x00, "$6000-$7FFF <- WRAM block 0"),
        (0x5127, 0x00, "CHR bank 0"),
        (0x512B, 0x00, "CHR bank 0 (guard for 8x16)"),
        (0x5130, 0x00, "high bits of the CHR bank number"),
        (0x5015, 0x00, "silence the MMC5 sound channels"),
        (0x5203, 0x00, "IRQ compare scanline"),
        (0x5204, 0x00, "IRQ disabled"),
        (0x5105, mirror_value, "nametable arrangement"),
        (0x5102, 0x02, "WRAM write enable (key 1)"),
        (0x5103, 0x01, "WRAM write enable (key 2)"),
    ]
    after = [
        (0x5115, 0x80 | RESIDENT_A_BANK, "$A000-$BFFF <- ROM bank 5"),
        (0x5116, 0x80 | NORMAL_C_BANK, "$C000-$DFFF <- ROM bank 6"),
        (0x5117, 0x80 | RESIDENT_E_BANK, "$E000-$FFFF <- ROM bank 7 (the bank we are already in)"),
    ]

    def emit_writes(items):
        acc = None                      # last value loaded into A; not reused across the probe
        for reg, value, note in items:
            if acc != value:
                a.emit("LDA", "imm", value)
                acc = value
            a.emit("STA", "abs", reg, note)

    emit_writes(before)
    emit_probe(a)
    emit_writes(after)
    a.emit("JMP", "abs", reset_addr, "to the relocated original reset handler")
    return a


def build_loader(end_table, resume):
    """The `GAME n` load routine. Called in place of the original at `$AD96`."""
    a = Asm(LOADER_ORG)
    a.emit("LDA", "zp", 0x05, "destination = start of the free area")
    a.emit("STA", "zp", 0x19)
    a.emit("LDA", "zp", 0x06)
    a.emit("STA", "zp", 0x1A)
    a.emit("LDA", "zp", 0xF3, "program number")
    a.emit("ASL", "imp")
    a.emit("TAX", "imp")
    a.emit("LDA", "abx", end_table, "table of end addresses (read before swapping banks)")
    a.emit("STA", "zp", 0x07)
    a.emit("LDA", "abx", end_table + 1)
    a.emit("STA", "zp", 0x08)
    a.emit("LDA", "zp", 0x32, "disable NMI while $D000-$DFFF is swapped out")
    a.emit("AND", "imm", 0x7F)
    a.emit("STA", "abs", 0x2000)
    a.emit("LDA", "zp", 0xF3, "map data bank n into $C000-$DFFF")
    a.emit("ORA", "imm", 0x80)
    a.emit("STA", "abs", 0x5116)
    a.emit("LDA", "imm", 0x00, "source = $D000, the upper half of the swapped-in bank")
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
    a.emit("LDA", "imm", 0x70, "until the destination reaches $7000 (as the original did)")
    a.emit("CMP", "zp", 0x1A)
    a.emit("BNE", "rel", loop)
    a.emit("LDA", "imm", 0x80 | NORMAL_C_BANK, "restore the bank")
    a.emit("STA", "abs", 0x5116)
    # WARNING: `$32` is BASIC's shadow of `$2000`, but it **does not carry bit 7 (NMI
    # enable)** (measured: `$32 = $10`). BASIC itself adds `ORA #$80` when re-enabling
    # (`$BE18`). Writing `LDA $32 / STA $2000` here leaves NMI disabled, killing both
    # screen updates and key reading (black screen, no response). That happened once on
    # real hardware.
    a.emit("LDA", "zp", 0x32, "re-enable NMI (add bit 7; $32 does not carry it)")
    a.emit("ORA", "imm", 0x80)
    a.emit("STA", "abs", 0x2000)
    a.emit("JMP", "abs", resume, "the original JMP $97A6, relocated")
    return a


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("relocated", help="the output of fb-relocate.py")
    ap.add_argument("--original", required=True,
                    help="the stock V3 the built-in programs are taken from")
    ap.add_argument("-o", "--out")
    ap.add_argument("--delta", default="5000",
                    help="the delta fb-relocate.py moved by (default 5000). The "
                         "relocation is redone from --original and compared, so a "
                         "mismatch stops the build")
    ap.add_argument("--listing", action="store_true", help="print a listing of the assembled code")
    args = ap.parse_args()

    delta = int(args.delta, 16)
    rel = bytearray(open(args.relocated, "rb").read())
    orig = open(args.original, "rb").read()
    check_inputs(rel, orig, args.original, delta)
    header = bytearray(rel[:16])
    rel_prg = bytearray(rel[16:16 + 0x8000])
    orig_prg = orig[16:16 + 0x8000]
    chr_data = rel[16 + 0x8000:]

    # $5105 is four 2-bit fields (DDCCBBAA; 0=CIRAM page 0, 1=page 1).
    # iNES vertical mirroring (byte6 bit0=1) = NT 0,1,0,1 = $44; horizontal = 0,0,1,1 = $50.
    # ($55 would be page 1 everywhere, i.e. single-screen, not horizontal mirroring)
    mirror = 0x44 if (header[6] & 1) else 0x50
    reset_addr = 0x80BA + delta
    resume = 0x97A6 + delta                           # where the original jumped after loading
    end_table = 0x8003 + delta

    # --- Assemble bank 7 (always mapped at $E000-$FFFF) ----------------------
    bank7 = bytearray(rel_prg[0xE000 - 0x8000:0x10000 - 0x8000])

    def put7(addr, data):
        bank7[addr - 0xE000:addr - 0xE000 + len(data)] = data

    put7(BG_DEST, orig_prg[BG_SRC - 0x8000:BG_SRC - 0x8000 + 0x400])
    init = build_init(mirror, reset_addr)
    loader = build_loader(end_table, resume)
    # **Actually run** the assembled probe on models with different readings of the bank
    # number. Only two machines are available here (MiSTer and an N8 PRO); a real ETROM
    # is not on hand
    for model in WRAM_MODELS:
        got, separated = simulate_init(init, model)
        note = "upper and lower are different RAM" if separated else \
               "no separation (8KB-only machine; BASIC will believe it has 16KB)"
        print(f"  probe check [{model}] -> $5114 = ${got:02X} / {note}")

    # check_inputs does not look at these pieces overlapping each other (it only checks
    # against the relocated body, the bridge and the vectors, using an estimated length of
    # 0x100). **Adding the probe made the init code longer**, so the real lengths are
    # checked here
    for name, org, size, limit in (("init code", INIT_ORG, len(init.code), BG_DEST),
                                   ("loader", LOADER_ORG, len(loader.code), 0xFFFA)):
        if org + size > limit:
            sys.exit(f"the {name} extends to ${org:04X}-${org + size - 1:04X} and runs "
                     f"into ${limit:04X} ({size} bytes)")
    put7(INIT_ORG, init.code)
    put7(LOADER_ORG, loader.code)
    for a in (0xFFFC, 0xFFFE):                        # point reset/IRQ at the init code
        put7(a, bytes([INIT_ORG & 0xFF, INIT_ORG >> 8]))

    # --- Patches inside the always-mapped bank -------------------------------
    resident_a = bytearray(rel_prg[0xA000 - 0x8000:0xC000 - 0x8000])

    def put_a(addr, expect, new, why):
        off = addr - 0xA000
        got = bytes(resident_a[off:off + len(expect)])
        if got != bytes(expect):
            sys.exit(f"${addr:04X}: differs from expectation ({got.hex(' ')} != {bytes(expect).hex(' ')}): {why}")
        resident_a[off:off + len(new)] = bytes(new)

    # Repoint the title graphic from $D000 to where it was moved
    put_a(0xADD5, [0xA9, 0xD0], [0xA9, BG_DEST >> 8], "source of the title-graphic copy (high byte)")
    put_a(0xADF2, [0xA9, 0xD4], [0xA9, (BG_DEST + 0x400) >> 8], "end of the title-graphic copy")
    # Repoint the load routine at the rebuilt one
    put_a(0xAD96, [0xA5, 0x05, 0x85, 0x19], [0x4C, LOADER_ORG & 0xFF, LOADER_ORG >> 8, 0xEA],
          "GAME load (originally copied $05/$06 into $19/$1A)")

    # --- Widen the free area to 16KB -----------------------------------------
    # The constant loaded as "end of area" at boot (V3 stores the address of the last
    # byte) and the ceiling in the CLEAR argument check. Both are at relocated addresses.
    bank6 = bytearray(rel_prg[0xC000 - 0x8000:0xE000 - 0x8000])
    top_at = 0x86A3 + delta                           # originally $86A3: LDA #$6F / STA $04
    off = top_at - 0xC000
    if bytes(bank6[off:off + 4]) != bytes([0xA9, 0x7F, 0x85, 0x04]) and \
       bytes(bank6[off:off + 4]) != bytes([0xA9, 0x6F, 0x85, 0x04]):
        sys.exit(f"${top_at:04X}: could not find the end-of-area constant "
                 f"({bytes(bank6[off:off+4]).hex(' ')})")
    old_top = bank6[off + 1]
    bank6[off + 1] = 0x9F                             # $9FFF, the last byte of 16KB
    clear_at = 0x97D7 + delta                         # originally $97D7: CMP #$70 / BCS
    coff = clear_at - 0xE000
    if bytes(bank7[coff:coff + 2]) not in (bytes([0xC9, 0x70]), bytes([0xC9, 0x80])):
        sys.exit(f"${clear_at:04X}: could not find the CLEAR ceiling "
                 f"({bytes(bank7[coff:coff+2]).hex(' ')})")
    old_clear = bank7[coff + 1]
    bank7[coff + 1] = 0xA0                            # CLEAR errors at or above $A000

    # `BGGET`/`BGPUT` share one 1KB screen buffer pinned to the **top of the area**
    # (`$6C00-$6FFF`, the last kilobyte of the unexpanded `$6000-$6FFF`). Widening the area
    # does not move it, so it lands in the middle of the user's program and `BGGET` refuses
    # with `?OM ERROR` as soon as the program passes `$6C00`. Confirmed on hardware
    # 2026-08-23: a 3,529-byte program printed `12846 BYTES FREE` and then `?OM ERROR`.
    # These three live in $A000-$BFFF, which the relocation does not move.
    # The low byte is `$00` in all three, so only the high byte changes.
    # The addresses below are the **instructions**; the byte that changes is the
    # immediate that follows, at `addr + 1` ($B1BE / $B1CB / $B20C).
    # The write itself goes through `put_a()` like every other patch in this bank,
    # so "every change to bank 5 is funnelled through one checked writer" stays true.
    # The two-value tolerance ($6C stock, $7C already 8KB-expanded) cannot be expressed
    # as a single `expect`, so it is checked here and the confirmed value handed on.
    # **$7C is unreachable today** and reviewers have said so three times (2026-08-23/24):
    # fb-relocate.py pins its input to the stock V3 by SHA-256, and check_inputs() then
    # reproduces the relocation byte for byte, so nothing 8KB-expanded can arrive here.
    # Kept deliberately, for one reason: the constants either side of it - the area top
    # ($6F/$7F) and the CLEAR ceiling ($70/$80) - carry exactly the same two-value
    # tolerance and are dead by exactly the same argument. Tightening only the newest of
    # the three would leave the file inconsistent about its own input contract. If the
    # contract is ever widened, all three change together; if it is narrowed, all three go.
    BG_PAGE = 0x9C                                    # $9C00-$9FFF, the last 1KB of 16KB
    bg_was = set()                                    # what was actually there, for the report
    for addr, opcode, why in ((0xB1BD, 0xC9, "BGGET: is there room for the buffer"),
                              (0xB1CA, 0xA9, "BGGET: where to store the screen"),
                              (0xB20B, 0xA9, "BGPUT: where to read the screen back")):
        off = addr - 0xA000
        old_page = resident_a[off + 1]
        bg_was.add(old_page)
        if resident_a[off] != opcode or old_page not in (0x6C, 0x7C):
            sys.exit(f"${addr:04X}: could not find the BGGET/BGPUT buffer "
                     f"({bytes(resident_a[off:off + 2]).hex(' ')}): {why}")
        put_a(addr + 1, [old_page], [BG_PAGE], why)

    # --- Lay out the banks ---------------------------------------------------
    banks = []
    c_low = rel_prg[0xC000 - 0x8000:0xD000 - 0x8000]  # duplicate kept alive during a swap
    for src, _n in PROGRAMS:
        data = bytearray(b"\x00" * 0x1000)
        chunk = orig_prg[src - 0x8000:]
        data[:min(len(chunk), 0x1000)] = chunk[:0x1000]
        banks.append(bytes(c_low) + bytes(data))
    banks.append(b"\xFF" * BANK)                      # bank 4 (unused)
    banks.append(bytes(resident_a))
    banks.append(bytes(bank6))
    banks.append(bytes(bank7))
    assert len(banks) == 8 and all(len(b) == BANK for b in banks)

    header[4] = 4                                     # PRG 64KB (low bits)
    header[6] = (header[6] & 0x0F) | 0x50             # mapper 5 (low nibble)
    header[7] = (header[7] & 0x0F)                    # mapper high bits = 0; keep the
                                                      # NES 2.0 marker and console type
    header[8] = 0x00                                  # mapper high bits / submapper
    header[9] = 0x00                                  # high bits of the PRG/CHR size
    header[10] = (header[10] & 0x0F) | 0x80           # NVRAM 64<<8 = 16KB
    out = bytes(header) + b"".join(banks) + chr_data

    print("built the 16KB MMC5 version")
    print(f"  free area $6000-$9FFF (16,384 bytes)"
          f" / end of area ${old_top:02X}FF -> $9FFF / CLEAR ceiling ${old_clear:02X} -> $A0")
    print(f"  BGGET/BGPUT buffer -> ${BG_PAGE:02X}00-${BG_PAGE + 3:02X}FF"
          f" (was {'/'.join(f'${p:02X}00' for p in sorted(bg_was))};"
          f" the immediates at $B1BE/$B1CB/$B20C,"
          f" i.e. the operands of the instructions at $B1BD/$B1CA/$B20B)")
    print(f"  init ${INIT_ORG:04X} ({len(init.code)} bytes)"
          f" / loader ${LOADER_ORG:04X} ({len(loader.code)} bytes)"
          f" / title graphic ${BG_DEST:04X}-${BG_DEST + 0x3FF:04X}")
    print("  four built-in programs -> $D000 in banks 0-3 (swapped in via $5116)")
    print(f"  PRG {len(banks) * BANK // 1024}KB / CHR {len(chr_data) // 1024}KB / NVRAM 16KB")

    if args.listing:
        for a in (init, loader):
            print()
            for addr, raw, mnem, note in a.listing:
                print(f"  ${addr:04X}  {raw.hex(' '):8s}  {mnem:16s} {note}")

    if args.out:
        open(args.out, "wb").write(out)
        print(f"\nwrote: {args.out}"
              f" ({len(out)} bytes / MD5 {hashlib.md5(out).hexdigest()})")


if __name__ == "__main__":
    main()
