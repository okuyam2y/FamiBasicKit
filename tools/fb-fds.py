#!/usr/bin/env python3
"""Put Family BASIC on a Famicom Disk System disk.

  $ ./fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios boot0.rom -o fcbasic.fds
  $ ./fb-fds.py "Family BASIC V3 (Japan).nes"      --bios boot0.rom -o fcbasic3.fds

## Which dumps

**V2.1A** (`8,126 BYTES FREE`) and **V3.0** (`8,182 BYTES FREE`). Which one it is decides
every patch site, so the dump is looked up in `VARIANTS` by SHA-1 and refused if it is not
there rather than patched with the other version's addresses. What differs between the two
was measured off both dumps rather than assumed; the short version is that V3 is the easier
port - it never calls into `$E000-$FFFF` (V2.1A does, in eleven places) and its system
variables are already on page `$60` - but it has `BGGET`/`BGPUT`, whose buffer has to move
with the area, and a battery-backup switch its boot will not start without.

## What this makes

A disk that boots Family BASIC with **8KB of program space** and **saves to and
loads from the disk**. The cartridge gives 2KB and widening it in place caps at 4KB,
because V1/V2 start the program area at `$7000`. A disk system has real RAM across
`$6000-$DFFF`, so the area starts at `$6000` and runs to `$7FFF` - all of it. The
`SAVE`/`LOAD` routines cost the user nothing; they live above `$8000`, over text that
cannot be printed on a disk (see below).

**This is not the roomiest build in this repo** - the MMC5 version reaches 16,374 bytes.
What the disk buys is the medium: CHR in RAM (characters can be rewritten while the
program runs), the FDS sound channel, and saving without a cassette. Choose this for
those, not for space.

## Saving and loading

    SAVE "NAME"        to the disk            LOAD "NAME"
    SAVE               to the disk, as BASIC  LOAD
    SAVE "DSK:NAME"    the disk, said aloud   LOAD "DSK:NAME"
    SAVE "CAS:NAME"    the cassette           LOAD "CAS:NAME"

The saved file's ID is **above** the disk's boot read file code, so turning the machine off
and on comes up exactly like a disk that was never saved to - the free-memory banner
included - and the program comes back when `LOAD` asks for it. See "SAVE and LOAD, on the
disk" below for the address they occupy, what a failure reports, and what is not covered.

## One program to a disk

`LOAD` reads the program that is there; it does not look the name up. **A disk carries one
program.** `SAVE` under a new name replaces it - the name is recorded on the disk (so a
directory listing has something to show) but it does not pick between programs.

Several programs to a disk is a bigger design than it looks: `LoadFiles` takes file IDs,
not names, so the name would have to be resolved through `GetDiskInfo` first, and each
save would need an ID that does not collide. `WriteFile` also drops every file after the
one it writes, so a second slot cannot simply be appended in the middle.

## Prior art, and why this is a rewrite rather than a port

The port was published as a manual procedure in **ファミコン改造マニュアル Vol.2 / Vol.3**
(三才ブックス, 1988; article by 熊沢文幸), and **TakuikaNinja's `FC-DiskBASIC`**
(https://github.com/TakuikaNinja/FC-DiskBASIC) automates it with the CC65 suite. Reading
those is what showed this was worth doing at all.

Everything below is written here, from the FDS specification on the NESdev wiki (*FDS disk
format*, *Family Computer Disk System*, *FDS BIOS*) and from the cartridge's own
disassembly:

  * the disk image builder
  * the power-on routine that replaces the cartridge's (`FDS_INIT`)
  * the interrupt vector table
  * the boot path that goes straight into GAME BASIC without taking the menu away

and the parts of `FC-DiskBASIC` that are its author's own code are **not used**: no save
hook, no `IPL-PRG` filler file, no `BGTOOL` rename. The disk therefore carries three files
instead of four and behaves slightly differently - see "Differences" below.

The `BGTOOL` rename exists over there because that build removes the title menu, and the
menu was the only way into `BG GRAPHIC`. This one leaves the menu answerable instead, so
the graphics editor is still reachable without adding a command.

## Where the patch sites come from

Every site is a constant inside one of Nintendo's instructions, located here by matching
the instruction around it. The addresses were read off `fb-disasm.py`'s output; a site
whose surrounding bytes do not match is refused rather than patched, and the input is
gated on its SHA-1, because a patch table aimed at one dump will happily corrupt another
and still produce a disk that looks fine.

## Differences from the cartridge

The cartridge column differs by version, so it is given per version rather than as one
number that is only true of V2.1A.

| | V2.1A cartridge | V3 cartridge | this disk |
|---|---|---|---|
| program area | `$7000-$77FF` (2KB) | `$6000-$6FFF` (4KB) | `$6000-$7FFF` (**8,126** from V2.1A, **8,182** from V3) |
| system variables | page `$70`, moved to `$60` here | already page `$60` | page `$60` |
| `SAVE` / `LOAD` | cassette | cassette | the disk (`CAS:` still reaches the cassette) |
| at power-on | title menu, choose 1/2/3 | asks for the battery-backup switch | straight into BASIC |
| the title menu | | | V2.1A keeps it - `SYSTEM` returns to it, so `BG GRAPHIC` stays reachable. V3's editor is the `BGTOOL` command and needs no menu |
| characters | CHR-ROM | CHR-ROM | CHR-RAM, loaded from the disk |
| built-in programs | in ROM above `$E000` | `GAME 0`-`GAME 3` | **gone** - that space is the BIOS on a disk, and on V3 `GAME` raises `?SN ERROR` |
| `BGGET` / `BGPUT` | not in this version | buffer at `$6C00` | V3 only, buffer moved to `$7C00` |

## The licence screen data lives inside the BIOS

The BIOS refuses to boot a disk whose `KYODAKU-` file does not match its own copy of the
message byte for byte. `--bios` extracts that copy, and **reads its address and length out
of the comparison loop itself** rather than trusting a number written down here:

    LDA $2007        ; what the disk put in VRAM
    CMP <table>,Y    ; <- the address is this operand
    BNE mismatch
    INY
    CPY #<length>    ; <- the length is this immediate
    BNE loop
"""

import argparse
import hashlib
import re
import sys

# --------------------------------------------------------------------------------------
# Input

# Which dumps are buildable is the `VARIANTS` table further down, keyed by the SHA-1 of the
# 40KB body without the header. `tools/fb-reach.py` pins the V2.1A hash separately and says
# to keep the two in step; that one is
# `8e90d9a6a6090307a7e408d1c1704d09ba8f94fc`.

PRG_BASE = 0x8000
PRG_KEEP = 0x6000        # $8000-$DFFF. $E000-$FFFF is the BIOS on a disk system.
CHR_SIZE = 0x2000

# --------------------------------------------------------------------------------------
# A few 6502 emitters, so the routines below read as code and not as a blob


def lda_i(v): return bytes([0xA9, v])
def sta_z(a): return bytes([0x85, a])
def sta_a(a): return bytes([0x8D, a & 0xFF, a >> 8])
def jmp_a(a): return bytes([0x4C, a & 0xFF, a >> 8])
def jsr_a(a): return bytes([0x20, a & 0xFF, a >> 8])
def word(a): return bytes([a & 0xFF, a >> 8])


PHA, PLA, RTI, NOP = b"\x48", b"\x68", b"\x40", b"\xEA"


# --------------------------------------------------------------------------------------
# A two-pass assembler, small enough to read in one sitting
#
# The power-on routine above is a straight line, so concatenating emitters was enough for
# it. The save hook branches, so it needs labels; hand-counting branch offsets in a routine
# this size is how off-by-one bugs get baked into a ROM that still boots.

class Asm:
    OPS = {
        ("LDA", "imm"): 0xA9, ("LDA", "zp"): 0xA5, ("LDA", "abs"): 0xAD,
        ("LDA", "abx"): 0xBD, ("LDA", "aby"): 0xB9, ("LDA", "zpx"): 0xB5,
        ("STA", "zp"): 0x85, ("STA", "abs"): 0x8D, ("STA", "abx"): 0x9D,
        ("STA", "zpx"): 0x95,
        ("STA", "aby"): 0x99,
        ("CMP", "imm"): 0xC9, ("CMP", "aby"): 0xD9, ("CPX", "imm"): 0xE0, ("CPY", "imm"): 0xC0,
        ("LDX", "imm"): 0xA2, ("LDY", "imm"): 0xA0,
        ("AND", "imm"): 0x29, ("ORA", "imm"): 0x09,
        ("SBC", "imm"): 0xE9, ("ADC", "imm"): 0x69,
        ("JMP", "abs"): 0x4C, ("JSR", "abs"): 0x20,
        ("BEQ", "rel"): 0xF0, ("BNE", "rel"): 0xD0, ("BPL", "rel"): 0x10,
        ("BMI", "rel"): 0x30, ("BCC", "rel"): 0x90, ("BCS", "rel"): 0xB0,
        ("INX", "imp"): 0xE8, ("INY", "imp"): 0xC8, ("DEX", "imp"): 0xCA,
        ("DEY", "imp"): 0x88, ("SEC", "imp"): 0x38, ("CLC", "imp"): 0x18,
        ("PHA", "imp"): 0x48, ("PLA", "imp"): 0x68, ("RTS", "imp"): 0x60,
        ("TAX", "imp"): 0xAA, ("TXA", "imp"): 0x8A, ("TAY", "imp"): 0xA8,
    }
    SIZE = {"imp": 1, "imm": 2, "zp": 2, "zpx": 2, "rel": 2, "abs": 3, "abx": 3,
            "aby": 3}

    def __init__(self, org):
        self.org = org
        self.items = []        # (size, emit(labels) -> bytes)
        self.labels = {}
        self.pc = org

    def _put(self, size, emit):
        self.items.append((size, emit))
        self.pc += size

    def label(self, name):
        if name in self.labels:
            raise ValueError(f"duplicate label {name}")
        self.labels[name] = self.pc
        return self

    def here(self):
        return self.pc

    def op(self, name, mode="imp", operand=None):
        code = self.OPS.get((name, mode))
        if code is None:
            raise ValueError(f"no opcode for {name} {mode}")
        size = self.SIZE[mode]

        def emit(labels, at=self.pc):
            v = labels[operand] if isinstance(operand, str) else operand
            if mode == "imp":
                return bytes([code])
            if mode == "rel":
                d = v - (at + 2)
                # A branch that cannot reach is a bug in the routine, not something to
                # paper over: refuse rather than emit a wrapped offset.
                if not -128 <= d <= 127:
                    raise ValueError(f"branch from ${at:04X} to ${v:04X} is out of range")
                return bytes([code, d & 0xFF])
            if mode in ("imm", "zp", "zpx"):
                if not 0 <= v <= 0xFF:
                    raise ValueError(f"${v:04X} does not fit in one byte")
                return bytes([code, v])
            return bytes([code, v & 0xFF, v >> 8])

        self._put(size, emit)
        return self

    def data(self, raw):
        self._put(len(raw), lambda labels, raw=raw: raw)
        return self

    def assemble(self):
        out = bytearray()
        for _, emit in self.items:
            out += emit(self.labels)
        if len(out) != self.pc - self.org:
            raise ValueError("the two passes disagree on the length")
        return bytes(out)

# --------------------------------------------------------------------------------------
# Power-on
#
# The cartridge's reset handler runs down to `$C432`, where it loads its PPUCTRL shadow,
# flips the NMI-enable bit, and calls a routine at `$EC7A` to write it. On a disk that
# routine is gone - `$E000-$FFFF` is the BIOS - so everything from `$C436` is replaced.
#
# What has to happen instead is set by the BIOS, not by taste (NESdev wiki, *FDS BIOS*):
# `$0100` picks which pseudo-vector an NMI takes, `$0101` the same for IRQ, and the BIOS
# only honours the game's reset vector when `$0102`/`$0103` hold $35/$53. BASIC's own
# reset handler has just cleared `$0000-$07FF`, so all four have to be written again.
#
# Two deliberate choices:
#
#   * **PPUCTRL is written last.** Writing it is what turns NMIs back on, and until BASIC
#     installs its handler at `$00ED` (the first thing `$80AD` does) an NMI would run
#     whatever the RAM clear left there. Enabling last shrinks that window to one JMP.
#   * **`$0101` is `$80`, not a game vector.** That is the BIOS's own "acknowledge and
#     delay" handler. BASIC executes `CLI` early on and the drive keeps generating
#     interrupts, so something has to acknowledge them; letting the BIOS do it means this
#     build needs no IRQ handler of its own.
FDS_INIT_AT = 0xC436
FDS_INIT_LIMIT = 0xC480        # nothing outside reaches into $C436-$C480 (checked with
                               # fb-disasm.py --xref: zero confirmed references)

FDS_INIT = (
    PHA                                     # hold the PPUCTRL value $C432 just computed
    + lda_i(0x0F) + sta_a(0x4015)           # APU: square/triangle/noise on, DMC off
    + lda_i(0x26) + sta_a(0x4025)           # disk port: motor off, read mode, mirroring
    + lda_i(0xC0) + sta_a(0x0100)           # NMI  -> game pseudo-vector #3 ($DFFA)
    + lda_i(0x80) + sta_a(0x0101)           # IRQ  -> the BIOS acknowledges it
    + lda_i(0x35) + sta_a(0x0102)           # reset flag: honour the game's reset vector
    + lda_i(0x53) + sta_a(0x0103)           # reset type: a soft reset by the user
    # The NMI trampoline, the same three bytes `$80AD` writes. Putting it in before the
    # next instruction turns NMIs on closes the window entirely: there is no moment where
    # an NMI can reach `$00ED` while the RAM clear's leftovers are still there.
    + lda_i(0x4C) + sta_z(0xED)             # JMP
    + lda_i(0x0F) + sta_z(0xEE)             #     $880F, BASIC's own NMI handler
    + lda_i(0x88) + sta_z(0xEF)
    + PLA + sta_a(0x2000) + sta_z(0x10)     # PPUCTRL last: this is what re-enables NMI
    # The rest of `$80AD`, minus the two things only the menu needs: `JSR $8131` draws the
    # menu and waits for a key to be released, and `JSR $AF88` reads the choice. `$80F0`
    # is where the ROM itself goes when that choice is '1'.
    + jsr_a(0xAA82)                         # the title graphic
    + jsr_a(0xADA9)
    + jmp_a(0x80F0)                         # GAME BASIC
)
# The IRQ pseudo-vector has to point somewhere even though `$0101` keeps the BIOS in
# charge of interrupts. It points here.
IRQ_STUB_AT = FDS_INIT_AT + len(FDS_INIT)
IRQ_STUB = RTI

# Interrupt vectors. The BIOS dispatches through `$DFF6-$DFFF` instead of `$FFFA-$FFFF`.
# All three NMI slots go to `$00ED`, the RAM trampoline BASIC rewrites whenever it wants a
# different handler; reset goes to BASIC's own handler, the same address the cartridge
# keeps in `$FFFC`.
VECTORS_AT = 0xDFF6
VECTORS = (word(0x00ED)          # NMI #1
           + word(0x00ED)        # NMI #2
           + word(0x00ED)        # NMI #3, the one $0100 selects above
           + word(0xC400)        # RESET
           + word(IRQ_STUB_AT))  # IRQ, unused while $0101 is $80

# V3 does not need `FDS_INIT` spliced into its reset handler the way V2.1A does - it never
# calls into `$E000-$FFFF` at all (measured: 11 such references in V2.1A, none in V3), so
# its own `$80BA` runs unchanged. What it *does* have is a wall of its own: `$81B2-$81CB`
# prints "BACKUP SWITCH WO ON NI SHITE KUDASAI" and loops on `$81F3` until the cartridge's
# battery-backup switch is on. A disk has no such switch, so that loop never ends - the
# machine stays alive (NMI running, rendering on, CHR loaded) with a screen of spaces.
#
# The ceremony exists to write two bytes: `$9E = $59` (the hot-start signature, at `$8183` -
# the only place in the ROM that writes it) and `$6001 = $4C` (the BACKUP marker, `$8198`).
# So this writes them and skips the rest.
#
# ⚠️ **The hot/cold decision has to be kept.** Jumping straight to the cold-start entry
# cost the program on every reset - measured `$07/$08` going `$6048` -> `$6007` across a
# soft reset, where the V2.1A build keeps `$6048`.
V3_FDS_INIT_AT = 0xD600        # in `txtGame0`, dead once `GAME` is patched out below
V3_FDS_INIT_LIMIT = 0xD700

V3_FDS_COLD = (
    lda_i(0x59) + sta_z(0x9E)               # = $8183, the hot-start signature
    + lda_i(0x4C) + sta_a(0x6001)           # = $8198, the BACKUP marker
    + jmp_a(0x825E)                         # RstNoBackupStartBasic
)
# ⚠️ The branch length is computed, not written down. Hand-counting it put the target two
# bytes inside `JMP $825E`'s operand, and the machine ran off into the leftovers of the
# built-in program and sat there. **A memory-only check called that a pass**, because a
# crashed CPU leaves memory alone; it took sampling the PC to see it.
V3_FDS_INIT = (
    lda_i(0x00) + sta_a(0x2000) + sta_a(0x2001)   # = $80BA-$80C1, before $00ED is ours
    + lda_i(0x0F) + sta_a(0x4015)                 # APU: square/triangle/noise on, DMC off
    + lda_i(0x26) + sta_a(0x4025)                 # disk port: motor off, read, mirroring
    + lda_i(0xC0) + sta_a(0x0100)                 # NMI  -> game pseudo-vector #3 ($DFFA)
    + lda_i(0x80) + sta_a(0x0101)                 # IRQ  -> the BIOS acknowledges it
    + lda_i(0x35) + sta_a(0x0102)                 # reset flag
    + lda_i(0x53) + sta_a(0x0103)                 # reset type
    + lda_i(0x4C) + sta_z(0xED)                   # = $80C8-$80D3, the NMI trampoline
    + lda_i(0x71) + sta_z(0xEE)                   #     $8971
    + lda_i(0x89) + sta_z(0xEF)
    + jsr_a(0xB42B) + jsr_a(0xB42B)               # = $80C2-$80C7, two vblank waits
    + jsr_a(0xB3AB) + jsr_a(0xB4A7)               # = $80D4-$80D9
    + lda_i(0x00) + sta_z(0x7C)                   # = $8121, what buSwOff does first
    + bytes([0xA5, 0x9E, 0xC9, 0x59])             # = $8125, LDA $9E / CMP #$59
    + bytes([0xF0, len(V3_FDS_COLD)])             #          BEQ over the cold path
    + V3_FDS_COLD
    + jmp_a(0x815A)                               # hot: HotStart, the program survives
)
V3_IRQ_STUB_AT = V3_FDS_INIT_AT + len(V3_FDS_INIT)
V3_VECTORS = (word(0x00ED) + word(0x00ED) + word(0x00ED)
              + word(V3_FDS_INIT_AT)              # RESET
              + word(V3_IRQ_STUB_AT))

# --------------------------------------------------------------------------------------
# SAVE and LOAD, on the disk instead of the cassette
#
# Both cassette routines begin by calling `$9869`, the parser that reads the optional name.
# **Pointing that one call at a routine here costs two bytes each and no token**: the
# reserved word table does not move, the dispatch table does not move, and everything above
# the hook - `LOAD`'s prologue included - is still Nintendo's code running unchanged.
# Returning normally carries on into the cassette code, which is what makes `CAS:` free.
#
# ## Where the routines live, and why that is free
#
# In the message text of the **built-in conversation program** - "I am a Family Computer",
# "shall I tell your fortune" - at `$C5A1`. 1,522 bytes of it, of which this takes 384.
#
# That program cannot run on a disk. It is driven by an NMI handler at `$CDA7`, and the
# only code that installs it is at `$C444`, **inside the `$C436-$C46F` that `FDS_INIT`
# above replaces**. `tools/fb-reach.py` follows that through: with the cartridge's handler
# added as an entry point the message fetcher at `$D3E0` is reachable and `$D000-$DFFF`
# coverage is 36%; without it, neither, while BG GRAPHIC, the title menu, the cassette
# routines and the keyboard read stay reachable in both.
#
# **Two guards keep that true** (both in `patch_prg`):
#
#   1. the built image is refused if anything still points the NMI trampoline at `$CDA7`
#   2. every message whose text this overwrites is repointed at a `?MSG` marker, so that
#      being wrong prints something visible instead of running 6502 through the character
#      generator
#
# The program area is not touched. Moving the routines is a matter of changing `SAVE_AT`.
#
# ## What it saves, and why that address
#
# It writes from **`$603A`**, not from the start of the program body at `$603E`. Those four
# bytes are the signature and the end-of-program pointer that BASIC's own restore path at
# `$80FB` reads, and `LOAD` hands them to `$9839` to rebuild the pointers - so the file has
# to carry them whichever way it is read back.
#
# What it does **not** do is arrive uninvited: the file's ID is above the disk's boot read
# file code, so the BIOS skips it at power-on and the machine comes up like a disk that was
# never saved to - banner, free-memory count and all. `LOAD` asks for that ID by name and
# gets it. See `DISK_FILE_ID` for why this is worth a constant's worth of explanation.
#
# ## The medium
#
#   SAVE "NAME"        the disk (this build's default)
#   SAVE "DSK:NAME"    the disk, said out loud
#   SAVE "CAS:NAME"    the cassette - handed straight to the cartridge's own routine
#   SAVE               the disk, under the name BASIC
#
# ⚠️ **`CAS:` is untested.** No emulator here has a Data Recorder (checked: neither FCEUX
# nor nestopia-ue), so the only way to try it is on hardware.
#
# ## Errors
#
# BASIC's error table is 18 two-letter codes with immediate code right behind it, so adding
# one means moving the table. Until that is worth doing, a failed save raises the error the
# cassette already has - `?TP ERROR` - and leaves the number that caused it at `ERRNO_AT`:
# `3` write protected, `41` disk full, `240` name too long. `0` means the last transfer
# went through, `255` that nothing has run yet, and `241` that `LOAD` found no program on
# the disk.
#
# ⚠️ **The address to `PEEK` differs by version, because the block does.** It is the last
# byte of the block, so `save_at + 0x17F`: `PEEK(50976)` (`$C720`) on V2.1A and
# `PEEK(54655)` (`$D57F`) on V3. The build prints the right one for the disk it made -
# writing one of them down as "the" address sends a V3 owner to read an unrelated byte
# (found in review).

# ★ Inside the built-in conversation program's message text, which cannot run on a disk.
# `tools/fb-reach.py` is the argument: the only thing that starts that program is the NMI
# handler at `$CDA7`, and the only code that installs it is `$C444` - inside the
# `$C436-$C46F` that `FDS_INIT` above replaces. Two guards keep that true; see `patch_prg`.
# Moving these routines is a matter of changing `SAVE_AT`; the program area is untouched
# and stays at its full `$6000-$7FFF`.
SAVE_AT = 0xC5A1
SAVE_SIZE = 0x0180             # 1522 bytes of message text are dead; this takes 384
MSG_LO_TABLE = 0xC529          # the message index, low bytes  ($D3E2 LDA $C529,Y)
MSG_HI_TABLE = 0xC565          # ...and high bytes             ($D3E7 LDA $C565,Y)
MSG_COUNT = MSG_HI_TABLE - MSG_LO_TABLE
MSG_BLOCK = (0xC5A1, 0xCB93)   # where the strings themselves are

DATA_AT = SAVE_AT + 0x14F      # code below here, data above
DEFAULT_NAME = b"BASIC   "     # what `SAVE` with no argument writes under
DEFAULT_NAME_AT = DATA_AT      # 8 bytes
DSK_TAG_AT = DATA_AT + 8       # 3 bytes
CAS_TAG_AT = DATA_AT + 11      # 3 bytes
ID_LIST_AT = DATA_AT + 14      # 2 bytes: the file to read, then $FF to end the list
DISK_ID_AT = DATA_AT + 16      # 10 bytes, the disk this build makes
FHDR_AT = DATA_AT + 26         # the 17-byte file header structure the BIOS wants
MARKER = b"?MSG\xFF"           # what a message that should be impossible now prints
MARKER_AT = DATA_AT + 43
ERRNO_AT = SAVE_AT + 0x17F     # the last byte of the block


def block_layout(v):
    """Where the pieces of the block sit **for one variant**.

    ⚠️ The constants directly above are V2.1A's, and their names do not say so. The block
    moves with the version - `$C5A1` on V2.1A, `$D400` on V3 - so every offset from
    `SAVE_AT` moves with it. Reading `FHDR_AT` off the module and applying it to a V3
    build reads an unrelated address: `$C716` against the `$D569` it is actually at.

    That is not hypothetical. The save test did exactly that the moment a check was
    added that needed the file header, and reported the name as `39C745C74BC751C7`
    (whatever was at `$C716`) instead of `4142434445464748`. So callers ask for a
    variant's layout rather than reaching for a constant that is only true of one dump.
    """
    save_at = v["save_at"]
    data_at = save_at + 0x14F
    return {
        "save_at": save_at,
        "data_at": data_at,
        "default_name_at": data_at,
        "dsk_tag_at": data_at + 8,
        "cas_tag_at": data_at + 11,
        "id_list_at": data_at + 14,
        "disk_id_at": data_at + 16,
        "fhdr_at": data_at + 26,
        "marker_at": data_at + 43,
        "errno_at": save_at + 0x17F,
    }

# **Above** the disk's boot read file code (`BOOT_READ_FILE_CODE`), so the BIOS does not
# read the saved program at power-on. `LOAD` names this ID explicitly in the list it hands
# `LoadFiles`, and an explicit list ignores the boot code entirely (measured from the BIOS: a
# list of `$FF` reads every ID from the boot code on, where naming one reads only that one), so
# saving and loading are unaffected.
#
# It used to be 5, i.e. below the code, which meant every power-on restored the program
# without being asked. Convenient, but it also meant BASIC took its restore path at `$8109`
# and jumped to `$820B`, straight past the `JSR $AA09` at `$8205` that prints
# `8126 BYTES FREE` - so from the first save onwards that disk never showed the free-memory
# banner again, and `SAVE` rewrites the signature every time, so there was no way back.
# `PRINT FRE(0)` still answers the same question, but losing the banner was not a decision
# anyone made. Restoring on demand (`LOAD`) rather than automatically gives the banner back.
DISK_FILE_ID = 0x10
DISK_FILE_POS = 3              # the disk carries three files; the program is the fourth
ERR_NAME_TOO_LONG = 0xF0       # no BIOS number collides: theirs stop at $31
ERR_NEVER_RAN = 0xFF           # what the error byte holds before the first save

ERR_NOT_FOUND = 0xF1           # LOAD found no program on the disk

BIOS_WRITE_FILE = 0xE239       # read off the BIOS disassembly, then confirmed by running it
BIOS_LOAD_FILES = 0xE1F8       # ...it sets A itself, so both arguments are embedded
NAME_BUF = 0x0580              # where BASIC's own argument parser leaves the name
BASIC_PARSE_NAME = 0x9869      # ...and the parser itself
BASIC_SAVE_PARSE = 0x978C      # `JSR $9869` inside the cassette SAVE - the hook site
BASIC_LOAD_PARSE = 0x9809      # `JSR $9869` inside the cassette LOAD - the hook site
BASIC_LOAD_TAIL = 0x9839       # the cassette LOAD's own fixup, given `$AD/$AE` = the end
BASIC_TP_ERROR = 0x9864        # LDA #$11 / JMP $A98F
BASIC_NMI_OFF = 0xB3EB         # LDA $32 / STA $2000   - what the cassette code calls
BASIC_NMI_ON = 0xB3F1          # LDA $32 / ORA #$80 / STA $2000


def disk_routines(v, disk_id):
    """`SAVE` and `LOAD`, in place of the cassette ones.

    Both are hooked the same way: the cassette routine's own `JSR $9869` - the call that
    parses the optional name - is pointed here instead. That means **the routine above the
    hook is Nintendo's, unchanged**: `LOAD`'s prologue, whatever the dispatch table does,
    all of it still runs. Returning normally hands control back to the cassette code, so
    `CAS:` costs nothing; the disk path drops that return address and never comes back.

    Hooking the dispatch table would have worked for `SAVE` and meant copying `LOAD`'s
    prologue byte for byte, which is a copy that can rot.
    """
    if len(disk_id) != 10:
        raise ValueError(f"the disk id CheckDiskHeader compares is 10 bytes, got {len(disk_id)}")

    # Everything the two dumps disagree about, bound once here. The body below then reads
    # the same names it always did, so adding V3 did not move a line of the routine.
    at = block_layout(v)
    SAVE_AT, DATA_AT = at["save_at"], at["data_at"]
    DEFAULT_NAME_AT, DSK_TAG_AT, CAS_TAG_AT = (
        at["default_name_at"], at["dsk_tag_at"], at["cas_tag_at"])
    ID_LIST_AT, DISK_ID_AT, FHDR_AT = at["id_list_at"], at["disk_id_at"], at["fhdr_at"]
    MARKER_AT, ERRNO_AT = at["marker_at"], at["errno_at"]
    ARG_FLAG = v["arg_flag"]                 # ⚠️ $B5 on V2.1A, $EA on V3
    BASIC_PARSE_NAME = v["basic_parse_name"]
    BASIC_LOAD_TAIL, BASIC_TP_ERROR = v["basic_load_tail"], v["basic_tp_error"]
    BASIC_NMI_OFF, BASIC_NMI_ON = v["basic_nmi_off"], v["basic_nmi_on"]

    # ⚠️ **The two versions lay the program out differently, and it is not a shift of one
    # base.** V2.1A puts a two-byte signature at `$603A`, the end pointer at `$603C/$603D`
    # and the body at `$603E`; V3 puts a one-byte signature at `$6001`, the end pointer at
    # `$6002/$6003` and the body at `$6006` (`docs/sav-format.md`, and measured on the
    # running V3 disk: `$05/$06` reads `$6006`). Saving V2.1A's layout on a V3 machine
    # writes from 52 bytes past the program and computes a length that underflows for a
    # short one. Found in review - and the save test did not catch
    # it because it built its program in V2's layout and poked it where the code looked.
    SAVE_FROM = v["save_from"]               # the first byte written to the disk
    END_PTR_AT = v["end_ptr_at"]             # where BASIC's power-on path reads the end
    SIGNATURE = v["signature"]               # [(address, byte), ...]
    a = Asm(SAVE_AT)
    o = a.op

    # -- which medium the argument names --------------------------------------------
    # Returns carry set for the cassette, clear for the disk, and X as the offset of the
    # name inside `$0580` (0, or 4 past a tag).
    a.label("medium")
    o("LDX", "imm", 0)
    o("LDA", "zp", ARG_FLAG)
    o("BEQ", "rel", "to_disk")               # no argument: the disk, under the default name
    o("LDA", "abs", NAME_BUF + 3)
    o("CMP", "imm", ord(":"))
    o("BNE", "rel", "to_disk")               # no tag: the whole thing is the name
    o("LDY", "imm", 2)
    a.label("dsk_cmp")
    o("LDA", "aby", NAME_BUF)
    o("CMP", "aby", DSK_TAG_AT)
    o("BNE", "rel", "cas_try")
    o("DEY")
    o("BPL", "rel", "dsk_cmp")
    o("LDX", "imm", 4)
    o("BNE", "rel", "to_disk")               # always: X is 4

    a.label("cas_try")
    o("LDY", "imm", 2)
    a.label("cas_cmp")
    o("LDA", "aby", NAME_BUF)
    o("CMP", "aby", CAS_TAG_AT)
    o("BNE", "rel", "to_disk")               # neither tag: it is just a name, so the disk
    o("DEY")
    o("BPL", "rel", "cas_cmp")
    # The cassette. Slide the name down over the tag first, because the cartridge's
    # routines copy `$0580` as it stands.
    a.label("cas_shift")
    o("LDA", "abx", NAME_BUF + 4)
    o("STA", "abx", NAME_BUF)
    o("INX")
    o("CPX", "imm", 12)
    o("BNE", "rel", "cas_shift")
    o("LDA", "imm", ord(" "))
    a.label("cas_pad")
    o("STA", "abx", NAME_BUF)
    o("INX")
    o("CPX", "imm", 16)
    o("BNE", "rel", "cas_pad")
    o("SEC")
    o("RTS")
    a.label("to_disk")
    o("CLC")
    o("RTS")

    # -- what a failure does ---------------------------------------------------------
    a.label("fail")                          # A holds the number to leave behind
    o("STA", "abs", ERRNO_AT)
    o("JMP", "abs", BASIC_TP_ERROR)

    # -- the zero page, saved and restored around every BIOS call --------------------
    # The BIOS uses $00-$0F as scratch (measured from the BIOS) and BASIC keeps its own
    # state there - the end-of-program pointer at `$07/$08` among it. Without this the
    # transfer works and the interpreter comes back to a zero page that is not its own: on
    # hardware that was `?SN ERROR IN 65376` right after a `SAVE` that had written the disk.
    # ⚠️ These two cannot be subroutines. A `JSR` leaves its return address on the stack,
    # and both of them push (or pull) sixteen bytes across it. They are written out at each
    # call site instead, which the emulator test found the hard way.
    def zp_save(tag):
        o("LDX", "imm", 0)
        a.label(tag)
        o("LDA", "zpx", 0x00)
        o("PHA")
        o("INX")
        o("CPX", "imm", 0x10)
        o("BNE", "rel", tag)

    def after_bios(tag):
        """Leaves the BIOS's number in A (and in `ERRNO_AT`), zero page back as it was."""
        o("STA", "abs", ERRNO_AT)
        o("LDX", "imm", 0x0F)
        a.label(tag)
        o("PLA")
        o("STA", "zpx", 0x00)
        o("DEX")
        o("BPL", "rel", tag)
        o("LDA", "abs", ERRNO_AT)

    # NMI off. The BIOS moves each byte from an IRQ handler while `$E7A3` spins, and an
    # interrupt sequence sets `I`, so a long NMI handler holds the disk's IRQ off past the
    # next byte - about 150 cycles at 96.4kbit/s.
    #
    # **The BIOS expects to be called with NMI already off.** Its own vblank wait
    # (`VINTWait` at `$E1B2`) turns NMI *on*, spins until the frame arrives, and turns it
    # back *off* before returning - which only makes sense in a world where NMI is off the
    # rest of the time. Nothing in the file I/O path touches `$2000` at all. A disk game
    # written for the disk therefore has nothing to do here; **Family BASIC is a cartridge
    # program**, and it leaves NMI on permanently because that is where its keyboard scan
    # lives. So the caller has to do it, and the cartridge's own cassette code does exactly
    # the same thing before a tape transfer.
    #
    # ⚠️ **No emulator or FPGA core here can show that it is needed.** Taking this call out
    # passes every test, on FCEUX *and* on MiSTer - because in both the disk waits for the
    # CPU. MiSTer's `FDS.sv` only advances `diskpos` inside
    # `if ((read_disk_d & ~write_en) | write_disk_d)`, so a late read loses nothing.
    # A real Disk System, where the medium actually turns, is the only thing that would say.

    # -- SAVE ------------------------------------------------------------------------
    a.label("save_hook")                     # `$978C` calls here instead of the parser
    o("JSR", "abs", BASIC_PARSE_NAME)
    o("JSR", "abs", "medium")
    o("BCC", "rel", "save_disk")
    o("RTS")                                 # the cassette: let Nintendo's routine carry on
    a.label("save_disk")
    o("PLA")                                 # drop the return into the cassette routine
    o("PLA")

    # the file name: eight characters, space padded. Everything else in the header is a
    # constant, written into the block at build time.
    o("LDY", "imm", 0)
    o("LDA", "zp", ARG_FLAG)
    o("BEQ", "rel", "name_default")
    # **`$00` ends the name, and nothing else.** The parser fills sixteen spaces and then
    # writes the name over the front with a `$00` right after it ($98A1 and $9897 in the
    # ROM), so the terminator is always there and always exact. Treating the first *space*
    # as the end as well meant `SAVE "A B"` put `A` on the disk and dropped the rest -
    # a name the user typed, saved under a different one, silently. Measured in the
    # emulator: the header came back `41 20 20 20 20 20 20 20` (found in review).
    a.label("name_copy")
    o("LDA", "abx", NAME_BUF)
    o("BEQ", "rel", "name_pad")              # the parser's terminator
    o("STA", "aby", FHDR_AT + 1)
    o("INX")
    o("INY")
    o("CPY", "imm", 8)
    o("BNE", "rel", "name_copy")
    # A ninth character is one too many. BASIC's parser has no length check of its own -
    # it writes as many bytes as the string has - so refusing here is the only refusal.
    #
    # Trailing spaces do not count: the header pads to eight with them anyway, so
    # `SAVE "ABCDEFGH   "` is the same name as `SAVE "ABCDEFGH"`. Anything else before the
    # `$00` does count - `SAVE "ABCDEFGH X"` used to stop at that space and be accepted,
    # truncated, which is the same silent renaming from the other end (found in review).
    # **It stops at `$0590`.** That is where the parser's terminator lives, so a name of
    # 16 characters or fewer is always decided inside the buffer. Without the limit the
    # scan followed a 17-character name's own terminator past `$0590` and read bytes
    # outside it - not a corruption, but it made the accept-or-refuse decision depend on
    # memory beyond the buffer, and it made this build the second thing that indexes
    # there, which the published explanation of why the overrun is harmless says nothing
    # does (found in review). Reaching the limit means the name is longer than the
    # buffer, which is too long whatever follows.
    a.label("name_check")
    o("LDA", "abx", NAME_BUF)
    o("BEQ", "rel", "size")                  # the terminator: nothing more to see
    o("CMP", "imm", ord(" "))
    o("BNE", "rel", "name_toolong")
    o("INX")
    o("CPX", "imm", 17)                      # past $0590 is past the buffer
    o("BEQ", "rel", "name_toolong")
    o("JMP", "abs", "name_check")
    a.label("name_toolong")
    o("LDA", "imm", ERR_NAME_TOO_LONG)
    o("JMP", "abs", "fail")

    a.label("name_pad")
    o("LDA", "imm", ord(" "))
    a.label("name_pad_loop")
    o("STA", "aby", FHDR_AT + 1)
    o("INY")
    o("CPY", "imm", 8)
    o("BNE", "rel", "name_pad_loop")
    o("BEQ", "rel", "size")                  # always: CPY left Z set

    a.label("name_default")                  # SAVE with no argument, or after a named one
    o("LDX", "imm", 7)
    a.label("name_default_loop")
    o("LDA", "abx", DEFAULT_NAME_AT)
    o("STA", "abx", FHDR_AT + 1)
    o("DEX")
    o("BPL", "rel", "name_default_loop")

    # how much to write, and the pointer the power-on restore reads. Both want `$07/$08`,
    # so they are written from the same load; `STA` leaves the carry alone, which is what
    # lets the subtraction run through the middle of it.
    a.label("size")
    o("SEC")
    o("LDA", "zp", 0x07)                     # BASIC's end-of-program pointer
    o("STA", "abs", END_PTR_AT)
    o("SBC", "imm", SAVE_FROM & 0xFF)
    o("STA", "abs", FHDR_AT + 11)
    o("LDA", "zp", 0x08)
    o("STA", "abs", END_PTR_AT + 1)
    o("SBC", "imm", SAVE_FROM >> 8)
    o("STA", "abs", FHDR_AT + 12)
    # the signature BASIC's own power-on path checks: two bytes on V2.1A ($80D7 writes
    # them before returning to the title), one on V3 ($8198, checked at $812E)
    for at, byte in SIGNATURE:
        o("LDA", "imm", byte)
        o("STA", "abs", at)

    zp_save("save_zp_loop")
    o("JSR", "abs", BASIC_NMI_OFF)
    o("LDA", "imm", DISK_FILE_POS)
    o("JSR", "abs", BIOS_WRITE_FILE)
    a.data(word(DISK_ID_AT))                 # the arguments are embedded after the JSR
    a.data(word(FHDR_AT))
    o("PHA")                                 # hold the error number across the NMI call
    o("JSR", "abs", BASIC_NMI_ON)
    o("PLA")
    after_bios("save_zp_back")               # 0 here means the save went through
    o("BEQ", "rel", "save_done")
    o("JMP", "abs", "fail")
    a.label("save_done")
    o("RTS")

    # -- LOAD ------------------------------------------------------------------------
    a.label("load_hook")                     # `$9809` calls here instead of the parser
    o("JSR", "abs", BASIC_PARSE_NAME)
    o("JSR", "abs", "medium")
    o("BCC", "rel", "load_disk")
    o("RTS")                                 # the cassette
    a.label("load_disk")
    o("PLA")
    o("PLA")
    zp_save("load_zp_loop")
    o("JSR", "abs", BASIC_NMI_OFF)
    o("JSR", "abs", BIOS_LOAD_FILES)         # it sets A itself: both arguments embedded
    a.data(word(DISK_ID_AT))
    a.data(word(ID_LIST_AT))
    o("PHA")
    o("JSR", "abs", BASIC_NMI_ON)
    o("PLA")
    after_bios("load_zp_back")               # Y survives: nothing on this path touches it
    o("BNE", "rel", "load_failed")
    o("CPY", "imm", 1)                       # ...and Y is how many files it read
    o("BCS", "rel", "load_ok")
    o("LDA", "imm", ERR_NOT_FOUND)
    a.label("load_failed")
    o("JMP", "abs", "fail")
    a.label("load_ok")
    # Hand the cassette LOAD's own fixup the end of what was read. It rebuilds `$07/$08`
    # and the variable pointers from `$AD/$AE` and returns to the dispatcher.
    o("LDA", "abs", END_PTR_AT)
    o("STA", "zp", 0xAD)
    o("LDA", "abs", END_PTR_AT + 1)
    o("STA", "zp", 0xAE)
    o("JMP", "abs", BASIC_LOAD_TAIL)

    code = a.assemble()
    if SAVE_AT + len(code) > DATA_AT:
        raise ValueError(f"the routines are {len(code)} bytes and only "
                         f"{DATA_AT - SAVE_AT} fit before their data")

    # The parts of the header that never change - the structure as the BIOS lays it out,
    # measured rather than taken from a description. The name
    # starts out as the default so a `SAVE` before anything else still has one.
    header = (bytes([DISK_FILE_ID]) + DEFAULT_NAME
              + word(SAVE_FROM)              # where it loads back to
              + word(0)                      # size: written at save time
              + bytes([KIND_PRG])            # the kind the disk records
              + word(SAVE_FROM)              # where the bytes are read from
              + bytes([0]))                  # 0 = CPU RAM, not the PPU
    assert len(header) == 17, len(header)

    # One run of bytes, laid down once. Placing each piece separately invites the constants
    # above and the placement below to drift apart, and a header full of zeroes still
    # builds a disk that looks fine.
    data = (DEFAULT_NAME + b"DSK" + b"CAS"
            + bytes([DISK_FILE_ID, 0xFF])    # the file list LoadFiles reads
            + disk_id + header + MARKER)
    for name, at, want in (("DSK", DSK_TAG_AT, DEFAULT_NAME_AT + len(DEFAULT_NAME)),
                           ("CAS", CAS_TAG_AT, DSK_TAG_AT + 3),
                           ("file list", ID_LIST_AT, CAS_TAG_AT + 3),
                           ("disk id", DISK_ID_AT, ID_LIST_AT + 2),
                           ("file header", FHDR_AT, DISK_ID_AT + len(disk_id)),
                           ("marker", MARKER_AT, FHDR_AT + 17)):
        if at != want:
            raise ValueError(f"{name} is declared at ${at:04X} but lands at ${want:04X}")
    if DATA_AT + len(data) > ERRNO_AT:
        raise ValueError(f"the data runs to ${DATA_AT + len(data) - 1:04X}, over the error "
                         f"byte at ${ERRNO_AT:04X}")

    block = bytearray(SAVE_SIZE)
    block[ERRNO_AT - SAVE_AT] = ERR_NEVER_RAN   # so "not run yet" is not read as "went through"
    block[0:len(code)] = code
    block[DATA_AT - SAVE_AT:DATA_AT - SAVE_AT + len(data)] = data
    return bytes(block), len(code), a.labels


# --------------------------------------------------------------------------------------
# Patch sites in Nintendo's code
#
# BASIC keeps the top of its program area, and the page its system variables live on, in
# constants scattered through the boot and editor code. Each entry is
# (name, CPU address of the byte, bytes expected around it, replacement).

# The page holding BASIC's system variables at `$703A-$7040`: thirteen absolute operands
# and two `LDA #$70` that build a pointer to them. Moving the area to `$6000` moves these.
PROGRAM_PAGE_OLD = 0x70
PROGRAM_PAGE_NEW = 0x60
# (address of the $70 byte, the two bytes before it) - the pair pins the instruction, so a
# stray $70 elsewhere in the ROM cannot be mistaken for one of these.
PROGRAM_PAGE_SITES = [
    (0x80DB, b"\x8D\x3A"),   # STA $703A
    (0x80E0, b"\x8D\x3B"),   # STA $703B
    (0x80E7, b"\x8D\x3C"),   # STA $703C
    (0x80EC, b"\x8D\x3D"),   # STA $703D
    (0x80FD, b"\xAD\x3A"),   # LDA $703A
    (0x8104, b"\xAD\x3B"),   # LDA $703B
    (0x810B, b"\xAD\x3C"),   # LDA $703C
    (0x8110, b"\xAD\x3D"),   # LDA $703D
    (0x8118, b"\x05\xA9"),   # STA $05 / LDA #$70   (pointer to $703E)
    (0x8210, b"\x8D\x3A"),   # STA $703A
    (0x8213, b"\x8D\x3B"),   # STA $703B
    (0x838D, b"\x8D\x3E"),   # STA $703E
    (0x8390, b"\x8D\x3F"),   # STA $703F
    (0x8393, b"\x8D\x40"),   # STA $7040
    (0x83A5, b"\x21\xA9"),   # STX $21 / LDA #$70   (pointer to $703E)
]

PATCHES = [
    # The end of the program area, and the ceiling `CLEAR` refuses to go above. They are
    # separate constants: raising one does not raise the other. Both go to the top of the
    # RAM now that the routines live above `$8000` instead of in the program area.
    ("end of area", 0x856F, b"\xA9\x77\x85", b"\xA9\x7F\x85"),          # LDA #$7F / STA $04
    ("CLEAR ceiling", 0x9259, b"\xC9\x78\xB0", b"\xC9\x80\xB0"),        # CMP #$80 / BCS

    # `$CD94` survives the move but its body calls three routines above `$E000`, so it can
    # no longer be entered at all.
    ("drop cartridge init", 0xC427, b"\x20\x94\xCD", b"\xEA\xEA\xEA"),

]

# The two hooks, applied in `patch_prg` because their targets are only known once the
# routines have been assembled. Each replaces the operand of the cassette routine's own
# call to the name parser, and the bytes on either side pin the instruction.
HOOK_SITES = [
    ("SAVE -> disk", BASIC_SAVE_PARSE, b"\x20\x69\x98\x20\xB4\x98", "save_hook"),
    ("LOAD -> disk", BASIC_LOAD_PARSE, b"\x20\x69\x98\x20\x30\xB4", "load_hook"),
]

# --------------------------------------------------------------------------------------
# The two dumps this can build from
#
# Everything above is V2.1A's. V3 needs a different address for nearly every one of them,
# so rather than a second copy of the tool the values live in a table and each function
# binds them at the top. Every address below was read off the dump with `fb-disasm.py`, and
# each is checked against the bytes around it before being patched.

V3_PROGRAM_PAGE_SITES = []     # V3's system variables are already on page $60 ($812B
                               # reads $6001), so none of V2.1A's fifteen sites apply

V3_PATCHES = [
    # The end of the area, and the ceiling `CLEAR` refuses to go above, as in V2.1A.
    ("end of area", 0x86A3, b"\xA9\x6F\x85\x04", b"\xA9\x7F\x85\x04"),
    ("CLEAR ceiling", 0x97D7, b"\xC9\x70\xB0", b"\xC9\x80\xB0"),

    # V3 alone has `BGGET`/`BGPUT`, and the 1KB buffer they share is pinned to the top of
    # the program area. Widening the area without moving it leaves the buffer in the middle
    # of the user's program, where `BGGET` errors out instead (`docs/ram-expansion.md`).
    # Three immediates, each pinned by the instruction around it.
    ("BGGET headroom", 0xB1BD, b"\xC9\x6C\xB0", b"\xC9\x7C\xB0"),
    ("BGGET buffer", 0xB1CA, b"\xA9\x6C\x85\x1A", b"\xA9\x7C\x85\x1A"),
    ("BGPUT buffer", 0xB20B, b"\xA9\x6C\x85\x1A", b"\xA9\x7C\x85\x1A"),

    # `GAME` cannot work on a disk: `txtGame2` is at $E682 and `txtGame3` at $F308, which
    # is the BIOS there, so it would read the BIOS in as a BASIC program. Killing it is
    # also what makes this build's placement sound - the copy loop at $ADB8 is the only
    # reader of $D000-$DFFF, which is where the routines and the power-on code go. `$01`
    # is `SN` in the error table at $B37F, so a typed `GAME` says `?SN ERROR` rather than
    # failing quietly.
    ("GAME -> ?SN ERROR", 0xAD5B, b"\xC9\x2C\xF0\x2B\x20", b"\xA9\x01\x4C\x37\xB2"),
]

V3_HOOK_SITES = [
    ("SAVE -> disk", 0x9D68, b"\x20\x52\x9E\x20\x9D\x9E", "save_hook"),
    ("LOAD -> disk", 0x9DF2, b"\x20\x52\x9E\x20\x53\xBE", "load_hook"),
]

# The dispatch entry `GAME` is reached through, checked before the patch above is applied:
# if `$B3` stops pointing at `$AD5B`, that patch would be silencing something else while
# `GAME` itself still ran, and the routines' home would no longer be dead.
V3_GAME_TOKEN = 0xB3
V3_GAME_DISPATCH = 0xCEBF      # jtbl_Commands; the index is token - $80
V3_GAME_HANDLER = 0xAD5B

VARIANTS = {
    "8e90d9a6a6090307a7e408d1c1704d09ba8f94fc": dict(
        name="Family BASIC (Japan) (Rev 2), V2.1A",
        program_page_sites=PROGRAM_PAGE_SITES,
        patches=PATCHES, hook_sites=HOOK_SITES,
        fds_init_at=FDS_INIT_AT, fds_init_limit=FDS_INIT_LIMIT, fds_init=FDS_INIT,
        irq_stub_at=IRQ_STUB_AT, vectors=VECTORS, reset_vector=0xC400,
        save_at=SAVE_AT, arg_flag=0xB5, bytes_free=8126,
        # What gets written to the disk, and what BASIC's power-on path reads back.
        # `docs/sav-format.md`: two-byte signature, then the end pointer, then the body.
        sav_version="v2",           # what `fb-basic-to-sav.py -V` calls it
        save_from=0x603A, end_ptr_at=0x603C,
        signature=((0x603A, 0x5A), (0x603B, 0x33)),
        body_at=0x603E,
        basic_parse_name=BASIC_PARSE_NAME,
        basic_load_tail=BASIC_LOAD_TAIL, basic_tp_error=BASIC_TP_ERROR,
        basic_nmi_off=BASIC_NMI_OFF, basic_nmi_on=BASIC_NMI_ON,
        guard="messages",
    ),
    "e232c621bfedbfc6b100677bfbfc50b910248282": dict(
        name="Family BASIC V3 (Japan), V3.0",
        program_page_sites=V3_PROGRAM_PAGE_SITES,
        patches=V3_PATCHES, hook_sites=V3_HOOK_SITES,
        fds_init_at=V3_FDS_INIT_AT, fds_init_limit=V3_FDS_INIT_LIMIT,
        fds_init=V3_FDS_INIT, irq_stub_at=V3_IRQ_STUB_AT, vectors=V3_VECTORS,
        reset_vector=V3_FDS_INIT_AT,
        save_at=0xD400,                # `txtGame0`, dead once `GAME` is patched out
        arg_flag=0xEA,                 # V2.1A leaves it in $B5, V3 in $EA
        bytes_free=8182,               # 56 more than V2.1A; measured on the screen
        # ⚠️ Not V2.1A's layout shifted - a different shape. One signature byte at $6001
        # (checked at $812E), the end pointer at $6002/$6003, the body at $6006. Measured
        # on the running disk: with no program, $05/$06 reads $6006 and $07/$08 $6007.
        sav_version="v3",
        save_from=0x6001, end_ptr_at=0x6002,
        signature=((0x6001, 0x4C),),
        body_at=0x6006,
        basic_parse_name=0x9E52,
        basic_load_tail=0x9E22, basic_tp_error=0x9E4D,
        basic_nmi_off=0xBE0E, basic_nmi_on=0xBE14,
        guard="game",
    ),
}

# --------------------------------------------------------------------------------------
# Disk structure (NESdev wiki, *FDS disk format*)

BLOCK_DISK_INFO, BLOCK_FILE_AMOUNT = 1, 2
BLOCK_FILE_HEADER, BLOCK_FILE_DATA = 3, 4
KIND_PRG, KIND_CHR, KIND_VRAM = 0, 1, 2

SIDE_SIZE = 65500            # one side in a .fds file, CRCs and gaps excluded
BOOT_READ_FILE_CODE = 0x0F   # load every file whose ID is at or below this
FWNES_MAGIC = b"FDS\x1a"


def disk_info_block():
    """Block 1. Past the verification string this is metadata the BIOS carries but does
    not act on, except the boot read file code at offset $19."""
    b = bytearray()
    b.append(BLOCK_DISK_INFO)
    b += b"*NINTENDO-HVC*"                      # the BIOS refuses a disk without this
    b.append(0x00)                              # licensee
    b += b"BAS"                                 # game name
    b.append(0x20)                              # game type: normal
    b += bytes([0x00, 0x00, 0x00, 0x00, 0x00])  # version, side A, disk 0, type, unused
    b.append(BOOT_READ_FILE_CODE)
    b += b"\xFF" * 5
    b += bytes([0x61, 0x11, 0x27])              # manufacturing date, BCD
    b.append(0x49)                              # country: Japan
    b += bytes(0x36 - len(b))                   # up to the "other" disk type field
    b.append(0x00)                              # yellow disk
    b += bytes(56 - len(b))
    assert len(b) == 56, len(b)
    return bytes(b)


def file_blocks(number, file_id, name, address, kind, data):
    """Blocks 3 and 4 for one file."""
    if len(name) != 8:
        raise ValueError(f"a file name is 8 characters: {name!r}")
    hdr = (bytes([BLOCK_FILE_HEADER, number, file_id]) + name
           + address.to_bytes(2, "little") + len(data).to_bytes(2, "little")
           + bytes([kind]))
    assert len(hdr) == 16, len(hdr)
    return hdr + bytes([BLOCK_FILE_DATA]) + data


# --------------------------------------------------------------------------------------
# Licence screen data

# LDA $2007 / CMP abs,Y / BNE rel / INY / CPY #imm / BNE rel
PAT_LICENCE_CHECK = re.compile(rb"\xAD\x07\x20\xD9(..)\xD0.\xC8\xC0(.)\xD0.", re.S)


def kyodaku_from_bios(path):
    """Read the licence screen data out of an FDS BIOS, taking its address and length from
    the BIOS's own comparison loop so a wrong guess cannot pass silently."""
    bios = open(path, "rb").read()
    if len(bios) != 0x2000:
        raise ValueError(f"{path}: expected an 8KB FDS BIOS, got {len(bios)} bytes")
    hits = list(PAT_LICENCE_CHECK.finditer(bios))
    if len(hits) != 1:
        raise ValueError(f"{path}: the licence comparison loop was not uniquely found "
                         f"({len(hits)} matches). Is this an FDS BIOS?")
    table = int.from_bytes(hits[0].group(1), "little")
    length = hits[0].group(2)[0]
    off = table - 0xE000
    if not 0 <= off <= len(bios) - length:
        raise ValueError(f"the table address ${table:04X} is outside the BIOS")
    data = bios[off:off + length]
    print(f"## licence screen: {path}")
    print(f"  MD5 {hashlib.md5(bios).hexdigest()}")
    print(f"  the BIOS checks it at ${0xE000 + hits[0].start():04X}: "
          f"CMP ${table:04X},Y / CPY #${length:02X}")
    print(f"  -> ${table:04X}-${table + length - 1:04X}, {length} bytes "
          f"/ MD5 {hashlib.md5(data).hexdigest()}")
    return data


# --------------------------------------------------------------------------------------

def load_rom(path):
    d = open(path, "rb").read()
    if d[:4] != b"NES\x1a":
        raise ValueError(f"{path}: not an iNES header")
    # The magic matching says nothing about the other 12 header bytes being there, and the
    # length check below reads three of them to work out what it should be checking. Indexing
    # a short file raises IndexError, which escapes main()'s ValueError handler as a raw
    # traceback - so the truncated-file diagnostic added below never fires at the one input
    # shape most likely to need it (found in review).
    if len(d) < 16:
        raise ValueError(f"{path}: an iNES header is 16 bytes, this file is {len(d)}")
    off = 16 + (512 if d[6] & 0x04 else 0)
    prg_size, chr_size = d[4] * 16384, d[5] * 8192
    # Slicing past the end of `d` does not raise; it silently returns fewer bytes than
    # prg_size + chr_size asked for. Say so plainly instead of letting a truncated file fall
    # through to a SHA-1 mismatch below, which is true but blames the wrong thing (found
    # verifying the size check just added, R4, NIT).
    if len(d) < off + prg_size + chr_size:
        raise ValueError(f"{path}: header declares {prg_size + chr_size} bytes of PRG+CHR "
                          f"starting at offset {off}, but the file is only {len(d)} bytes")
    sha1 = hashlib.sha1(d[off:off + prg_size + chr_size]).hexdigest()
    print(f"## input: {path}")
    print(f"  PRG {prg_size} / CHR {chr_size} / body SHA-1 {sha1}")
    # Which dump this is decides every patch site, so it is looked up rather than checked:
    # a table entry is the only way a version becomes buildable, and a dump that is not in
    # it is refused instead of being patched with somebody else's addresses.
    if sha1 not in VARIANTS:
        want = "\n".join(f"       want SHA-1 {h} ({e['name']})"
                         for h, e in VARIANTS.items())
        raise ValueError(
            f"this is not a dump the patch sites were read off.\n{want}\n"
            f"       got  SHA-1 {sha1}")
    v = VARIANTS[sha1]
    print(f"  recognised as {v['name']}")
    # The hash above only covers whatever `prg_size + chr_size` bytes slicing happens to
    # return; a corrupted header that overstates one size and understates the other can
    # still hash the same *available* bytes (Python slicing does not error past the end of
    # the buffer) while silently handing the rest of this function a wrong-length PRG and
    # an empty CHR. Pin both sizes to what this ROM actually is (missed when this function
    # was first written - found in review).
    if prg_size != 0x8000 or chr_size != CHR_SIZE:
        raise ValueError(f"expected 32KB PRG and {CHR_SIZE} CHR, got {prg_size}/{chr_size}")
    return v, d[off:off + prg_size], d[off + prg_size:off + prg_size + chr_size]


def patch_prg(v, prg, hooks, block):
    """Apply everything to the 24KB that survives, checking each site before writing."""
    out = bytearray(prg[:PRG_KEEP])
    changed = set()

    # As in `disk_routines`: bind what the two dumps disagree about, so the body below is
    # the same code it was when there was only one of them.
    PROGRAM_PAGE_SITES = v["program_page_sites"]
    PATCHES, HOOK_SITES = v["patches"], v["hook_sites"]
    FDS_INIT_AT, FDS_INIT_LIMIT = v["fds_init_at"], v["fds_init_limit"]
    FDS_INIT, IRQ_STUB_AT, VECTORS = v["fds_init"], v["irq_stub_at"], v["vectors"]
    SAVE_AT = block_layout(v)["save_at"]
    MARKER_AT = block_layout(v)["marker_at"]

    def put(what, addr, new):
        off = addr - PRG_BASE
        if not 0 <= off <= len(out) - len(new):
            raise ValueError(f"{what}: ${addr:04X} is outside $8000-$DFFF")
        for i, b in enumerate(new):
            if out[off + i] != b:
                out[off + i] = b
                changed.add(off + i)

    print()
    print("## patches")

    # The program-area page. Each site is confirmed by the two bytes in front of it.
    for addr, before in PROGRAM_PAGE_SITES:
        off = addr - PRG_BASE
        if bytes(out[off - 2:off]) != before or out[off] != PROGRAM_PAGE_OLD:
            raise ValueError(
                f"program area page (${addr:04X}): expected {before.hex(' ')} "
                f"{PROGRAM_PAGE_OLD:02x}, found {bytes(out[off - 2:off + 1]).hex(' ')}")
        put("program area page", addr, bytes([PROGRAM_PAGE_NEW]))
    # Only say the page moved when it did. V3 already keeps its system variables on page
    # $60, so `V3_PROGRAM_PAGE_SITES` is empty - printing V2.1A's "$6000-$7FFF instead of
    # $7000-$77FF" there is a false account of a build that patched nothing (found in
    # review).
    if PROGRAM_PAGE_SITES:
        print(f"  program area page   {len(PROGRAM_PAGE_SITES)} sites  "
              f"#${PROGRAM_PAGE_OLD:02X} -> #${PROGRAM_PAGE_NEW:02X}"
              f"   (${PROGRAM_PAGE_NEW:02X}00-$7FFF instead of "
              f"${PROGRAM_PAGE_OLD:02X}00-$77FF)")
    else:
        print(f"  program area page   already on ${PROGRAM_PAGE_NEW:02X}"
              f"   (nothing to move)")

    for what, addr, old, new in PATCHES:
        off = addr - PRG_BASE
        have = bytes(out[off:off + len(old)])
        if have != old:
            raise ValueError(f"{what} (${addr:04X}): expected {old.hex(' ')}, "
                             f"found {have.hex(' ')}")
        put(what, addr, new)
        print(f"  {what:<19} ${addr:04X}     {len(new)} byte(s)")

    # The power-on routine and the vector table, both written here.
    end = IRQ_STUB_AT + len(IRQ_STUB)
    if end > FDS_INIT_LIMIT:
        raise ValueError(f"the power-on routine runs to ${end:04X}, past ${FDS_INIT_LIMIT:04X}")
    put("FDS power-on", FDS_INIT_AT, FDS_INIT + IRQ_STUB)
    print(f"  FDS power-on        ${FDS_INIT_AT:04X}     {len(FDS_INIT)} byte(s)"
          f", IRQ stub at ${IRQ_STUB_AT:04X}")
    put("vectors", VECTORS_AT, VECTORS)
    print(f"  vectors             ${VECTORS_AT:04X}     {len(VECTORS)} byte(s)"
          f"   (NMI $00ED x3 / RESET ${v['reset_vector']:04X}"
          f" / IRQ ${IRQ_STUB_AT:04X})")

    for what, addr, expect, label in HOOK_SITES:
        off = addr - PRG_BASE
        have = bytes(out[off:off + len(expect)])
        if have != expect:
            raise ValueError(f"{what} (${addr:04X}): expected {expect.hex(' ')}, "
                             f"found {have.hex(' ')}")
        put(what, addr + 1, word(hooks[label]))
        print(f"  {what:<19} ${addr + 1:04X}     2 byte(s)   -> ${hooks[label]:04X}")

    # -- the routines themselves, and the two guards that keep their home dead ----------
    put("disk SAVE/LOAD", SAVE_AT, block)
    print(f"  disk SAVE/LOAD      ${SAVE_AT:04X}     {len(block)} byte(s)"
          f"   (SAVE ${hooks['save_hook']:04X} / LOAD ${hooks['load_hook']:04X})")

    # V3 keeps its routines in `txtGame0` instead, and what makes *that* dead is the `GAME`
    # patch applied above. The guard is on the assumption behind it: `GAME`'s dispatch entry
    # must be the handler that was just silenced. If token `$B3` stops pointing at `$AD5B`,
    # the patch silenced something else while `GAME` itself still ran - and `GAME` is the
    # only reader of the block's home.
    # ⚠️ Every variant names its guard, and an unknown name is refused rather than falling
    # through to V2.1A's. Falling through would run the message-table rewrite - which is
    # tied to $C529/$C565 and to a block at $C5A1 - against a dump that has neither, and
    # the failure would be a wrong disk rather than a refusal (found in review).
    if v["guard"] not in ("game", "messages"):
        raise ValueError(f"unknown guard {v['guard']!r} for {v['name']}: refusing to build "
                         f"rather than apply another version's guard")

    if v["guard"] == "game":
        e = V3_GAME_DISPATCH + (V3_GAME_TOKEN - 0x80) * 2 - PRG_BASE
        entry = out[e] | (out[e + 1] << 8)
        if entry != V3_GAME_HANDLER:
            raise ValueError(
                f"token ${V3_GAME_TOKEN:02X} (`GAME`) dispatches to ${entry:04X}, not "
                f"${V3_GAME_HANDLER:04X}. The patch that turns `GAME` into `?SN ERROR` "
                f"would be silencing something else, and `GAME` is the only thing that "
                f"reads ${SAVE_AT:04X}'s home.")
        print(f"  guard: `GAME` (${V3_GAME_TOKEN:02X}) dispatches to "
              f"${entry:04X}, now `?SN ERROR`   ok")
        print(f"  ---- {len(changed)} bytes differ from the cartridge")
        return bytes(out)

    # Guard 1. The block sits in the built-in conversation program's message text, and the
    # reason that program cannot run is that nothing installs its NMI handler `$CDA7` any
    # more - the only code that did was inside the `$C436-$C46F` replaced above. If a
    # different dump, or a future patch, put that install back, this whole placement is
    # wrong. So look for it and refuse rather than build a disk that corrupts itself.
    for pattern, what in ((b"\xA9\xA7\x85\xEE", "LDA #$A7 / STA $EE"),
                          (b"\xA9\xCD\x85\xEF", "LDA #$CD / STA $EF")):
        at = bytes(out).find(pattern)
        if at >= 0:
            raise ValueError(
                f"something still points the NMI trampoline at $CDA7 "
                f"({what} at ${PRG_BASE + at:04X}). That is what makes the message text at "
                f"${SAVE_AT:04X} dead; with it back, these routines would be overwritten.")
    print(f"  guard: NMI -> $CDA7 nowhere in the image   ok")

    # Guard 2. Repoint every message whose text this just overwrote at a marker, so that if
    # the argument is wrong after all, the machine prints `?MSG` instead of running these
    # bytes through the character generator. A silent corruption becomes a loud one.
    def msg_target(i):
        return out[MSG_LO_TABLE - PRG_BASE + i] | (out[MSG_HI_TABLE - PRG_BASE + i] << 8)

    outside = [i for i in range(MSG_COUNT) if not MSG_BLOCK[0] <= msg_target(i) < MSG_BLOCK[1]]
    if outside:
        raise ValueError(f"message index entries {outside} do not point into "
                         f"${MSG_BLOCK[0]:04X}-${MSG_BLOCK[1] - 1:04X}; the table is not "
                         f"where this thinks it is")
    hit = [i for i in range(MSG_COUNT) if SAVE_AT <= msg_target(i) < SAVE_AT + SAVE_SIZE]
    for i in hit:
        out[MSG_LO_TABLE - PRG_BASE + i] = MARKER_AT & 0xFF
        out[MSG_HI_TABLE - PRG_BASE + i] = MARKER_AT >> 8
        changed.add(MSG_LO_TABLE - PRG_BASE + i)
        changed.add(MSG_HI_TABLE - PRG_BASE + i)
    print(f"  guard: {len(hit)} of {MSG_COUNT} messages repointed at "
          f"{MARKER[:-1].decode()} (${MARKER_AT:04X})")

    print(f"  ---- {len(changed)} bytes differ from the cartridge")
    return bytes(out)


def build(rom_path, kyodaku, out_path, fwnes_header=False, expect=None):
    v, prg, chr_rom = load_rom(rom_path)

    info = disk_info_block()
    # The ten bytes CheckDiskHeader compares, taken from the block this build just made
    # rather than written down twice. With these in the routine, a `SAVE` with somebody
    # else's disk in the drive is refused by the BIOS instead of overwriting their file.
    block, code_len, hooks = disk_routines(v, info[15:25])
    patched = patch_prg(v, prg, hooks, block)

    side = bytearray()
    side += info
    side += bytes([BLOCK_FILE_AMOUNT, 3])
    side += file_blocks(0, 0, b"KYODAKU-", 0x2800, KIND_VRAM, kyodaku)
    side += file_blocks(1, 1, b"CHR-ROM ", 0x0000, KIND_CHR, chr_rom)
    side += file_blocks(2, 2, b"PRG-ROM ", PRG_BASE, KIND_PRG, patched)

    used = len(side)
    if used > SIDE_SIZE:
        raise ValueError(f"a side holds {SIDE_SIZE} bytes, this needs {used}")
    side += bytes(SIDE_SIZE - used)

    image = bytes(side)
    if fwnes_header:
        image = FWNES_MAGIC + bytes([1]) + bytes(11) + image

    open(out_path, "wb").write(image)
    md5 = hashlib.md5(image).hexdigest()
    print()
    print(f"## output: {out_path}")
    # The banner figure is not derivable from the area alone - the two versions lay their
    # system variables out differently and differ by 56 bytes - so it is recorded per
    # version, measured off the running machine rather than computed here.
    errno_at = v["save_at"] + 0x17F
    print(f"  program area ${PROGRAM_PAGE_NEW:02X}00-$7FFF"
          f"  ->  {v['bytes_free']} BYTES FREE"
          f"   ({code_len} bytes of code at ${v['save_at']:04X})")
    print(f"  saves from ${v['save_from']:04X}"
          f"  (program body ${v['body_at']:04X})"
          f"   /  a failure leaves its number in PRINT PEEK({errno_at})")
    print(f"  3 files, {used} bytes used of {SIDE_SIZE} on side A"
          f"{' / 16-byte fwNES header' if fwnes_header else ' / no fwNES header'}")
    print(f"  {len(image)} bytes / MD5 {md5}")
    if expect:
        if md5.lower() != expect.lower():
            raise ValueError(f"MD5 mismatch: wanted {expect.lower()}, got {md5}")
        print(f"  verified against {expect.lower()}")
    return md5


def main():
    ap = argparse.ArgumentParser(
        description="Put Family BASIC on a Famicom Disk System disk")
    ap.add_argument("rom", help="Family BASIC (Japan) (Rev 2).nes, or "
                               "Family BASIC V3 (Japan).nes")
    ap.add_argument("-o", "--output", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bios", metavar="PATH",
                     help="an FDS BIOS to read the licence screen data out of "
                          "(MiSTer keeps it as games/NES/boot0.rom)")
    src.add_argument("--kyodaku", metavar="PATH",
                     help="the licence screen data as a file, if you already have it")
    ap.add_argument("--header", action="store_true",
                    help="prepend the 16-byte fwNES header some emulators want")
    ap.add_argument("--expect", metavar="MD5",
                    help="fail unless the image comes out to this MD5")
    args = ap.parse_args()

    try:
        if args.bios:
            kyodaku = kyodaku_from_bios(args.bios)
        else:
            kyodaku = open(args.kyodaku, "rb").read()
            print(f"## licence screen: {args.kyodaku} / {len(kyodaku)} bytes "
                  f"/ MD5 {hashlib.md5(kyodaku).hexdigest()}")
        build(args.rom, kyodaku, args.output,
              fwnes_header=args.header, expect=args.expect)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
