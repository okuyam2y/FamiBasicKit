# Expanding the program area of Family BASIC

[日本語](README.ja.md)

Tools for raising the free-area limit of Family BASIC (NS-HuBASIC, Famicom). The limit is
a constant burned into the ROM rather than the amount of RAM the hardware provides, so
rewriting a few bytes widens it — to 4KB on V1/V2 and 8KB on V3. Going past that means
moving the BASIC interpreter out of `$8000-$9FFF`, which is what the MMC5 build does to
reach 16KB. The same tools also rebuild the cartridge as a Famicom Disk System image, with
`SAVE` and `LOAD` going to the disk.

> ⚠️ **No ROM data is included.** Supply your own Family BASIC `.nes` dump. This
> repository ships Python scripts and documentation only.

## What you can build

| Build | From | Free area | What it takes |
|---|---|---|---|
| NROM 4KB | V1.0 / V2.0A / V2.1A | `4031 BYTES FREE` (V1.0), `4030 BYTES FREE` (V2.1A) | 2 PRG bytes + 1 header byte |
| NROM 8KB | V3.0 | `8182 BYTES FREE` | 5 PRG bytes + 1 header byte |
| MMC5 16KB | V3.0 | `16374 BYTES FREE` | MMC5 conversion + relocating the interpreter |
| Disk BASIC | V2.1A or V3.0 | 8,126 / 8,182 bytes | rebuilt as an FDS image; CHR becomes RAM and the FDS sound channel is reachable |

There is nothing between 8KB and 16KB: RAM is mapped in 8KB units, so 12KB is not an
option. The disk build is not the roomiest: choose it to save to disk, to rewrite
characters while a program runs, or to reach the FDS sound channel.

## Building one

```bash
# 8KB (V3) — or 4KB from a V1/V2 dump, which the tool detects
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB (V3 only)
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# a disk
./tools/fb-fds.py "Family BASIC V3 (Japan).nes" --bios disksys.rom -o fcbasic3.fds
```

Each tool verifies its input before doing anything and refuses a dump it does not
recognise. The accepted dumps, the flags, and how to put a program on the machine from a
PC are in [docs/manual/building.md](docs/manual/building.md).

## Documentation

Three kinds, in three directories, kept apart on purpose.

**[docs/manual/](docs/manual/)** — to use a build:

- [building.md](docs/manual/building.md) — which one to make, how to make it, getting a program in from a PC
- [disk-basic.md](docs/manual/disk-basic.md) — `SAVE` and `LOAD` on a disk build, and what the error numbers mean

**[docs/reference/](docs/reference/)** — to change one; read before you write, because
getting these wrong breaks quietly:

- [build-differences.md](docs/reference/build-differences.md) — what each build changes, and what it does not
- [ram-expansion.md](docs/reference/ram-expansion.md) — which bytes get rewritten, and why that is enough
- [sav-format.md](docs/reference/sav-format.md) — how a BASIC program is stored
- [token-numbering.md](docs/reference/token-numbering.md) — the rules for adding a keyword
- [mmc5-wram-banks.md](docs/reference/mmc5-wram-banks.md) — why an MMC5 bank number cannot be hard-coded, and what it corrupts if you do

**[docs/background/](docs/background/)** — why things are the way they are; needed for
neither of the above:

- [area-ceiling.md](docs/background/area-ceiling.md) — why this stops at 16KB, and what the next step would cost
- [relocation-notes.md](docs/background/relocation-notes.md) — the traps hit while relocating 8KB of code

## The tools

| File | What it does |
|---|---|
| `tools/fb-expand-basic-area.py` | Rewrites the area-limit constants (up to 8KB; mapper unchanged) |
| `tools/fb-relocate.py` | Moves V3's interpreter from `$8000-$9FFF` to `$D000` and up |
| `tools/fb-mmc5-16k.py` | Builds the 16KB MMC5 ROM from the relocated image |
| `tools/fb-fds.py` | Builds the Famicom Disk System image, with disk `SAVE`/`LOAD` (V2.1A and V3) |
| `tools/fb-fds-file.py` | Reads and writes the saved program inside a used disk image, from a PC |
| `tools/fb-reach.py` | Answers "can this address ever run on the disk build?" soundly |
| `tools/fb-basic-to-sav.py` | Text BASIC → `.sav`, with a ROM-driven self-test |
| `tools/fb-disasm.py` | Recursive-descent disassembler; used to enumerate every address reference |
| `tools/fb-gen-bigtest.py` | Generates a test program large enough to fill the free area, plus its expected output |

Python 3 standard library only; no dependencies.

## Verified on

| Hardware | 8KB build | 16KB build | disk build |
|---|---|---|---|
| MiSTer FPGA (NES core) | ✅ | ✅ | ✅ save, power cycle, load |
| EverDrive N8 PRO | ✅ | ✅ | — |
| Real Famicom + RAM adapter | — | — | ✅ `SAVE` / `LOAD` by hand |
| Unmodified original cart | ❌ mirrors back | ❌ | — |

⚠️ **Useless on an unmodified cart** — its 2KB/4KB SRAM just mirrors. These patches only
matter on hardware that provides RAM as declared in the header.

⚠️ **On hardware that genuinely has only 8KB of WRAM, the 16KB build's boot probe finds
nothing to switch to** and BASIC proceeds as if it had 16KB, silently overwriting itself
once a program passes 8KB.

Writing to a real disk through a drive has never been done here; every round trip so far
went through a RAM adapter.

## Prior art

- **Makimura Seisakusho, "MMC5 BASIC v0.9β2"** — got to 16KB first (PRG 128KB, CHR RAM 8KB).
  **The distribution site is gone**; only
  [the description page survives on archive.org](https://web.archive.org/web/20221116224414/http://rdev.php.xdomain.jp/makimura/archive/family-basic/mmc5-basic).
  This project fits in PRG 64KB, keeps CHR as ROM, and keeps all four built-in programs
- **"ファミベの改造" (nigaMSX)** `http://niga2.sytes.net/msx/famibe.html` — independently
  documents that patching `$8570` in V2.1A from `$77` to `$7F` yields `4030 BYTES FREE`,
  matching what was derived here
- **[micahcowan/fbdasm](https://github.com/micahcowan/fbdasm)** — a disassembly of V3
- **ファミコン改造マニュアル Vol.2 / Vol.3** (三才ブックス, 1988; article by 熊沢文幸) —
  published the disk port as a manual procedure
- **[TakuikaNinja/FC-DiskBASIC](https://github.com/TakuikaNinja/FC-DiskBASIC)** — automates
  that procedure with the CC65 suite. Reading it is what showed the disk build was worth
  doing. `fb-fds.py` is a rewrite rather than a port and carries none of its code; what is
  deliberately not used is listed in that file
- **[NipponNoraneko/fdsbasicV3](https://github.com/NipponNoraneko/fdsbasicV3)** — V3 on the
  FDS with disk commands of its own. Its patch positions agree with the addresses measured
  here, which is useful corroboration in both directions

## License

Tools and documentation: MIT ([LICENSE](LICENSE)). **No ROM data is included.**
Family BASIC (NS-HuBASIC) is copyright Nintendo / SHARP / Hudson Soft.
