"""Paperang (P1 系) のパケットプロトコル実装。

パケット構造 (リトルエンディアン):

    +------+-----+-------+--------+----------+--------+------+
    | 0x02 | cmd | index | len(2) | data(len)| crc(4) | 0x03 |
    +------+-----+-------+--------+----------+--------+------+

- crc は data 部分のみを対象にした CRC32 (zlib 互換)。初期値に「CRC キー」を使う。
- 電源投入直後の実機は標準キー 0x35769521 で送受信する。標準キーで SET_CRC_KEY
  (鍵 0x06B8EF59) を送ると、以降は送受信とも 0x33CE7A78 になる (電源を切るまで)。
  スマホのアプリがこの登録をするので、アプリ使用後はすでに 0x33CE7A78 になっている。
- 各コマンドには同じ cmd 番号で 1 バイトの ACK が返る (00=OK, 03=エラー)。
- GET_xxx 系の問い合わせはデータ 0x01 を付けないとエラーになる。
- 1 パケットあたりのデータ長は最大 1536 バイト。超える場合は分割し index を 0,1,2.. と振る。

参考: オープンソース実装 python-paperang (BtCommandByte など)
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

STANDARD_CRC_KEY = 0x35769521
# 実機で SET_CRC_KEY (DEFAULT_SESSION_KEY) を登録した後に使われる鍵。
# 登録済みの状態で SET_CRC_KEY を送っても変わらない。
DEVICE_CRC_KEY = 0x33CE7A78
DEFAULT_SESSION_KEY = 0x6968634 ^ 0x2E696D
MAX_DATA_PER_PACKET = 1536

PACKET_START = 0x02
PACKET_END = 0x03
HEADER_SIZE = 5  # start + cmd + index + len(2)
FOOTER_SIZE = 5  # crc(4) + end


class Cmd(IntEnum):
    PRINT_DATA = 0
    PRINT_DATA_COMPRESS = 1
    FIRMWARE_DATA = 2
    USB_UPDATE_FIRMWARE = 3
    GET_VERSION = 4
    SENT_VERSION = 5
    GET_MODEL = 6
    SENT_MODEL = 7
    GET_BT_MAC = 8
    SENT_BT_MAC = 9
    GET_SN = 10
    SENT_SN = 11
    GET_STATUS = 12
    SENT_STATUS = 13
    GET_VOLTAGE = 14
    SENT_VOLTAGE = 15
    GET_BAT_STATUS = 16
    SENT_BAT_STATUS = 17
    GET_TEMP = 18
    SENT_TEMP = 19
    SET_FACTORY_STATUS = 20
    GET_FACTORY_STATUS = 21
    SENT_FACTORY_STATUS = 22
    SENT_BT_STATUS = 23
    SET_CRC_KEY = 24
    SET_HEAT_DENSITY = 25
    FEED_LINE = 26
    PRINT_TEST_PAGE = 27
    GET_HEAT_DENSITY = 28
    SENT_HEAT_DENSITY = 29
    SET_POWER_DOWN_TIME = 30
    GET_POWER_DOWN_TIME = 31
    SENT_POWER_DOWN_TIME = 32
    FEED_TO_HEAD_LINE = 33
    PRINT_DEFAULT_PARA = 34
    GET_BOARD_VERSION = 35
    SENT_BOARD_VERSION = 36
    GET_HW_INFO = 37
    SENT_HW_INFO = 38
    SET_MAX_GAP_LENGTH = 39
    GET_MAX_GAP_LENGTH = 40
    SENT_MAX_GAP_LENGTH = 41
    GET_PAPER_TYPE = 42
    SENT_PAPER_TYPE = 43
    SET_PAPER_TYPE = 44
    GET_COUNTRY_NAME = 45
    SENT_COUNTRY_NAME = 46
    DISCONNECT_BT_CMD = 47


def crc32(data: bytes, key: int) -> int:
    """zlib.crc32 にキーを初期値として渡したもの (python-paperang と同じ計算)。"""
    return zlib.crc32(data, key) & 0xFFFFFFFF


def build_packet(cmd: int, data: bytes, index: int = 0, key: int = STANDARD_CRC_KEY) -> bytes:
    if len(data) > 0xFFFF:
        raise ValueError("data too long for a single packet")
    return (
        struct.pack("<BBBH", PACKET_START, int(cmd), index & 0xFF, len(data))
        + data
        + struct.pack("<IB", crc32(data, key), PACKET_END)
    )


def build_packets(cmd: int, data: bytes, key: int = STANDARD_CRC_KEY,
                  chunk: int = MAX_DATA_PER_PACKET) -> list[bytes]:
    """データを chunk バイトごとに分割してパケット列を作る。空データでも 1 パケット返す。"""
    if not data:
        return [build_packet(cmd, b"", 0, key)]
    return [build_packet(cmd, data[i:i + chunk], n, key)
            for n, i in enumerate(range(0, len(data), chunk))]


@dataclass
class Packet:
    cmd: int
    index: int
    data: bytes
    crc: int

    def crc_ok(self, key: int) -> bool:
        return crc32(self.data, key) == self.crc

    @property
    def cmd_name(self) -> str:
        try:
            return Cmd(self.cmd).name
        except ValueError:
            return f"UNKNOWN({self.cmd})"


class PacketParser:
    """バイトストリームからパケットを切り出すインクリメンタルパーサ。"""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Packet]:
        self._buf += chunk
        out: list[Packet] = []
        while True:
            # 先頭の 0x02 まで読み飛ばす
            start = self._buf.find(PACKET_START)
            if start < 0:
                self._buf.clear()
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < HEADER_SIZE:
                break
            _, cmd, index, length = struct.unpack_from("<BBBH", self._buf, 0)
            total = HEADER_SIZE + length + FOOTER_SIZE
            if len(self._buf) < total:
                break
            if self._buf[total - 1] != PACKET_END:
                # 同期ずれ。1 バイト進めて再同期する
                del self._buf[:1]
                continue
            data = bytes(self._buf[HEADER_SIZE:HEADER_SIZE + length])
            (crc,) = struct.unpack_from("<I", self._buf, HEADER_SIZE + length)
            out.append(Packet(cmd, index, data, crc))
            del self._buf[:total]
        return out
