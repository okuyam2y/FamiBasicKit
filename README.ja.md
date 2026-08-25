# ファミリーベーシックのプログラム領域を広げる

[English](README.md)

ファミリーベーシック（NS-HuBASIC）で書けるプログラムの上限を、**4KB から 8KB、
さらに 16KB まで**広げるための道具と、その解説です。

| 版 | 元のフリーエリア | 広げた後 | 要るもの |
|---|---|---|---|
| V1.0 / V2.0A / V2.1A | 約 2KB | **4KB** | PRG 2バイト ＋ ヘッダ 1バイト |
| V3.0 | 4KB | **8KB** | PRG 5バイト ＋ ヘッダ 1バイト |
| V3.0 | 4KB | **16KB** | MMC5 化 ＋ BASIC 本体の再配置 |
| V2.1A / V3.0 | 2KB / 4KB | **8KB・ディスク上** | ディスクシステムのイメージとして組み直す |

**MiSTer FPGA の NES コアと EverDrive N8 PRO で、16KB 版が実際に動くことを確認済みです**
（`16374 BYTES FREE`／9,816 バイトのプログラムが `LIST`・`RUN` まで通る）。

> ⚠️ **ROM は含まれていません。** 自分で用意したファミリーベーシックの `.nes` を
> 道具に食わせてください。このリポジトリが配るのは Python スクリプトと解説だけです。

## 文書

3種類を、3つのディレクトリに分けてあります。

**[docs/manual/](docs/manual/)** — 使う:

- [building.ja.md](docs/manual/building.ja.md) — どれを作るか、作り方、PC からプログラムを入れる
- [disk-basic.ja.md](docs/manual/disk-basic.ja.md) — ディスク版の `SAVE` と `LOAD`、エラー番号の意味

**[docs/reference/](docs/reference/)** — 改造する。書く前に読む物で、間違えると静かに壊れます:

- [build-differences.ja.md](docs/reference/build-differences.ja.md) — 版ごとに何が変わり、何は変わらないか
- [ram-expansion.ja.md](docs/reference/ram-expansion.ja.md) — どのバイトを書き換えるか、なぜそれで足りるか
- [sav-format.ja.md](docs/reference/sav-format.ja.md) — BASIC プログラムの格納形式
- [token-numbering.ja.md](docs/reference/token-numbering.ja.md) — 命令を足すときのトークン番号の決まり
- [mmc5-wram-banks.ja.md](docs/reference/mmc5-wram-banks.ja.md) — MMC5 のバンク番号を決め打ちできない理由

**[docs/background/](docs/background/)** — なぜそうなっているか。上の2つには要りません:

- [area-ceiling.ja.md](docs/background/area-ceiling.ja.md) — なぜ 16KB で止まるのか、次の一段の値段
- [relocation-notes.ja.md](docs/background/relocation-notes.ja.md) — 8KB のコードを動かすときに踏んだ落とし穴

## なぜ広がるのか

**BASIC が使える RAM の量は、載っている RAM の量ではなく ROM に焼かれた定数で決まります。**
機材が `$6000-$7FFF` の 8KB を用意していても、BASIC は決め打ちの範囲しか使わず、
余りは存在しないものとして扱われます。だから**定数を書き換えるだけ**で領域が広がります
（合計は V1/V2 で3バイト・V3 で6バイト。`CLEAR` の上限と、V3 の `BGGET` バッファは別の定数です）。

広げられる上限は、領域の**先頭がどこか**で決まります。

- V1/V2 は先頭が `$7000` → `$7FFF` まで伸ばして **4KB** が上限
- V3 は先頭が `$6000` → `$7FFF` まで伸ばして **8KB**（V3 が最も広い）

8KB を超えるには `$8000` 以降にも RAM を置く必要があり、そこは本来 PRG-ROM の場所なので
**バンク切り替え（MMC5）が要ります**。さらに `$8000-$9FFF` に載っている BASIC 本体 8KB の
置き場所が無くなるため、**本体を丸ごと別の番地へ引っ越す**ことになります。

## 8KB までは MMC5 が要りません

よくある誤解ですが、**マッパー0（NROM）は「RAM は 2KB まで」という意味ではありません。**
バンク切り替え機構を持たないというだけで、`$6000-$7FFF` の 8KB 窓に実 RAM を置くことは
妨げません。実カートの V1/V2 が 2KB、V3 が 4KB なのは**載っている SRAM チップの都合**で、
残りはミラー（折り返し）になっているだけです。

∴ **ヘッダの宣言どおりに RAM を用意する機材（エミュレータ・FPGA 実装・フラッシュカート）なら、
8KB まではマッパー0 のままで足ります。**

```bash
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"
```

書き換わるのは V3 で PRG の5バイト（`BGGET` を持たない V1/V2 は2バイト）と、
iNES ヘッダの 10 バイト目（NVRAM サイズの宣言）1バイトです。

| 目標 | 変更量 | 実機での表示 |
|---|---|---|
| V1.0 を 4KB に | PRG 2B ＋ ヘッダ 1B | `4031 BYTES FREE` |
| V2.1A を 4KB に | PRG 2B ＋ ヘッダ 1B | `4030 BYTES FREE` |
| V3.0 を 8KB に | PRG 5B ＋ ヘッダ 1B | `8182 BYTES FREE` |

（EverDrive N8 PRO での実測。`BYTES FREE` はプログラムが空のときの満額です）

## 16KB 版（V3 のみ）

```bash
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"
```

できあがるのは PRG 64KB / CHR 8KB / NVRAM 16KB の MMC5（マッパー5）ROM です。

| CPU から見える所 | 中身 |
|---|---|
| `$6000-$7FFF` | WRAM ブロック0（フリーエリア前半） |
| `$8000-$9FFF` | **WRAM のもう1ブロック**（後半・番号は起動時に判定する） |
| `$A000-$BFFF` | ROM バンク5（常駐。その場で書き換えている→[詳細](docs/reference/ram-expansion.ja.md)） |
| `$C000-$DFFF` | ROM バンク6（元の `$C000-$CFFF` ＋ 引っ越した本体の前半） |
| `$E000-$FFFF` | ROM バンク7（本体の後半 ＋ 背景画 ＋ 初期化 ＋ ローダ ＋ ベクタ） |
| バンク0-3 | 内蔵プログラム1本ずつ（下半分は `$C000-$CFFF` の複製） |

**内蔵プログラム4本（`GAME 0`〜`GAME 3`）はそのまま使えます**（`GAME 2,1` の背景画も含めて実機確認済み）。

⚠️ **8KB と 16KB の中間はありません。** RAM を貼れる単位が 8KB なので、12KB という選択肢は
存在しません。

詳しくは [docs/reference/ram-expansion.ja.md](docs/reference/ram-expansion.ja.md)。

## ディスク版

```bash
./tools/fb-fds.py "Family BASIC (Japan) (Rev 2).nes" --bios disksys.rom -o fcbasic.fds
./tools/fb-fds.py "Family BASIC V3 (Japan).nes"      --bios disksys.rom -o fcbasic3.fds
```

ディスクシステムのイメージを作ります。空きは **V2.1A から 8,126 バイト**、**V3 から 8,182
バイト**。そして `SAVE` と `LOAD` が**元からある書き方のまま**ディスクへ向きます:

    SAVE "NAME"        ディスクへ          LOAD "NAME"
    SAVE "DSK:NAME"    ディスクへ（明示）  LOAD "DSK:NAME"
    SAVE "CAS:NAME"    カセットへ          LOAD "CAS:NAME"

**ここで一番広い版ではありません** — MMC5 版は 16,374 バイトです。ディスクで得られるのは
**媒体の側**で、CHR が RAM なので走らせながら文字の絵を書き換えられること、FDS の音源、
カセット無しで保存できることです。**広さではなく、そちらが目的のときに選んでください。**

**保存のルーチンは空きを1バイトも減らしません。** 内蔵の会話プログラムの台詞の上に置いて
あります。ディスク版ではそこへ到達できないからで、その**論拠が `tools/fb-reach.py`** です
——主張ではなく道具で、「この番地は走りうるか」を、分岐表を**読む命令が到達したときだけ**
開く形で答えます。

1枚のディスクにプログラムは1つです。別の名前で `SAVE` すると置き換わります。
`tools/fb-fds-file.py` は使用済みのディスクの中身を PC から触るので、実機で打った
プログラムをテキストとして取り出して版管理に置けます。

ディスク版で何を打つのか、エラー番号が何を意味するのかは
[使い方](docs/manual/building.ja.md)にあります。3つの版を並べて、それぞれ何を諦めているかは
[docs/reference/build-differences.ja.md](docs/reference/build-differences.ja.md) にあります。

## この作業で分かった、MMC5 の落とし穴

**`$5114` に書くバンク番号の意味が、基板やコアの実装で割れます。**
MMC5 のバンク番号は **bit2 が「A15」と「RAM チップの /CE 0 と 1 の選択」を兼ねている**ため
（NESdev wiki の `RAAA AaAA`）、同じ番号でも指す先が変わります。

| | `$5114 = $01` | `$5114 = $04` |
|---|---|---|
| 実 ETROM（8KB×2）／EverDrive N8 PRO | チップ0のまま＝**`$6000-$7FFF` のミラー** | チップ1＝別の 8KB ✅ |
| MiSTer の NES コア | ブロック1 ✅ | ブロック4（`.sav` の範囲外に出る） |
| 実 EWROM（32KB＝1チップ） | 2枚目の 8KB ✅ | open bus |

市販の MMC5 ゲームはこの経路（16KB＝8KB SRAM 2枚差し）を踏まないので、実装によって
抜けていても誰も気づきません。∴ **`fb-mmc5-16k.py` は決め打ちをやめ、起動時に書いて
読み比べて選びます。**

同じ罠は MMC5 で WRAM を 8KB より多く使おうとする人なら誰でも踏みます。
詳しくは [docs/reference/mmc5-wram-banks.ja.md](docs/reference/mmc5-wram-banks.ja.md)。

## プログラムを外から入れる

テキストで書いた BASIC を、そのまま `.sav`（バッテリーバックアップの中身）に変換できます。

```bash
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --expanded   # V3 8KB 版
./tools/fb-basic-to-sav.py prog.bas -o out.sav -V v3 --16k        # V3 16KB 版（32KB の .sav）
```

トークン表は ROM から読み直して突き合わせており、**ROM 内蔵プログラム4本（387 行・8,999 バイト）を
復号 → 再変換して1バイトも違わない**ことを検定に使っています。

```bash
./tools/fb-basic-to-sav.py --selftest "Family BASIC V3 (Japan).nes"
```

格納形式の詳細は [docs/reference/sav-format.ja.md](docs/reference/sav-format.ja.md)。

## 道具の一覧

| ファイル | 何をするか |
|---|---|
| `tools/fb-expand-basic-area.py` | 領域の上限の定数を書き換える（8KB まで。マッパーはそのまま） |
| `tools/fb-relocate.py` | V3 の BASIC 本体 `$8000-$9FFF` を `$D000` 以降へ引っ越す |
| `tools/fb-mmc5-16k.py` | 引っ越し済みの ROM から 16KB 版の MMC5 ROM を組む |
| `tools/fb-fds.py` | ディスクシステムのイメージを組む。ディスクの `SAVE`/`LOAD` つき（V2.1A・V3） |
| `tools/fb-fds-file.py` | 使用済みのディスクイメージの中のプログラムを PC から読み書きする |
| `tools/fb-reach.py` | 「この番地はディスク版で走りうるか」に健全に答える |
| `tools/fb-basic-to-sav.py` | テキストの BASIC → `.sav`。ROM を使った自己検定つき |
| `tools/fb-disasm.py` | 再帰下降の逆アセンブラ。番地の参照を全数数えるために使う |
| `tools/fb-gen-bigtest.py` | フリーエリアを埋める大きさのテストプログラムと、その答えを作る |

Python 3 の標準ライブラリだけで動きます。追加のパッケージは要りません。

## 動作を確認した環境

| 機材 | 8KB 版 | 16KB 版 |
|---|---|---|
| MiSTer FPGA（NES コア） | ✅ | ✅ `16374 BYTES FREE` |
| EverDrive N8 PRO | ✅ `8182 BYTES FREE` | ✅ |
| 実カート（無改造） | ❌ ミラーで折り返す | ❌ |

⚠️ **実カートでは意味がありません。** 載っている SRAM が 2KB / 4KB なので折り返します。
効くのは「ヘッダの宣言どおりに RAM を用意する」機材だけです。

⚠️ **WRAM が本当に 8KB しか無い機材では、16KB 版の起動時判定が空振りします**
（BASIC は 16KB のつもりで動いてしまう）。面倒を見るには BASIC 本体の「領域の最後」の定数を
RAM 参照へ書き換える改造が別途必要です。

## 入力に使う ROM

道具は入力が素の ROM であることを確かめてから動きます（`fb-relocate.py` は PRG+CHR の
SHA-256 と3本のベクタ、未定義オペコードの位置まで固定しています）。

| 版 | ROM の MD5 | NVRAM |
|---|---|---|
| NS-HUBASIC V1.0 | `0c3fc3d2971ba12a86633ddea7bfbc76` | 2KB |
| NS-HUBASIC V2.0A | `7d66309f4de33d4c9db34a61eac5f67e` | 2KB |
| NS-HUBASIC V2.1A | `4aa19a05f941f42a728bd96a3b3ce15d` | 2KB |
| NS-HUBASIC V3.0 | `0cc06af39cb084885c34233b1b93b975` | 4KB |

16KB 化の入力は **V3.0 のみ**です（PRG+CHR の SHA-256
`c8c0b6c21bdda7503bab7592aea0f945a0259c18504bb241aafb1eabe65846f3`）。

## 先行事例

- **牧村製作所「MMC5 BASIC v0.9β2」** — 同じことを先にやった人がいます（PRG 128KB /
  CHR RAM 8KB / フリーエリア 16KB）。**配布元のサイトは消滅**しており、
  [archive.org に説明ページだけ](https://web.archive.org/web/20221116224414/http://rdev.php.xdomain.jp/makimura/archive/family-basic/mmc5-basic)
  が残っています。こちらは PRG 64KB で済ませ、CHR は ROM のまま、内蔵プログラム4本も残しました
- **「ファミベの改造」（にがMSX）** `http://niga2.sytes.net/msx/famibe.html` —
  V2.1A の `$8570` を `$77`→`$7F` にすると `4030 BYTES FREE` になる、という記録があり、
  ここで独立に導いた patch と一致しました
- **[micahcowan/fbdasm](https://github.com/micahcowan/fbdasm)** — V3 の逆アセンブル
- **『ファミコン改造マニュアル』Vol.2 / Vol.3**（三才ブックス・1988年・熊沢文幸氏の記事）
  — ディスクへの移植を手順として世に出したもの
- **[TakuikaNinja/FC-DiskBASIC](https://github.com/TakuikaNinja/FC-DiskBASIC)** —
  その手順を CC65 で自動化したもの。**これを読んだことが、ディスク版をやる価値があると
  分かった理由**です。`fb-fds.py` は移植ではなく書き直しで、先方のコードは持っていません。
  何を意図的に使っていないかは `fb-fds.py` の中に書いてあります
- **[NipponNoraneko/fdsbasicV3](https://github.com/NipponNoraneko/fdsbasicV3)** —
  V3 をディスクで動かし、独自のディスク命令を持つもの。あちらのパッチ位置は、
  ここで実測した番地と一致しました（互いの裏取りになります）

## ライセンス

道具と解説は MIT License（[LICENSE](LICENSE)）。**ROM のデータは含みません。**
ファミリーベーシック（NS-HuBASIC）の著作権は任天堂／シャープ／ハドソンにあります。
