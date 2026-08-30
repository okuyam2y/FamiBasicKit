#!/usr/bin/env python3
"""Read and replace the pictures: the 8KB of tiles a Family BASIC ROM draws from.

  $ ./fb-chr.py "Family BASIC V3 (Japan).nes" --sheet tiles.png
  $ ./fb-chr.py "Family BASIC V3 (Japan).nes" --apply tiles.png -o "V3 (new art).nes"
  $ ./fb-chr.py "Family BASIC V3 (Japan).nes" --map
  $ ./fb-chr.py "Family BASIC V3 (Japan).nes" --show 0

## What is going on

Everything the machine can draw - Mario, the fighter fly, the letters, the kana - is 8KB
sitting at the end of the `.nes` file: **512 tiles of 8x8 pixels, four shades each**. It is
not compressed and not addressed by any table; the picture for tile `n` is the sixteen
bytes at `n * 16`.

* **tiles 0-255** are what sprites are built from - the characters `MOVE` and `SPRITE` put
  on screen
* **tiles 256-511** are what the text screen draws - ASCII in the same numbering BASIC
  uses, so tile `256 + 65` is `A`, and the kana follow

The four dumps this repository works from - V1.0, V2.0A, V2.1A and V3.0 - carry **the same
8KB, byte for byte** (`ec06b3c44be7ee18133e3d32c82465dc`). One edited sheet therefore fits
every version.

★ **Change the art last.** Every builder here copies the block through untouched, so a
built ROM takes new art perfectly well - measured on 2026-08-31 for the 8KB NROM, the 16KB
MMC5 and the VRC7 builds, and each one drew the new tile on screen. Going the other way
round does not work: `fb-relocate.py` and `fb-vrc7.py` check a digest that covers the CHR
as well as the program, and `fb-mmc5-16k.py` requires its two inputs to carry the same CHR,
so all three refuse a dump whose pictures have been touched. (`fb-expand-basic-area.py`
pins no digest and does accept one.) Those guards exist to make sure the patch addresses
belong to the dump in hand, and are better left alone.

⚠️ **Replacing the art changes the ROM's MD5.** The figures recorded in this repository -
`541f9769...` for the 16KB build and the rest - are for stock art. A rebuild with new
tiles is *supposed* to differ from them.

⚠️ **The disk build has no such block**: it holds its tiles in RAM and loads them from the
disk, so this tool has nothing to edit there.

## The sheet

`--sheet` writes the 512 tiles as a 128x256 PNG, sixteen tiles across and thirty-two down,
one pixel per pixel. `--apply` reads that same shape back. The four shades are white, light
grey, dark grey and black - shade 0 is what the PPU leaves transparent on a sprite and
fills with the backdrop colour on the text screen.

Colours must land back on those four exactly. An editor that anti-aliases or converts to a
different palette will produce shades in between, and this tool stops rather than guessing;
`--snap` says "take the nearest of the four" and reports how many pixels it moved. Fully
transparent pixels count as shade 0, so erasing works as an editor makes you expect.

## Which tiles are which character (`--map`)

A sprite the user moves is four tiles in a 2x2 block, and the ROM holds a table of those
blocks for each of the sixteen characters `SPRITE(c, ...)` accepts. This tool reads that
table **out of the ROM handed to it**, by finding the structure rather than by knowing an
address: sixteen pointers whose targets are each three more pointers, the middle of which
is a sheet of nine more (one per facing), each of those a block of four tile numbers. Only
one place in a Family BASIC ROM looks like that, and the tool refuses to guess if it finds
none or more than one.

The number the machine actually draws is

    tile = block[i] + offset[c] + 4 * frame

where `offset[c]` is a per-character constant from the sixteen bytes that follow the
pointer table, and `frame` steps through the animation. That is why several characters
share one block table: the fighter fly, and four others, are the same nine blocks read
through different offsets.

Two things about that table are not uniform, and both are read out of the ROM rather than
switched on a version number: V1.0 and V2.x keep a frame count already multiplied by
sixteen and their flips in a separate table, where V3.0 packs both into one byte; and
characters 0, 1 and 4 have a climbing frame written into the code instead of into the
table. Neither adds a tile, but getting either wrong changes the tile list - see
`CODE_SECOND_FRAME` and `frame_count_form`.
"""

import argparse
import hashlib
import os
import struct
import sys
import zlib

TILE_BYTES = 16
CHR_SIZE = 8192
N_TILES = CHR_SIZE // TILE_BYTES        # 512
SHEET_COLS = 16
SHEET_ROWS = N_TILES // SHEET_COLS      # 32
SHEET_W = SHEET_COLS * 8                # 128
SHEET_H = SHEET_ROWS * 8                # 256

# The four shades, in the order the PPU numbers them. Grey ramp rather than anything
# prettier: the point is that an editor shows four obviously different colours and hands
# them back unchanged.
PALETTE = [(0xFF, 0xFF, 0xFF), (0xAA, 0xAA, 0xAA), (0x55, 0x55, 0x55), (0x00, 0x00, 0x00)]
ART = " .+#"                            # the same four, for a terminal


# ---------------------------------------------------------------- the ROM file

class Rom:
    """An iNES / NES 2.0 file split into the parts this tool touches."""

    def __init__(self, path):
        data = open(path, "rb").read()
        if data[:4] != b"NES\x1a":
            sys.exit(f"{path}: not an iNES file (no 'NES\\x1a' at the start)")
        self.path = path
        self.header = bytearray(data[:16])
        nes2 = (data[7] & 0x0C) == 0x08
        prg_units, chr_units = data[4], data[5]
        if nes2:
            if (data[9] & 0x0F) == 0x0F or (data[9] >> 4) == 0x0F:
                sys.exit(f"{path}: NES 2.0 exponent-form sizes are not handled")
            prg_units |= (data[9] & 0x0F) << 8
            chr_units |= (data[9] >> 4) << 8
        prg_len, chr_len = prg_units * 16384, chr_units * 8192
        off = 16
        self.trainer = b""
        if data[6] & 0x04:
            self.trainer, off = data[16:528], 528
        self.prg = bytearray(data[off:off + prg_len])
        self.chr = bytearray(data[off + prg_len:off + prg_len + chr_len])
        self.tail = data[off + prg_len + chr_len:]
        if len(self.prg) != prg_len or len(self.chr) != chr_len:
            sys.exit(f"{path}: file is shorter than its header says "
                     f"(wants {prg_len}+{chr_len} bytes, has {len(data) - off})")

    def bytes(self):
        return bytes(self.header) + self.trainer + bytes(self.prg) + bytes(self.chr) + self.tail

    def require_chr(self):
        if len(self.chr) == 0:
            sys.exit(f"{self.path}: this ROM has no CHR-ROM - its tiles live in RAM, "
                     "so there is nothing in the file to edit")
        if len(self.chr) != CHR_SIZE:
            sys.exit(f"{self.path}: expected {CHR_SIZE} bytes of CHR, found {len(self.chr)}")


# ---------------------------------------------------------------- tiles

def tile_pixels(chr_data, n):
    """The 8x8 shade numbers of tile `n`."""
    b = chr_data[n * TILE_BYTES:(n + 1) * TILE_BYTES]
    rows = []
    for y in range(8):
        lo, hi = b[y], b[y + 8]
        rows.append([((lo >> (7 - x)) & 1) | (((hi >> (7 - x)) & 1) << 1) for x in range(8)])
    return rows


def tile_bytes(rows):
    """The sixteen ROM bytes for one 8x8 block of shade numbers."""
    out = bytearray(TILE_BYTES)
    for y in range(8):
        lo = hi = 0
        for x in range(8):
            v = rows[y][x]
            lo |= (v & 1) << (7 - x)
            hi |= ((v >> 1) & 1) << (7 - x)
        out[y], out[y + 8] = lo, hi
    return out


def chr_to_rows(chr_data):
    """The whole 8KB as `SHEET_H` rows of `SHEET_W` shade numbers."""
    rows = [bytearray(SHEET_W) for _ in range(SHEET_H)]
    for n in range(N_TILES):
        tx, ty = (n % SHEET_COLS) * 8, (n // SHEET_COLS) * 8
        px = tile_pixels(chr_data, n)
        for y in range(8):
            rows[ty + y][tx:tx + 8] = bytes(px[y])
    return rows


def rows_to_chr(rows):
    out = bytearray(CHR_SIZE)
    for n in range(N_TILES):
        tx, ty = (n % SHEET_COLS) * 8, (n // SHEET_COLS) * 8
        px = [list(rows[ty + y][tx:tx + 8]) for y in range(8)]
        out[n * TILE_BYTES:(n + 1) * TILE_BYTES] = tile_bytes(px)
    return out


# ---------------------------------------------------------------- PNG

def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_png(path, rows):
    raw = bytearray()
    for r in rows:
        raw.append(0)                   # filter type 0: store the row as it is
        raw += r
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 3, 0, 0, 0))
           + _chunk(b"PLTE", b"".join(bytes(c) for c in PALETTE))
           + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + _chunk(b"IEND", b""))
    open(path, "wb").write(png)


def read_png(path):
    """Any ordinary PNG, as (width, height, list of rows of (r, g, b, a)).

    Enough of the format to read back what an image editor saves: every colour type, every
    bit depth, both with and without an alpha channel. Interlaced files are refused rather
    than half-handled.
    """
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        sys.exit(f"{path}: not a PNG")
    pos, ihdr, plte, trns, idat = 8, None, None, None, bytearray()
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            ihdr = payload
        elif tag == b"PLTE":
            plte = payload
        elif tag == b"tRNS":
            trns = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if ihdr is None or not idat:
        sys.exit(f"{path}: PNG has no image data")
    w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if interlace:
        sys.exit(f"{path}: this PNG is interlaced; save it again without interlacing")
    if ctype not in (0, 2, 3, 4, 6):
        sys.exit(f"{path}: colour type {ctype} is not a PNG colour type")
    if ctype == 3 and plte is None:
        sys.exit(f"{path}: palette image with no palette")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = (w * channels * depth + 7) // 8
    bpp = max(1, (channels * depth) // 8)
    raw = zlib.decompress(bytes(idat))
    if len(raw) < h * (stride + 1):
        sys.exit(f"{path}: image data is short ({len(raw)} bytes for {h} rows of {stride})")

    lines, prev, p = [], bytearray(stride), 0
    for _y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                b = prev[i]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            sys.exit(f"{path}: unknown row filter {f}")
        lines.append(line)
        prev = line

    maxv = (1 << depth) - 1

    def sample(line, x, c):
        if depth == 8:
            return line[x * channels + c]
        if depth == 16:
            return line[(x * channels + c) * 2]      # high byte is enough for four shades
        per = 8 // depth
        idx = x * channels + c
        return (line[idx // per] >> (8 - depth * (idx % per + 1))) & maxv

    def scale(v):
        return v * 255 // maxv if depth < 8 else (v if depth == 8 else v)

    out = []
    for line in lines:
        row = []
        for x in range(w):
            if ctype == 0:
                g = scale(sample(line, x, 0))
                row.append((g, g, g, 255))
            elif ctype == 4:
                g = scale(sample(line, x, 0))
                row.append((g, g, g, scale(sample(line, x, 1))))
            elif ctype == 2:
                row.append(tuple(scale(sample(line, x, c)) for c in range(3)) + (255,))
            elif ctype == 6:
                row.append(tuple(scale(sample(line, x, c)) for c in range(4)))
            else:
                i = sample(line, x, 0)
                if (i + 1) * 3 > len(plte):
                    sys.exit(f"{path}: pixel refers to palette entry {i}, which is not there")
                a = trns[i] if trns is not None and i < len(trns) else 255
                row.append((plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2], a))
        out.append(row)
    return w, h, out


def png_to_rows(path, snap):
    """A PNG as shade numbers, or an exit with what stopped it."""
    w, h, pixels = read_png(path)
    if (w, h) != (SHEET_W, SHEET_H):
        sys.exit(f"{path}: expected a {SHEET_W}x{SHEET_H} sheet, this one is {w}x{h}")
    exact = {c: i for i, c in enumerate(PALETTE)}
    rows, strays, moved = [], {}, 0
    for line in pixels:
        row = bytearray(w)
        for x, (r, g, b, a) in enumerate(line):
            if a == 0:
                row[x] = 0
                continue
            i = exact.get((r, g, b))
            if i is None:
                strays[(r, g, b)] = strays.get((r, g, b), 0) + 1
                i = min(range(4), key=lambda k: sum((PALETTE[k][j] - (r, g, b)[j]) ** 2
                                                    for j in range(3)))
                moved += 1
            row[x] = i
        rows.append(row)
    if strays and not snap:
        listing = sorted(strays.items(), key=lambda kv: -kv[1])[:8]
        detail = ", ".join(f"#{r:02X}{g:02X}{b:02X} x{n}" for (r, g, b), n in listing)
        sys.exit(f"{path}: {moved} pixels are not one of the four shades ({detail}"
                 f"{', ...' if len(strays) > 8 else ''}).\n"
                 f"The four are " + ", ".join(f"#{r:02X}{g:02X}{b:02X}" for r, g, b in PALETTE)
                 + ".\nEdit with those exact colours, or pass --snap to take the nearest.")
    if moved:
        print(f"note: {moved} pixels snapped to the nearest of the four shades")
    return rows


# ---------------------------------------------------------------- the character tables

N_ACTORS = 16       # what `SPRITE(c, ...)` accepts for c
N_FACINGS = 9       # stationary, then eight directions

# Characters 0, 1 and 4 - the two people and the penguin - have a second climbing frame
# that no table names: for facings 1 and 5 the code overwrites the block pointer with an
# address written into the instruction stream and zeroes the frame offset. Every one of
# those blocks is **the same four tiles as the first frame, mirrored**, so the animation
# adds no tile; stepping the number on by four, the way every other facing works, would
# name tiles that belong to a different character. Read from the ROM at `$C9A7` (V3.0) and
# `$BF30` (V1.0/V2.x), where `CMP #$01` / `CMP #$04` pick the three characters out.
# The check that this is right is that the sixteen tile sets come out disjoint; with the
# four-step left in, the penguin runs into the fireball - which is how this is held to
# account: the sixteen tile sets have to come out disjoint, and they only do with it.
CODE_SECOND_FRAME = {(c, d) for c in (0, 1, 4) for d in (1, 5)}


def find_actor_tables(prg, base=0x8000):
    """Where the sixteen pointers indexed by a character number live, in this ROM.

    Found by shape, not by address, because the address moves between versions (`$C709` in
    V3.0, `$BC75` in V1.0/V2.x) and could move again in a build made here. The shape asked
    for is three levels deep:

        sixteen pointers -> a three-pointer record -> nine pointers -> four tile numbers

    Nothing else in a 32KB Family BASIC ROM satisfies it, and this refuses to answer if it
    finds no match or more than one, rather than picking.
    """
    top = base + len(prg)

    def word(a):
        i = a - base
        return prg[i] | (prg[i + 1] << 8)

    def inrom(a, need=1):
        return base <= a and a + need <= top

    hits = []
    for a in range(base, top - (32 + 16)):
        ptrs = [word(a + 2 * i) for i in range(N_ACTORS)]
        # The records sit after the pointer table and the sixteen offset bytes that follow
        # it, and close by: this is one block of data, not scattered.
        if not all(a + 48 <= p <= a + 0x200 and inrom(p, 6) for p in ptrs):
            continue
        records = sorted(set(ptrs))
        if not 3 <= len(records) <= 10:
            continue
        ok = True
        for t in records:
            counts_a, sheet, counts_b = word(t), word(t + 2), word(t + 4)
            if not (inrom(sheet, 2 * N_FACINGS) and sheet > counts_a and sheet > counts_b):
                ok = False
                break
            if not all(inrom(c, N_FACINGS) and abs(c - t) <= 0x100 for c in (counts_a, counts_b)):
                ok = False
                break
            quads = [word(sheet + 2 * d) for d in range(N_FACINGS)]
            if not all(inrom(q, 4) and sheet < q <= sheet + 0x200 for q in quads):
                ok = False
                break
        if ok:
            hits.append(a)
    if not hits:
        sys.exit("could not find the character tables in this ROM. A build that moved that "
                 "data would need this tool taught about it; nothing here does that today.")
    if len(hits) > 1:
        sys.exit("found " + str(len(hits)) + " places shaped like the character tables ("
                 + ", ".join(f"${h:04X}" for h in hits) + "); refusing to pick one")
    return hits[0]


def actor_map(prg, base=0x8000):
    """For each of the sixteen characters: the tile numbers it is drawn from."""
    a = find_actor_tables(prg, base)

    def word(x):
        i = x - base
        return prg[i] | (prg[i + 1] << 8)

    def byte(x):
        return prg[x - base]

    records = [word(a + 2 * c) for c in range(N_ACTORS)]
    shift = frame_count_form(prg, base, records)

    actors = []
    for c in range(N_ACTORS):
        record = records[c]
        offset = byte(a + 32 + c)
        counts_at, sheet, flags_at = word(record), word(record + 2), word(record + 4)
        facings, tiles = [], set()
        for d in range(N_FACINGS):
            quad_at = word(sheet + 2 * d)
            quad = [byte(quad_at + i) for i in range(4)]
            entry = byte(counts_at + d)
            frames = (entry >> 4) if shift else (entry & 0x0F)
            flags = byte(flags_at + d)
            in_code = (c, d) in CODE_SECOND_FRAME
            shown = 1 if in_code else max(frames, 1)
            drawn = [[(q + offset + 4 * f) & 0xFF for q in quad] for f in range(shown)]
            for row in drawn:
                tiles.update(row)
            facings.append({"quad_at": quad_at, "quad": quad, "frames": frames,
                            "hflip": bool(flags & 0x40), "vflip": bool(flags & 0x80),
                            "in_code": in_code, "drawn": drawn})
        actors.append({"n": c, "record": record, "sheet": sheet, "offset": offset,
                       "facings": facings, "tiles": sorted(tiles)})
    return a, actors


def frame_count_form(prg, base, records):
    """Is a frame count the low nibble of its byte, or the whole byte times sixteen?

    V3.0 stores the count in the low nibble and multiplies by sixteen when it uses it;
    V1.0 and V2.x store it already multiplied, and keep the flips in a separate table.
    Same numbers either way - `1 2 1 3 1 2 1 3 1` for the people in both - so the two are
    told apart by the bytes themselves rather than by a version this tool would have to be
    told. Anything that is neither stops it: a wrong reading here silently shortens every
    character's tile list.
    """
    def word(x):
        return prg[x - base] | (prg[x - base + 1] << 8)

    seen = set()
    for r in records:
        at = word(r)
        for d in range(N_FACINGS):
            seen.add(prg[at - base + d])
    packed = all(1 <= (b & 0x0F) <= 3 for b in seen)
    shifted = all((b & 0x0F) == 0 and 1 <= (b >> 4) <= 3 for b in seen)
    if packed == shifted:
        sys.exit("the frame-count tables read as neither form "
                 f"({' '.join(f'{b:02X}' for b in sorted(seen))}); refusing to guess")
    return shifted


def runs(numbers):
    """[0,1,2,5] -> '0-2, 5'."""
    out, i = [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        out.append(f"${numbers[i]:02X}" if i == j else f"${numbers[i]:02X}-${numbers[j]:02X}")
        i = j + 1
    return ", ".join(out)


# ---------------------------------------------------------------- terminal pictures

def art_lines(chr_data, tiles, wide=True):
    """`tiles` is a list of rows of tile numbers; returns the picture as text lines."""
    lines = []
    for trow in tiles:
        px = [tile_pixels(chr_data, t) for t in trow]
        for y in range(8):
            s = ""
            for p in px:
                for v in p[y]:
                    s += ART[v] * (2 if wide else 1)
            lines.append(s)
    return lines


def show_actor(chr_data, actor):
    print(f"character {actor['n']}  (blocks at ${actor['sheet']:04X}, "
          f"tile offset +${actor['offset']:02X})")
    print(f"  tiles: {runs(actor['tiles'])}")
    for d, f in enumerate(actor["facings"]):
        flips = "".join(x for x, on in (("H", f["hflip"]), ("V", f["vflip"])) if on)
        head = (f"  facing {d}  block ${f['quad_at']:04X}  "
                f"{f['frames']} frame{'' if f['frames'] == 1 else 's'}"
                + (f"  flip {flips}" if flips else ""))
        print(head)
        if f["in_code"] and f["frames"] > 1:
            print("    (its later frames are written into the code: the same four "
                  "tiles, mirrored)")
        for fr, quad in enumerate(f["drawn"]):
            rows = [[quad[0], quad[1]], [quad[2], quad[3]]]
            label = f"    frame {fr}: " + " ".join(f"${t:02X}" for t in quad)
            print(label)
            for line in art_lines(chr_data, rows):
                print("      " + line)


def draw_tile(grid, x, y, chr_data, tile, scale):
    px = tile_pixels(chr_data, tile)
    for ty in range(8):
        for tx in range(8):
            v = px[ty][tx]
            for sy in range(scale):
                row = grid[y + ty * scale + sy]
                for sx in range(scale):
                    row[x + tx * scale + sx] = v


def draw_text(grid, x, y, text, chr_data, scale=1):
    """Write with the ROM's own font: tile `256 + code` is the character `code` draws."""
    for i, ch in enumerate(text):
        draw_tile(grid, x + i * 8 * scale, y, chr_data, 256 + (ord(ch) & 0x7F), scale)


def index_sheet(chr_data, actors, scale=4, cols=4):
    """One picture of every character, with the number `SPRITE()` wants for it."""
    cell_w, cell_h = 16 * scale, 16 * scale + 10
    rows_of = (len(actors) + cols - 1) // cols
    w, h = cols * cell_w, rows_of * cell_h
    grid = [bytearray(w) for _ in range(h)]
    for a in actors:
        cx, cy = (a["n"] % cols) * cell_w, (a["n"] // cols) * cell_h
        for i, tile in enumerate(a["facings"][0]["drawn"][0]):
            draw_tile(grid, cx + (i % 2) * 8 * scale, cy + (i // 2) * 8 * scale,
                      chr_data, tile, scale)
        draw_text(grid, cx + 1, cy + 16 * scale + 1, f"{a['n']:2d}", chr_data)
    return grid


# ---------------------------------------------------------------- command line

def parse_range(text, limit):
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        base = 16 if part.lower().startswith("$") or part.lower().startswith("0x") else 10
        part = part.lstrip("$")
        if "-" in part[1:]:
            i = part.index("-", 1)
            lo, hi = int(part[:i], base), int(part[i + 1:].lstrip("$"), base)
        else:
            lo = hi = int(part, base)
        if not (0 <= lo <= hi < limit):
            sys.exit(f"{text}: outside 0-{limit - 1}")
        out += list(range(lo, hi + 1))
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Read and replace the tiles a Family BASIC ROM draws from.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("## What is going on", 1)[1])
    ap.add_argument("rom")
    ap.add_argument("--sheet", metavar="OUT.png", help="write the 512 tiles as a PNG")
    ap.add_argument("--apply", metavar="IN.png", help="put a PNG back into the ROM")
    ap.add_argument("-o", "--output", metavar="OUT.nes", help="where --apply writes the ROM")
    ap.add_argument("--snap", action="store_true",
                    help="with --apply: take the nearest of the four shades instead of stopping")
    ap.add_argument("--index", metavar="OUT.png",
                    help="write one picture of each of the sixteen characters, numbered")
    ap.add_argument("--map", action="store_true",
                    help="print which tiles each of the sixteen characters is drawn from")
    ap.add_argument("--show", metavar="N", help="draw a character in the terminal (e.g. 0, 0-3)")
    ap.add_argument("--tiles", metavar="N", help="draw raw tiles in the terminal (e.g. $100-$10F)")
    args = ap.parse_args()

    if not any((args.sheet, args.apply, args.map, args.index, args.show, args.tiles)):
        ap.error("nothing asked for: pick --sheet, --apply, --map, --index, --show or --tiles")

    rom = Rom(args.rom)
    if args.sheet or args.apply or args.index or args.show or args.tiles:
        rom.require_chr()

    if args.sheet:
        write_png(args.sheet, chr_to_rows(rom.chr))
        print(f"{args.sheet}: {SHEET_W}x{SHEET_H}, {N_TILES} tiles "
              f"(md5 of the tiles {hashlib.md5(bytes(rom.chr)).hexdigest()})")

    if args.apply:
        if not args.output:
            sys.exit("--apply needs -o: this tool never writes over the ROM it was given")
        if os.path.abspath(args.output) == os.path.abspath(args.rom):
            sys.exit("-o names the input ROM; write somewhere else")
        new = rows_to_chr(png_to_rows(args.apply, args.snap))
        before, after = hashlib.md5(bytes(rom.chr)).hexdigest(), hashlib.md5(bytes(new)).hexdigest()
        changed = sum(1 for n in range(N_TILES)
                      if new[n * TILE_BYTES:(n + 1) * TILE_BYTES]
                      != rom.chr[n * TILE_BYTES:(n + 1) * TILE_BYTES])
        rom.chr = new
        open(args.output, "wb").write(rom.bytes())
        print(f"{args.output}: {changed} of {N_TILES} tiles differ from the ROM's own")
        print(f"  tiles {before} -> {after}")
        print(f"  whole file md5 {hashlib.md5(rom.bytes()).hexdigest()}")

    if args.map or args.index or args.show:
        if len(rom.prg) != 32768:
            sys.exit(f"{args.rom}: the character tables are read from a 32KB program area; "
                     f"this ROM has {len(rom.prg)} bytes. Read the map from a stock dump - "
                     "the tables are the same data in every build.")
        at, actors = actor_map(rom.prg)

    if args.map:
        print(f"character tables at ${at:04X} (pointers), ${at + 32:04X} (tile offsets)")
        print()
        print("  c   blocks  offset  frames per facing        tiles")
        for a in actors:
            frames = "".join(str(f["frames"]) for f in a["facings"])
            print(f"  {a['n']:2d}  ${a['sheet']:04X}   +${a['offset']:02X}    {frames}"
                  f"    {runs(a['tiles'])}")
        print()
        print("  `frames per facing` is one digit per facing: 0 = stationary, then the")
        print("  eight directions. Characters that share a `blocks` address are the same")
        print("  nine pictures read through a different tile offset.")

    if args.index:
        grid = index_sheet(rom.chr, actors)
        write_png(args.index, grid)
        print(f"{args.index}: {len(grid[0])}x{len(grid)}, the sixteen characters as "
              "`SPRITE()` numbers them")

    if args.show:
        for n in parse_range(args.show, N_ACTORS):
            show_actor(rom.chr, actors[n])
            print()

    if args.tiles:
        for n in parse_range(args.tiles, N_TILES):
            print(f"tile ${n:03X} ({n})")
            for line in art_lines(rom.chr, [[n]]):
                print("  " + line)


if __name__ == "__main__":
    main()
