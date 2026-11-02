"""Paperang プリンタの高レベル API。"""

from __future__ import annotations

import struct
import time

from .protocol import (
    DEFAULT_SESSION_KEY,
    DEVICE_CRC_KEY,
    STANDARD_CRC_KEY,
    Cmd,
    Packet,
    PacketParser,
    build_packets,
)
from .transport import Transport

# P1 の印字幅 (203dpi, 48 バイト/ライン)
DEFAULT_WIDTH = 384

ACK_OK = 0x00
# 印字ヘッドから切り口までの距離 (実機で約 7mm)
TEAR_OFFSET_DOTS = 56


class PaperangError(RuntimeError):
    pass


class Paperang:
    """
    crc_key     : 鍵登録後に送受信で使う CRC 鍵。既定は実機で確認した 0x33CE7A78。
    session_key : 接続時に SET_CRC_KEY で登録する鍵 (None で登録しない)。
                  電源投入直後の実機は標準キーでしか受け付けず、この鍵を登録すると
                  以降は crc_key (0x33CE7A78) で通信するようになる。登録済みの状態で
                  もう一度送っても害はない。
    """

    def __init__(self, transport: Transport, width: int = DEFAULT_WIDTH,
                 crc_key: int = DEVICE_CRC_KEY,
                 session_key: int | None = DEFAULT_SESSION_KEY,
                 verbose: bool = False) -> None:
        if width % 8:
            raise ValueError("width must be a multiple of 8")
        self.t = transport
        self.width = width
        self.verbose = verbose
        self._key = crc_key
        self._parser = PacketParser()
        self._pending: list[Packet] = []
        if session_key is not None:
            self._register_crc_key(session_key)

    # ---- 低レベル -------------------------------------------------------
    def send(self, cmd: Cmd, data: bytes = b"") -> None:
        for pkt in build_packets(cmd, data, key=self._key):
            if self.verbose:
                print(f">> {cmd.name} {len(pkt)}B {pkt[:16].hex()}{'..' if len(pkt) > 16 else ''}")
            self.t.write(pkt)

    def receive(self, timeout: float = 1.0) -> list[Packet]:
        pkts = self._parser.feed(self.t.read(timeout))
        if self.verbose:
            for p in pkts:
                ok = "ok" if p.crc_ok(self._key) else "crc-ng"
                print(f"<< {p.cmd_name} {p.data.hex()} ({ok})")
        return pkts

    def _wait_for(self, want: set[int], timeout: float) -> dict[int, bytes]:
        """want に含まれる cmd の応答が揃うまで待つ (揃わなければ取れた分だけ返す)。"""
        got: dict[int, bytes] = {}
        # 前回までに読み過ごした応答も確認する
        for p in self._pending:
            if p.cmd in want:
                got.setdefault(p.cmd, p.data)
        self._pending = [p for p in self._pending if p.cmd not in want]
        deadline = time.monotonic() + timeout
        while want - got.keys() and time.monotonic() < deadline:
            for p in self.receive(min(0.5, max(0.05, deadline - time.monotonic()))):
                if p.cmd in want and p.cmd not in got:
                    got[p.cmd] = p.data
                else:
                    self._pending.append(p)
        return got

    def command(self, cmd: Cmd, data: bytes = b"", timeout: float = 2.0,
                strict: bool = True) -> bool:
        """コマンドを送り ACK (同じ cmd 番号の 1 バイト) を確認する。"""
        self.send(cmd, data)
        ack = self._wait_for({cmd}, timeout).get(cmd)
        ok = ack is not None and ack[:1] == bytes([ACK_OK])
        if not ok and strict:
            detail = "応答なし" if ack is None else f"エラー応答 {ack.hex()}"
            raise PaperangError(f"{cmd.name}: {detail}")
        return ok

    def query(self, cmd: Cmd, expect: Cmd, timeout: float = 2.0) -> bytes | None:
        """GET_xxx を送り、SENT_xxx の応答データを返す。GET 系はデータ 0x01 が必要。"""
        self.send(cmd, b"\x01")
        return self._wait_for({expect, cmd}, timeout).get(expect)

    def _register_crc_key(self, key: int) -> None:
        # 登録パケットは標準キーで CRC を計算し、以降は crc_key を使う。
        # 登録済みの機体はこのパケットに応答しない (または 03 を返す) ので、応答は読み捨てる
        crc_key = self._key
        self._key = STANDARD_CRC_KEY
        self.send(Cmd.SET_CRC_KEY, struct.pack("<I", key))
        self._key = crc_key
        self.receive(0.5)

    # ---- 情報取得 -------------------------------------------------------
    def get_info(self) -> dict[str, object]:
        info: dict[str, object] = {}

        def _str(b: bytes | None) -> str | None:
            return None if b is None else b.rstrip(b"\x00").decode("ascii", "replace")

        info["model"] = _str(self.query(Cmd.GET_MODEL, Cmd.SENT_MODEL))
        info["version"] = self._fmt_version(self.query(Cmd.GET_VERSION, Cmd.SENT_VERSION))
        info["board_version"] = self._fmt_version(
            self.query(Cmd.GET_BOARD_VERSION, Cmd.SENT_BOARD_VERSION))
        info["serial"] = _str(self.query(Cmd.GET_SN, Cmd.SENT_SN))
        mac = self.query(Cmd.GET_BT_MAC, Cmd.SENT_BT_MAC)
        info["bt_mac"] = None if mac is None else ":".join(f"{b:02X}" for b in mac)
        bat = self.query(Cmd.GET_BAT_STATUS, Cmd.SENT_BAT_STATUS)
        info["battery_%"] = None if not bat else bat[0]
        temp = self.query(Cmd.GET_TEMP, Cmd.SENT_TEMP)
        info["temperature"] = None if not temp else temp[0]
        dens = self.query(Cmd.GET_HEAT_DENSITY, Cmd.SENT_HEAT_DENSITY)
        info["heat_density"] = None if not dens else dens[0]
        pdt = self.query(Cmd.GET_POWER_DOWN_TIME, Cmd.SENT_POWER_DOWN_TIME)
        info["power_down_sec"] = None if not pdt or len(pdt) < 2 else struct.unpack_from("<H", pdt)[0]
        status = self.query(Cmd.GET_STATUS, Cmd.SENT_STATUS)
        info["status_raw"] = None if status is None else status.hex()
        if all(v is None for v in info.values()):
            raise PaperangError("どの問い合わせにも応答がありません (接続はできたがプリンタと通信できていません)")
        return info

    @staticmethod
    def _fmt_version(b: bytes | None) -> str | None:
        if not b:
            return None
        text = b.rstrip(b"\x00")
        if text and all(0x20 <= x < 0x7F for x in text):  # ASCII 文字列 (例: "V0.…")
            return text.decode("ascii")
        if len(b) >= 3 and all(x < 100 for x in b[:3]):
            return ".".join(str(x) for x in b[:3])
        return b.hex()

    # ---- 設定 -----------------------------------------------------------
    def set_heat_density(self, value: int) -> None:
        """印字濃度 (目安 0-100、既定 75 前後)。"""
        self.command(Cmd.SET_HEAT_DENSITY, bytes([max(0, min(100, value))]))

    def set_power_down_time(self, seconds: int) -> None:
        self.command(Cmd.SET_POWER_DOWN_TIME, struct.pack("<H", seconds))

    def set_paper_type(self, paper_type: int = 0) -> None:
        self.command(Cmd.SET_PAPER_TYPE, bytes([paper_type]))

    # ---- 印刷 -----------------------------------------------------------
    def feed(self, dots: int) -> None:
        """白紙を印字して紙を送る (単位: ドット行。8 ≒ 1mm で正確)。"""
        if dots > 0:
            self.print_bitmap(self.blank_rows(dots))

    def feed_line(self, dots: int) -> None:
        """FEED_LINE コマンドで紙送り。実機では指定どおりの長さにならない (80 で 10mm 未満)。"""
        self.command(Cmd.FEED_LINE, struct.pack("<H", dots))

    def blank_rows(self, dots: int) -> bytes:
        return bytes(self.width // 8 * max(0, dots))

    def print_bitmap_with_margin(self, bitmap: bytes, margin: int) -> None:
        """印刷し、切り口から margin ドット分の余白が出るところまで送る。

        ヘッドから切り口まで TEAR_OFFSET_DOTS あるので、その分を足した白紙を
        画像の後ろにつなげて 1 回で送る。margin=0 なら送らない。
        """
        extra = margin + TEAR_OFFSET_DOTS if margin > 0 else 0
        self.print_bitmap(bitmap + self.blank_rows(extra))

    def print_test_page(self) -> None:
        self.command(Cmd.PRINT_TEST_PAGE, timeout=10)

    def print_bitmap(self, bitmap: bytes) -> None:
        """1bpp (MSB 先頭, 1=黒) のラスタデータを送る。長さは width/8 の倍数。"""
        bpl = self.width // 8
        if len(bitmap) % bpl:
            raise ValueError(f"bitmap length must be a multiple of {bpl}")
        # 送信の流量制御はトランスポート側で行う (BLE は応答付き書き込みを挟む)。
        # プリンタは正常に受け取ったパケットには応答せず、欠けたパケットには
        # 約 1 秒後に 0x02 を返して捨てる。
        self.send(Cmd.PRINT_DATA, bitmap)
        self.wait_until_printed(len(bitmap) // bpl)
        bad = [p for p in self._pending
               if p.cmd == Cmd.PRINT_DATA and p.data[:1] != bytes([ACK_OK])]
        self._pending = [p for p in self._pending if p.cmd != Cmd.PRINT_DATA]
        if bad:
            raise PaperangError(
                f"PRINT_DATA: エラー応答 {bad[0].data.hex()} x{len(bad)} (データの一部が欠けて印字が途中で切れた可能性があります)")

    def wait_until_printed(self, rows: int = 0) -> None:
        """プリンタがバッファを印字し終えるまで待つ。

        印字中に切断すると残りが捨てられて途中で切れるため、印刷後は必ず待つ。
        プリンタはコマンドを順に処理するので、GET_STATUS の応答が返った時点で
        それより前の印刷は終わっている。
        """
        timeout = 3.0 + rows * 0.02
        self.send(Cmd.GET_STATUS, b"\x01")
        # 実機は SENT_STATUS と GET_STATUS の ACK を続けて返す。ACK だけ待てば十分
        if Cmd.GET_STATUS not in self._wait_for({Cmd.GET_STATUS}, timeout):
            # BLE の書き込み自体は通っても、プリンタが受け取っていないことがある
            raise PaperangError(
                "印刷後の状態確認に応答がありません (プリンタにデータが届いておらず、"
                "印刷されていない可能性があります)")
        self._pending = [p for p in self._pending if p.cmd != Cmd.SENT_STATUS]

    def close(self) -> None:
        self.t.close()

    def __enter__(self) -> "Paperang":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
