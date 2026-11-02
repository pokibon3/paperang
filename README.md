# Paperang サンプルソフト

Paperang (P1 系) サーマルミニプリンタを Mac から操作する Python のサンプルです。
テキスト (日本語可) や画像の印刷、紙送り、機種・電池残量の取得ができます。

## セットアップ

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

接続は **BLE が既定**です。ペアリングは不要で、Paperang の電源を入れておけば
名前 `Paperang*` のデバイスを自動で探して接続します (P1S で動作確認済み)。

- スマホの Paperang アプリとつながっていると見つからないので、アプリ側は切断しておく
- 初回はターミナルに Bluetooth の使用許可を求められることがある
- 見つからないときは `.venv/bin/python -m paperang scan --all` で周囲のデバイスを確認

## 使い方

```sh
# 情報表示 (機種 / FW / 電池 / 濃度など)
.venv/bin/python -m paperang info

# テキスト印刷 (日本語はヒラギノで描画)
.venv/bin/python -m paperang text "こんにちは Paperang!" --size 32 --timestamp
echo "標準入力からも印刷" | .venv/bin/python -m paperang text -

# 画像印刷 (Floyd–Steinberg ディザ / --no-dither で二値化)
.venv/bin/python -m paperang image photo.jpg --density 80

# 紙送り・テストページ・設定
.venv/bin/python -m paperang feed 80   # 80 ドット ≒ 10mm
.venv/bin/python -m paperang testpage
.venv/bin/python -m paperang config --density 75 --power-down 3600
```

共通オプション:

| オプション | 説明 |
|---|---|
| (指定なし) | BLE で `Paperang*` をスキャンして接続 |
| `--ble ADDR` | BLE のアドレス (macOS では `scan` に出る UUID) を指定 |
| `--serial` | シリアル (Bluetooth SPP) で接続。ポートは自動検出 |
| `--port PATH` | シリアルポートを指定して接続 |
| `--dry-run` | 実機に送らずにパケット生成だけ行う |
| `--preview out.png` | 印刷イメージを PNG で保存 (text / image) |
| `--width N` | 印字幅ドット。P1 は 384 |
| `-v` | 送受信パケットを 16 進で表示 |

実機なしで仕上がりを確認したいときは:

```sh
.venv/bin/python -m paperang text "テスト" --dry-run --preview out.png
```

## MCP サーバー

Claude Code などの MCP クライアントから Paperang を操作できます。

```sh
.venv/bin/pip install -e ".[mcp]"   # paperang-mcp コマンドが入る
```

このディレクトリの `.mcp.json` に登録済みなので、Claude Code をこのディレクトリで起動すると
読み込まれます (初回は使用許可の確認あり)。他の場所から使う場合:

```sh
claude mcp add paperang -s user -- /Users/ooe/src/paperang/.venv/bin/paperang-mcp
```

| ツール | 内容 |
|---|---|
| `printer_status` | 機種・FW・電池・濃度などを取得 |
| `print_text` | テキストを印刷 (日本語可、`timestamp` / `feed` / `density` 指定可) |
| `print_image` | 画像ファイルを印刷 (`dither` でディザ / 二値化を切替) |
| `preview_text` / `preview_image` | 印刷せずに印刷イメージを PNG で返す |
| `feed_paper` | 紙送り |
| `configure` | 印字濃度・自動電源オフ時間を設定 |

呼び出しのたびに BLE で接続し、終わると切断します。環境変数 (`.mcp.json` の `env`) で
`PAPERANG_BLE_ADDRESS` / `PAPERANG_PORT` / `PAPERANG_FEED` / `PAPERANG_WIDTH` /
`PAPERANG_DRY_RUN=1` を指定できます。

## ライブラリとして使う

```python
from PIL import Image
from paperang import Paperang
from paperang.transport import BleTransport
from paperang.imaging import render_text, to_mono, mono_to_bitmap

with Paperang(BleTransport()) as p:
    print(p.get_info())
    img = render_text("Hello\nペーパーアング", p.width, font_size=40)
    # 切り口から 80 ドット (≒10mm) の余白が出るまで送る
    p.print_bitmap_with_margin(mono_to_bitmap(to_mono(img, p.width, dither=False)), 80)
```

## 構成

```
paperang/
  protocol.py   パケット組み立て/解析・CRC・コマンド定義
  transport.py  BLE / Serial (Bluetooth SPP) / DryRun の通信路
  printer.py    高レベル API (情報取得・設定・印刷)
  imaging.py    画像・テキスト → 1bpp ラスタ変換 (Pillow)
  cli.py        コマンドラインツール
  mcp_server.py MCP サーバー (paperang-mcp)
tools/diag_ble.py  BLE 接続の診断 (生の送受信と CRC 鍵の判定)
tests/          プロトコル部の単体テスト (python3 -m unittest discover -s tests -t .)
```

## プロトコル概要

```
0x02 | cmd | index | len (u16 LE) | data | crc32 (u32 LE) | 0x03
```

- CRC は data 部のみ、`zlib.crc32(data, key)`。実機 (BLE 接続) では **鍵 `0x33CE7A78` 固定**
  (python-paperang の資料にある標準キー `0x35769521` + `SET_CRC_KEY` での鍵登録は、この機体では無効)
- 各コマンドには同じ cmd 番号で 1 バイトの ACK が返る (`00`=OK、`03`=エラー)
- `GET_xxx` の問い合わせはデータ `01` を付ける。値は `SENT_xxx` パケットで返る
- 印刷データは `PRINT_DATA (0)`、1 ライン 48 バイト (384 ドット, 1=黒)、1 パケット最大 1536 バイト
- 紙送りは `FEED_LINE (26)` に u16 のドット数

コマンド番号はオープンソースの解析実装 (python-paperang) に基づいています。非公式の
プロトコルのため、機種 (P2/P2S は印字幅や通信方式が異なる場合あり) やファームウェアに
よっては動作しないことがあります。
