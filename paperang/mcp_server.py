"""Paperang を操作する MCP サーバー (stdio)。

ツール:
  printer_status   機種・電池・濃度などを取得
  print_text       テキストを印刷 (日本語可)
  print_image      画像ファイルを印刷
  preview_text     テキストの印刷イメージを PNG で返す (印刷しない)
  preview_image    画像の印刷イメージを PNG で返す (印刷しない)
  feed_paper       紙送り
  configure        印字濃度・自動電源オフ時間の設定

接続は呼び出しごとに BLE で行い、終わったら切断する (スマホアプリや CLI と取り合わないため)。

環境変数:
  PAPERANG_BLE_ADDRESS  BLE アドレス (UUID)。省略時は名前 'Paperang*' をスキャン
  PAPERANG_PORT         指定するとシリアルで接続
  PAPERANG_WIDTH        印字幅ドット (既定 384)
  PAPERANG_FEED         印刷後に切り口から出す余白ドット (既定 80 ≒ 10mm)
  PAPERANG_DRY_RUN      1 にすると実機に送らない (動作確認用)
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime
from pathlib import Path

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .printer import DEFAULT_WIDTH, Paperang, PaperangError
from .transport import BleTransport, DryRunTransport, SerialTransport

WIDTH = int(os.environ.get("PAPERANG_WIDTH", DEFAULT_WIDTH))
DEFAULT_FEED = int(os.environ.get("PAPERANG_FEED", 80))
DRY_RUN = os.environ.get("PAPERANG_DRY_RUN", "") not in ("", "0", "false")

mcp = MCPServer(
    "paperang",
    instructions=(
        "Paperang サーマルミニプリンタ (幅 384 ドット ≒ 48mm, 白黒) を操作する。"
        "印刷は紙を消費するので、レイアウトを確かめたいときは preview_* で先に確認する。"
    ),
)

_lock = threading.Lock()  # プリンタは 1 接続のみ


def _open() -> Paperang:
    if DRY_RUN:
        t = DryRunTransport()
    elif os.environ.get("PAPERANG_PORT"):
        t = SerialTransport(os.environ["PAPERANG_PORT"])
    else:
        t = BleTransport(address=os.environ.get("PAPERANG_BLE_ADDRESS") or None)
    return Paperang(t, width=WIDTH)


async def _with_printer(fn):
    """接続 → fn(printer) → 切断 をワーカースレッドで実行する。"""
    def run():
        with _lock:
            try:
                with _open() as p:
                    return fn(p)
            except PaperangError as e:
                raise ToolError(f"プリンタがエラーを返しました: {e}") from e
            except (RuntimeError, OSError) as e:
                raise ToolError(
                    f"{e} (Paperang の電源が入っているか、スマホのアプリとつながって"
                    "いないか確認してください)") from e
    return await anyio.to_thread.run_sync(run)


def _text_mono(text: str, font_size: int, timestamp: bool):
    from .imaging import render_text, to_mono

    if timestamp:
        text = datetime.now().strftime("%Y-%m-%d %H:%M") + "\n" + text
    return to_mono(render_text(text, WIDTH, font_size=font_size), WIDTH, dither=False)


def _image_mono(path: str, dither: bool):
    from PIL import Image as PILImage

    from .imaging import to_mono

    p = Path(path).expanduser()
    if not p.is_file():
        raise ToolError(f"画像ファイルが見つかりません: {p}")
    return to_mono(PILImage.open(p), WIDTH, dither=dither)


def _png(mono) -> Image:
    buf = io.BytesIO()
    mono.convert("L").save(buf, format="PNG")
    return Image(data=buf.getvalue(), format="png")


async def _print_mono(mono, feed: int, density: int | None) -> str:
    from .imaging import mono_to_bitmap

    bitmap = mono_to_bitmap(mono)

    def job(p: Paperang):
        if density is not None:
            p.set_heat_density(density)
        p.print_bitmap_with_margin(bitmap, feed)

    await _with_printer(job)
    mm = mono.height / 8
    return (f"印刷しました ({mono.width}x{mono.height} ドット, 約 {mm:.0f}mm)"
            + (" [dry-run]" if DRY_RUN else ""))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def printer_status() -> dict:
    """プリンタの機種・ファームウェア・電池残量(%)・印字濃度・自動電源オフ秒数などを取得する。"""
    info = await _with_printer(lambda p: p.get_info())
    return {k: v for k, v in info.items() if v is not None}


@mcp.tool()
async def print_text(text: str, font_size: int = 28, timestamp: bool = False,
                     feed: int = DEFAULT_FEED, density: int | None = None) -> str:
    """テキストを印刷する。日本語可、幅に合わせて自動で折り返す。

    Args:
        text: 印刷する文字列 (改行可)
        font_size: 文字の大きさ (px)。28 で 1 行約 13 文字 (全角)
        timestamp: 先頭に現在日時を入れる
        feed: 印刷の終わりから切り口までの余白ドット数 (8 ≒ 1mm、0 で送らない)
        density: 印字濃度 0-100 (省略時はプリンタの設定のまま)
    """
    return await _print_mono(_text_mono(text, font_size, timestamp), feed, density)


@mcp.tool()
async def print_image(path: str, dither: bool = True, feed: int = DEFAULT_FEED,
                      density: int | None = None) -> str:
    """画像ファイル (PNG/JPEG など) を印刷幅に縮小して印刷する。

    Args:
        path: 画像ファイルの絶対パス
        dither: True で写真向けのディザ、False で白黒二値化 (ロゴ・図向け)
        feed: 印刷の終わりから切り口までの余白ドット数 (8 ≒ 1mm、0 で送らない)
        density: 印字濃度 0-100 (省略時はプリンタの設定のまま)
    """
    return await _print_mono(_image_mono(path, dither), feed, density)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def preview_text(text: str, font_size: int = 28, timestamp: bool = False) -> Image:
    """print_text と同じ条件で印刷イメージを作り、PNG で返す。印刷はしない。"""
    return _png(_text_mono(text, font_size, timestamp))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def preview_image(path: str, dither: bool = True) -> Image:
    """print_image と同じ条件で印刷イメージを作り、PNG で返す。印刷はしない。"""
    return _png(_image_mono(path, dither))


@mcp.tool()
async def feed_paper(dots: int = DEFAULT_FEED) -> str:
    """紙を送る。dots はドット数 (8 ≒ 1mm)。白紙を印字して送るので長さは正確。"""
    await _with_printer(lambda p: p.feed(dots))
    return f"{dots} ドット (約 {dots / 8:.0f}mm) 紙送りしました"


@mcp.tool()
async def configure(density: int | None = None, power_down_seconds: int | None = None) -> str:
    """プリンタの設定を変更する (プリンタ本体に保存される)。

    Args:
        density: 印字濃度 0-100
        power_down_seconds: 自動電源オフまでの秒数
    """
    if density is None and power_down_seconds is None:
        raise ToolError("density か power_down_seconds を指定してください")

    def job(p: Paperang):
        if density is not None:
            p.set_heat_density(density)
        if power_down_seconds is not None:
            p.set_power_down_time(power_down_seconds)

    await _with_printer(job)
    return "設定しました"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
