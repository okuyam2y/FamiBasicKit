# ビルドを作る

*ビルドを作って動かしたい人のための文書です。作ったディスク版への入力方法は
[disk-basic.ja.md](disk-basic.ja.md) にあります。*

[English](building.md)

## どのビルドを作るか

| | NROM 8KB | MMC5 16KB | Disk BASIC | VRC7 |
|---|---|---|---|---|
| 元の ROM | V3（V1/V2 は 4KB） | V3 のみ | V2.1A または V3 | V2.1A または V3 |
| 空き容量 | `8182 BYTES FREE` | `16374 BYTES FREE` | V2.1A から 8,126 バイト、V3 から 8,182 バイト | V3 なら `8182 BYTES FREE`。V2.1A は入力のまま、`--8k` をつけると `8126 BYTES FREE` |
| `SAVE` / `LOAD` | カセット | カセット | ディスク | カセット |
| 必要な環境 | ヘッダの RAM 宣言に従う環境 | 同じ環境＋MMC5 | ディスクシステム（RAM アダプタ可） | 同じ環境＋VRC7 |
| 追加で使えるもの | なし | CHR バンク、拡張属性、走査線 IRQ、矩形波 2 本と PCM、乗算器 | FDS 音源。CHR が RAM なので実行中に文字の絵を書き換えられる | **`POKE` で鳴らせる FM 音源**（[vrc7.ja.md](../reference/vrc7.ja.md)） |

空き容量が目的なら MMC5 版が最も広くなります。ディスク版を選ぶ理由は容量ではなく、
ディスクに保存できること、CHR-RAM を書き換えられること、FDS 音源が使えることです。

8KB と 16KB の中間はありません。RAM は 8KB 単位で割り当てるため、12KB という選択肢は
存在しません。

VRC7 版で増えるのは音だけで、空き容量はマッパー 0 の 8KB 版と同じです。16KB 版とは
同時に選べません。FM 音源の窓口が、16KB 版が RAM にしている `$8000-$9FFF` の中にあるためです。

⚠️ **V2.1A の VRC7 版は、電源投入時の会話・占いプログラムを失います。** 初期化コードの
置き場所がそこしかないためです（`$E000-$FFFF` で他から使われていないのはデモの領域だけ）。
メニュー・BG GRAPHIC・`PLAY`・カセットのルーチンは残ります（[vrc7.ja.md](../reference/vrc7.ja.md)）。

## 作り方

ROM は自分で用意してください。このリポジトリに ROM のデータは含まれていません。

```bash
# 8KB（V3）。V1/V2 のダンプなら 4KB になります。バージョンは自動で判別します
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB（V3 のみ）
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# ディスク
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
./tools/fb-fds.py "Family BASIC V3 (Japan).nes"      --bios disksys.rom -o fcbasic3.fds

# FM 音源。V3 には上の 8KB 版を渡す（容量は入力のまま引き継ぐ）
./tools/fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"

# V2.1A も同じ道具。こちらは --8k をつけると領域の拡張までこの道具がやる
./tools/fb-vrc7.py --8k "Family BASIC (Japan) (Rev 2).nes" -o "V2.1A (VRC7 8KB).nes"
```

各ツールは処理を始める前に入力を検査します。想定しているダンプでなければ、
何もせずに終了します。

| バージョン | ROM の MD5 | 標準の NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

## PC で書いたプログラムを入れる

テキストで書いた BASIC は、そのまま `.sav`（起動時に読み込まれるバッテリーバックアップの
内容）に変換できます。

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav                    # V2.1A（既定）
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3・8KB 版
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3・16KB 版
```

`--expanded` は領域が `$7FFF` で終わる拡張済み ROM 向けです。`--16k` は `.sav` の
サイズを 32KB にします。16KB 版の `.sav` は 8KB 版と互換性がありません。
`--16k` と `-V v2` を同時に指定した場合や、`--16k` に `--size 8192` を併用した場合は、
後で問題が起きる前にエラーになります。

署名と終端ポインタは自動で設定されます。予約語の表は指定した ROM から読み直すため、
ツールが内部に持つ一覧が古くても影響しません。

ディスク版へはこの方法ではなく、[disk-basic.ja.md](disk-basic.ja.md) の
`fb-fds-file.py` を使います。

## 動作しない場合

⚠️ 無改造のカートリッジでは効果がありません。搭載されている 2KB / 4KB の SRAM が
ミラーで折り返すため、広げた領域は同じ場所に重なります。効果があるのは、ヘッダの
宣言どおりに RAM を用意する環境（エミュレータ、FPGA コア、フラッシュカート）だけです。

⚠️ WRAM が 8KB しかない環境では、16KB 版の起動時判定が切り替え先を見つけられません。
それでも BASIC は 16KB あるものとして動作します。プログラムが 8KB を超えた時点から、
上半分が下半分を上書きします。エラーは出ません。

⚠️ `BYTES FREE` はプログラムを読み込んでいない状態の値です。`.sav` がある場合
（`BASIC HOT START`）はプログラムのサイズだけ減ります。比較するときは条件を揃えてください。

`PEEK` と `POKE` はどのビルドでも全アドレスに届きます。ただし `BGGET`/`BGPUT` が使う
バッファのアドレスは領域の広さによって変わります。未改造の V3 が `$6C00`、8KB 版が
`$7C00`、16KB 版が `$9C00` です。拡張したビルドで `$6C00` を読むと、自分のプログラムの
途中を読むことになります。

## 動作を確認した環境

| 環境 | 8KB 版 | 16KB 版 | ディスク版 |
|---|---|---|---|
| MiSTer FPGA（NES コア） | ✅ | ✅ `16374 BYTES FREE` | ✅ 保存、電源断、読み込み |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ | — |
| 実物の MMC5 カートリッジ（維新の嵐の基板の flash に書いたもの・2026-09-04） | — | ✅ `16374 BYTES FREE`。テープから読んだ 16,029 バイトのプログラムが走り、`BACKUP` のあと電源を切っても `BASIC HOT START` で戻る | — |
| 実機＋RAM アダプタ | — | — | ✅ 手入力で `SAVE` / `LOAD` |
| 無改造の実カート | ❌ ミラーで折り返す | ❌ | — |

ドライブを使って実ディスクへ書き込んだことはありません。ここまでの動作確認はすべて
RAM アダプタ経由で、ディスクのモーターは動作していません。

### 実物のカートリッジで、電源を切ってもプログラムを残す

SRAM に電池が付いているだけでは、V3 はプログラムを残しません。起動時に領域の先頭にある印
（`$6001` が `$4C`、`$6002/$6003` にプログラム終端のポインタ）を見て、印があれば復元し、
復元したあとで印を消します。**この印を書くのは `BACKUP` 命令だけです。** 元のカートリッジでは、
`BACKUP` はそのあとバックアップスイッチ（SRAM を書き込み禁止にする）が入るのを待ってから
「電源を切ってください」と表示します。

スイッチの無い基板では、この待ちが終わりません。印は表示が出た時点で書き終わっているので、
`BACKUP` と打ち、表示が出たら **STOP キー**を押し、そのあとプログラムを直さずに電源を切ってください。
次に電源を入れると `BASIC HOT START` と出てプログラムが戻ります。上の基板で、16,029 バイトの
プログラムが正しい答えを返すまで確かめました。16KB の全部が電池側のチップに載っています。
