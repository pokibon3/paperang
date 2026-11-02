"""Paperang サンプル CLI.

例:
    python -m paperang ports
    python -m paperang info
    python -m paperang text "こんにちは Paperang!"
    python -m paperang image photo.jpg --density 80
    python -m paperang text "テスト" --dry-run --preview out.png
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .printer import DEFAULT_WIDTH, Paperang
from .transport import BleTransport, DryRunTransport, SerialTransport, find_serial_ports


def open_printer(args) -> Paperang:
    if args.dry_run:
        t = DryRunTransport()
    elif args.port or args.serial:
        port = args.port
        if port is None:
            ports = find_serial_ports()
            if not ports:
                sys.exit("Paperang のシリアルポートが見つかりません。--port で指定するか、"
                         "BLE (既定) で接続してください。")
            port = ports[0]
        if args.verbose:
            print(f"connect (serial): {port}")
        t = SerialTransport(port)
    else:
        if args.verbose:
            print(f"connect (BLE): {args.ble or 'scan Paperang*'}")
        try:
            t = BleTransport(address=args.ble)
        except RuntimeError as e:
            sys.exit(f"{e}\nPaperang の電源が入っているか、スマホのアプリとつながって"
                     "いないか確認してください。")
    return Paperang(t, width=args.width, verbose=args.verbose)


def print_mono(args, mono) -> None:
    from .imaging import mono_to_bitmap

    if args.preview:
        mono.save(args.preview)
        print(f"preview saved: {args.preview} ({mono.width}x{mono.height})")
    with open_printer(args) as p:
        if args.density is not None:
            p.set_heat_density(args.density)
        p.print_bitmap_with_margin(mono_to_bitmap(mono), args.feed)
        if args.dry_run:
            sent = p.t.sent  # type: ignore[attr-defined]
            print(f"dry-run: {len(sent)} packets, {sum(map(len, sent))} bytes")


def cmd_ports(args) -> None:
    ports = find_serial_ports()
    print("\n".join(ports) if ports else "(Paperang らしきシリアルポートなし)")


def cmd_scan(args) -> None:
    import asyncio

    from bleak import BleakScanner

    from .transport import BLE_SERVICE

    async def _scan():
        found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        for d, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
            if not d.name and not args.all:
                continue
            mark = "*" if BLE_SERVICE in [u.lower() for u in adv.service_uuids] else " "
            print(f"{mark} {adv.rssi:4} dBm  {d.address}  {d.name or '-'}")
        print("(* = Paperang 系の UART サービスを広告しているデバイス)")

    asyncio.run(_scan())


def cmd_info(args) -> None:
    with open_printer(args) as p:
        for k, v in p.get_info().items():
            print(f"{k:15}: {v}")


def cmd_text(args) -> None:
    from .imaging import render_text, to_mono

    text = sys.stdin.read() if args.text == "-" else args.text
    if args.timestamp:
        text = datetime.now().strftime("%Y-%m-%d %H:%M") + "\n" + text
    img = render_text(text, args.width, font_size=args.size, font_path=args.font)
    print_mono(args, to_mono(img, args.width, dither=False))


def cmd_image(args) -> None:
    from PIL import Image

    from .imaging import to_mono

    mono = to_mono(Image.open(args.path), args.width, dither=not args.no_dither,
                   threshold=args.threshold)
    print_mono(args, mono)


def cmd_feed(args) -> None:
    with open_printer(args) as p:
        p.feed(args.dots)


def cmd_testpage(args) -> None:
    with open_printer(args) as p:
        p.print_test_page()


def cmd_config(args) -> None:
    with open_printer(args) as p:
        if args.density is not None:
            p.set_heat_density(args.density)
        if args.power_down is not None:
            p.set_power_down_time(args.power_down)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("接続")
    g.add_argument("--ble", nargs="?", const=None, metavar="ADDR",
                   help="BLE のアドレス (UUID) を指定。既定は BLE で名前 'Paperang*' をスキャン")
    g.add_argument("--serial", action="store_true",
                   help="BLE ではなくシリアル (Bluetooth SPP) で接続。ポートは自動検出")
    g.add_argument("--port", help="シリアルポートを指定して接続 (例: /dev/cu.Paperang-XXXX)")
    g.add_argument("--dry-run", action="store_true", help="実機に送らずパケット数だけ表示")
    g.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="印字幅ドット (P1=384)")
    g.add_argument("-v", "--verbose", action="store_true", help="送受信パケットを表示")

    pr = argparse.ArgumentParser(add_help=False)
    g = pr.add_argument_group("印刷")
    g.add_argument("--density", type=int, help="印字濃度 0-100")
    g.add_argument("--feed", type=int, default=80, help="切り口から出す余白ドット数 (既定 80≒10mm, 0 で送らない)")
    g.add_argument("--preview", metavar="PNG", help="印刷イメージを PNG に保存")

    ap = argparse.ArgumentParser(prog="paperang", description="Paperang サンプルツール")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="候補のシリアルポートを表示").set_defaults(func=cmd_ports)
    s = sub.add_parser("scan", help="周囲の BLE デバイスを表示")
    s.add_argument("--timeout", type=float, default=8.0)
    s.add_argument("--all", action="store_true", help="名前のないデバイスも表示")
    s.set_defaults(func=cmd_scan)
    sub.add_parser("info", parents=[common], help="機種・電池などを表示").set_defaults(func=cmd_info)

    s = sub.add_parser("text", parents=[common, pr], help="テキストを印刷 ('-' で標準入力)")
    s.add_argument("text")
    s.add_argument("--size", type=int, default=28, help="フォントサイズ (px)")
    s.add_argument("--font", help="フォントファイルのパス")
    s.add_argument("--timestamp", action="store_true", help="先頭に日時を付ける")
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("image", parents=[common, pr], help="画像を印刷")
    s.add_argument("path")
    s.add_argument("--no-dither", action="store_true", help="ディザなし (二値化)")
    s.add_argument("--threshold", type=int, default=128, help="二値化しきい値 (--no-dither 時)")
    s.set_defaults(func=cmd_image)

    s = sub.add_parser("feed", parents=[common], help="紙送り")
    s.add_argument("dots", type=int, nargs="?", default=80)
    s.set_defaults(func=cmd_feed)

    sub.add_parser("testpage", parents=[common], help="内蔵テストページを印刷").set_defaults(
        func=cmd_testpage)

    s = sub.add_parser("config", parents=[common], help="設定を書き込む")
    s.add_argument("--density", type=int, help="印字濃度 0-100")
    s.add_argument("--power-down", type=int, metavar="SEC", help="自動電源オフまでの秒数")
    s.set_defaults(func=cmd_config)
    return ap


def main(argv: list[str] | None = None) -> None:
    from .printer import PaperangError

    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except PaperangError as e:
        sys.exit(f"プリンタがエラーを返しました: {e}")


if __name__ == "__main__":
    main()
