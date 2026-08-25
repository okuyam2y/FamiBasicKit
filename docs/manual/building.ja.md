# 版を作る
*この版を動かしたい人のための文書です。手に入れたディスク版に打ち込む話は
[disk-basic.ja.md](disk-basic.ja.md) にあります。*

[English](building.md)

## どれを作るか

| | NROM 8KB | MMC5 16KB | Disk BASIC |
|---|---|---|---|
| 元の ROM | V3（V1/V2 は 4KB） | V3 のみ | V2.1A または V3 |
| 空き容量 | `8182 BYTES FREE` | `16374 BYTES FREE` | V2.1A から `8126`、V3 から `8182` |
| `SAVE` / `LOAD` | カセット | カセット | **ディスク** |
| 要るもの | ヘッダの RAM 宣言に従うハードウェア | 同じ＋MMC5 | ディスクシステム（RAM アダプタ可） |
| ほかに使えるもの | なし | CHR バンク・拡張属性・走査線 IRQ・矩形波2＋PCM・乗算器 | FDS 音源、および CHR が RAM なので**走らせながら文字を書き換えられる** |

**ディスク版が一番広いわけではありません** — 広さなら MMC5 版の 16,374 バイトです。
ディスクが買うのは**媒体**です。広さではなくそちらを目的に選んでください。

⚠️ **8KB と 16KB の間はありません。** RAM は 8KB 単位で割り付くので 12KB という選択肢は
存在しません。

## 作る

ROM は自分で用意してください（このリポジトリに ROM のデータは入っていません）。

```bash
# 8KB（V3）— V1/V2 のダンプなら 4KB。版は自動で判別します
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB（V3 のみ）
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# ディスク
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
./tools/fb-fds.py "Family BASIC V3 (Japan).nes"      --bios disksys.rom -o fcbasic3.fds
```

どの道具も**何かをする前に入力を検査**し、知らないダンプは受け付けません。

| 版 | ROM の MD5 | 素の NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

## PC からプログラムを入れる

テキストで書いた BASIC は、そのまま `.sav`（電池バックアップの WRAM イメージ）になります。

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav                    # V2.1A（既定）
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3・8KB 版
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3・16KB 版
```

`--expanded` は領域が `$7FFF` で終わる拡張済み ROM 向けです。`--16k` は `.sav` の大きさを
32KB にします — **16KB 版の `.sav` は 8KB 版と互換ではありません**。`--16k` に `-V v2` を
重ねたり `--size 8192` を渡したりすると、後で驚く代わりに**その場でエラー**になります。

署名と終端ポインタは自動で埋まります。予約語は指定した ROM から読み直すので、
この道具の中に持っている一覧が正しいかどうかに依存しません。

ディスク版へはこの方法ではなく、後述の `fb-fds-file.py` で入れます。

## うまくいかないとき

⚠️ **改造していないカートリッジでは、これらは何もしません。** 積んでいる 2KB/4KB の SRAM が
折り返すだけなので、広げた領域は同じ場所に重なります。効くのは**ヘッダの宣言どおりに RAM を
用意するハードウェア**（エミュレータ・FPGA コア・フラッシュカート）だけです。

⚠️ **本当に 8KB しか WRAM が無いハードウェアでは、16KB 版の起動時の判定は切り替え先を
見つけられません。** それでも BASIC は 16KB のつもりで進みます。プログラムが 8KB を超えた
時点から、上半分が下半分を**黙って上書きします**。

⚠️ **`BYTES FREE` はプログラムを読み込んでいないときの数字です。** `.sav` が入っていると
（`BASIC HOT START`）プログラムの分だけ減ります。比べるときは同じ条件で比べてください。

`PEEK`/`POKE` はどの版でも全番地に届きますが、**`BGGET`/`BGPUT` のバッファの番地は領域の
広さについて動きます** — 素の V3 が `$6C00`、8KB 版が `$7C00`、16KB 版が `$9C00`。
広げた版で素の番地を読むと、**自分のプログラムの途中**を読みます。

## 実機で確認した範囲

| ハードウェア | 8KB 版 | 16KB 版 | ディスク版 |
|---|---|---|---|
| MiSTer FPGA（NES コア） | ✅ | ✅ `16374 BYTES FREE` | ✅ 保存・電源断・読み込み |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ | — |
| 実機＋RAM アダプタ | — | — | ✅ 手打ちで `SAVE` / `LOAD` |
| 改造していない実カート | ❌ 折り返す | ❌ | — |

**ドライブを使って実ディスクへ書いたことは一度もありません。** ここまでの往復はすべて
RAM アダプタ経由で、ディスクのモーターは回っていません。
