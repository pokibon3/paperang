"""BLE 接続した Paperang に問い合わせ方法を何通りか試し、生の応答を表示する診断スクリプト。

使い方: .venv/bin/python tools/diag_ble.py [BLEアドレス(UUID)]
紙送りや印刷は行いません (情報取得コマンドのみ)。
"""

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paperang.protocol import (  # noqa: E402
    DEFAULT_SESSION_KEY,
    STANDARD_CRC_KEY,
    Cmd,
    PacketParser,
    build_packet,
)
from paperang.transport import BleTransport  # noqa: E402

REPLY_KEY = 0x33CE7A78  # 1 回目の診断で応答 CRC から逆算した鍵
KEYS = {"standard": STANDARD_CRC_KEY, "session": DEFAULT_SESSION_KEY, "reply": REPLY_KEY,
        "zero": 0}


def crc_report(raw: bytes) -> str:
    """応答パケットの CRC がどの計算方法と一致するか調べる。"""
    if len(raw) < 10:
        return "too short"
    length = struct.unpack_from("<H", raw, 3)[0]
    data = raw[5:5 + length]
    (crc,) = struct.unpack_from("<I", raw, 5 + length)
    hits = []
    for kname, key in KEYS.items():
        for rname, region in (("data", data), ("cmd..data", raw[1:5 + length]),
                              ("stx..data", raw[:5 + length])):
            if zlib.crc32(region, key) & 0xFFFFFFFF == crc:
                hits.append(f"{rname}/{kname}")
    return f"crc={crc:08x} match={hits or 'none'}"


def exchange(t, parser, label, cmd, data, key):
    pkt = build_packet(cmd, data, key=key)
    t.write(pkt)
    rx = t.read(1.5)
    print(f"\n[{label}]")
    print(f"  >> {pkt.hex()}")
    print(f"  << raw {rx.hex() or '(no reply)'}")
    for p in parser.feed(rx):
        raw = build_packet(p.cmd, p.data, p.index, 0)[:5] + p.data + struct.pack("<IB", p.crc, 3)
        print(f"     {p.cmd_name} idx={p.index} data={p.data.hex()} {crc_report(raw)}")


def round2(t, parser):
    """応答と同じ鍵 0x33CE7A78 で送ると受け付けられるか。紙送り (少量) も試す。"""
    R, S, STD = REPLY_KEY, DEFAULT_SESSION_KEY, STANDARD_CRC_KEY
    exchange(t, parser, "I: GET_MODEL reply-key no-payload", Cmd.GET_MODEL, b"", R)
    exchange(t, parser, "J: GET_MODEL reply-key payload=01", Cmd.GET_MODEL, b"\x01", R)
    exchange(t, parser, "K: GET_BAT reply-key payload=01", Cmd.GET_BAT_STATUS, b"\x01", R)
    exchange(t, parser, "L: GET_VERSION reply-key no-payload", Cmd.GET_VERSION, b"", R)
    feed = struct.pack("<H", 16)  # 16 ドット ≒ 2mm
    exchange(t, parser, "M: FEED_LINE 16 std-key", Cmd.FEED_LINE, feed, STD)
    exchange(t, parser, "N: FEED_LINE 16 reply-key", Cmd.FEED_LINE, feed, R)
    exchange(t, parser, "O: SET_CRC_KEY", Cmd.SET_CRC_KEY, struct.pack("<I", S), STD)
    exchange(t, parser, "P: FEED_LINE 16 session-key", Cmd.FEED_LINE, feed, S)
    exchange(t, parser, "Q: GET_MODEL session-key no-payload", Cmd.GET_MODEL, b"", S)


def main():
    addr = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
    t = BleTransport(address=addr)
    parser = PacketParser()
    try:
        print("connected. mtu =", t._mtu)
        if "--round2" in sys.argv:
            round2(t, parser)
            return
        # 1) 鍵登録前: 標準キーで問い合わせ
        exchange(t, parser, "A: GET_MODEL std-key no-payload", Cmd.GET_MODEL, b"", STANDARD_CRC_KEY)
        exchange(t, parser, "B: GET_MODEL std-key payload=01", Cmd.GET_MODEL, b"\x01", STANDARD_CRC_KEY)
        exchange(t, parser, "C: GET_BAT std-key payload=01", Cmd.GET_BAT_STATUS, b"\x01", STANDARD_CRC_KEY)
        exchange(t, parser, "D: GET_VERSION std-key payload=01", Cmd.GET_VERSION, b"\x01", STANDARD_CRC_KEY)
        # 2) 鍵登録後
        exchange(t, parser, "E: SET_CRC_KEY", Cmd.SET_CRC_KEY,
                 struct.pack("<I", DEFAULT_SESSION_KEY), STANDARD_CRC_KEY)
        exchange(t, parser, "F: GET_MODEL session-key payload=01", Cmd.GET_MODEL, b"\x01", DEFAULT_SESSION_KEY)
        exchange(t, parser, "G: GET_MODEL std-key payload=01 (after key set)", Cmd.GET_MODEL, b"\x01", STANDARD_CRC_KEY)
        exchange(t, parser, "H: GET_BAT session-key payload=01", Cmd.GET_BAT_STATUS, b"\x01", DEFAULT_SESSION_KEY)
    finally:
        t.close()


if __name__ == "__main__":
    main()
