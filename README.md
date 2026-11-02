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

Claude Code に登録します (このディレクトリで実行。`-s project` ならこのディレクトリの
`.mcp.json` に、`-s user` ならどこからでも使えるように登録されます):

```sh
claude mcp add paperang -s project -- "$PWD/.venv/bin/paperang-mcp"
```

| ツール | 内容 |
|---|---|
| `printer_status` | 機種・FW・電池・濃度などを取得 |
| `print_text` | テキストを印刷 (日本語可、`timestamp` / `feed` / `density` 指定可) |
| `print_image` | 画像ファイルを印刷 (`dither` でディザ / 二値化を切替) |
| `preview_text` / `preview_image` | 印刷せずに印刷イメージを PNG で返す |
| `feed_paper` | 紙送り |
| `configure` | 印字濃度・自動電源オフ時間を設定 |

呼び出しのたびに BLE で接続し、終わると切断します。環境変数 (`claude mcp add -e KEY=VALUE` や `.mcp.json` の `env`) で
`PAPERANG_BLE_ADDRESS` / `PAPERANG_PORT` / `PAPERANG_FEED` / `PAPERANG_WIDTH` /
`PAPERANG_DRY_RUN=1` を指定できます。

### キングジム Rolto (ロルト) PT10

同じ MCP サーバーから、Wi-Fi 接続の Rolto PT10 (幅 384 ドット) も使えます。
PT10 を同じ LAN の Wi-Fi に参加させておくと、UDP ブロードキャストで自動的に見つけます
(`PT10_HOST` で IP アドレスを固定することもできます)。

| ツール | 内容 |
|---|---|
| `pt10_status` | IP アドレスと状態 (印字中か) を取得 |
| `pt10_print_text` / `pt10_print_image` | テキスト・画像を印刷 (`feed` で末尾に白紙を足す) |
| `pt10_feed_paper` | 白紙を印字して紙送り |

印字幅は 384 ドット (48mm) を左右の端まで使えます。印字ヘッドから切り口までは約 7mm (56 ドット) あり、
`feed` を指定するとこの分を自動で足して、切り口から指定どおりの余白が出るようにします
(`paperang/rolto.py` の `TEAR_OFFSET_DOTS`)。

プロトコルは `paperang/rolto.py` の先頭にまとめてあります (iPhone の Rolto アプリの通信を
記録して解析したもの)。

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
  rolto.py      キングジム Rolto PT10 (Wi-Fi) のドライバ
tools/diag_ble.py  BLE 接続の診断 (生の送受信と CRC 鍵の判定)
tests/          プロトコル部の単体テスト (python3 -m unittest discover -s tests -t .)
```

## プロトコル概要

```
0x02 | cmd | index | len (u16 LE) | data | crc32 (u32 LE) | 0x03
```

- CRC は data 部のみ、`zlib.crc32(data, key)`。電源投入直後の実機は標準キー `0x35769521` で送受信し、
  標準キーで `SET_CRC_KEY` (鍵 `0x06B8EF59`) を送ると、電源を切るまで **鍵 `0x33CE7A78`** になる。
  スマホのアプリもこの登録をするので、ドライバは接続のたびに登録してから `0x33CE7A78` で通信する
- 各コマンドには同じ cmd 番号で 1 バイトの ACK が返る (`00`=OK、`03`=エラー)
- `GET_xxx` の問い合わせはデータ `01` を付ける。値は `SENT_xxx` パケットで返る
- 印刷データは `PRINT_DATA (0)`、1 ライン 48 バイト (384 ドット, 1=黒)、1 パケット最大 1536 バイト
- 紙送りは `FEED_LINE (26)` に u16 のドット数

コマンド番号はオープンソースの解析実装 (python-paperang) に基づいています。非公式の
プロトコルのため、機種 (P2/P2S は印字幅や通信方式が異なる場合あり) やファームウェアに
よっては動作しないことがあります。
