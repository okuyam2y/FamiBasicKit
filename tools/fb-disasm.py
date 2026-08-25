#!/usr/bin/env python3
"""Disassemble a Family BASIC PRG ROM by recursive descent and enumerate every address reference.

  $ ./fb-disasm.py "Family BASIC V3 (Japan).nes" --xref 8000-9FFF
  $ ./fb-disasm.py "Family BASIC V3 (Japan).nes" --list C000-C0FF

## What it is for

Deciding **whether part of a ROM can be moved to a different address**. To move it you
need both of:

  1. which bytes in that range are instructions and which are data
  2. where, anywhere in the ROM, the 16-bit values pointing into that range live

Searching by byte pattern alone mistakes data for instructions
(see `docs/background/relocation-notes.md`), so **only instructions actually reachable from a vector**
are treated as instructions.

## How it descends

Recursive descent from `RESET` / `NMI` / `IRQ`, adding the targets of `JSR`/`JMP`/branches
as entry points and pinning down instruction boundaries as it goes. **Jump tables reached
via `JMP (indirect)` or an `RTS` trick cannot be followed statically**, so:

  * `--table <range>` declares "this is a table of 16-bit addresses" and adds its contents
    as entry points
  * `--find-tables` proposes runs whose every entry happens to be an in-ROM address

Regions that could not be reached are reported as **"unconfirmed", not "data"**.
That distinction matters: while anything is unconfirmed, you cannot claim to have found
every reference.
"""

import argparse
import sys

# (mnemonic, size in bytes, addressing mode)
#   imp=implied / imm=immediate / zp=zero page / zpx / zpy / abs=absolute / abx / aby
#   ind=indirect / izx=(zp,X) / izy=(zp),Y / rel=relative branch
OPCODES = {}


def _op(code, name, size, mode):
    OPCODES[code] = (name, size, mode)


for code, name, mode in [
    (0x00, "BRK", "imp"), (0x01, "ORA", "izx"), (0x05, "ORA", "zp"), (0x06, "ASL", "zp"),
    (0x08, "PHP", "imp"), (0x09, "ORA", "imm"), (0x0A, "ASL", "imp"), (0x0D, "ORA", "abs"),
    (0x0E, "ASL", "abs"), (0x10, "BPL", "rel"), (0x11, "ORA", "izy"), (0x15, "ORA", "zpx"),
    (0x16, "ASL", "zpx"), (0x18, "CLC", "imp"), (0x19, "ORA", "aby"), (0x1D, "ORA", "abx"),
    (0x1E, "ASL", "abx"), (0x20, "JSR", "abs"), (0x21, "AND", "izx"), (0x24, "BIT", "zp"),
    (0x25, "AND", "zp"), (0x26, "ROL", "zp"), (0x28, "PLP", "imp"), (0x29, "AND", "imm"),
    (0x2A, "ROL", "imp"), (0x2C, "BIT", "abs"), (0x2D, "AND", "abs"), (0x2E, "ROL", "abs"),
    (0x30, "BMI", "rel"), (0x31, "AND", "izy"), (0x35, "AND", "zpx"), (0x36, "ROL", "zpx"),
    (0x38, "SEC", "imp"), (0x39, "AND", "aby"), (0x3D, "AND", "abx"), (0x3E, "ROL", "abx"),
    (0x40, "RTI", "imp"), (0x41, "EOR", "izx"), (0x45, "EOR", "zp"), (0x46, "LSR", "zp"),
    (0x48, "PHA", "imp"), (0x49, "EOR", "imm"), (0x4A, "LSR", "imp"), (0x4C, "JMP", "abs"),
    (0x4D, "EOR", "abs"), (0x4E, "LSR", "abs"), (0x50, "BVC", "rel"), (0x51, "EOR", "izy"),
    (0x55, "EOR", "zpx"), (0x56, "LSR", "zpx"), (0x58, "CLI", "imp"), (0x59, "EOR", "aby"),
    (0x5D, "EOR", "abx"), (0x5E, "LSR", "abx"), (0x60, "RTS", "imp"), (0x61, "ADC", "izx"),
    (0x65, "ADC", "zp"), (0x66, "ROR", "zp"), (0x68, "PLA", "imp"), (0x69, "ADC", "imm"),
    (0x6A, "ROR", "imp"), (0x6C, "JMP", "ind"), (0x6D, "ADC", "abs"), (0x6E, "ROR", "abs"),
    (0x70, "BVS", "rel"), (0x71, "ADC", "izy"), (0x75, "ADC", "zpx"), (0x76, "ROR", "zpx"),
    (0x78, "SEI", "imp"), (0x79, "ADC", "aby"), (0x7D, "ADC", "abx"), (0x7E, "ROR", "abx"),
    (0x81, "STA", "izx"), (0x84, "STY", "zp"), (0x85, "STA", "zp"), (0x86, "STX", "zp"),
    (0x88, "DEY", "imp"), (0x8A, "TXA", "imp"), (0x8C, "STY", "abs"), (0x8D, "STA", "abs"),
    (0x8E, "STX", "abs"), (0x90, "BCC", "rel"), (0x91, "STA", "izy"), (0x94, "STY", "zpx"),
    (0x95, "STA", "zpx"), (0x96, "STX", "zpy"), (0x98, "TYA", "imp"), (0x99, "STA", "aby"),
    (0x9A, "TXS", "imp"), (0x9D, "STA", "abx"), (0xA0, "LDY", "imm"), (0xA1, "LDA", "izx"),
    (0xA2, "LDX", "imm"), (0xA4, "LDY", "zp"), (0xA5, "LDA", "zp"), (0xA6, "LDX", "zp"),
    (0xA8, "TAY", "imp"), (0xA9, "LDA", "imm"), (0xAA, "TAX", "imp"), (0xAC, "LDY", "abs"),
    (0xAD, "LDA", "abs"), (0xAE, "LDX", "abs"), (0xB0, "BCS", "rel"), (0xB1, "LDA", "izy"),
    (0xB4, "LDY", "zpx"), (0xB5, "LDA", "zpx"), (0xB6, "LDX", "zpy"), (0xB8, "CLV", "imp"),
    (0xB9, "LDA", "aby"), (0xBA, "TSX", "imp"), (0xBC, "LDY", "abx"), (0xBD, "LDA", "abx"),
    (0xBE, "LDX", "aby"), (0xC0, "CPY", "imm"), (0xC1, "CMP", "izx"), (0xC4, "CPY", "zp"),
    (0xC5, "CMP", "zp"), (0xC6, "DEC", "zp"), (0xC8, "INY", "imp"), (0xC9, "CMP", "imm"),
    (0xCA, "DEX", "imp"), (0xCC, "CPY", "abs"), (0xCD, "CMP", "abs"), (0xCE, "DEC", "abs"),
    (0xD0, "BNE", "rel"), (0xD1, "CMP", "izy"), (0xD5, "CMP", "zpx"), (0xD6, "DEC", "zpx"),
    (0xD8, "CLD", "imp"), (0xD9, "CMP", "aby"), (0xDD, "CMP", "abx"), (0xDE, "DEC", "abx"),
    (0xE0, "CPX", "imm"), (0xE1, "SBC", "izx"), (0xE4, "CPX", "zp"), (0xE5, "SBC", "zp"),
    (0xE6, "INC", "zp"), (0xE8, "INX", "imp"), (0xE9, "SBC", "imm"), (0xEA, "NOP", "imp"),
    (0xEC, "CPX", "abs"), (0xED, "SBC", "abs"), (0xEE, "INC", "abs"), (0xF0, "BEQ", "rel"),
    (0xF1, "SBC", "izy"), (0xF5, "SBC", "zpx"), (0xF6, "INC", "zpx"), (0xF8, "SED", "imp"),
    (0xF9, "SBC", "aby"), (0xFD, "SBC", "abx"), (0xFE, "INC", "abx"),
]:
    size = {"imp": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "izx": 2, "izy": 2,
            "rel": 2, "abs": 3, "abx": 3, "aby": 3, "ind": 3}[mode]
    _op(code, name, size, mode)

# Instructions after which execution does not fall through to the next byte
TERMINAL = {"JMP", "RTS", "RTI", "BRK"}
BRANCHES = {"BPL", "BMI", "BVC", "BVS", "BCC", "BCS", "BNE", "BEQ"}
ABS16 = {"abs", "abx", "aby", "ind"}


class Rom:
    """Addresses a PRG ROM the way the CPU sees it."""

    def __init__(self, path):
        raw = open(path, "rb").read()
        if raw[:4] != b"NES\x1a":
            sys.exit(f"{path}: no iNES header")
        # The magic matching says nothing about the remaining 12 header bytes existing, and
        # every check below indexes into them. Indexing a short file raises IndexError as a
        # raw traceback instead of the usual one-line exit
        if len(raw) < 16:
            sys.exit(f"{path}: an iNES header is 16 bytes, this file is {len(raw)}")
        # With a trainer the PRG starts 512 bytes later. Without this check the whole
        # disassembly would be shifted and it would report "0 references"
        if raw[6] & 0x04:
            sys.exit(f"{path}: ROMs with a trainer are not supported")
        if raw[9]:
            # NES 2.0 keeps the high bits of the PRG/CHR size in byte 9. Ignoring it
            # would accept an input whose header claims a huge PRG but holds only 32KB
            sys.exit(f"{path}: the NES 2.0 size high bits (byte9 = ${raw[9]:02X}) are not supported")
        prg_size = raw[4] * 16384
        # Cross-check the declared size against the real file length. Silently reading a
        # truncated ROM would throw off `base`, disassembling at entirely wrong addresses
        # and reporting "0 references"
        want = 16 + prg_size + raw[5] * 8192
        if len(raw) != want:
            sys.exit(f"{path}: file length disagrees with the header ({len(raw)} != {want})")
        self.prg = raw[16:16 + prg_size]
        # 32KB maps from $8000; for 64KB and up, assume the last 32KB is what appears
        # at $8000-$FFFF
        self.base = 0x10000 - min(len(self.prg), 0x8000)
        self.view = self.prg[-min(len(self.prg), 0x8000):]

    def __contains__(self, addr):
        return self.base <= addr < 0x10000

    def byte(self, addr):
        return self.view[addr - self.base]

    def word(self, addr):
        return self.byte(addr) | (self.byte(addr + 1) << 8)


def parse_range(text):
    lo, _, hi = text.partition("-")
    return int(lo, 16), int(hi, 16)


class Trace:
    """The result of the recursive descent."""

    def __init__(self, rom):
        self.rom = rom
        self.code = {}          # first address of an instruction -> (name, size, mode, operand)
        self.covered = set()    # addresses of bytes occupied by instructions
        self.entries = {}       # entry address -> where it came from
        self.refs = []          # (address of the referring instruction, name, mode, target)
        self.indirect = []      # the abs of a JMP (abs)
        self.bad = []           # addresses where an undefined opcode was hit

    def add_entry(self, addr, why):
        if addr in self.rom:
            self.entries.setdefault(addr, why)

    def run(self):
        pending = list(self.entries)
        while pending:
            addr = pending.pop()
            while True:
                if addr not in self.rom or addr in self.code:
                    break
                op = self.rom.byte(addr)
                if op not in OPCODES:
                    self.bad.append(addr)
                    break
                name, size, mode = OPCODES[op]
                operand = None
                if mode in ABS16:
                    operand = self.rom.word(addr + 1)
                elif mode == "rel":
                    off = self.rom.byte(addr + 1)
                    operand = addr + 2 + (off - 256 if off >= 0x80 else off)
                elif size == 2:
                    operand = self.rom.byte(addr + 1)
                self.code[addr] = (name, size, mode, operand)
                self.covered.update(range(addr, addr + size))

                if mode in ABS16 or mode == "rel":
                    self.refs.append((addr, name, mode, operand))
                if name == "JSR" or (name == "JMP" and mode == "abs") or name in BRANCHES:
                    if operand in self.rom and operand not in self.code:
                        self.add_entry(operand, f"{name} from ${addr:04X}")
                        pending.append(operand)
                if name == "JMP" and mode == "ind":
                    self.indirect.append((addr, operand))
                if name in TERMINAL:
                    break
                addr += size


def find_tables(rom, min_entries=8):
    """Propose runs whose every entry is an in-ROM address as candidate address tables.

    A hint for the gap left by jump tables that cannot be followed statically.
    **These are candidates, not evidence** — always confirm the reader
    (`JMP (tbl)` or `LDA tbl,X`) before acting on one.
    """
    out = []
    addr = rom.base
    end = 0x10000
    while addr + 1 < end:
        n = 0
        while addr + 2 * (n + 1) <= end:
            v = rom.word(addr + 2 * n)
            if v in rom:
                n += 1
            else:
                break
        if n >= min_entries:
            out.append((addr, n))
            addr += 2 * n
        else:
            addr += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rom")
    ap.add_argument("--entry", action="append", default=[],
                    help="an extra entry point in hex (repeatable)")
    ap.add_argument("--table", action="append", default=[],
                    help="range of a 16-bit address table (e.g. C400-C4FF); its entries become entry points")
    ap.add_argument("--xref", help="list every reference pointing into this range (e.g. 8000-9FFF)")
    ap.add_argument("--list", dest="listing", help="disassemble and print this range")
    ap.add_argument("--find-tables", action="store_true", help="propose candidate address tables")
    ap.add_argument("--gaps", action="store_true", help="print the ranges that could not be reached")
    args = ap.parse_args()

    rom = Rom(args.rom)
    tr = Trace(rom)
    for name, vec in (("RESET", 0xFFFC), ("NMI", 0xFFFA), ("IRQ", 0xFFFE)):
        tr.add_entry(rom.word(vec), name)
    for e in args.entry:
        tr.add_entry(int(e, 16), "given")
    for t in args.table:
        lo, hi = parse_range(t)
        for a in range(lo, hi, 2):
            tr.add_entry(rom.word(a), f"table ${a:04X}")
    tr.run()

    total = 0x10000 - rom.base
    print(f"ROM ${rom.base:04X}-$FFFF ({total} bytes)")
    print(f"  {len(tr.code)} instructions / {len(tr.covered)} bytes covered"
          f" ({100 * len(tr.covered) / total:.1f}%)")
    print(f"  stopped on an undefined opcode at {len(tr.bad)} places")
    print(f"  JMP (indirect): {len(tr.indirect)} places " +
          " ".join(f"${a:04X}->(${o:04X})" for a, o in tr.indirect[:8]))
    print("  coverage per 4KB:")
    for base in range(rom.base, 0x10000, 0x1000):
        n = sum(1 for a in range(base, base + 0x1000) if a in tr.covered)
        print(f"    ${base:04X}-${base + 0xFFF:04X}  {100 * n / 4096:5.1f}%")

    if args.xref:
        lo, hi = parse_range(args.xref)
        hits = [r for r in tr.refs if lo <= r[3] <= hi]
        print(f"\nreferences into ${lo:04X}-${hi:04X} (among reachable instructions): {len(hits)}")
        for addr, name, mode, target in sorted(hits, key=lambda r: r[0]):
            print(f"  ${addr:04X}  {name} {mode:3s} -> ${target:04X}")
        # Byte sequences never reached as instructions may also hold 16-bit values
        # pointing into the same range
        raw = 0
        for a in range(rom.base, 0xFFFF):
            if a in tr.covered:
                continue
            v = rom.word(a)
            if lo <= v <= hi:
                raw += 1
        print(f"  + 16-bit values into the same range inside unconfirmed regions: {raw}"
              f" (data or reference, unknown)")

    if args.gaps:
        print("\nranges that could not be reached (8 bytes or more):")
        start = None
        for a in range(rom.base, 0x10000 + 1):
            inside = a < 0x10000 and a not in tr.covered
            if inside and start is None:
                start = a
            elif not inside and start is not None:
                if a - start >= 8:
                    print(f"  ${start:04X}-${a - 1:04X}  {a - start} bytes")
                start = None

    if args.find_tables:
        print("\ncandidate address tables (runs where every 16-bit value stays in ROM):")
        for addr, n in find_tables(rom):
            print(f"  ${addr:04X}  {n} entries  e.g. " +
                  " ".join(f"${rom.word(addr + 2 * i):04X}" for i in range(min(n, 6))))

    if args.listing:
        lo, hi = parse_range(args.listing)
        print()
        a = lo
        while a <= hi:
            if a in tr.code:
                name, size, mode, operand = tr.code[a]
                raw = " ".join(f"{rom.byte(a + i):02X}" for i in range(size))
                if mode in ABS16:
                    text = {"abs": f"${operand:04X}", "abx": f"${operand:04X},X",
                            "aby": f"${operand:04X},Y", "ind": f"(${operand:04X})"}[mode]
                elif mode == "rel":
                    text = f"${operand:04X}"
                elif mode == "imm":
                    text = f"#${operand:02X}"
                elif mode in ("zp", "zpx", "zpy", "izx", "izy"):
                    text = {"zp": f"${operand:02X}", "zpx": f"${operand:02X},X",
                            "zpy": f"${operand:02X},Y", "izx": f"(${operand:02X},X)",
                            "izy": f"(${operand:02X}),Y"}[mode]
                else:
                    text = ""
                print(f"  ${a:04X}  {raw:8s}  {name} {text}")
                a += size
            else:
                print(f"  ${a:04X}  {rom.byte(a):02X}        .byte")
                a += 1


if __name__ == "__main__":
    main()
