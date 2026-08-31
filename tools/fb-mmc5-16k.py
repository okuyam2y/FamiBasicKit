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

# --- --chr-ram: where the picture goes when it is not in the file --------------
# Bank 4 is the one bank nothing uses (it is `$FF` filled in the normal build) and 8KB is
# exactly the size of the picture, so nothing has to move to make room.
CHR_SRC_BANK = 4
CHR_WINDOW = 0xC000          # the `$5116` window the bank is mapped into while copying
CHR_PTR = 0x00               # zero-page pair used as the source pointer; saved and restored
CHR_RAM_SHIFT = 7            # NES 2.0 header byte 11: 64 << 7 = 8192

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
        # Added to load character RAM at boot (--chr-ram; see emit_chr_load)
        ("BIT", "abs"): 0x2C, ("BPL", "rel"): 0x10,
        ("LDX", "imm"): 0xA2, ("DEX", "imp"): 0xCA,
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


def simulate_init(init, model, prg_banks=None):
    """**Actually execute** the assembled init code and confirm the probe does its job.

    With `prg_banks` (a `--chr-ram` build) it confirms the other half as well: that running
    the init leaves **the whole picture** in character RAM. That check is the reason the
    model has a PPU at all - `tests/` needs an emulator to see a screen, but this runs on
    every build, so a `--chr-ram` ROM cannot be written unless its own init put all 8,192
    bytes where they belong.

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
    a, x, y, zflag, nflag, pc = 0, 0, 0, False, False, init.org
    code = init.code
    # Zero page as a hot start leaves it: **not zero**, so a routine that fails to save and
    # restore what it borrows is caught rather than flattered by an all-zero page
    zp = bytearray((0x5A + i) & 0xFF for i in range(0x100))
    zp_before = bytes(zp)
    steps = 0

    # --- The PPU, only as far as this code touches it -------------------------
    # Modelled from the NESdev wiki's "PPU power up state": `$2000`, `$2001` and `$2006` are
    # **ignored** until the machine has warmed up, while `$2007` works from the start. The
    # warm-up is counted in vblanks seen rather than cycles (the two-loop wait that page
    # calls best practice is exactly what the init does), so leaving the wait out means the
    # address writes are dropped and the picture lands at the address the PPU powered up
    # with - which is what `POWER_ON_VRAM_ADDR` stands in for.
    POWER_ON_VRAM_ADDR = 0x0AB1
    VBLANK_EVERY = 20                     # model steps; only has to be more than one read
    chr_ram = bytearray(0x2000)
    # ⚠️ **The flag reset leaves behind is not a vblank.** NESdev: after a quick power
    # cycle `PPUSTATUS` comes up with bit 7 already set, which is why the canonical wait
    # reads `$2002` once to throw it away *before* the two loops. Modelling that first read
    # as a vblank would make **one** loop enough here while the machine needs two (the
    # first real vblank is at about 27,384 cycles and the writes start taking at 29,658).
    # ⚠️ **The model starts in the state a warm reset leaves behind**: NMI enabled and
    # rendering on. A Famicom reset does not reset the PPU, so `$2000` and `$2001` keep
    # whatever BASIC left in them, and that is the case the init has to survive. Modelling
    # only the cold start is what let two separate warm-start guards go untested, one after
    # the other; this is the shape that covers both.
    # ★ The warm-up "writes are ignored" rule does **not** gate these bits, and that is
    # deliberate: that rule is a property of the PPU having been *reset*, and the scenario
    # this models is the one where it was not. The latch behaviour below stays gated.
    # ⚠️ Gating the address step by warm-up instead would make this model **reject ROMs that
    # work**: the NESdev power-up table gives PPUCTRL as `0000 0000` both at power and after
    # reset, so a cold machine steps by one whether or not anyone writes `$2000`. (Asked
    # twice in review; the answer is in the imported table, not in the argument.)
    # `inc` is **unknown** until the routine writes `$2000`: on a warm start it holds
    # whatever BASIC left, so a routine that streams 8KB without setting it is relying on
    # something it did not establish.
    ppu = {"addr": POWER_ON_VRAM_ADDR, "latch": None, "stale": True, "vbl_at": VBLANK_EVERY,
           "vbl_seen": 0, "nmi": True, "rendering": True, "inc": None}

    def ppu_warm():
        return ppu["vbl_seen"] >= 2

    def ppu_read_status():
        if ppu["stale"]:
            ppu["stale"] = False
            return 0x80
        set_now = steps >= ppu["vbl_at"]
        if set_now:
            ppu["vbl_seen"] += 1
            ppu["vbl_at"] = steps + VBLANK_EVERY
        return 0x80 if set_now else 0x00

    def ppu_write(addr, v):
        if addr == 0x2006:
            if not ppu_warm():
                return                    # dropped, and the latch does not toggle either
            if ppu["latch"] is None:
                ppu["latch"] = v
            else:
                ppu["addr"] = ((ppu["latch"] << 8) | v) & 0x3FFF
                ppu["latch"] = None
        elif addr == 0x2007:
            # ⚠️ **Not while the PPU is drawing.** A write to `$2007` during rendering does
            # not land where the address says; the address is the rendering fetch address.
            # On a warm start rendering is still on until the init turns it off.
            # ★ **Everything the copy depends on is asserted here, not assumed.** Four
            # separate reviews found the model missing one of these in turn - NMI, then
            # rendering, then the address step, then the CHR mapping - because it stored the
            # control registers as opaque bytes and hard-coded the behaviour the routine
            # happened to want. They are decoded now, and a routine that has not established
            # what it relies on is refused rather than flattered.
            if ppu["rendering"]:
                raise AssertionError("$2007 was written while rendering is still on - on a "
                                     "warm start the bytes do not land where the address says")
            if ppu["inc"] is None:
                raise AssertionError("$2007 was written without setting the VRAM address "
                                     "step ($2000 bit 2) - a warm start leaves it unknown")
            if regs.get(0x5101) != 0x00:
                raise AssertionError(f"$2007 was written with $5101 = "
                                     f"{'unset' if 0x5101 not in regs else '$%02X' % regs[0x5101]}"
                                     f", not 0 - only mode 0 maps one 8KB bank across both "
                                     f"pattern tables")
            for reg in (0x5127, 0x512B):
                if regs.get(reg) not in (0x00, None) or reg not in regs:
                    raise AssertionError(f"$2007 was written with ${reg:04X} = "
                                         f"{'unset' if reg not in regs else '$%02X' % regs[reg]}"
                                         f", not bank 0")
            if ppu["addr"] < 0x2000:
                chr_ram[ppu["addr"]] = v
            ppu["addr"] = (ppu["addr"] + ppu["inc"]) & 0x3FFF
        elif addr in (0x2000, 0x2001):
            if addr == 0x2000:
                # ⚠️ **Both directions of the same invariant.** Refusing to move the bank
                # while NMI is on is only half of it: NMI must stay off for the *whole*
                # time the picture is standing where the handler lives, and that is about
                # six frames: two waiting for the PPU and roughly four copying (8,192 bytes
                # at fourteen cycles each is 115,000, and an NTSC frame is 29,780).
                # Turning it back on in the middle is the same hazard.
                if v & 0x80 and (regs.get(0x5116, 0xFF) & 0x7F) != NORMAL_C_BANK:
                    raise AssertionError(
                        "NMI was re-enabled while $5116 still maps a bank other than the "
                        "normal one - the handler is not there yet")
                ppu["nmi"] = bool(v & 0x80)      # see the note where `nmi` is initialised
                ppu["inc"] = 32 if v & 0x04 else 1         # VRAM address step
            if addr == 0x2001:
                ppu["rendering"] = bool(v & 0x18)          # background or sprites enabled
            if ppu_warm():
                regs[addr] = v
        else:
            raise AssertionError(f"${addr:04X}: the model does not know this PPU register")

    def block_at(cpu_addr):
        if 0x6000 <= cpu_addr < 0x8000:
            return blocks[to_block(regs.get(0x5113, 0))], cpu_addr - 0x6000
        if not 0x8000 <= cpu_addr < 0xA000:
            raise AssertionError(f"${cpu_addr:04X}: touched something outside WRAM")
        v = regs.get(0x5114)
        if v is None or v & 0x80:
            raise AssertionError(f"${cpu_addr:04X} was touched but $5114 is not set to RAM")
        return blocks[to_block(v)], cpu_addr - 0x8000

    def read8(addr):
        if addr < 0x100:
            return zp[addr]
        if 0x2000 <= addr < 0x2008:
            if addr != 0x2002:
                raise AssertionError(f"${addr:04X}: the model only answers reads of $2002")
            return ppu_read_status()
        if CHR_WINDOW <= addr < CHR_WINDOW + BANK:
            if prg_banks is None:
                raise AssertionError(f"${addr:04X}: read from the bank window, but this "
                                     f"build has no picture to carry there")
            bank = regs.get(0x5116)
            if bank is None or not bank & 0x80:
                raise AssertionError(f"${addr:04X}: read while $5116 does not select ROM")
            if (bank & 0x7F) not in prg_banks:
                raise AssertionError(f"${addr:04X}: read while $5116 selects bank "
                                     f"{bank & 0x7F}, which holds nothing this model knows")
            return prg_banks[bank & 0x7F][addr - CHR_WINDOW]
        blk, i = block_at(addr)
        return blk[i]

    def write8(addr, v):
        if addr < 0x100:
            zp[addr] = v
        elif 0x2000 <= addr < 0x2008:
            ppu_write(addr, v)
        elif 0x5000 <= addr < 0x6000:
            # ⚠️ **The bank under the NMI handler must not move while NMI is enabled.**
            # `$C000-$DFFF` holds the relocated interpreter, including the NMI handler the
            # RAM trampoline jumps to. Swapping it out with NMI live means the next vblank
            # executes whatever is in the new bank. `build_loader` disables NMI first for
            # this reason; the init has to as well.
            if addr == 0x5116 and (v & 0x7F) != NORMAL_C_BANK and ppu["nmi"]:
                raise AssertionError(
                    f"$5116 moves the bank under the NMI handler (bank {v & 0x7F}) while "
                    f"NMI is still enabled - a warm-start NMI would execute that bank")
            regs[addr] = v
        else:
            blk, i = block_at(addr)
            blk[i] = v

    for steps in range(2_000_000):
        off = pc - init.org
        if not 0 <= off < len(code):
            raise AssertionError(f"execution left the init code (${pc:04X})")
        op = code[off]
        imm = code[off + 1] if off + 1 < len(code) else None
        absa = (code[off + 1] | (code[off + 2] << 8)) if off + 2 < len(code) else None
        if op == 0x78:                                        # SEI
            pc += 1
        elif op == 0xA9:                                      # LDA #imm
            a = imm; zflag = (a == 0); pc += 2
        elif op == 0xA2:                                      # LDX #imm
            x = imm; zflag = (x == 0); pc += 2
        elif op == 0xA0:                                      # LDY #imm
            y = imm; zflag = (y == 0); pc += 2
        elif op == 0xA5:                                      # LDA zp
            a = zp[imm]; zflag = (a == 0); pc += 2
        elif op == 0x85:                                      # STA zp
            zp[imm] = a; pc += 2
        elif op == 0xE6:                                      # INC zp
            zp[imm] = (zp[imm] + 1) & 0xFF; zflag = (zp[imm] == 0); pc += 2
        elif op == 0xB1:                                      # LDA (zp),Y
            base = zp[imm] | (zp[(imm + 1) & 0xFF] << 8)
            a = read8((base + y) & 0xFFFF); zflag = (a == 0); pc += 2
        elif op == 0xC8:                                      # INY
            y = (y + 1) & 0xFF; zflag = (y == 0); pc += 1
        elif op == 0xCA:                                      # DEX
            x = (x - 1) & 0xFF; zflag = (x == 0); pc += 1
        elif op == 0xAD:                                      # LDA abs
            a = read8(absa); zflag = (a == 0); pc += 3
        elif op == 0x2C:                                      # BIT abs
            v = read8(absa); nflag = bool(v & 0x80); zflag = ((a & v) == 0); pc += 3
        elif op == 0x8D:                                      # STA abs
            write8(absa, a); pc += 3
        elif op == 0x48:                                      # PHA
            stack[sp] = a; sp = (sp - 1) & 0xFF; pc += 1
        elif op == 0x68:                                      # PLA
            sp = (sp + 1) & 0xFF; a = stack[sp]; zflag = (a == 0); pc += 1
        elif op == 0xC9:                                      # CMP #imm
            zflag = (a == imm); pc += 2
        elif op in (0xF0, 0xD0, 0x10):                        # BEQ / BNE / BPL rel
            take = zflag if op == 0xF0 else (not zflag) if op == 0xD0 else (not nflag)
            pc += 2
            if take:
                pc += imm - 256 if imm >= 0x80 else imm
        elif op == 0x4C:                                      # JMP abs (to the real reset handler)
            break
        else:
            raise AssertionError(f"${pc:04X}: the model does not know opcode ${op:02X}. "
                                 f"Extend the model whenever the init code grows")
    else:
        raise AssertionError("the init code never terminated")

    if bytes(zp) != zp_before:
        differ = [i for i in range(0x100) if zp[i] != zp_before[i]]
        raise AssertionError(f"[{model}] the zero page was left changed at "
                             f"{', '.join('$%02X' % i for i in differ[:8])} - a hot start "
                             f"has BASIC's state there")
    if prg_banks is not None:
        want = prg_banks[CHR_SRC_BANK]
        same = sum(p == q for p, q in zip(chr_ram, want))
        if same != len(want):
            raise AssertionError(f"[{model}] character RAM holds {same}/{len(want)} of the "
                                 f"picture after the init "
                                 f"(PPU address ended at ${ppu['addr']:04X})")
        back = regs.get(0x5116)
        if back != (0x80 | NORMAL_C_BANK):
            raise AssertionError(f"[{model}] $5116 was left at "
                                 f"{'unset' if back is None else '$%02X' % back}, not the "
                                 f"normal bank (${0x80 | NORMAL_C_BANK:02X})")

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


def emit_chr_load(a):
    """`--chr-ram` only: put the picture in front of the PPU at power-on.

    A `--chr-ram` build declares character RAM instead of character ROM, so **the file has
    no picture in it**. Nothing draws until this runs: the eight kilobytes are carried in
    the spare PRG bank and written through `$2007` here.

    ★ **Where this sits is what makes it small.** It is the tail of the init, so
    `$5101` (CHR mode 0, one 8KB bank) has already been written by the register list above
    and the reset vector already points at the init. Neither is repeated.
    ⚠️ An earlier prototype ran **before** the init - it was reached by pointing the reset
    vector at itself - and wrote all 8,192 bytes into nothing, because `$5101` was still
    whatever the machine powered up with.

    WARNING: **the PPU ignores `$2000`, `$2001` and `$2006` for about 29,658 CPU cycles
    after reset** (NESdev wiki, "PPU power up state"; `$2007` itself works immediately).
    A version without the wait therefore writes 8,192 bytes that go wherever the PPU's
    address happened to be. The two-loop wait below is the form that page gives.

    WARNING: **on a Famicom the reset button does not reset the PPU** (same source), so on
    a hot start rendering can still be on. `$2001` is cleared after the wait, which covers
    both starts: a PPU that *was* reset has rendering off already, and one that was not
    takes the write. (Writing it before the wait is not wrong - a PPU that was not reset
    accepts that too - it is simply not the write that matters.)

    WARNING: **on a hot start the zero page holds BASIC's state.** The two bytes used as
    the source pointer are pushed and pulled back, the way `emit_probe` handles the two
    WRAM bytes it needs.
    """
    # ⚠️ **NMI is already off when this runs** - `build_init` turns it off at the very top,
    # before any bank moves, because the swap below puts the picture where the NMI handler
    # lives. The `$2000` write after the wait below is a different one: it settles the VRAM
    # increment on a cold start, where the early write was ignored.
    a.emit("LDA", "zp", CHR_PTR, "save the zero-page pair used as the source pointer")
    a.emit("PHA", "imp")
    a.emit("LDA", "zp", CHR_PTR + 1)
    a.emit("PHA", "imp")
    a.emit("LDA", "imm", 0x80 | CHR_SRC_BANK, f"bank {CHR_SRC_BANK} (the picture) -> ${CHR_WINDOW:04X}")
    a.emit("STA", "abs", 0x5116)
    a.emit("BIT", "abs", 0x2002, "clear the vblank flag if reset left it set")
    w1 = a.label("chr_vwait1")
    a.emit("BIT", "abs", 0x2002)
    a.emit("BPL", "rel", w1, "first vblank")
    w2 = a.label("chr_vwait2")
    a.emit("BIT", "abs", 0x2002)
    a.emit("BPL", "rel", w2, "second: only now do $2000/$2001/$2006 take")
    a.emit("LDA", "imm", 0x00)
    a.emit("STA", "abs", 0x2001, "rendering off (a Famicom reset leaves the PPU running)")
    a.emit("STA", "abs", 0x2000, "NMI off, VRAM address steps by one")
    a.emit("STA", "abs", 0x2006, "PPU address $0000")
    a.emit("STA", "abs", 0x2006)
    a.emit("STA", "zp", CHR_PTR, f"source = ${CHR_WINDOW:04X}")
    a.emit("LDA", "imm", CHR_WINDOW >> 8)
    a.emit("STA", "zp", CHR_PTR + 1)
    a.emit("LDX", "imm", BANK >> 8, f"{BANK >> 8} pages = {BANK // 1024}KB")
    a.emit("LDY", "imm", 0x00)
    loop = a.label("chr_copy")
    a.emit("LDA", "izy", CHR_PTR)
    a.emit("STA", "abs", 0x2007)
    a.emit("INY", "imp")
    a.emit("BNE", "rel", loop)
    a.emit("INC", "zp", CHR_PTR + 1)
    a.emit("DEX", "imp")
    a.emit("BNE", "rel", loop)
    a.emit("LDA", "imm", 0x80 | NORMAL_C_BANK, "put the normal bank back")
    a.emit("STA", "abs", 0x5116)
    a.emit("PLA", "imp", None, "and the zero page")
    a.emit("STA", "zp", CHR_PTR + 1)
    a.emit("PLA", "imp")
    a.emit("STA", "zp", CHR_PTR)


def build_init(mirror_value, reset_addr, chr_ram=False):
    """Configure MMC5 at power-on, then hand over to the relocated reset handler.

    `$5114` (`$8000-$9FFF`) **is the one value that is not hard-coded**: the right answer
    differs between machines, so `emit_probe` decides it after write access is enabled.
    """
    a = Asm(INIT_ORG)
    a.emit("SEI", "imp", note="disable interrupts (MMC5 IRQ powers up undefined)")
    if chr_ram:
        # ⚠️ **NMI off first, before any bank moves.** `SEI` does not stop NMI. On a warm
        # start `$2000` can still have it enabled - a Famicom reset does not reset the PPU
        # (NESdev wiki, "PPU power up state") - and the NMI vector goes through a RAM
        # trampoline into `$D971`, which lives in the `$C000-$DFFF` window. Every bank this
        # init puts there is therefore a window where an NMI would execute the wrong bytes,
        # and `--chr-ram` widens it to about six frames - two waiting for the PPU and
        # roughly four copying 8,192 bytes through `$2007`. `build_loader`
        # guards the same hazard the same way. Ignored on a cold start, where NMI is
        # already off; the `$2000` write after the warm-up is what settles the increment.
        a.emit("LDA", "imm", 0x00)
        a.emit("STA", "abs", 0x2000, "NMI off before anything moves (a warm reset leaves it on)")
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
    if chr_ram:
        emit_chr_load(a)
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
    ap.add_argument("--chr-ram", action="store_true",
                    help="build a version whose tiles live in RAM, so a running program "
                         "can change the picture (fb-pcg.py). The picture moves into the "
                         "spare PRG bank and the init copies it out at power-on. "
                         "⚠️ verified on FCEUX, on MiSTer and on a Famicom through an "
                         "EverDrive N8 PRO. No MMC5 cartridge was ever made with character "
                         "RAM, so anything else is untried; where the declaration is not "
                         "honoured there is no picture at all, so this is never the default")
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
    if args.chr_ram and len(chr_data) != BANK:
        sys.exit(f"--chr-ram carries the picture in one {BANK // 1024}KB bank, but this "
                 f"input has {len(chr_data)} bytes of it")
    init = build_init(mirror, reset_addr, chr_ram=args.chr_ram)
    loader = build_loader(end_table, resume)
    # **Actually run** the assembled probe on models with different readings of the bank
    # number. Only two machines are available here (MiSTer and an N8 PRO); a real ETROM
    # is not on hand
    picture = {CHR_SRC_BANK: bytes(chr_data)} if args.chr_ram else None
    for model in WRAM_MODELS:
        # ⚠️ **A refusal, not a traceback.** The model raises `AssertionError`, and a tool
        # that lets that out ends with a stack trace - which reads as "the tool broke", not
        # "the ROM would be wrong", and which a mutation suite has to treat as a crash
        # rather than a catch: a refusal has to be non-zero **and** carry the words
        # **and** leave no traceback, or a suite cannot tell the two apart.
        try:
            got, separated = simulate_init(init, model, prg_banks=picture)
        except AssertionError as e:
            sys.exit(f"running the init on the [{model}] model says the ROM would be "
                     f"wrong: {e}")
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
    # ⚠️ **What was validated has to be what gets written.** Everything above checks the
    # assembled objects; this checks the bytes that actually land in the image. Without it,
    # anything that touched the bank between here and the write - a stray `put7`, an
    # off-by-one placement, a later patch - produced a ROM nobody had run.
    for name, org, code in (("init", INIT_ORG, init.code), ("loader", LOADER_ORG, loader.code)):
        off = org - 0xE000
        if bytes(bank7[off:off + len(code)]) != bytes(code):
            sys.exit(f"the {name} in the image is not the {name} that was checked "
                     f"(${org:04X}, {len(code)} bytes)")
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
    # **$7C is unreachable today**, established three times over (2026-08-23/24):
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

    def reloc_bank(addr, size, why):
        """Which bank holds `size` bytes at `addr` in the **relocated** address space.

        Every address handed to this is derived as `stock + delta`, never written out.
        A hard-coded relocated address keeps pointing at the old place the moment
        `--delta` changes, and the patch then lands in the middle of some other
        instruction without anything noticing.
        """
        for bank, base in ((bank6, 0xC000), (bank7, 0xE000)):
            if base <= addr and addr + size <= base + BANK:
                return bank, addr - base
        sys.exit(f"${addr:04X}+{size}: does not fall inside a single relocated bank "
                 f"($C000-$FFFF). Check --delta: {why}")

    def put_reloc(addr, expect, new, why):
        """`put_a` for the relocated banks: refuse to write unless what is already there
        is exactly what this patch was written against."""
        assert len(new) == len(expect), "a patch in place has to keep the same length"
        bank, off = reloc_bank(addr, len(expect), why)
        got = bytes(bank[off:off + len(expect)])
        if got != bytes(expect):
            sys.exit(f"${addr:04X}: differs from expectation "
                     f"({got.hex(' ')} != {bytes(expect).hex(' ')}): {why}")
        bank[off:off + len(new)] = bytes(new)

    # --- SAVE: the cassette header length has to be computed unsigned ---------
    # `SAVE` writes the program's length into the cassette header, and reached it by
    # calling BASIC's general-purpose **signed** 16-bit subtract (`$8E0B` stock), which
    # refuses either operand once bit 15 is set:
    #
    #     $8E1A  LDA $29 / BMI ...     ; high byte of the end address
    #     $8E1E  LDA $2D / BMI ...     ; high byte of the start address
    #     $8E2C  JMP $8EF2             ; -> LDA #$05 / JMP $B237 = ?OV ERROR
    #
    # While the area ended at `$6FFF` (or `$7FFF` once expanded) that branch could never
    # fire. At 16KB the end address crosses `$8000`, reads as negative, and **`SAVE`
    # alone** dies with `?OV ERROR`: `RUN` still works and `BYTES FREE` is still right,
    # so nothing gives it away until a program is written out. Measured on hardware
    # 2026-08-28 - a 7,607-byte program (ending below `$8000`) saves, one crossing
    # `$8000` does not, so it is the boundary and not the size. **This is a 16KB-only
    # problem**: the 8KB build ends at `$7FFF` and cannot reach the branch.
    #
    # WARNING: **do not touch `$8E0B` itself.** It is the subtract the whole interpreter
    # shares, and making it unsigned would change every other subtraction in BASIC. Only
    # this one caller wants an unsigned result, so the caller is what changes: an inline
    # SBC pair. That comes to 19 bytes against the original 21, so two `NOP`s pad it out
    # and it fits **where it already sits** - which it has to, because the last bank has
    # no free run of 32 bytes anywhere to move it to.
    #
    # `$28/$29` (the length) and `$2C/$2D` (the start address) are left holding what the
    # original code left there: the cassette write that follows reads both.
    save_len_at = 0x9D81 + delta          # stock $9D81, inside the moved interpreter
    sub16 = 0x8E0B + delta                # the shared signed subtract, as the caller sees it
    put_reloc(
        save_len_at,
        # LDA $07 / STA $28 / LDA $08 / STA $29 / JSR sub16
        #     / LDA $28 / STA $0512 / LDA $29 / STA $0513
        bytes([0xA5, 0x07, 0x85, 0x28, 0xA5, 0x08, 0x85, 0x29,
               0x20, sub16 & 0xFF, sub16 >> 8,
               0xA5, 0x28, 0x8D, 0x12, 0x05, 0xA5, 0x29, 0x8D, 0x13, 0x05]),
        # SEC / LDA $07 / SBC $2C / STA $28 / STA $0512
        #     / LDA $08 / SBC $2D / STA $29 / STA $0513 / NOP / NOP
        bytes([0x38,
               0xA5, 0x07, 0xE5, 0x2C, 0x85, 0x28, 0x8D, 0x12, 0x05,
               0xA5, 0x08, 0xE5, 0x2D, 0x85, 0x29, 0x8D, 0x13, 0x05,
               0xEA, 0xEA]),
        "SAVE: the length written into the cassette header")

    # The fix above is deliberately in the caller. Check that the shared subtract really
    # was left alone: its bytes must still be exactly what fb-relocate.py produced.
    # Without this, "only the caller changed" is a claim in a comment instead of a fact
    # the build refuses to proceed without.
    sub16_end = 0x8EF4 + delta            # out through the ?OV ERROR stub at $8EF2
    sub16_len = sub16_end - sub16 + 1
    sub_bank, sub_off = reloc_bank(sub16, sub16_len, "BASIC's shared signed subtract")
    if bytes(sub_bank[sub_off:sub_off + sub16_len]) != \
       bytes(rel_prg[sub16 - 0x8000:sub16_end + 1 - 0x8000]):
        sys.exit(f"${sub16:04X}-${sub16_end:04X} was modified. BASIC's shared signed "
                 f"subtract must stay untouched - only the SAVE caller changes")

    # --- Lay out the banks ---------------------------------------------------
    banks = []
    c_low = rel_prg[0xC000 - 0x8000:0xD000 - 0x8000]  # duplicate kept alive during a swap
    for src, _n in PROGRAMS:
        data = bytearray(b"\x00" * 0x1000)
        chunk = orig_prg[src - 0x8000:]
        data[:min(len(chunk), 0x1000)] = chunk[:0x1000]
        banks.append(bytes(c_low) + bytes(data))
    # Bank 4 is unused in the normal build. `--chr-ram` is what it is for: the picture is
    # no longer in the file's CHR section, so it rides here and the init copies it out
    banks.append(bytes(chr_data) if args.chr_ram else b"\xFF" * BANK)
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
    if args.chr_ram:
        header[5] = 0x00                              # no CHR ROM in the file
        header[11] = CHR_RAM_SHIFT                    # 64 << 7 = 8KB of CHR RAM
    out = bytes(header) + b"".join(banks) + (b"" if args.chr_ram else chr_data)

    print("built the 16KB MMC5 version")
    print(f"  free area $6000-$9FFF (16,384 bytes)"
          f" / end of area ${old_top:02X}FF -> $9FFF / CLEAR ceiling ${old_clear:02X} -> $A0")
    print(f"  BGGET/BGPUT buffer -> ${BG_PAGE:02X}00-${BG_PAGE + 3:02X}FF"
          f" (was {'/'.join(f'${p:02X}00' for p in sorted(bg_was))};"
          f" the immediates at $B1BE/$B1CB/$B20C,"
          f" i.e. the operands of the instructions at $B1BD/$B1CA/$B20B)")
    print(f"  SAVE length computed unsigned at ${save_len_at:04X} (stock $9D81)"
          f" - was JSR ${sub16:04X}, the signed subtract that rejected any end"
          f" address past $8000")
    print(f"  init ${INIT_ORG:04X} ({len(init.code)} bytes)"
          f" / loader ${LOADER_ORG:04X} ({len(loader.code)} bytes)"
          f" / title graphic ${BG_DEST:04X}-${BG_DEST + 0x3FF:04X}")
    print("  four built-in programs -> $D000 in banks 0-3 (swapped in via $5116)")
    if args.chr_ram:
        print(f"  picture -> PRG bank {CHR_SRC_BANK} ({len(chr_data)} bytes), copied into "
              f"character RAM by the init")
        print("  ⚠️ tiles in RAM: verified on FCEUX, on MiSTer, and on a Famicom through "
              "an EverDrive N8 PRO. No MMC5 cartridge was ever made this way, so anything "
              "else is untried - and where the declaration is not honoured, nothing draws")
    print(f"  PRG {len(banks) * BANK // 1024}KB /"
          f" CHR {'RAM 8KB (declared, not in the file)' if args.chr_ram else str(len(chr_data) // 1024) + 'KB'}"
          f" / NVRAM 16KB")

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
