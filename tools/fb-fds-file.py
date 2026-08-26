#!/usr/bin/env python3
"""Read and write the saved BASIC program inside a Disk BASIC disk image, from a PC.

  $ ./fb-fds-file.py disk.fds --list
  $ ./fb-fds-file.py disk.fds --extract -o program.bas
  $ ./fb-fds-file.py disk.fds --insert program.bas -o disk-with-program.fds

## Why this exists

`fb-fds.py` builds the disk; this reaches inside one that has been used. Without it the
only way to see what a machine saved is to put the disk back in a machine.

With it, a program typed on real hardware can be pulled back out as text - read, diffed
and kept in version control like anything else - and a program written on a PC can replace
it.

**`--insert` replaces; it cannot create.** It takes the file id from the saved program
already on the disk, and a freshly built disk has not got one (it carries three files, none
of them a program). So a disk has to be saved to once, from a machine, before this can put
anything on it.

## The three shapes a used disk arrives in

The same disk comes back differently depending on what wrote it, and all three are
accepted. **The shape is detected, never assumed**, because guessing wrong here means
reading file headers out of the middle of somebody's program:

| | what it looks like |
|---|---|
| raw `.fds` | the side itself, 65,500 bytes per side, block 1 first |
| fwNES `.fds` | 16-byte `FDS\x1a` header, then sides |
| MiSTer `.sav` | the side with 16 bytes of slack in front (measured 2026-08-24) |

**FDSKey writes back into the `.fds` in place**, keeping the original as `.fds.bak`
(measured 2026-08-24: a save changed 30 bytes - the file count, and one new file). So a
disk that has been used on real hardware through FDSKey is just a raw `.fds` again, and
the `.bak` beside it is what it was built as.

## What a saved program looks like on the disk

`fb-fds.py`'s `SAVE` writes one file, at position 3 (the fourth file), holding a short
header and then the program. Its file id is **above** the disk's boot read file code so
the BIOS does not read it at power-on - see `DISK_FILE_ID` in `fb-fds.py`.

⚠️ **The header's shape is the build's, not this tool's**, and the builds do not share
one: they differ in where the file loads, in how many bytes the save signature is, and in
whether anything sits between the end pointer and the body. This file used to hold one
build's numbers as constants, and a disk from the other came back as "no saved program"
while `--list` was still printing the file.

So every one of those numbers is read from `fb-fds.py`'s `VARIANTS` table (see `Layout`)
and **none of them is written down here** - including in this sentence. A number in prose
goes stale exactly the way a number in a constant does, and the first version of this
paragraph proved it by listing all four addresses two lines after saying they were not
copied (found in review). `--list` prints the build it decided on and
where that build saves, so the current values come from the tool rather than from here.

This tool finds that file by **where it loads**, not by its name and not by its id. A name
selects nothing on these builds, and the id is a constant that has already changed once.
Which build wrote the disk is decided from that same address rather than asked of the
caller: the disk knows, and an answer from the command line could contradict it.

## What it will not do

* **It does not add a second program.** The build saves one program to a disk and `LOAD`
  reads whatever is there; `--insert` therefore replaces. Several programs to a disk is a
  bigger design than it looks - see "One program to a disk" in `fb-fds.py`.
* **It does not write a disk from nothing.** That is `fb-fds.py`. This edits one file
  inside a disk that already boots.
"""

import argparse
import hashlib
import importlib.util
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FB_SAV = os.path.join(HERE, "fb-basic-to-sav.py")
FB_FDS = os.path.join(HERE, "fb-fds.py")

SIDE_SIZE = 65500
FWNES_MAGIC = b"FDS\x1a"
MISTER_SLACK = 16              # what MiSTer puts in front of the side (measured)

BLOCK_DISK_INFO, BLOCK_FILE_AMOUNT = 1, 2
BLOCK_FILE_HEADER, BLOCK_FILE_DATA = 3, 4
INFO_SIZE, HEADER_SIZE = 56, 16
COUNT_AT = 0x39                # the file-count byte inside the side



def load_sav_module():
    spec = importlib.util.spec_from_file_location("fbsav", FB_SAV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def kind_prg():
    """The file kind that means "a program", from `fb-fds.py` rather than copied.

    Same reasoning as `prg_load_at`: the builder owns what it writes."""
    return load_fds_module().KIND_PRG


def prg_load_at():
    """Where the disk's own BASIC loads, from `fb-fds.py` rather than copied.

    It was a constant here with a comment saying it was `fb-fds.py`'s `PRG_BASE` - a
    layout fact in two places, admitted in writing (found in review). The comment is the
    tell: a value that has to name where it really comes from is a value that should be
    fetched from there."""
    return load_fds_module().PRG_BASE


_FDS_MODULE = []


def load_fds_module():
    """`fb-fds.py`, loaded once. Cached because several small facts are read out of it and
    executing the module for each one is both slow and pointlessly repeated work."""
    if not _FDS_MODULE:
        spec = importlib.util.spec_from_file_location("fbfds", FB_FDS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FDS_MODULE.append(mod)
    return _FDS_MODULE[0]


class Layout:
    """Where a build keeps its saved program, taken from `fb-fds.py`'s own table.

    ⚠️ **The builds do not share a shape** - it is not one layout shifted. They differ in
    the load address, in the length of the save signature, and in whether bytes sit
    between the end pointer and the body. `fb-fds.py` says so at its own variant entries.

    This file used to hold one build's numbers as constants. A disk from the other then
    came back as "no saved program": `--list` printed the file, `--insert` refused it, and
    nothing said why the two disagreed. That is the same shape as the faults
    `program_fault` already carries scars from - one rule, two readers - only here the
    second reader was a constant.

    So the numbers come from the builder and are **not** written down here, in code or in
    prose. What is asserted below is the *shape* this file depends on, not the values: the
    signature starts at the load address and runs unbroken, and the end pointer follows
    it. A variant added later that breaks either is refused rather than misread.
    """

    __slots__ = ("name", "load_at", "sig", "end_ptr_off", "body_off", "sav_version",
                 "bytes_free")

    def __init__(self, v):
        self.name = v["name"]
        self.load_at = v["save_from"]
        self.sav_version = v["sav_version"]
        self.bytes_free = v["bytes_free"]
        # The signature has to start at the load address and be contiguous, because that
        # is what makes it a prefix of the file. Checked rather than assumed: a variant
        # added later that breaks it would otherwise be read as a shorter signature and
        # silently accept the wrong bytes.
        want = tuple(range(v["save_from"], v["save_from"] + len(v["signature"])))
        if tuple(a for a, _ in v["signature"]) != want:
            raise ValueError(f"{v['name']}: the signature is not contiguous from "
                             f"${v['save_from']:04X}; this file assumes it is a prefix")
        self.sig = bytes(b for _, b in v["signature"])
        self.end_ptr_off = v["end_ptr_at"] - v["save_from"]
        self.body_off = v["body_at"] - v["save_from"]
        if self.end_ptr_off != len(self.sig):
            raise ValueError(f"{v['name']}: the end pointer does not follow the signature")

    def __repr__(self):
        return f"<{self.name} at ${self.load_at:04X}>"


def layouts():
    return [Layout(v) for v in load_fds_module().VARIANTS.values()]


def find_layout(files, path):
    """Which build's saved program this disk could be holding, by where files load.

    Not by asking the caller. Two builds put it in different places, and the disk knows
    which one it is; a `--version` argument could only repeat that, and could contradict
    it (the same reasoning as `read_tokens` reading the words off the disk)."""
    hits = [(lay, [f for f in files if f["address"] == lay.load_at]) for lay in layouts()]
    hits = [(lay, fs) for lay, fs in hits if fs]
    if not hits:
        return None
    if len(hits) > 1:
        where = ", ".join(f"${lay.load_at:04X} ({lay.name})" for lay, _ in hits)
        sys.exit(f"{path}: files load at more than one build's program address ({where}); "
                 f"which build wrote this disk is not decidable, so this stops here")
    return hits[0][0]


def find_side(raw, path):
    """Work out where the side starts, by checking rather than by trusting the extension.

    A side begins with block 1: the byte $01 then `*NINTENDO-HVC*`. Three shapes, but only
    **two offsets** - 0 or 16. What separates the pair that share 16 is the 16 bytes in
    front of them, so the shape is decided by looking, never by the file's extension.
    """
    want = bytes([BLOCK_DISK_INFO]) + b"*NINTENDO-HVC*"
    if raw[:len(want)] == want:
        return 0, "raw .fds"
    # fwNES and a MiSTer .sav both put the side at offset 16, so the offset alone cannot
    # tell them apart - what differs is those 16 bytes. fwNES writes `FDS\x1a` and a
    # count; MiSTer leaves them zero (measured 2026-08-24 on a .sav it wrote).
    if raw[16:16 + len(want)] == want:
        if raw[:4] == FWNES_MAGIC:
            return 16, f"fwNES .fds ({raw[4]} side(s) declared)"
        if raw[:16] == bytes(16):
            return 16, "MiSTer .sav (16 zero bytes of slack)"
        return 16, "16-byte header of an unfamiliar kind"
    sys.exit(f"{path}: no FDS side found. Looked for the block-1 signature at offsets "
             f"0, 16 (fwNES) and 16 (MiSTer .sav); none matched, so this is not a disk "
             f"image this tool understands.")


def header_fault(side, at):
    """Why the bytes at `at` are not a file header, as a short code, or None if they are.

    One definition, two readers. `read_files` walks the files the disk declares and turns
    each code into its own worded refusal; `hidden_file_in` searches for a file nobody
    declared and only asks whether there is one. They used to decide this separately, and
    drifted the way separate copies do: the search also demanded a printable name and a
    kind the format defines, which made it *stricter* than the reader. A file `read_files`
    would have accepted could therefore be dismissed as rubbish by the search and then
    written over - the sixth time in this review that one rule lived in two places and
    only one of them got the next change (found in review).

    The name and kind tests are gone rather than copied across. They were there to keep
    leftover program bytes from reading as a file, and measured against the bytes actually
    scanned - the tail of a replaced program - they caught nothing the structure did not:
    0 candidates in 4,886 bytes either way. Over a whole side the two do differ, but the
    search never covers a whole side; it covers what a write would touch, which is always
    program leftovers. Structure decides, and the strictness that could lose a file is
    gone."""
    if at + HEADER_SIZE > len(side):
        return "past-end"
    if side[at] != BLOCK_FILE_HEADER:
        return "not-header"
    data_at = at + HEADER_SIZE
    if data_at >= len(side):
        return "no-data-room"
    if side[data_at] != BLOCK_FILE_DATA:
        return "no-data"
    size = side[at + 13] | (side[at + 14] << 8)
    if data_at + 1 + size > len(side):
        return "too-big"
    return None


def read_files(side):
    """Walk the side's blocks and return every file as a dict. Stops at the count the
    disk declares, because bytes past the last file are whatever was there before."""
    declared = side[COUNT_AT]
    out = []
    at = INFO_SIZE
    if side[at] != BLOCK_FILE_AMOUNT:
        sys.exit(f"block 2 (file amount) is not where it should be, at offset {at}")
    at += 2
    while len(out) < declared:
        # Every index below is checked before it is used. A disk whose header says one
        # thing and whose blocks say another is a disk to refuse in words, not one to read
        # past the end of and die on IndexError (review; same class as the 4-byte
        # ROM that used to produce a raw traceback).
        fault = header_fault(side, at)
        h = side[at:at + HEADER_SIZE]
        size = h[13] | (h[14] << 8) if len(h) == HEADER_SIZE else 0
        data_at = at + HEADER_SIZE
        if fault == "past-end":
            sys.exit(f"file header {len(out)} would start at offset {at}, past the end of "
                     f"the side ({len(side)}) - the disk says it has {declared} files")
        if fault == "not-header":
            sys.exit(f"expected file header {len(out)} at offset {at}, found "
                     f"${side[at]:02X} - the disk says it has {declared} files")
        if fault == "no-data-room":
            sys.exit(f"file {len(out)} has no room for a data block at offset {data_at}")
        if fault == "no-data":
            sys.exit(f"file {len(out)} has no data block at offset {data_at}")
        if fault == "too-big":
            sys.exit(f"file {len(out)} says it holds {size} bytes from offset "
                     f"{data_at + 1}, which runs past the end of the side ({len(side)})")
        out.append({
            "number": h[1], "id": h[2], "name": bytes(h[3:11]),
            "address": h[11] | (h[12] << 8), "size": size, "kind": h[15],
            "header_at": at, "data_at": data_at + 1,
            "data": bytes(side[data_at + 1:data_at + 1 + size]),
            "end": data_at + 1 + size,
        })
        at = out[-1]["end"]
    return declared, out


def find_program(files, lay):
    """The saved program, or None if no build's address matched at all.

    ★ **`None` means exactly one thing**: `lay` is `None`, so nothing on this disk loads
    where any build keeps its program. Every other absence - the wrong kind, two
    candidates, a missing signature - is refused here, in words, rather than returned. A
    caller that treats `None` as "some absence" ends up writing a branch that cannot run
    (found in review).

    What makes a file the saved program is `program_fault`'s to say - the address, the
    kind, being the only one, and the save signature. This turns each way of failing into
    its own refusal; `--list` turns the same codes into notes. They used to decide it
    separately and disagreed five times over (found in review)."""
    if lay is None:
        return None
    at_load = [f for f in files if f["address"] == lay.load_at]
    if not at_load:
        # ⚠️ Not reachable today: `lay` is decided **by** a file loading here, so if `lay`
        # exists the file exists. It returned `None` instead, which is the answer the
        # caller reads as "no build matched at all" - a wrong message rather than no
        # message (found in review).
        #
        # It refuses rather than being deleted, and the difference matters: this is a
        # guard on a contract between two functions, not a branch offering the reader a
        # case that cannot happen. If `find_layout` is ever changed so that a layout can
        # be decided some other way, this says so loudly instead of returning a value the
        # caller will misread. `Layout.__init__` keeps its invariants the same way.
        sys.exit(f"{lay.name} was decided from ${lay.load_at:04X}, but no file loads "
                 f"there. find_layout and find_program disagree; this is a fault in the "
                 f"tool, not in the disk.")
    good = [f for f in at_load if program_fault(f, files, lay) is None]
    if len(good) == 1:
        return good[0]
    fault = program_fault(at_load[0], files, lay)
    if fault == "mixed-kinds" or fault == "wrong-kind":
        wrong = [f for f in at_load if f["kind"] != kind_prg()]
        names = ", ".join(f'{f["name"].decode("ascii", "replace").rstrip()} (kind {f["kind"]})'
                          for f in wrong)
        sys.exit(f"{len(wrong)} file(s) load at ${lay.load_at:04X} but are not "
                 f"kind {kind_prg()}, so the BIOS sends them to the PPU, not to "
                 f"${lay.load_at:04X}: {names}. This will not treat one as the saved "
                 f"program.")
    if fault == "ambiguous":
        sys.exit(f"{len(at_load)} files load at ${lay.load_at:04X}; "
                 f"this build writes one")
    sys.exit(f"the file at ${lay.load_at:04X} does not start with the save "
             f"signature {lay.sig.hex(' ')} - it holds "
             f"{at_load[0]['data'][:len(lay.sig)].hex(' ') or '(nothing)'}. Something "
             f"other than a saved BASIC program is there, and this will not write over it.")


def hidden_file_in(side, start, stop):
    """A file the disk does not count, anywhere in `start`..`stop`.

    `read_files` stops at the declared count, because bytes past the last file are whatever
    was there before. The format allows a file to sit past the count as a hiding place and
    the BIOS ignores it (NESdev wiki, *FDS disk format*), so "not counted" does not mean "not
    there", and a longer program written over one would take it with it.

    Looking only immediately after the last counted file was not enough (found in review).
    The tool makes the gap itself: replace a long program with a short one and the tail of
    the old one stays on the disk deliberately, which pushes any hidden file away from the
    end of the counted files. The next longer program then reached right over it. So the
    whole span the write would cover is searched.

    What counts as a file is `header_fault`'s to say, and nothing else's - see there for
    why the name and kind tests this used to add are gone."""
    # Two separate limits, and getting either one wrong has now happened once each.
    #
    # The write covers `start`..`stop - 1`, so `stop` is where a file may legally sit
    # untouched - scanning it refuses a program that fits exactly in front of a hidden
    # file, which is a correct disk to accept (found in review).
    #
    # The other limit is where a header can start at all: `len - HEADER_SIZE - 1`, since it
    # needs 16 bytes and the data block's marker byte. `range` stops before its limit, so
    # that one needs the `+ 1` it now has folded into it - without it, a zero-length hidden
    # file ending on the side's last byte was never looked at (found in review).
    for at in range(max(start, 0), min(stop, len(side) - HEADER_SIZE)):
        if header_fault(side, at) is not None:
            continue
        h = side[at:at + HEADER_SIZE]
        return {"header_at": at,
                "name": bytes(h[3:11]).decode("ascii", "replace").rstrip()}
    return None


def program_fault(f, files, lay):
    """Why `f` is not the saved program, as a short code, or None if it is.

    One definition, two readers - the same shape as `header_fault`, and for the same
    reason. `find_program` turns each code into a refusal and `program_note` turns it into
    a note beside the listing, and while they decided this separately the two drifted every
    single time a rule was added: the kind (R5), two files at one address (R7), a mix of
    kinds (R9), the save signature (R10, reintroduced within the round that fixed the
    others). Five times, always the same shape - a rule added to one and not the other,
    and always caught by a reviewer rather than by a test, because either branch refuses
    for *some* reason and only the wording differs (found in review).

    So the rule is here, and the wording is theirs."""
    if f["address"] != lay.load_at:
        return "elsewhere"
    if f["kind"] != kind_prg():
        return "wrong-kind"
    at_load = [g for g in files if g["address"] == lay.load_at]
    if any(g["kind"] != kind_prg() for g in at_load):
        return "mixed-kinds"
    if len(at_load) > 1:
        return "ambiguous"
    if f["data"][:len(lay.sig)] != lay.sig:
        return "no-signature"
    return None


def program_note(f, files, lay):
    """What `--list` says about a file. The rule is `program_fault`'s; this is the wording.

    Listing is the command that has to keep showing a disk the other two refuse, so it says
    why rather than stopping."""
    if lay is None:
        return ""
    fault = program_fault(f, files, lay)
    at_load = [g for g in files if g["address"] == lay.load_at]
    if fault is None:
        return "   <- the saved program"
    if fault == "elsewhere":
        return ""
    if fault == "wrong-kind":
        return f"   <- at ${lay.load_at:04X} but kind {f['kind']}, so the BIOS sends it to the PPU"
    if fault == "mixed-kinds":
        return (f"   <- at ${lay.load_at:04X}, but {len(at_load)} files are and not all of them are "
                f"kind {kind_prg()}, so which is unclear")
    if fault == "ambiguous":
        return (f"   <- one of {len(at_load)} files at ${lay.load_at:04X}; this build writes one, "
                f"so which is unclear")
    return (f"   <- at ${lay.load_at:04X}, but without the save signature: "
            f"not a saved program")


def read_tokens(files, disk_path):
    """The reserved words, read from the BASIC **on this disk**.

    Not from a dump handed in on the command line. The disk carries its own interpreter as
    the `PRG-ROM` file, and that is the one that will run the program, so its table is the
    only one that can be right. Taking a `--rom` instead made it possible to tokenize with
    a V3 dump - words V2.1A has never heard of (`GAME`, `BGGET`, 19 more), each of which
    means something else or nothing over there - and write a program the disk cannot run
    (found in review). Checking a supplied dump against this one would have closed that,
    but then the argument could only ever repeat what the disk already says.

    Reading the table rather than keeping a copy is the project's rule for it
    (`docs/reference/token-numbering.md`), and not theoretical: a written-down copy had `SCR$`
    missing and nothing compared it to a dump.
    """
    sav = load_sav_module()
    # By what it is, not only by what it is called. An FDS name selects nothing and is not
    # required to be unique, so "the first file called PRG-ROM" could be a PPU file with
    # that name, or the second of two - and the words would then come from something other
    # than the interpreter that runs the program (found in review). The interpreter is
    # the file the builder writes: kind 0, loaded where the builder loads it.
    prg_base = prg_load_at()
    hits = [f for f in files
            if f["kind"] == kind_prg() and f["address"] == prg_base]
    if not hits:
        sys.exit(f"{disk_path}: no file on this disk is a program loaded at "
                 f"${prg_base:04X}, so there is no BASIC to read the reserved words from")
    if len(hits) > 1:
        names = ", ".join(f["name"].decode("ascii", "replace").rstrip() for f in hits)
        sys.exit(f"{disk_path}: {len(hits)} files load at ${prg_base:04X} ({names}); "
                 f"which one is the BASIC that runs is not decidable, so this stops here")
    try:
        return sav.read_token_table(hits[0]["data"])
    except ValueError:
        sys.exit(f"{disk_path}: the file at ${prg_base:04X} on this disk has no "
                 f"reserved-word table where one is expected - is this a Family BASIC disk?")


def program_text(data, tokens, lay):
    """Turn the saved bytes into BASIC text, with the words the disk itself answers.

    The save signature is `find_program`'s to check, not this one's - it decides what the
    saved program *is*, and all three commands go through it. There was a second check
    here as well until the first one existed; keeping it meant a refusal no input could
    reach any more, which is a guard that reports coverage it does not have."""
    sav = load_sav_module()
    # The two bytes after the signature are the end-of-program pointer: the address of the
    # byte after the `$00` that terminates the last line. Ignoring it, and stopping at
    # whichever comes first of "a $00 length byte" and "the end of the file", meant a
    # truncated or overlong file decoded into whatever prefix happened to parse and said
    # nothing - bytes lost on the way out, and a different program on the way back in
    # (found in review). Checking it also checks the file: the disk's own
    # record of where the program ends has to agree with how many bytes the disk holds.
    if len(data) < lay.body_off + 1:
        sys.exit(f"the saved file is {len(data)} bytes; this build's header and an empty "
                 f"program need {lay.body_off + 1}")
    end_ptr = data[lay.end_ptr_off] | (data[lay.end_ptr_off + 1] << 8)
    end_off = end_ptr - lay.load_at
    if not lay.body_off <= end_off <= len(data):
        sys.exit(f"the end pointer says the program stops at ${end_ptr:04X}, which is not "
                 f"inside this file (${lay.load_at:04X}..${lay.load_at + len(data):04X})")
    if end_off != len(data):
        sys.exit(f"the end pointer says the program stops at ${end_ptr:04X}, "
                 f"{len(data) - end_off} byte(s) before the file ends - this file and the "
                 f"disk's record of it disagree")
    body, lines, escaped = data[lay.body_off:], [], 0
    i = 0
    while i < len(body) and body[i] != 0:
        length = body[i]
        if length < 4 or i + length > len(body):
            sys.exit(f"a line at offset {i} claims {length} bytes, which does not fit")
        # Each line ends with a $00 of its own; `body[i + 3:i + length - 1]` drops it
        # unread. If it is not there, the length byte is not describing a line.
        if body[i + length - 1] != 0:
            sys.exit(f"the line at offset {i} does not end with $00 - it claims {length} "
                     f"bytes and byte {length - 1} is ${body[i + length - 1]:02X}")
        number = body[i + 1] | (body[i + 2] << 8)
        # ★ Which numeric forms this build uses is the build's, so it comes from the
        # layout the disk was recognised by. It was pinned to V2.1A's answer, which is
        # `False` - and V3 stores 0-9 in one byte. So a program a **machine** saved to a V3
        # disk came out with its single digits as `\xNN` escapes - and the round trip could
        # not see it, because both directions were pinned the same way and so agreed with
        # each other about a wrong answer (found in review).
        try:
            text, n = sav.decode_body(body[i + 3:i + length - 1], tokens,
                                      sav.SMALL_DIGITS_BY_VERSION[lay.sav_version])
        except ValueError as e:
            # Says which line, which the shared decoder cannot know (found in review).
            sys.exit(f"line {number} (at offset {i}) will not decode: {e}")
        escaped += n
        lines.append(f"{number} {text}")
        i += length
    # Falling out of the loop at the end of the body instead of on the `$00` means the last
    # line ran to the edge of the file with no terminator after it.
    if i >= len(body):
        sys.exit(f"the program has no $00 after its last line - it runs to the end of the "
                 f"file ({len(body)} bytes) and stops there")
    if i + 1 != len(body):
        sys.exit(f"the program ends at offset {i} but the file holds {len(body) - i - 1} "
                 f"more byte(s) - two programs, or one and a leftover")
    # Bytes it could not read come back as `\xNN`. Saying how many keeps "it printed
    # something" apart from "it understood it" - the round-trip test counts them for the
    # same reason.
    return "\n".join(lines) + "\n", escaped


def text_to_program(text, tokens, lay):
    """Tokenize BASIC text, with the reserved words read out of the dump.

    It calls `build_program` directly rather than running the tool, because the tool also
    lays out a `.sav`: V2 there means the area at `$7000`, which stops at `$7FFF` and so
    caps a program at 4,034 bytes. The disk build moved the area to `$6000` and has room
    for 8,130, and a program in between is perfectly legal on a disk - the tool would
    refuse it for a reason that does not apply here (found in review).

    The tokenizer itself is version-specific and shared; only the layout differs. Nothing
    inside a program refers to an address - lines carry a length, not a link - so the same
    bytes are correct at either base.

    **The words come from the ROM, not from the table written down in that module.** A
    first version of this took `TOKENS_BY_VERSION["v2"]`, which quietly made `--rom`
    ignored on this side while `--extract` honoured it - and a hand-written copy of this
    table is exactly what went stale before: `SCR$` was missing from the V2 list and
    nothing compared it to a dump. Found in review.
    """
    sav = load_sav_module()
    # ★ **Long string literals are allowed here, and refused by the converter.** They are
    # not the same job. `fb-basic-to-sav.py` authors a new program, where a literal past
    # the machine's limit is almost always a mistake worth stopping. This writes into a
    # disk that already holds one, and its promise is that extracting and re-inserting
    # rebuilds the same bytes - a machine **can** store and save a line it will later
    # refuse to run, so refusing here broke the round trip on a disk the tool had just
    # read (found in review).
    #
    # ⚠️ The consequence is that `--insert` will take a program that stops on that line.
    # It says so rather than deciding for the user, in `long_literal_note` below.
    return sav.build_program(text, tokens, sav.SMALL_DIGITS_BY_VERSION[lay.sav_version],
                             allow_long_strings=True)


def long_literal_note(text, tokens, lay):
    """Which lines hold a literal the machine will not print, for `--insert` to report."""
    sav = load_sav_module()
    over = []
    for raw in text.splitlines():
        m = re.match(r"\s*(\d+)\s?(.*)$", raw)
        if not m:
            continue
        found = []
        try:
            sav.encode_body(m.group(2), tokens,
                            sav.SMALL_DIGITS_BY_VERSION[lay.sav_version], found)
        except ValueError:
            continue                      # a fault the caller reports with its own words
        # ★ The closure is carried, not discarded. The converter distinguishes a measured
        # outcome (a closed literal past the limit raises `?IL ERROR`) from a conservative
        # one (whether an unterminated literal does has not been measured) - and this
        # warning said "will raise" for both, presenting a guess as a measurement (found in
        # review, by both reviewers).
        for n, closed in found:
            if n > sav.MAX_STRING:
                over.append((int(m.group(1)), closed))
                break
    return over


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("disk", help="a Disk BASIC .fds, or a MiSTer .sav of one")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--list", action="store_true", help="what is on the disk")
    what.add_argument("--extract", action="store_true", help="the saved program, as text")
    what.add_argument("--insert", metavar="PROGRAM.BAS", help="replace the saved program")
    ap.add_argument("-o", "--output", help="where to write (default: stdout for --extract)")
    ap.add_argument("--over-tail", action="store_true",
                    help="write over what looks like a file the disk does not count "
                         "(it may be the tail of a program that was replaced)")
    args = ap.parse_args()

    # `--list` writes its listing to stdout and has nowhere to put a file. Accepting `-o`
    # and ignoring it let someone believe the listing had been saved - and the behaviour
    # was not even consistent, because the same-file guard below did fire on it when the
    # path already existed (found in review).
    # `is not None`, not truthiness: `-o ""` came through as "no output given" and both
    # --list and --extract then printed to stdout and exited 0, so `-o "$OUT"` with an
    # unset variable reported success with no file written (found in review).
    if args.output is not None and not args.output:
        ap.error("-o was given an empty name")
    if args.list and args.output is not None:
        ap.error("--list writes to stdout; -o has nothing to do here")

    # No output may name any input. Comparing path strings is not enough: a symlink, a
    # hard link or a second mount reaches the same bytes under a different name, so ask the
    # filesystem what the file *is*. The first version guarded only --insert's disk, which
    # left `--extract -o disk.fds` free to overwrite the disk with its own listing and
    # `--insert prog.bas -o prog.bas` free to overwrite the source (found in review).
    if args.output and args.output != "-" and os.path.exists(args.output):
        inputs = [("the disk", args.disk)]
        if args.insert:
            inputs.append(("the program it was told to insert", args.insert))
        for what, path in inputs:
            if os.path.exists(path) and os.path.samefile(path, args.output):
                sys.exit(f"-o names {what} ({args.output}); this will not write over its "
                         f"own input")

    with open(args.disk, "rb") as fh:
        raw = fh.read()
    offset, shape = find_side(raw, args.disk)
    side = bytearray(raw[offset:offset + SIDE_SIZE])
    if len(side) < SIDE_SIZE:
        sys.exit(f"{args.disk}: a side is {SIDE_SIZE} bytes, only {len(side)} are here")
    declared, files = read_files(side)
    # Which build wrote this disk, decided once and used by all three commands. Deciding
    # it per command is how `--list` and `--insert` came to disagree in the first place.
    lay = find_layout(files, args.disk)

    # Diagnostics go to stderr so that `--extract` without `-o` can be redirected or piped
    # and still produce a file that `--insert` accepts. Mixing them was caught in review.
    note = (lambda *a: print(*a, file=sys.stderr)) if args.extract else print
    note(f"## {args.disk}")
    note(f"  shape        {shape}, side starts at offset {offset}")
    note(f"  MD5          {hashlib.md5(raw).hexdigest()}")
    note(f"  files        {declared}")
    if lay is not None:
        note(f"  build        {lay.name}  (saved program at ${lay.load_at:04X})")

    if args.list:
        used = files[-1]["end"] if files else INFO_SIZE + 2
        for f in files:
            name = f["name"].decode("ascii", "replace").rstrip()
            print(f"    #{f['number']} id={f['id']:<4} {name:<9} "
                  f"-> ${f['address']:04X}  {f['size']:>6} bytes"
                  f"{program_note(f, files, lay)}")
        print(f"  used         {used} of {SIDE_SIZE} ({SIDE_SIZE - used} free)")
        return 0

    if args.extract:
        prog = find_program(files, lay)
        if prog is None:
            # `find_program` answers `None` only when no build's address matched at all -
            # every other absence it refuses itself, naming the reason. So there is one
            # case here, not two.
            #
            # ⚠️ It was written as two, with a second message quoting `lay.load_at` for a
            # disk where `lay` had been decided. That branch cannot run: `lay` is decided
            # *by* a file loading at that address, so if `lay` exists the file exists
            # (found in review). A branch that cannot run is a message nobody will ever
            # read and a claim nobody can check.
            #
            # The addresses come from the table rather than being typed, so this message
            # cannot go on quoting one build's address to a disk from the other.
            where = ", ".join(f"${l.load_at:04X} ({l.name})" for l in layouts())
            sys.exit(f"no file on this disk loads where a saved program goes "
                     f"({where}), so nothing has been saved to it")
        text, escaped = program_text(prog["data"], read_tokens(files, args.disk), lay)
        note(f"  program      {len(text.splitlines())} lines, "
             f"{escaped} byte(s) not understood"
             f"{'' if escaped == 0 else '  <- shown as \\xNN'}")
        if args.output and args.output != "-":
            # `with`, and inside the refusal. A bare `open(...).write(...)` leaves the
            # close to the garbage collector, which swallows any error it raises - so a
            # full disk or a failing write could be reported as success (review
            # R11). Opening it can fail too: a path in a directory that is not there was
            # a traceback, the way the input paths were.
            with open(args.output, "w") as fh:
                fh.write(text)
            note(f"  -> {args.output} ({len(text.splitlines())} lines)")
        else:
            sys.stdout.write(text)
        return 0

    # --insert
    if not args.output:
        sys.exit("--insert needs -o: it will not write over the disk it was given")
    if args.output == "-":
        # `-` means stdout for --extract, and --insert writes a 65,500-byte image; opening
        # it as a file named `-` would also slip past the same-file check above (found in review).
        sys.exit("--insert writes a disk image; give -o a filename, not `-`")

    try:
        with open(args.insert) as fh:
            text = fh.read()
    except UnicodeDecodeError as e:
        # A `.bas` is text. Handing this one a disk image or a ROM by mistake is an easy
        # slip, and it came back as a codec traceback (found in review).
        sys.exit(f"{args.insert}: not text ({e.reason} at byte {e.start}) - a program "
                 f"source is what goes here, not a disk image or a ROM")
    # The program being replaced is found **before** the new one is encoded, for two
    # reasons. Its header is what the new header is made from - everything between the end
    # pointer and the body is bytes this file has no name for, and inventing them is how a
    # build gets a header that boots into nothing. And which build wrote the disk decides
    # how the new program is encoded, so there is nothing to encode until that is known.
    prog = find_program(files, lay)
    if prog is None:
        sys.exit("this disk has no saved program, so there is no file id to reuse. Save "
                 "once from a machine and try again - a freshly built disk has not got one "
                 "either.")
    # ⚠️ Long enough to *have* the bytes that are about to be carried over. A file holding
    # only its signature satisfies `program_fault` - that check is about what the file is,
    # not how long it is - and the slice below then silently came up short, producing a
    # disk whose own end pointer pointed past its own data. This tool's `--extract` then
    # refused the disk this tool had just written (found in review).
    if len(prog["data"]) < lay.body_off:
        sys.exit(f"the saved program on this disk is {len(prog['data'])} bytes, too short "
                 f"to hold this build's {lay.body_off}-byte header. Nothing can be carried "
                 f"over from it, so this will not write a replacement based on it.")
    try:
        body = text_to_program(text, read_tokens(files, args.disk), lay)
    except ValueError as e:
        # The tokenizer refuses by raising, and nothing caught it - so a typo in the
        # program, which is the likeliest mistake anyone makes here, came out as a
        # traceback with the tool's own source in it. Every other bad input this tool
        # takes is refused in words; this one was not (found in review).
        sys.exit(f"{args.insert}: {e}")
    # Taken, not refused - see `text_to_program` - but said out loud, because the program
    # that comes back will stop on those lines if they ever run.
    for number, closed in long_literal_note(text, read_tokens(files, args.disk), lay):
        note(f"  ⚠️ line {number} holds a string literal past the machine's limit; it "
             + ("will raise ?IL ERROR if that line runs (measured)"
                if closed else
                "is unterminated, and whether that raises ?IL ERROR has not been measured"))
    end = lay.load_at + lay.body_off + len(body)
    # fb-basic-to-sav.py lays V2 out at $7000 and stops at $7FFF, so it accepts 4,034
    # bytes; the disk build moved the area to $6000 and has far more. A program between
    # the two is legal here and refused there, so the check that matters is this one - and
    # the message has to name the real ceiling (found in review).
    #
    # ★ The ceiling is the build's own `BYTES FREE`, read from the same table as everything
    # else. It used to be `$7FFF`, the top of the RAM, which is **four bytes higher** than
    # what the machine reports free - for both builds, which is what makes it look like a
    # rule rather than a coincidence (found in review).
    #
    # ⚠️ **What those four bytes are is not established anywhere in this repository.**
    # `bytes_free` was read off the screen; `$7FFF` is where the RAM ends. So this takes
    # the smaller of the two on purpose: refusing four bytes that might have been usable
    # costs four bytes, and accepting four bytes the interpreter has spoken for costs a
    # program that misbehaves after it loads. **This is a conservative choice, not a
    # measurement.** What would settle it: fill the area to `bytes_free`, then to
    # `bytes_free + 4`, save and reload each on a machine, and see which comes back.
    if len(body) > lay.bytes_free:
        sys.exit(f"the program is {len(body)} bytes and this build reports "
                 f"{lay.bytes_free} BYTES FREE. It is {len(body) - lay.bytes_free} "
                 f"byte(s) too long.")
    keep = prog["data"][lay.end_ptr_off + 2:lay.body_off]      # empty on V2.1A
    data = lay.sig + end.to_bytes(2, "little") + keep + body
    position, cut, name, file_id = prog["number"], prog["header_at"], prog["name"], prog["id"]
    # Replacing a program with one of a different length moves everything behind it. The
    # BIOS walks the files in order, so a file that follows is not found by looking it up -
    # it is found by arriving at it, and it is no longer where arriving lands. A longer
    # program writes straight over it; a shorter one leaves a gap the walk falls into.
    # Either way the following files are gone (found in review).
    #
    # The reason used to be given as the file count: an earlier version set it to
    # `position + 1` and so dropped anything after this file from it. That line was
    # removed rounds ago - the count is left alone now - but the comment and the message
    # kept saying it, which sent anyone reading them after the wrong thing.
    #
    # By where the file physically sits, not by the number in its header: block 2's count
    # and block 3's number are separate fields (NESdev wiki, *FDS disk format*), so a disk whose
    # numbers are duplicated or out of order would hide a file that is really after this
    # one and get it overwritten (found in review).
    # A file past the declared count is invisible to `read_files`, which stops there
    # because bytes after the last file are whatever was on the disk before. The format
    # allows exactly that as a hiding place - the BIOS ignores anything past the count
    # (NESdev wiki, *FDS disk format*) - so "not in the count" does not mean "not there", and a
    # longer program written over one would take it with it (found in review).
    #
    # Only a real header counts: block $03, then a block $04 where its length says. Old
    # program bytes left on the disk are expected here (the tool deliberately does not
    # blank them), and demanding both markers keeps *most* of them from reading as a file.
    #
    # **Most, not all, and being cleverer cannot fix it.** A `REM` holds any bytes, so a
    # program can contain a perfectly formed header - both reviewers built one - and after
    # a long program is replaced by a short one, the next long program is refused for
    # ever. Nothing distinguishes "a file somebody hid here" from "bytes that look like
    # one": they are the same bytes. The refusal stays, because losing a file is worse
    # than stopping, and `--over-tail` is how you say "that is my old program"
    # (found in review).

    after = [f for f in files if f["header_at"] > cut]
    if after:
        names = ", ".join(f["name"].decode("ascii", "replace").rstrip() for f in after)
        sys.exit(f"{len(after)} file(s) sit after the saved program on this disk ({names}). "
                 f"A program of a different length moves everything behind it, and the "
                 f"BIOS finds those files by walking to them, so they would be lost. This "
                 f"refuses rather than lose them.")

    hdr = (bytes([BLOCK_FILE_HEADER, position, file_id]) + name
           + lay.load_at.to_bytes(2, "little") + len(data).to_bytes(2, "little")
           + bytes([kind_prg()]))
    block = hdr + bytes([BLOCK_FILE_DATA]) + data
    hidden = None if args.over_tail else hidden_file_in(side, prog["end"],
                                                       cut + len(block))
    if hidden:
        sys.exit(f"a file the disk does not count sits at offset {hidden['header_at']} "
                 f"({hidden['name'] or 'unnamed'}), and this program is long enough to "
                 f"reach it. The disk's file count hides it from the BIOS, not from the "
                 f"bytes, so this refuses rather than write over it.\n\n"
                 f"If it is not a file - the tail of a longer program replaced by a "
                 f"shorter one lands here, and a REM can hold bytes shaped exactly like a "
                 f"header - pass --over-tail and it will be written over.")
    if cut + len(block) > SIDE_SIZE:
        sys.exit(f"the program needs {len(block)} bytes at offset {cut}, past the end of "
                 f"the side ({SIDE_SIZE})")

    # Write only as far as the block reaches, the way the drive does. Zeroing to the end
    # of the side would be tidier and is wrong: hardware that replaces a long program with
    # a short one leaves the tail of the old one physically on the disk, and blanking it
    # makes this tool's output differ from the disk it read - the round trip that is
    # supposed to prove the tool agrees with the format (found in review).
    side[cut:cut + len(block)] = block
    # The count is left alone. Replacing does not change how many files there are, and
    # `after` above has already established this one is physically last. Deriving it from
    # the header's number instead (`position + 1`) only happens to be right when the
    # numbers run 0..n-1, and corrupts the count on a disk where they do not - the same
    # half-applied fix as the one that used to pick `after` by number (found in review).
    out = bytearray(raw)
    out[offset:offset + SIDE_SIZE] = side
    with open(args.output, "wb") as fh:
        fh.write(bytes(out))
    print(f"  -> {args.output}")
    print(f"     program   {len(text.splitlines())} lines, {len(body)} bytes"
          f" (ends at ${end:04X})")
    print(f"     file      #{position} id={file_id} at offset {cut}, {len(block)} bytes")
    print(f"     MD5       {hashlib.md5(bytes(out)).hexdigest()}")
    return 0



def _run(fn):
    """Turn a filesystem refusal into a sentence, at the one place every path ends up.

    Every tool here takes paths from the command line, and until 2026-08-26 a missing or
    unreadable one arrived as a `FileNotFoundError` traceback with this file's own source
    in it - while every other bad input was refused in words (found in review).
    Catching it per `open()` would have meant the same rule at a dozen call sites; here it
    is one place per tool.

    ⚠️ **And one place per tool means one.** Four `open()` calls in this file kept their
    own `except OSError` after this went in, each producing the same `path: reason` this
    does. They were harmless and they were a second copy of the rule, which is the shape
    this project keeps finding - a reviewer read the sentence above, counted the copies,
    and the sentence was the thing that was wrong (found in review). The copies are gone.

    ⚠️ **One place per tool, not one place** - this function is copied into each of them,
    which is the shape this project usually refuses. It is allowed here for a stated
    reason: every tool in `tools/` is a single file that runs on its own with nothing but
    the standard library, so there is nowhere shared to put it that does not make the
    tools depend on each other. What makes copies dangerous is holding a **fact** that can
    drift apart; this holds none - no address, no version, no size - so two copies can
    only ever differ in wording, not in what they decide.

    `BrokenPipeError` is deliberately not caught: `| head` closing the pipe is not a fault
    to report, and turning it into a message would put one on every truncated listing."""
    try:
        return fn()
    except BrokenPipeError:
        raise
    except OSError as e:
        where = f"{e.filename}: " if getattr(e, "filename", None) else ""
        sys.exit(f"{where}{e.strerror or e}")


if __name__ == "__main__":
    sys.exit(_run(main))
