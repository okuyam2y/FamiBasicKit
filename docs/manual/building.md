# Making a build
*For anyone who wants one of these builds running. To type on a disk build once you
have one, see [disk-basic.md](disk-basic.md).*

[日本語](building.ja.md)

## Which one to make

| | NROM 8KB | MMC5 16KB | Disk BASIC | VRC7 |
|---|---|---|---|---|
| base ROM | V3 (also V1/V2, at 4KB) | V3 only | V2.1A or V3 | V2.1A or V3 |
| free area | `8182 BYTES FREE` | `16374 BYTES FREE` | `8126` from V2.1A, `8182` from V3 | `8182 BYTES FREE` from V3; from V2.1A the input's own, or `8126 BYTES FREE` with `--8k` |
| `SAVE` / `LOAD` | cassette | cassette | **the disk** | cassette |
| what it needs | any hardware that honours the header's RAM declaration | the same, plus MMC5 | a Famicom Disk System, or a RAM adapter | the same, plus VRC7 |
| what else you get | nothing | CHR banks, extended attributes, scanline IRQ, two extra square waves + PCM, the multiplier | the FDS sound channel, and CHR as RAM so characters can be rewritten while a program runs | **FM sound from `POKE`** ([vrc7.md](../reference/vrc7.md)) |

**The disk build is not the roomiest** — the MMC5 one reaches 16,374 bytes. What the disk
buys is the medium. Choose it for that, not for space.

**The VRC7 build is not roomier than plain mapper 0** — what it buys is the sound, and it
cannot be combined with the 16KB area: the FM ports are inside the `$8000-$9FFF` the 16KB
build turns into RAM.

⚠️ **The V2.1A VRC7 build loses the boot demo** — the conversation and fortune-telling
program V2.1A runs at power-on. That is where its init code goes; nothing else in
`$E000-$FFFF` is unreachable. The title menu, BG GRAPHIC, `PLAY` and the cassette routines
all stay ([vrc7.md](../reference/vrc7.md)).

⚠️ **There is nothing between 8KB and 16KB.** RAM is mapped in 8KB units, so 12KB is not
an option.

## Making one

Supply your own Family BASIC `.nes` dump; no ROM data is included here.

```bash
# 8KB (V3) — or 4KB from a V1/V2 dump, which the tool detects
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB (V3 only)
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# a disk
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
./tools/fb-fds.py "Family BASIC V3 (Japan).nes"      --bios disksys.rom -o fcbasic3.fds

# FM sound. Feed V3 the 8KB build above; the area stays whatever the input had
./tools/fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"

# the same for V2.1A, where --8k widens the area here instead of beforehand
./tools/fb-vrc7.py --8k "Family BASIC (Japan) (Rev 2).nes" -o "V2.1A (VRC7 8KB).nes"
```

Each tool verifies its input before doing anything, and refuses a dump it does not
recognise.

| Version | ROM MD5 | Stock NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

## Getting a program in from a PC

BASIC source written as plain text converts straight to a `.sav` — the battery-backed
WRAM image the machine loads at boot.

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav                    # V2.1A (default)
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3, 8KB build
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3, 16KB build
```

`--expanded` targets an expanded ROM (area ending at `$7FFF`). `--16k` sets the `.sav`
size to 32KB automatically — **the 16KB build's `.sav` is not interchangeable with the
8KB build's**. Combining `--16k` with `-V v2`, or passing `--size 8192` alongside it, is
an error rather than a surprise later.

The signature and end pointer are filled in for you. Reserved words are read out of the
ROM you point the tool at, not from a list kept here.

For the disk build, put the program on the disk instead — see below.

## When it does not work

⚠️ **On an unmodified cartridge, none of this does anything.** Its 2KB/4KB SRAM simply
mirrors, so the widened area folds back onto the same storage. These patches only matter on
hardware that provides RAM as declared in the header — emulators, FPGA cores, flash carts.

⚠️ **On hardware that genuinely has only 8KB of WRAM, the 16KB build's boot probe finds
nothing to switch to** and BASIC proceeds as if it had 16KB. Everything works until the
program exceeds 8KB, and then the upper half silently writes over the lower half.

⚠️ **`BYTES FREE` is the figure with no program loaded.** With a `.sav` in place
(`BASIC HOT START`) it drops by the size of the program. Compare like with like.

`PEEK` and `POKE` reach the whole address space on every build, but **the address of the
`BGGET`/`BGPUT` buffer moves with the size of the area** — `$6C00` stock, `$7C00` on the
8KB build, `$9C00` on the 16KB one. Reading the stock address on a widened build reads the
middle of your own program.

## Verified on

| Hardware | 8KB build | 16KB build | disk build |
|---|---|---|---|
| MiSTer FPGA (NES core) | ✅ | ✅ `16374 BYTES FREE` | ✅ save, power cycle, load |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ | — |
| Real Famicom + RAM adapter | — | — | ✅ `SAVE` / `LOAD` by hand |
| Unmodified original cart | ❌ mirrors back | ❌ | — |

**Writing to a real disk through a drive has never been done here.** Every round trip so
far went through a RAM adapter; no disk motor has turned.
