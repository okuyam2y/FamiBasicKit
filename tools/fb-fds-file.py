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

`fb-fds.py`'s `SAVE` writes one file, at position 3 (the fourth file), holding everything
from `$603A`: two signature bytes, the two-byte end-of-program pointer, then the program.
Its file id is **above** the disk's boot read file code so the BIOS does not read it at
power-on - see `DISK_FILE_ID` in `fb-fds.py`.

This tool finds that file by **where it loads** (`$603A`), not by its name and not by its
id. A name selects nothing on this build, and the id is a constant that has already
changed once.

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

SIDE_SIZE = 65500
FWNES_MAGIC = b"FDS\x1a"
MISTER_SLACK = 16              # what MiSTer puts in front of the side (measured)

BLOCK_DISK_INFO, BLOCK_FILE_AMOUNT = 1, 2
BLOCK_FILE_HEADER, BLOCK_FILE_DATA = 3, 4
INFO_SIZE, HEADER_SIZE = 56, 16
COUNT_AT = 0x39                # the file-count byte inside the side

PROGRAM_LOAD_AT = 0x603A       # signature, end pointer, then the program
AREA_TOP = 0x7FFF              # the disk build's program area ends here
SIGNATURE = b"\x5A\x33"
KIND_PRG = 0
PRG_LOAD_AT = 0x8000    # where the disk's own BASIC loads (fb-fds.py's PRG_BASE)


def load_sav_module():
    spec = importlib.util.spec_from_file_location("fbsav", FB_SAV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def find_program(files):
    """The saved program, or None if this disk has not got one.

    What makes a file the saved program is `program_fault`'s to say - the address, the
    kind, being the only one, and the save signature. This turns each way of failing into
    its own refusal; `--list` turns the same codes into notes. They used to decide it
    separately and disagreed five times over (found in review)."""
    at_load = [f for f in files if f["address"] == PROGRAM_LOAD_AT]
    if not at_load:
        return None
    good = [f for f in at_load if program_fault(f, files) is None]
    if len(good) == 1:
        return good[0]
    fault = program_fault(at_load[0], files)
    if fault == "mixed-kinds" or fault == "wrong-kind":
        wrong = [f for f in at_load if f["kind"] != KIND_PRG]
        names = ", ".join(f'{f["name"].decode("ascii", "replace").rstrip()} (kind {f["kind"]})'
                          for f in wrong)
        sys.exit(f"{len(wrong)} file(s) load at ${PROGRAM_LOAD_AT:04X} but are not "
                 f"kind {KIND_PRG}, so the BIOS sends them to the PPU, not to $603A: "
                 f"{names}. This will not treat one as the saved program.")
    if fault == "ambiguous":
        sys.exit(f"{len(at_load)} files load at ${PROGRAM_LOAD_AT:04X}; "
                 f"this build writes one")
    sys.exit(f"the file at ${PROGRAM_LOAD_AT:04X} does not start with the save "
             f"signature {SIGNATURE.hex(' ')} - it holds "
             f"{at_load[0]['data'][:2].hex(' ') or '(nothing)'}. Something other than a "
             f"saved BASIC program is there, and this will not write over it.")


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


def program_fault(f, files):
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
    if f["address"] != PROGRAM_LOAD_AT:
        return "elsewhere"
    if f["kind"] != KIND_PRG:
        return "wrong-kind"
    at_load = [g for g in files if g["address"] == PROGRAM_LOAD_AT]
    if any(g["kind"] != KIND_PRG for g in at_load):
        return "mixed-kinds"
    if len(at_load) > 1:
        return "ambiguous"
    if f["data"][:2] != SIGNATURE:
        return "no-signature"
    return None


def program_note(f, files):
    """What `--list` says about a file. The rule is `program_fault`'s; this is the wording.

    Listing is the command that has to keep showing a disk the other two refuse, so it says
    why rather than stopping."""
    fault = program_fault(f, files)
    at_load = [g for g in files if g["address"] == PROGRAM_LOAD_AT]
    if fault is None:
        return "   <- the saved program"
    if fault == "elsewhere":
        return ""
    if fault == "wrong-kind":
        return f"   <- at $603A but kind {f['kind']}, so the BIOS sends it to the PPU"
    if fault == "mixed-kinds":
        return (f"   <- at $603A, but {len(at_load)} files are and not all of them are "
                f"kind {KIND_PRG}, so which is unclear")
    if fault == "ambiguous":
        return (f"   <- one of {len(at_load)} files at $603A; this build writes one, "
                f"so which is unclear")
    return "   <- at $603A, but without the save signature: not a saved program"


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
    # the file the builder writes: kind 0, loaded at $8000.
    hits = [f for f in files
            if f["kind"] == KIND_PRG and f["address"] == PRG_LOAD_AT]
    if not hits:
        sys.exit(f"{disk_path}: no file on this disk is a program loaded at "
                 f"${PRG_LOAD_AT:04X}, so there is no BASIC to read the reserved words from")
    if len(hits) > 1:
        names = ", ".join(f["name"].decode("ascii", "replace").rstrip() for f in hits)
        sys.exit(f"{disk_path}: {len(hits)} files load at ${PRG_LOAD_AT:04X} ({names}); "
                 f"which one is the BASIC that runs is not decidable, so this stops here")
    try:
        return sav.read_token_table(hits[0]["data"])
    except ValueError:
        sys.exit(f"{disk_path}: the file at ${PRG_LOAD_AT:04X} on this disk has no "
                 f"reserved-word table where one is expected - is this a Family BASIC disk?")


def program_text(data, tokens):
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
    if len(data) < 5:
        sys.exit(f"the saved file is {len(data)} bytes; the signature, the end pointer and "
                 f"an empty program need 5")
    end_ptr = data[2] | (data[3] << 8)
    end_off = end_ptr - PROGRAM_LOAD_AT
    if not 4 <= end_off <= len(data):
        sys.exit(f"the end pointer says the program stops at ${end_ptr:04X}, which is not "
                 f"inside this file (${PROGRAM_LOAD_AT:04X}..${PROGRAM_LOAD_AT + len(data):04X})")
    if end_off != len(data):
        sys.exit(f"the end pointer says the program stops at ${end_ptr:04X}, "
                 f"{len(data) - end_off} byte(s) before the file ends - this file and the "
                 f"disk's record of it disagree")
    body, lines, escaped = data[4:], [], 0
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
        # V2.1A does not use the one-byte 0-9 form, and `text_to_program` already encodes
        # with it off. Leaving the default on made the two directions disagree about what a
        # low byte means (found in review).
        try:
            text, n = sav.decode_body(body[i + 3:i + length - 1], tokens,
                                      sav.SMALL_DIGITS_BY_VERSION["v2"])
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


def text_to_program(text, tokens):
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
    return sav.build_program(text, tokens, sav.SMALL_DIGITS_BY_VERSION["v2"])


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

    try:
        with open(args.disk, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        sys.exit(f"{args.disk}: {e.strerror or e}")
    offset, shape = find_side(raw, args.disk)
    side = bytearray(raw[offset:offset + SIDE_SIZE])
    if len(side) < SIDE_SIZE:
        sys.exit(f"{args.disk}: a side is {SIDE_SIZE} bytes, only {len(side)} are here")
    declared, files = read_files(side)

    # Diagnostics go to stderr so that `--extract` without `-o` can be redirected or piped
    # and still produce a file that `--insert` accepts. Mixing them was caught in review.
    note = (lambda *a: print(*a, file=sys.stderr)) if args.extract else print
    note(f"## {args.disk}")
    note(f"  shape        {shape}, side starts at offset {offset}")
    note(f"  MD5          {hashlib.md5(raw).hexdigest()}")
    note(f"  files        {declared}")

    if args.list:
        used = files[-1]["end"] if files else INFO_SIZE + 2
        for f in files:
            name = f["name"].decode("ascii", "replace").rstrip()
            print(f"    #{f['number']} id={f['id']:<4} {name:<9} "
                  f"-> ${f['address']:04X}  {f['size']:>6} bytes"
                  f"{program_note(f, files)}")
        print(f"  used         {used} of {SIDE_SIZE} ({SIDE_SIZE - used} free)")
        return 0

    if args.extract:
        prog = find_program(files)
        if prog is None:
            sys.exit("no file on this disk loads at $603A, so nothing has been saved to it")
        text, escaped = program_text(prog["data"], read_tokens(files, args.disk))
        note(f"  program      {len(text.splitlines())} lines, "
             f"{escaped} byte(s) not understood"
             f"{'' if escaped == 0 else '  <- shown as \\xNN'}")
        if args.output and args.output != "-":
            # `with`, and inside the refusal. A bare `open(...).write(...)` leaves the
            # close to the garbage collector, which swallows any error it raises - so a
            # full disk or a failing write could be reported as success (review
            # R11). Opening it can fail too: a path in a directory that is not there was
            # a traceback, the way the input paths were.
            try:
                with open(args.output, "w") as fh:
                    fh.write(text)
            except OSError as e:
                sys.exit(f"{args.output}: {e.strerror or e}")
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
    except OSError as e:
        sys.exit(f"{args.insert}: {e.strerror or e}")
    except UnicodeDecodeError as e:
        # A `.bas` is text. Handing this one a disk image or a ROM by mistake is an easy
        # slip, and it came back as a codec traceback (found in review).
        sys.exit(f"{args.insert}: not text ({e.reason} at byte {e.start}) - a program "
                 f"source is what goes here, not a disk image or a ROM")
    try:
        body = text_to_program(text, read_tokens(files, args.disk))
    except ValueError as e:
        # The tokenizer refuses by raising, and nothing caught it - so a typo in the
        # program, which is the likeliest mistake anyone makes here, came out as a
        # traceback with the tool's own source in it. Every other bad input this tool
        # takes is refused in words; this one was not (found in review).
        sys.exit(f"{args.insert}: {e}")
    end = PROGRAM_LOAD_AT + 4 + len(body)
    # fb-basic-to-sav.py lays V2 out at $7000 and stops at $7FFF, so it accepts 4,034
    # bytes; the disk build moved the area to $6000 and has room for 8,130. A program
    # between the two is legal here and refused there, so the check that matters is this
    # one - and the message has to name the real ceiling (found in review).
    if end > AREA_TOP + 1:
        sys.exit(f"the program needs ${PROGRAM_LOAD_AT:04X}-${end - 1:04X}, past the top "
                 f"of the disk build's area (${AREA_TOP:04X}). It is "
                 f"{end - 1 - AREA_TOP} byte(s) too long.")
    data = SIGNATURE + end.to_bytes(2, "little") + body

    prog = find_program(files)
    if prog is None:
        sys.exit("this disk has no saved program, so there is no file id to reuse. Save "
                 "once from a machine and try again - a freshly built disk has not got one "
                 "either.")
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
           + PROGRAM_LOAD_AT.to_bytes(2, "little") + len(data).to_bytes(2, "little")
           + bytes([KIND_PRG]))
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
    try:
        with open(args.output, "wb") as fh:
            fh.write(bytes(out))
    except OSError as e:
        sys.exit(f"{args.output}: {e.strerror or e}")
    print(f"  -> {args.output}")
    print(f"     program   {len(text.splitlines())} lines, {len(body)} bytes"
          f" (ends at ${end:04X})")
    print(f"     file      #{position} id={file_id} at offset {cut}, {len(block)} bytes")
    print(f"     MD5       {hashlib.md5(bytes(out)).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
