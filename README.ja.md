# ファミリーベーシックのプログラム領域を広げる

[English](README.md)

ファミリーベーシック（NS-HuBASIC）のプログラム領域を拡張するツールです。
BASIC が使う RAM の範囲は、搭載されている RAM の量ではなく ROM に書かれた定数で
決まっています。そのため数バイト書き換えるだけで領域が広がり、V1/V2 は 4KB、
V3 は 8KB になります。それ以上にするには BASIC 本体を `$8000-$9FFF` から
移動させる必要があり、MMC5 版がこれを行って 16KB にしています。
同じツールで、ディスクシステムのイメージとして組み直すこともできます。

> ⚠️ **ROM のデータは含みません。** ファミリーベーシックの `.nes` は自分で用意してください。
> このリポジトリにあるのは Python スクリプトと解説だけです。

## 作れるもの

| ビルド | 元にできる ROM | 空き容量 | 必要な変更 |
|---|---|---|---|
| NROM 4KB | V1.0 / V2.0A / V2.1A | `4031 BYTES FREE`（V1.0）、`4030 BYTES FREE`（V2.1A） | PRG 2 バイト ＋ ヘッダ 1 バイト |
| NROM 8KB | V3.0 | `8182 BYTES FREE` | PRG 5 バイト ＋ ヘッダ 1 バイト |
| MMC5 16KB | V3.0 | `16374 BYTES FREE` | MMC5 化 ＋ BASIC 本体の再配置 |
| ディスク版 | V2.1A / V3.0 | 8,126 / 8,182 バイト | ディスクシステムのイメージとして再構成。CHR が RAM になり、FDS 音源も使える |
| VRC7 版 | V2.1A または V3.0 | V3 は `8182 BYTES FREE`、V2.1A は入力のまま（`--8k` で `8126 BYTES FREE`） | VRC7 のマッパーに載せ替え。**`POKE` で FM 音源が鳴る**。⚠️ V2.1A 版は起動時のデモを失う |

8KB と 16KB の中間はありません。RAM は 8KB 単位で割り当てるため、12KB という選択肢は
存在しません。ディスク版は容量では最大になりません。ディスクへの保存、実行中の文字の
書き換え、FDS 音源といった、ディスク固有の機能を使いたい場合に選んでください。

## 作り方

```bash
# 8KB（V3）。V1/V2 のダンプなら 4KB になります。バージョンは自動で判別します
./tools/fb-expand-basic-area.py "Family BASIC V3 (Japan).nes" -o "V3 (8KB).nes"

# 16KB（V3 のみ）
./tools/fb-relocate.py "Family BASIC V3 (Japan).nes" -o v3-reloc.nes
./tools/fb-mmc5-16k.py v3-reloc.nes --original "Family BASIC V3 (Japan).nes" \
    -o "Family BASIC V3.0 (MMC5 16KB).nes"

# ディスク
./tools/fb-fds.py "Family BASIC V3 (Japan).nes" --bios disksys.rom -o fcbasic3.fds

# FM 音源。容量は 8KB のまま。16KB 版とは両立しません
./tools/fb-vrc7.py "V3 (8KB).nes" -o "Family BASIC V3.0 (VRC7).nes"
./tools/fb-vrc7.py --8k "Family BASIC (Japan) (Rev 2).nes" -o "V2.1A (VRC7 8KB).nes"
```

各ツールは処理を始める前に入力を検査し、想定しているダンプでなければ何もせずに終了します。
受け付けるダンプの一覧、オプション、PC で書いたプログラムを本体に入れる方法は
[docs/manual/building.ja.md](docs/manual/building.ja.md) にあります。

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
- [mmc5-wram-banks.ja.md](docs/reference/mmc5-wram-banks.ja.md) — MMC5 のバンク番号を決め打ちできない理由と、誤った場合に起きること
- [vrc7.ja.md](docs/reference/vrc7.ja.md) — FM 音源が鳴るビルド。作り方・レジスタ・音を出すプログラム
- [chr.ja.md](docs/reference/chr.ja.md) — 絵。タイル 512 枚の見方と差し替え方、キャラクタごとのタイル番号

**[docs/background/](docs/background/)** — 背景。使用にも改造にも必須ではありません

- [area-ceiling.ja.md](docs/background/area-ceiling.ja.md) — 16KB が上限になる理由
- [relocation-notes.ja.md](docs/background/relocation-notes.ja.md) — 本体を移動したときに問題になった点

## ツール一覧

| ファイル | 内容 |
|---|---|
| `tools/fb-expand-basic-area.py` | 領域の上限値を書き換える（8KB まで。マッパーは変更しない） |
| `tools/fb-relocate.py` | V3 の BASIC 本体 `$8000-$9FFF` を `$D000` 以降へ移動する |
| `tools/fb-mmc5-16k.py` | 移動済みの ROM から 16KB 版の MMC5 ROM を作る |
| `tools/fb-vrc7.py` | VRC7 版の ROM を作る。`POKE &H9010`／`POKE &H9030` が FM 音源に届く |
| `tools/fb-fds.py` | ディスクシステムのイメージを作る（ディスクの `SAVE`/`LOAD` つき。V2.1A・V3） |
| `tools/fb-fds-file.py` | 使用済みディスクイメージ内のプログラムを PC から読み書きする |
| `tools/fb-kana-layout.py` | かなの配列を JIS キーボードのキートップに合わせる。英数の打鍵は変わらない |
| `tools/fb-reach.py` | 指定したアドレスがディスク版で実行される可能性があるかを判定する |
| `tools/fb-basic-to-sav.py` | テキストの BASIC を `.sav` に変換する（ROM を使った自己検証つき） |
| `tools/fb-chr.py` | タイル 512 枚（キャラクタと文字）を PNG として読み書きする。キャラクタごとのタイル番号も出す |
| `tools/fb-disasm.py` | 再帰下降の逆アセンブラ。アドレス参照の全数調査に使う |
| `tools/fb-gen-bigtest.py` | フリーエリアを埋める大きさのテストプログラムと期待値を生成する |

Python 3 の標準ライブラリだけで動きます。追加のパッケージは不要です。

## 動作を確認した環境

| 環境 | 8KB 版 | 16KB 版 | ディスク版 |
|---|---|---|---|
| MiSTer FPGA（NES コア） | ✅ | ✅ | ✅ 保存、電源断、読み込み |
| EverDrive N8 PRO | ✅ | ✅ | — |
| 実機＋RAM アダプタ | — | — | ✅ 手入力で `SAVE` / `LOAD` |
| 無改造の実カート | ❌ ミラーで折り返す | ❌ | — |

⚠️ **無改造のカートリッジでは効果がありません。** 搭載されている 2KB / 4KB の SRAM が
ミラーで折り返すためです。効果があるのは、ヘッダの宣言どおりに RAM を用意する環境だけです。

⚠️ **WRAM が 8KB しかない環境では、16KB 版の起動時判定が切り替え先を見つけられません。**
それでも BASIC は 16KB あるものとして動作し、プログラムが 8KB を超えた時点から
自分自身を上書きします。エラーは出ません。

ドライブを使って実ディスクへ書き込んだことはありません。ここまでの動作確認はすべて
RAM アダプタ経由です。

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
