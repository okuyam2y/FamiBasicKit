# テスト用の BASIC プログラム

[English](README.md)

引っ越し（`fb-relocate.py`）の欠陥は「**めったに通らない経路だけが静かに壊れる**」形で出ます。
∴「動いた」ではなく「**改造していない素の V3 と同じ画面になる**」で確かめました。
ここにあるのは、そのために書いたプログラムです。

## 使い方

`.bas` を `fb-basic-to-sav.py` で `.sav` に変換し、素の V3 と拡張版の両方に読ませて
`RUN` し、**画面を突き合わせます**。

```bash
./tools/fb-basic-to-sav.py tests/basic/core.bas -o core-stock.sav -V v3
./tools/fb-basic-to-sav.py tests/basic/core.bas -o core-16k.sav  -V v3 --16k
```

画面の**上4行は版ごとに違う**ので（`BASIC HOT START` / `nnnnn BYTES FREE` / `OK` / `RUN`）、
**5 行目以降だけ**を比べてください。1 文字 8px なので、`y >= 32px` で切ると行の境目と一致します。

> このリポジトリには実機へ流し込むハーネスは入っていません（特定の MiSTer 実機の
> 接続先に依存するため）。エミュレータでも同じ比べ方ができます。

## 中身

| ファイル | 何を見ているか |
|---|---|
| `core.bas` | 四則・`MOD`・`FOR`/`NEXT`・`GOSUB`・`IF THEN`・`LEN`/`ASC` |
| `numeric.bas` | 数値の格納形式3通り（1桁短縮形・16bit 整数・`&H`）と、負数・論理演算・比較 |
| `strings.bas` | `LEFT$`/`RIGHT$`/`MID$`/`STR$`/`VAL`/`HEX$`/`INSTR`/`SWAP`・文字列の連結と比較 |
| `arrays.bas` | `DIM` と添字、`DATA`/`READ`/`RESTORE`（行番号つきの `RESTORE` も） |
| `flow.bas` | 二重 `FOR`・`STEP -3`・`ON GOTO`/`ON GOSUB`（行番号トークン `$0B` の経路） |
| `printfmt.bas` | **未実装の予約語**（`TAB`/`SPC`/`COLOR`）に当たったときの `ON ERROR`＋`RESUME` の巡回 |
| `screen.bas` | `CLS`/`LOCATE`/`POS`/`CSRLIN`・`PRINT` の `,` 区切り |
| `sprite.bas` | `SPRITE ON`/`CGSET`/`PALETB`/`DEF MOVE`/`POSITION`/`MOVE`/`XPOS`/`CUT` |
| `errhandle.bas` | ゼロ除算 → `ON ERROR GOTO` → `ERR`/`ERL` → `RESUME <行>` |
| `mid.bas` / `mid.expect` | 5.5KB。**素の V3（4KB）には載らない**大きさ。答えが計算で分かる |
| `big.bas` / `big.expect` | 9.8KB。**フリーエリアを実際に埋める**大きさ。答えが計算で分かる |

## `.expect` があるものは、素の V3 と比べない

**素の V3 のフリーエリアは 4KB しかありません。** ∴ `$8000` より上を使う大きさの
プログラムは、素の V3 に載らず**比べる相手がいません**。

そこで正解を別の所から持ってきます: **答えが計算で分かるプログラムを作る。**
同じ計算を Python 側でもやって `.expect` に書き出し、実機の画面と突き合わせます。
生成は `tools/fb-gen-bigtest.py`。

```bash
./tools/fb-gen-bigtest.py --bytes 12000 -o tests/basic/big.bas
```

これで見ているもの:

- **`$8000` をまたいで置かれた行が読めるか** — 行は番地の順に並ぶので、大きくすると必ずまたぐ
- **またいだ先の行へ `GOSUB` で飛んで戻れるか** — 行の探索は先頭から辿るので、境目で壊れれば出る
- **変数がプログラムの直後（＝上半分）に取られても壊れないか**

数は `MOD 997` で抑えてあります。ファミベの整数は 16bit 符号付きで、
**掛け算の桁あふれを捕まえ損なう不具合が報告されている**ため（micahcowan/fbdasm）、
あふれさせません。
