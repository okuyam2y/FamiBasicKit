# ファミリーベーシックのプログラム領域を広げる

[English](README.md)

ファミリーベーシック（NS-HuBASIC）のプログラム領域を拡張するツールです。
標準では V1/V2 が約 2KB、V3 が 4KB ですが、ROM 内の上限値を書き換えることで
V1/V2 は 4KB、V3 は 8KB まで拡張できます。さらに V3 では MMC5 を使い、
BASIC 本体を再配置することで 16KB まで拡張できます。
ディスクシステムのイメージとして組み直すこともできます。
以下ではこの 3 種類（NROM 8KB 版・MMC5 16KB 版・ディスク版）をまとめて
「ビルド」と呼びます。

> ⚠️ **ROM のデータは含みません。** ファミリーベーシックの `.nes` は自分で用意してください。
> このリポジトリにあるのは Python スクリプトと解説だけです。

## 何ができるか

| バージョン | 標準 | 拡張後 | 必要な変更 |
|---|---|---|---|
| V1.0 / V2.0A / V2.1A | 約 2KB | 4KB | PRG 2 バイト ＋ ヘッダ 1 バイト |
| V3.0 | 4KB | 8KB | PRG 5 バイト ＋ ヘッダ 1 バイト |
| V3.0 | 4KB | **16KB** | MMC5 化 ＋ BASIC 本体の再配置 |
| V2.1A / V3.0 | 2KB / 4KB | 8KB（ディスク） | ディスクシステムのイメージとして再構成 |

16KB 版は MiSTer FPGA の NES コアと EverDrive N8 PRO で動作を確認しています
（`16374 BYTES FREE`。9,816 バイトのプログラムが `LIST` と `RUN` まで通ります）。

8KB と 16KB の中間はありません。RAM は 8KB 単位で割り当てるため、12KB という選択肢は
存在しません。

## なぜ拡張できるのか

BASIC が使う RAM の範囲は、搭載されている RAM の量ではなく、ROM に書かれた定数で
決まっています。ハードウェアが `$6000-$7FFF` の 8KB を用意していても、BASIC は
定数で指定された範囲しか使いません。残りは存在しないものとして扱われます。

したがって、この定数を書き換えれば領域が広がります。変更するのは V1/V2 で 3 バイト、
V3 で 6 バイトです（領域の末尾を示す値のほかに、`CLEAR` が受け付ける上限と、
V3 では `BGGET`/`BGPUT` 用バッファの位置が別の定数になっているためです）。

どこまで広げられるかは、領域の先頭がどこにあるかで決まります。

- V1/V2 は `$7000` から始まるため、`$7FFF` まで伸ばして 4KB
- V3 は `$6000` から始まるため、`$7FFF` まで伸ばして 8KB

## 使い方

```bash
# 8KB（V3）。V1/V2 のダンプなら 4KB になります。バージョンは自動で判別します
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB（V3 のみ）
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# ディスク
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
```

実測値は次のとおりです（EverDrive N8 PRO で測定。プログラムを読み込んでいない状態の値です）。

| 変更 | 表示 |
|---|---|
| V1.0 → 4KB | `4031 BYTES FREE` |
| V2.1A → 4KB | `4030 BYTES FREE` |
| V3.0 → 8KB | `8182 BYTES FREE` |

作り方と操作方法の詳細は [docs/manual/building.ja.md](docs/manual/building.ja.md) にあります。

## 8KB までは MMC5 が不要です

マッパー 0（NROM）はバンク切り替え回路を持たないだけで、`$6000-$7FFF` に RAM を
置けないわけではありません。実際のカートリッジが 2KB（V1/V2）や 4KB（V3）なのは、
搭載された SRAM の容量がそれだけだったためで、残りの範囲はミラーになっています。

そのため、ヘッダの宣言どおりに RAM を用意する環境（エミュレータ、FPGA コア、
フラッシュカート）であれば、マッパー 0 のままで 8KB まで届きます。

逆に、無改造の実カートでは拡張しても意味がありません。ミラーによって同じ場所に
折り返すためです。

## 16KB 版が行っていること

8KB を超えるには `$8000` 以降も RAM にする必要があります。しかしこの範囲は本来
PRG-ROM で、バンク切り替え（MMC5）が必要です。さらに問題があります。
`$8000-$9FFF` には BASIC 本体 8KB が置かれているため、そのままでは RAM にできません。

そこで 16KB 版では、BASIC 本体を別の ROM 領域へ移動し、空いた `$8000-$9FFF` を
WRAM として使います。移動先は内蔵プログラム 4 本があった場所で、内蔵プログラムは
それぞれ専用のバンクへ移します。

電源投入直後のメモリ配置は次のようになります。

| CPU アドレス | 内容 |
|---|---|
| `$6000-$7FFF` | WRAM ブロック 0（フリーエリアの下半分） |
| `$8000-$9FFF` | WRAM の 2 つめのブロック（上半分。バンク番号は起動時に判定） |
| `$A000-$BFFF` | ROM バンク 5（常駐。一部を書き換え済み） |
| `$C000-$DFFF` | ROM バンク 6（元の `$C000-$CFFF` ＋ 移動した本体の前半） |
| `$E000-$FFFF` | ROM バンク 7（本体の後半、タイトル画面、初期化、ローダ、ベクタ） |
| バンク 0-3 | 内蔵プログラム 1 本ずつ（下半分は `$C000-$CFFF` の複製） |

内蔵プログラム 4 本（`GAME 0`〜`GAME 3`）は 16KB 版でもすべて動作します。
`GAME 2,1` の背景グラフィックを含めて実機で確認しています。

書き換えの内容は [docs/reference/ram-expansion.ja.md](docs/reference/ram-expansion.ja.md)、
移動の際に問題になった点は
[docs/background/relocation-notes.ja.md](docs/background/relocation-notes.ja.md) にあります。

## MMC5 のバンク番号は決め打ちできません

16KB 版を作る過程で分かった問題です。MMC5 で 8KB を超える WRAM を使う場合、
実装によって同じバンク番号が別の場所を指します。

`$5114` に書く値は、bit 2 が「PRG-ROM の A15」と「どちらの RAM チップを選ぶか」を
兼ねています（NESdev wiki の `RAAA AaAA`）。そのため、基板やエミュレータの実装で
結果が変わります。

| | `$5114 = $01` | `$5114 = $04` |
|---|---|---|
| 実 ETROM（8KB × 2）／EverDrive N8 PRO | チップ 0 のまま（`$6000-$7FFF` のミラー） | チップ 1（別の 8KB）✅ |
| MiSTer の NES コア | ブロック 1 ✅ | ブロック 4（`.sav` の範囲外） |
| 実 EWROM（32KB・1 チップ） | 2 つめの 8KB ✅ | open bus |

市販の MMC5 ゲームはこの経路を使わないため、実装が対応していなくても表面化しません。
ミラーになっていてもエラーは出ず、BASIC は 16KB あるつもりで動きます。
プログラムが 8KB を超えた時点で、上半分が下半分を上書きします。

そのため `fb-mmc5-16k.py` は起動時に判定します。`$01` を書いて `$6000-$7FFF` に置いた
目印が壊れるかどうかを見て、壊れていればミラーだと判断し `$04` に切り替えます。

詳細は [docs/reference/mmc5-wram-banks.ja.md](docs/reference/mmc5-wram-banks.ja.md) にあります。

## ディスク版の違い

ディスクシステムのイメージとして組み直すと、`SAVE` と `LOAD` の保存先がディスクに
なります。書式は元のままです。

    SAVE "NAME"        ディスクへ            LOAD "NAME"
    SAVE "DSK:NAME"    ディスクへ（明示）    LOAD "DSK:NAME"
    SAVE "CAS:NAME"    カセットへ            LOAD "CAS:NAME"

空き容量は V2.1A から 8,126 バイト、V3 から 8,182 バイトです。MMC5 版の 16,374 バイトより
狭いので、広さが目的ならディスク版を選ぶ理由はありません。ディスク版の利点は次の 3 点です。

- ディスクに保存できる（カセットが不要）
- CHR が RAM なので、プログラムの実行中に文字の絵を書き換えられる
- FDS 音源が使える

保存ルーチンを追加していますが、空き容量は減っていません。内蔵の会話プログラムの
台詞データの上に配置しているためです。ディスク版ではこの部分が実行されることはありません。
その確認には `tools/fb-reach.py` を使っています。分岐表を読む命令から到達できる範囲だけを
たどって、指定した番地が実行される可能性があるかどうかを判定するツールです。

1 枚のディスクに保存できるプログラムは 1 本です。別の名前で `SAVE` すると置き換わります。
`tools/fb-fds-file.py` を使うと、使用済みのディスクイメージの中身を PC から読み書きできます。
実機で入力したプログラムをテキストとして取り出し、バージョン管理に置けます。

操作方法とエラー番号は [docs/manual/disk-basic.ja.md](docs/manual/disk-basic.ja.md)、
ビルドごとの違いは
[docs/reference/build-differences.ja.md](docs/reference/build-differences.ja.md) にあります。

## PC で書いたプログラムを入れる

テキストで書いた BASIC を `.sav`（バッテリーバックアップの内容）に変換できます。

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3・8KB 版
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3・16KB 版（32KB の .sav）
```

予約語の表は ROM から読み直して照合します。動作確認には、ROM 内蔵のプログラム 4 本
（387 行・8,999 バイト）を復号してから再変換し、1 バイトも違わないことを求めています。

```bash
./tools/fb-basic-to-sav.py --selftest "Family BASIC V3 (Japan).nes"
```

格納形式は [docs/reference/sav-format.ja.md](docs/reference/sav-format.ja.md) にあります。

## 文書

読む人ごとに 3 つに分けています。

**[docs/manual/](docs/manual/)** — 使う場合

- [building.ja.md](docs/manual/building.ja.md) — どのビルドを作るか、作り方、PC からの入力
- [disk-basic.ja.md](docs/manual/disk-basic.ja.md) — ディスク版の `SAVE` と `LOAD`、エラー番号

**[docs/reference/](docs/reference/)** — 改造する場合。書き換える前に読む必要があります

- [build-differences.ja.md](docs/reference/build-differences.ja.md) — ビルドごとの違い
- [ram-expansion.ja.md](docs/reference/ram-expansion.ja.md) — 書き換える箇所とその理由
- [sav-format.ja.md](docs/reference/sav-format.ja.md) — プログラムの格納形式
- [token-numbering.ja.md](docs/reference/token-numbering.ja.md) — 命令を追加する場合の規則
- [mmc5-wram-banks.ja.md](docs/reference/mmc5-wram-banks.ja.md) — MMC5 のバンク番号の扱い

**[docs/background/](docs/background/)** — 背景。使用にも改造にも必須ではありません

- [area-ceiling.ja.md](docs/background/area-ceiling.ja.md) — 16KB が上限になる理由
- [relocation-notes.ja.md](docs/background/relocation-notes.ja.md) — 本体移動時に問題になった点

## ツール一覧

| ファイル | 内容 |
|---|---|
| `tools/fb-expand-basic-area.py` | 領域の上限値を書き換える（8KB まで。マッパーは変更しない） |
| `tools/fb-relocate.py` | V3 の BASIC 本体 `$8000-$9FFF` を `$D000` 以降へ移動する |
| `tools/fb-mmc5-16k.py` | 移動済みの ROM から 16KB 版の MMC5 ROM を作る |
| `tools/fb-fds.py` | ディスクシステムのイメージを作る（ディスクの `SAVE`/`LOAD` つき。V2.1A・V3） |
| `tools/fb-fds-file.py` | 使用済みディスクイメージ内のプログラムを PC から読み書きする |
| `tools/fb-reach.py` | 指定した番地がディスク版で実行される可能性があるかを判定する |
| `tools/fb-basic-to-sav.py` | テキストの BASIC を `.sav` に変換する（ROM を使った自己検証つき） |
| `tools/fb-disasm.py` | 再帰下降の逆アセンブラ。アドレス参照の全数調査に使う |
| `tools/fb-gen-bigtest.py` | フリーエリアを埋める大きさのテストプログラムと期待値を生成する |

Python 3 の標準ライブラリだけで動きます。追加のパッケージは不要です。

## 動作を確認した環境

| 環境 | 8KB 版 | 16KB 版 |
|---|---|---|
| MiSTer FPGA（NES コア） | ✅ | ✅ `16374 BYTES FREE` |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ |
| 無改造の実カート | ❌ ミラーで折り返す | ❌ |

⚠️ 無改造の実カートでは効果がありません。搭載 SRAM が 2KB / 4KB のため折り返します。
ヘッダの宣言どおりに RAM を用意する環境でのみ意味があります。

⚠️ WRAM が 8KB しかない環境では、16KB 版の起動時判定が切り替え先を見つけられません。
それでも BASIC は 16KB あるものとして動作します。対応するには、BASIC 本体が持つ
「領域の末尾」の定数を RAM 参照に変更する改造が別途必要です。

## 入力に使う ROM

各ツールは、入力が未改造の ROM であることを確認してから処理します
（`fb-relocate.py` は PRG+CHR の SHA-256、3 本のベクタ、未定義オペコードの位置まで
照合します）。

| バージョン | ROM の MD5 | NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

16KB 化に使えるのは V3.0 のみです（PRG+CHR の SHA-256
`c8c0b6c21bdda7503bab7592aea0f945a0259c18504bb241aafb1eabe65846f3`）。

## 先行事例

- **牧村製作所「MMC5 BASIC v0.9β2」** — 16KB 化を先に実現した例です
  （PRG 128KB / CHR RAM 8KB）。配布元のサイトは消滅しており、
  [archive.org に説明ページ](https://web.archive.org/web/20221116224414/http://rdev.php.xdomain.jp/makimura/archive/family-basic/mmc5-basic)
  だけが残っています。こちらは PRG 64KB に収め、CHR は ROM のまま、内蔵プログラム 4 本も
  残しています
- **「ファミベの改造」（にがMSX）** `http://niga2.sytes.net/msx/famibe.html` —
  V2.1A の `$8570` を `$77` から `$7F` に変更すると `4030 BYTES FREE` になるという記録が
  あり、こちらで独立に求めた変更箇所と一致しました
- **[micahcowan/fbdasm](https://github.com/micahcowan/fbdasm)** — V3 の逆アセンブル
- **『ファミコン改造マニュアル』Vol.2 / Vol.3**（三才ブックス・1988 年・熊沢文幸氏の記事）
  — ディスクへの移植を手順として公開したもの
- **[TakuikaNinja/FC-DiskBASIC](https://github.com/TakuikaNinja/FC-DiskBASIC)** —
  その手順を CC65 で自動化したものです。ディスク版に取り組む価値があると判断したのは、
  これを読んだためです。`fb-fds.py` は移植ではなく独自に書き直したもので、
  先方のコードは含みません。意図的に使用しなかった箇所は `fb-fds.py` 内に記載しています
- **[NipponNoraneko/fdsbasicV3](https://github.com/NipponNoraneko/fdsbasicV3)** —
  V3 をディスクシステムで動かし、独自のディスク命令を追加したものです。
  パッチ位置がこちらで実測したアドレスと一致しており、相互の裏付けになっています

## ライセンス

ツールと文書は MIT License（[LICENSE](LICENSE)）です。ROM のデータは含みません。
ファミリーベーシック（NS-HuBASIC）の著作権は任天堂／シャープ／ハドソンにあります。
