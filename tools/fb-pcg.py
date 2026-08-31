#!/usr/bin/env python3
"""Turn an edited tile sheet into a BASIC program that installs it while BASIC is up.

  $ ./fb-chr.py "Family BASIC V3 (Japan).nes" --sheet tiles.png
  ... edit tiles.png ...
  $ ./fb-pcg.py "Family BASIC V3 (Japan).nes" tiles.png -o pcg.bas

## What this is and what `fb-chr.py --apply` is

`fb-chr.py --apply` changes the picture **in the file**. The machine comes up with the new
art and no program can change it afterwards, because the tiles are read out of ROM.

This writes a **program**. Running it replaces tiles at run time, which is the only way a
program can draw with pictures it made itself - and the only way to have more than one set
of pictures in a session.

⚠️ **It needs a build whose tiles live in RAM.** Today that is the **disk build**
(`fb-fds.py`): the FDS has 8KB of character RAM and the disk loads the tiles into it. The
NROM, MMC5 and VRC7 builds keep their tiles in ROM, so the program runs to the end, prints
nothing wrong, and **the picture does not change**. There is no error to see: writes to
character ROM are dropped by the board, not refused.

## What the program does

Nothing here is a new machine instruction or a patched ROM. Family BASIC already has the
two doors this needs:

* **`POKE`** puts a short 6502 routine into the top of the free area, and
* **`CALL`** jumps to them (it is `JMP ($0400)` after evaluating one argument, so a routine
  that ends in `RTS` comes back to the command dispatcher).

The routine turns NMI off, waits for vblank, points the PPU at one tile and writes sixteen
bytes. NMI has to be off because BASIC's own frame handler writes `$2006` too, and an
interrupt between the two halves of an address loses it. `$32` is BASIC's shadow copy of
`$2000` and **does not carry bit 7**; BASIC adds it, so putting NMI back is
`LDA $32 / ORA #$80`.

Rendering is left on. Sixteen bytes fit inside vblank with room to spare, so there is no
flicker and no need to touch `$2001`.

## Which tiles

Whichever ones differ from the ROM's. The sheet is the same 128x256 PNG `fb-chr.py --sheet`
writes, so the way to say "change these" is to change them in the picture. Every tile that
still matches the ROM is left out of the program - the program is as short as the edit is
small.

## The size check is the machine's own

A tile costs eighteen `DATA` numbers - two for where it goes and sixteen for the picture -
and the program has to fit **as BASIC stores it**, not
as text. So the emitted source is tokenised with the ROM's own reserved-word table
(`fb-basic-to-sav.py`) and measured, rather than estimated from the length of the file.

What the reported `BYTES FREE` does not cover, and this does:

* **which version the dump is.** V3 keeps a single digit in one byte; the V2 series spends
  three on every one of them, so measuring a V2 program with V3's rule understates it. The
  version comes from the ROM's own version string.
* **the page the program gives away.** Line 10 lowers BASIC's ceiling so the routine is out
  of reach, and those bytes are gone from the moment the program runs.
* **the program's own variables.** They are allocated after the program, so one that only
  just fits answers `?OM ERROR` at its first assignment and installs nothing. What the four
  cost was measured with `FRE` rather than guessed.

## The self-check

Before writing anything the tool **reads back the program it just emitted** and runs it:
the `DATA` numbers are handed out in order the way `READ` hands them out, the routine is
executed on a model of the 6502, and what the model's PPU received is compared against the
PNG. So a mistake in the emitted text - a number dropped from a `DATA` line, a tile
addressed at the wrong place, the two bit planes swapped - stops the tool instead of
becoming a program that runs and draws the wrong picture.

This works only while the addresses it uses are below `$8000`, because Family BASIC's
numbers are 16-bit **signed** and `&H8000` and up are negative. A build with a free area
past `$8000` - the MMC5 16KB one - needs that measured before this can target it, and
until then `--top` refuses it rather than emitting arithmetic nobody has run.
"""

import argparse
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fbc = _load("fb_chr", "fb-chr.py")
fbs = _load("fb_basic_to_sav", "fb-basic-to-sav.py")

TILE_BYTES = 16
TILE_COUNT = 512
CHR_SIZE = TILE_BYTES * TILE_COUNT

# The layout inside the 256 bytes the program reserves at the top of the free area.
# `PARAM` is where BASIC leaves the PPU address for the next tile; `BUF` is the tile.
CODE_OFF, PARAM_OFF, BUF_OFF = 0x00, 0x3E, 0x40
RESERVED = 0x100

# How wide a `DATA` line is allowed to get. BASIC accepts 251 characters; this is shorter
# so that a line stays readable on a 32-column screen and so that a test that types the
# program in does not depend on the input buffer's length.
DATA_WIDTH = 120

# Where BASIC keeps the vertical scroll of `SCREEN 0`, and the value the self-check puts
# there. The address is the machine's; the value is this file's, chosen so that a routine
# writing a constant instead of reading `$E4` cannot pass.
# What the disk build reports free, per version. ⚠️ **The two are not the same**: a disk
# built from V3 says `8182 BYTES FREE` and one built from V2.1A says `8126`, so a default
# taken from V3 lets a near-limit V2 program through. The disk build is the only one these
# programs do anything on, which is why one figure per version is enough; `--area` is there
# for any other layout.
# Where the program body starts, per version, on a build whose free area begins at
# `$6000`. V3 puts its system variables on page `$60` to begin with; the disk build moves
# the V2 series' there, which is why this is `$603E` and not the cartridge's `$703E`.
#
# ★ The figures these produce are **checkable against the machine's own**: with the area
# ending at `$7FFF`, `top + 1 - prog - AREA_RESERVED` is 8182 for V3 and 8126 for V2.1A,
# which is exactly what those two disk builds print. Two independent numbers reproduced by
# one rule is why the rule is here rather than a table of constants that stops being right
# the moment `--top` moves.
PROG_START = {"v3": 0x6006, "v2": 0x603E}

# What this program's own variables cost, **asked of the machine**. `PRINT FRE(0)` before
# and after each assignment on a V3 disk build: `I` takes 4 bytes and `A`, `H` and `L` take
# 5 each, and a `FOR` frame costs nothing in the program area (it lives on BASIC's stack).
# Four variables, nineteen bytes.
#
# ⚠️ **Without this the gate accepted a program that cannot run.** The largest it would take
# was 114 tiles, ten bytes under the ceiling; typed into a disk build it answered
# `?OM ERROR` at the first assignment and installed nothing. 113 tiles, seventy-eight bytes
# under, ran and installed all of them. Nineteen sits between the two, which is what the
# `FRE` figures say it should.
#
# It is this program's four variables, not a general allowance: emitting one that used more
# would mean measuring again.
VARIABLES = 19


def free_area(version, top):
    """What the build reports free, for a free area ending at `top`."""
    return top + 1 - PROG_START[version] - fbs.AREA_RESERVED

# Where BASIC keeps the vertical scroll, **per version**, and which screen is displayed.
# `$E4` is `SCREEN 0`'s and `$E5` is `SCREEN 1`'s, selected by `$F0`.
#
# ⚠️ **Only V3 is in this table, because only V3 has been measured.** The addresses come
# from the V3 symbol file. Emitting them for a V2 dump was tried and measured on a disk
# built from V2.1A: the picture stayed displaced by twenty-one per cent of the screen,
# exactly as it does with no restore at all - the addresses are somewhere else there. So a
# V2 program is emitted **without** the restore and the tool says so, rather than writing a
# number nobody has checked into the PPU.
#
# **What retires this**: measuring the three addresses on a V2 dump. Then a row here and
# the restore comes back for it.
SCROLL_BY_VERSION = {"v3": (0xE4, 0xF0)}
BASIC_VSCROLL_VALUE = 0x57
BASIC_SCREEN_VALUE = 1        # the self-check runs on SCREEN 1, so picking $E4 blindly fails


class Fault(Exception):
    """Something the tool checked about its own output did not hold."""


# ---------------------------------------------------------------- the routine

def routine(base, scroll=None):
    """The bytes `CALL` jumps to, assembled for a given base address.

    Reads the PPU address from `base+$3E`/`base+$3F` and sixteen bytes from `base+$40`.

    ⚠️ **The two writes to `$2006` are also the PPU's scroll.** There is no separate VRAM
    pointer on this machine: `$2006` sets the same register the scroll comes out of. BASIC
    does not rewrite the scroll every frame - only when it next draws - so leaving it
    holding a tile address displaces the picture until then. Measured under an emulator:
    nineteen per cent of the screen differed for ten frames, and came back the moment BASIC
    printed. So the routine puts the scroll back itself, from BASIC's own copy.

    The restore rests on three zero-page addresses of BASIC's, documented for V3: `$E4` and
    `$E5` hold the vertical scroll of `SCREEN 0` and `SCREEN 1`, and `$F0` says which of the
    two is displayed, so the routine indexes rather than picking one. The horizontal scroll
    of a text screen is zero. Where a version keeps those has to have been measured before
    the restore is emitted for it at all - see `SCROLL_BY_VERSION` - and measuring them for
    the V2 series is what would let it be emitted there.
    """
    param, buf = base + PARAM_OFF, base + BUF_OFF
    code = [
        0xA5, 0x32,                       # LDA $32        BASIC's shadow of $2000
        0x29, 0x7F,                       # AND #$7F       clear bit 7
        0x8D, 0x00, 0x20,                 # STA $2000      NMI off
        0x2C, 0x02, 0x20,                 # BIT $2002      drop a stale vblank flag
        0x2C, 0x02, 0x20,                 # BIT $2002    <-+
        0x10, 0xFB,                       # BPL -5         wait for vblank
        0xAD, param & 0xFF, param >> 8,   # LDA param      high half of the PPU address
        0x8D, 0x06, 0x20,                 # STA $2006
        0xAD, (param + 1) & 0xFF, (param + 1) >> 8,   # LDA param+1
        0x8D, 0x06, 0x20,                 # STA $2006
        0xA0, 0x00,                       # LDY #$00
        0xB9, buf & 0xFF, buf >> 8,       # LDA buf,Y    <-+
        0x8D, 0x07, 0x20,                 # STA $2007      |
        0xC8,                             # INY            |
        0xC0, 0x10,                       # CPY #16        |
        0xD0, 0xF5,                       # BNE -11      --+
        0xA5, 0x32,                       # LDA $32
        0x09, 0x80,                       # ORA #$80       BASIC adds bit 7 itself
        0x8D, 0x00, 0x20,                 # STA $2000      NMI back on
        0x60,                             # RTS            back to the dispatcher
    ]
    if scroll is not None:
        vscroll, screen = scroll
        code[-8:-8] = [
            0xA9, 0x00,                   # LDA #$00       put the scroll back: X
            0x8D, 0x05, 0x20,             # STA $2005
            0xA6, screen,                 # LDX $F0        which screen is displayed
            0xB5, vscroll,                # LDA $E4,X      that screen's vertical scroll
            0x8D, 0x05, 0x20,             # STA $2005      Y
        ]
    return bytes(code)


# ---------------------------------------------------------------- a 6502 for the above

class Ppu:
    """Enough PPU to answer the routine: a vblank flag, an address latch, 8KB of RAM."""

    def __init__(self, chr_ram):
        self.chr = bytearray(chr_ram)
        self.addr = 0
        self.latch_high = True
        self.reads = 0
        # ★ The **sequence**, not the final state. `AND #$7F` turned into `ORA #$80` leaves
        # NMI enabled through the whole routine and still ends with it on, so a check that
        # only asks "is it on when we return" passes a routine with no guard at all - which
        # is the one thing this routine exists to do (found in review).
        self.nmi_writes = []
        self.scroll_writes = []

    def read(self, addr):
        if addr == 0x2002:
            self.reads += 1
            self.latch_high = True
            # Vblank arrives on the second look, so a routine that does not wait at all
            # cannot pass by accident.
            return 0x80 if self.reads >= 2 else 0x00
        raise Fault(f"the routine read ${addr:04X}, which is not modelled")

    def write(self, addr, value):
        if addr == 0x2000:
            self.nmi_writes.append(bool(value & 0x80))
        elif addr == 0x2006:
            if self.latch_high:
                self.addr = (value << 8) | (self.addr & 0xFF)
            else:
                self.addr = (self.addr & 0xFF00) | value
            self.latch_high = not self.latch_high
        elif addr == 0x2007:
            if self.addr >= CHR_SIZE:
                raise Fault(f"the routine wrote outside the character RAM (${self.addr:04X})")
            self.chr[self.addr] = value
            self.addr += 1
        elif addr == 0x2005:
            # `$2005` and `$2006` share one write toggle. It makes no difference to this
            # routine - every call starts by reading `$2002`, which resets the toggle, and
            # the two `$2005` writes are a pair - but a model that does not have it cannot
            # notice an edit that leaves the toggle set.
            self.scroll_writes.append(value)
            self.latch_high = not self.latch_high
        elif addr == 0x2001:
            raise Fault("the routine touched $2001; it is meant to leave rendering alone")
        else:
            raise Fault(f"the routine wrote ${addr:04X}, which is not modelled")


class Bus:
    """The whole address space as RAM, plus the PPU ports.

    ⚠️ **It covers all 64KB on purpose.** It used to stop at `$8000`, which is where the
    tool refuses to put the routine anyway - so removing that refusal made the model raise
    `IndexError` instead of letting the tool produce something a check could judge. A
    mutation was then "caught" by the crash rather than by the check that names it, which
    is the one thing the mutation mode must not do. A model that never raises on an address
    a 6502 can form leaves the judging to the checks.
    """

    def __init__(self, ppu, shadow_2000):
        self.ram = bytearray(0x10000)
        self.ppu = ppu
        self.ram[0x32] = shadow_2000

    def read(self, addr):
        if 0x2000 <= addr <= 0x3FFF:
            return self.ppu.read(0x2000 + (addr & 7))
        return self.ram[addr & 0xFFFF]

    def write(self, addr, value):
        if 0x2000 <= addr <= 0x3FFF:
            self.ppu.write(0x2000 + (addr & 7), value)
        else:
            self.ram[addr & 0xFFFF] = value


class Cpu:
    """Only the instructions this tool emits. Anything else stops the run."""

    RETURN = 0xFFFF

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
        if op == 0xA9:                                        # LDA #imm
            self.a = self._set(b1); self.pc += 2
        elif op == 0xA5:                                      # LDA zp
            self.a = self._set(r(b1)); self.pc += 2
        elif op == 0xA6:                                      # LDX zp
            self.x = self._set(r(b1)); self.pc += 2
        elif op == 0xB5:                                      # LDA zp,X
            self.a = self._set(r((b1 + self.x) & 0xFF)); self.pc += 2
        elif op == 0xAD:                                      # LDA abs
            self.a = self._set(r(abs_)); self.pc += 3
        elif op == 0xB9:                                      # LDA abs,Y
            self.a = self._set(r((abs_ + self.y) & 0xFFFF)); self.pc += 3
        elif op == 0x29:                                      # AND #imm
            self.a = self._set(self.a & b1); self.pc += 2
        elif op == 0x09:                                      # ORA #imm
            self.a = self._set(self.a | b1); self.pc += 2
        elif op == 0x8D:                                      # STA abs
            w(abs_, self.a); self.pc += 3
        elif op == 0x2C:                                      # BIT abs
            v = r(abs_)
            self.z, self.n = (self.a & v) == 0, bool(v & 0x80)
            self.pc += 3
        elif op == 0xA0:                                      # LDY #imm
            self.y = self._set(b1); self.pc += 2
        elif op == 0xC8:                                      # INY
            self.y = self._set((self.y + 1) & 0xFF); self.pc += 1
        elif op == 0xC0:                                      # CPY #imm
            self._set((self.y - b1) & 0xFF); self.pc += 2
        elif op in (0xD0, 0x10):                              # BNE / BPL
            taken = (not self.z) if op == 0xD0 else (not self.n)
            self.pc += 2
            if taken:
                self.pc = (self.pc + (b1 - 256 if b1 > 127 else b1)) & 0xFFFF
        elif op == 0x60:                                      # RTS
            self.pc = self.RETURN
        else:
            raise Fault(f"${self.pc:04X}: opcode ${op:02X} is not modelled")

    def run(self, limit=100_000):
        for _ in range(limit):
            if self.pc == self.RETURN:
                return
            self.step()
        raise Fault(f"the routine did not reach its RTS in {limit} instructions")


# ---------------------------------------------------------------- emitting

def tile_data(chr_data, tile):
    return bytes(chr_data[tile * TILE_BYTES:(tile + 1) * TILE_BYTES])


def data_lines(numbers, first_line, step):
    """`DATA` statements, filled to `DATA_WIDTH` characters."""
    out, line, items = [], first_line, []
    for value in numbers:
        text = str(value)
        head = f"{line} DATA "
        width = len(head) + sum(len(x) + 1 for x in items) + len(text)
        if items and width > DATA_WIDTH:
            out.append(head + ",".join(items))
            line, items = line + step, [text]
        else:
            items.append(text)
    if items:
        out.append(f"{line} DATA " + ",".join(items))
        line += step
    return out, line


def emit(chr_new, chr_stock, top, scroll):
    """The BASIC source, and the tiles it carries."""
    base = top + 1 - RESERVED
    code = routine(base, scroll)
    changed = [t for t in range(TILE_COUNT)
               if tile_data(chr_new, t) != tile_data(chr_stock, t)]
    if not changed:
        sys.exit("the sheet is identical to the ROM's tiles - there is nothing to install")

    payload = list(code)
    for tile in changed:
        addr = tile * TILE_BYTES
        payload += [addr >> 8, addr & 0xFF] + list(tile_data(chr_new, tile))
    payload.append(-1)                       # what line 40 stops on

    src = [
        f"10 CLEAR &H{base - 1:04X}",
        f"20 FOR I=0 TO {len(code) - 1}:READ A:POKE &H{base:04X}+I,A:NEXT",
        "30 READ H",
        "40 IF H<0 THEN 90",
        f"50 READ L:POKE &H{base + PARAM_OFF:04X},H:POKE &H{base + PARAM_OFF + 1:04X},L",
        f"60 FOR I=0 TO 15:READ A:POKE &H{base + BUF_OFF:04X}+I,A:NEXT",
        f"70 CALL &H{base:04X}",
        "80 GOTO 30",
        "90 END",
    ]
    body, _ = data_lines(payload, 100, 10)
    return "\n".join(src + body) + "\n", changed


# ---------------------------------------------------------------- checking

def read_data_numbers(source):
    """The `DATA` numbers, taken back out of the emitted text.

    Deliberately parsed from the source rather than kept from `emit`: the point of the
    check is that what was **written** installs the picture, so it must not share the list
    that produced it.
    """
    out = []
    for raw in source.splitlines():
        text = raw.strip()
        if not text:
            continue
        head, _, rest = text.partition(" ")
        if not head.isdigit():
            raise Fault(f"a line without a line number reached the output: {raw!r}")
        if not rest.upper().startswith("DATA "):
            continue
        for item in rest[5:].split(","):
            item = item.strip()
            if not (item.lstrip("-").isdigit()):
                raise Fault(f"a DATA item is not a number: {item!r}")
            out.append(int(item))
    return out


def replay(source, chr_stock, top, scroll):
    """Run the emitted program's own numbers and return the character RAM they leave.

    `READ` hands the numbers out in order; that is the only thing about BASIC modelled
    here. Everything else - the routine, the PPU address, the sixteen writes - actually
    runs.
    """
    base = top + 1 - RESERVED
    numbers = read_data_numbers(source)
    code_len = len(routine(base, scroll))
    if len(numbers) < code_len + 1:
        raise Fault("the program carries fewer numbers than its own routine is long")
    code, rest = numbers[:code_len], numbers[code_len:]
    if any(not 0 <= b <= 255 for b in code):
        raise Fault("a byte of the routine is outside 0-255")

    ppu = Ppu(chr_stock)
    installed = []
    i = 0
    while True:
        if i >= len(rest):
            raise Fault("the program's DATA ends without the -1 line 40 stops on")
        head = rest[i]
        if head < 0:
            if i != len(rest) - 1:
                raise Fault("the terminator is not the last number")
            break
        if i + 2 + TILE_BYTES > len(rest):
            raise Fault("a tile's numbers are cut short")
        hi, lo = rest[i], rest[i + 1]
        body = rest[i + 2:i + 2 + TILE_BYTES]
        if any(not 0 <= b <= 255 for b in body) or not 0 <= lo <= 255:
            raise Fault("a tile byte is outside 0-255")
        i += 2 + TILE_BYTES

        # What BASIC's lines 50 and 60 do, then line 70.
        bus = Bus(ppu, shadow_2000=0x08)         # a plausible shadow with bit 7 already off
        if scroll:
            vscroll, screen = scroll
            bus.ram[screen] = BASIC_SCREEN_VALUE
            bus.ram[vscroll] = 0xFF              # SCREEN 0's - must NOT be the one restored
            bus.ram[vscroll + BASIC_SCREEN_VALUE] = BASIC_VSCROLL_VALUE
        bus.ram[base:base + code_len] = bytes(code)
        bus.ram[base + PARAM_OFF] = hi
        bus.ram[base + PARAM_OFF + 1] = lo
        bus.ram[base + BUF_OFF:base + BUF_OFF + TILE_BYTES] = bytes(body)
        ppu.reads = 0
        del ppu.nmi_writes[:]
        del ppu.scroll_writes[:]
        cpu = Cpu(bus, base)
        cpu.run()
        if ppu.reads < 2:
            raise Fault("the routine wrote without waiting for vblank")
        want_scroll = [0x00, BASIC_VSCROLL_VALUE] if scroll else []
        if ppu.scroll_writes != want_scroll:
            raise Fault(f"the routine's writes to $2005 were {ppu.scroll_writes}, and this "
                        f"build wants {want_scroll}")
        if ppu.nmi_writes != [False, True]:
            raise Fault(f"NMI has to go off and then back on, once each; the routine's "
                        f"writes to $2000 were {ppu.nmi_writes}")
        addr = (hi << 8) | lo
        if addr % TILE_BYTES:
            raise Fault(f"${addr:04X} is not the start of a tile")
        installed.append(addr // TILE_BYTES)
    return bytes(ppu.chr), installed


def check(source, chr_new, chr_stock, top, changed, scroll):
    got, installed = replay(source, chr_stock, top, scroll)
    if installed != changed:
        raise Fault(f"the program installs tiles {installed[:8]}... but the sheet changed "
                    f"{changed[:8]}...")
    if got != bytes(chr_new):
        first = next(i for i in range(CHR_SIZE) if got[i] != chr_new[i])
        raise Fault(f"after running its own program the character RAM differs from the "
                    f"sheet, first at byte {first} (tile {first // TILE_BYTES})")


def rom_version(prg):
    """`"v3"` or `"v2"`, from the version string the ROM prints.

    ⚠️ **How BASIC stores a number depends on this.** V3 keeps 0-9 in one byte; the V2
    series spends three on every one of them (`fb-basic-to-sav.py`,
    `SMALL_DIGITS_BY_VERSION`).

    The tile numbers are **not** where this shows: everything after `DATA` is stored as raw
    text, so the payload costs the same either way. It is the driver lines that differ -
    measured at six bytes for the program below, and it does not grow with the number of
    tiles. Small, but it is the number the fit check depends on, and getting it from the
    dump costs nothing.
    """
    m = re.search(rb"NS-HUBASIC V(\d)\.\d[A-Z]?", bytes(prg))
    if not m:
        sys.exit("this dump carries no NS-HuBASIC version string, so how it stores "
                 "numbers is unknown and the program cannot be measured")
    return "v3" if m.group(1) == b"3" else "v2"


def measure(source, prg, version, area_bytes):
    """How many bytes BASIC stores the program in, and whether it fits under `CLEAR`.

    Two things the reported `BYTES FREE` does **not** already account for:

    * **the page the program reserves for itself.** Line 10 lowers BASIC's ceiling by
      `RESERVED` bytes so the routine is out of reach, and that page is gone from the
      moment the program runs.
    * **variables.** They are allocated after the program, so a program that just fits
      still answers `?OM ERROR` when it assigns one. This program assigns four, and what
      they cost was asked of the machine rather than guessed - see `VARIABLES`.
    """
    tokens = fbs.read_token_table(prg)
    stored = fbs.build_program(source, tokens,
                               small_digits=fbs.SMALL_DIGITS_BY_VERSION[version])
    room = area_bytes - RESERVED - VARIABLES
    if len(stored) > room:
        sys.exit(f"the program needs {len(stored)} bytes and only {room} are usable "
                 f"({area_bytes} free, less the {RESERVED} bytes CLEAR reserves for the "
                 f"routine and the {VARIABLES} its own variables take). Change fewer tiles.")
    return len(stored), room


# ---------------------------------------------------------------- command line

def main():
    ap = argparse.ArgumentParser(
        description="write a BASIC program that installs an edited tile sheet at run time")
    ap.add_argument("rom", help="a Family BASIC dump - it supplies the tiles to diff "
                               "against and the reserved-word table")
    ap.add_argument("sheet", help="the edited 128x256 PNG from `fb-chr.py --sheet`")
    ap.add_argument("-o", "--output", metavar="OUT.bas", help="where to write the program")
    ap.add_argument("--snap", action="store_true",
                    help="take the nearest of the four shades instead of refusing strays")
    ap.add_argument("--top", metavar="ADDR", default="0x7FFF",
                    help="last address of the free area (default $7FFF, the disk build)")
    ap.add_argument("--area", metavar="N", type=int, default=None,
                    help="bytes free the build reports. The default follows --top and the "
                         "dump's version, which is 8182 for V3 and 8126 for V2.1A on a "
                         "disk; give it when the layout is something else")
    args = ap.parse_args()

    try:
        top = int(args.top, 0)
    except ValueError:
        sys.exit(f"--top {args.top!r}: not a number. Give the last address of the free "
                 f"area, as $7FFF is written here: 0x7FFF or 32767")
    if not 0x2000 <= top < 0x8000:
        sys.exit(f"--top ${top:04X}: this tool only emits addresses below $8000, because "
                 f"Family BASIC's numbers are 16-bit signed and nobody here has measured "
                 f"what POKE does with a negative one")
    if (top + 1) % 0x100:
        sys.exit(f"--top ${top:04X}: the free area has to end at a page boundary")

    rom = fbc.Rom(args.rom)
    rom.require_chr()
    chr_stock = bytes(rom.chr)
    chr_new = fbc.rows_to_chr(fbc.png_to_rows(args.sheet, args.snap))

    version = rom_version(rom.prg)
    area = args.area if args.area is not None else free_area(version, top)

    scroll = SCROLL_BY_VERSION.get(version)
    source, changed = emit(chr_new, chr_stock, top, scroll)
    try:
        check(source, chr_new, chr_stock, top, changed, scroll)
    except Fault as e:
        sys.exit(f"refusing to write: {e}")
    stored, room = measure(source, rom.prg, version, area)

    base = top + 1 - RESERVED
    print(f"{len(changed)} tile(s) differ from {os.path.basename(args.rom)}: "
          f"{fbc.runs(changed)}")
    print(f"  routine at ${base:04X}, tile buffer ${base + BUF_OFF:04X}, "
          f"CLEAR ${base - 1:04X}")
    print(f"  {len(source.splitlines())} lines / {stored} bytes as {version.upper()} "
          f"stores it ({room - stored} left of {room}: {area} free, less the "
          f"{RESERVED} CLEAR takes back and {VARIABLES} for its own variables)")
    if scroll is None:
        print(f"  ⚠️ no scroll restore: where {version.upper()} keeps its vertical scroll "
              f"has not been measured, so the picture stays displaced until BASIC next "
              f"draws (measured at a fifth of the screen on a V2.1A disk)")
    print("  self-check: the program's own DATA, run on a model of the 6502, "
          "leaves the sheet's tiles in character RAM")

    if args.output:
        open(args.output, "w").write(source)
        print(f"\nwrote: {args.output}")
        print("⚠️ needs a build whose tiles are in RAM - the disk build. On the cartridge "
              "builds it runs and changes nothing.")
    else:
        sys.stdout.write("\n" + source)


if __name__ == "__main__":
    main()
