"""キングジム Rolto (ロルト) PT10 を Wi-Fi で操作する。

プロトコル (iPhone の Rolto アプリの通信を記録して解析。2026-11 実機で確認):

UDP 20480 (検索・状態)
  -> e2 00 00 00                        ブロードキャストで検索
  <- c4 00 00 15 00 07 00 0a "KINGJIMRolto PT10"
  -> e1 06 00 00                        状態取得
  <- c3 06 00 0c  BUSY(u32) ?(u16) ?(u16) VALUE(u32)
     BUSY は印字中 1。VALUE は時間とともに減る値 (電池か温度と思われる。未確定)

TCP 16384 (印刷)  各コマンドは CMD(u16 BE) + LEN(u16 BE) + DATA
  -> e101 len4 00000000                 開始。<- c301 len12 の応答を待つ
  -> e102 len8 6000(?) WIDTH(u16) HEIGHT(u32)
  -> e103 len3076 ROWS(u16) MORE(u16) + 3072 バイト
                                        64 行 (48 バイト/行) ずつ。足りない分は 0 埋め。
                                        最後のブロックだけ MORE=0
  -> e104 len0                          印刷実行
  画像は 1bpp、1=黒、バイト内は LSB が左 (Paperang と逆)。
  印刷完了の応答は無い。印字中に切断すると途中で切れるので、印字が終わるまで待つ。
"""

from __future__ import annotations

import socket
import struct
import time

WIDTH = 384
BYTES_PER_ROW = WIDTH // 8
ROWS_PER_BLOCK = 64
TCP_PORT = 16384
UDP_PORT = 20480
MODEL_NAME = b"KINGJIMRolto PT10"
# 印字ヘッドから切り口までの距離 (ドット)。PT10 では未計測
TEAR_OFFSET_DOTS = 0


class RoltoError(RuntimeError):
    pass


def _cmd(code: int, data: bytes = b"") -> bytes:
    return struct.pack(">HH", code, len(data)) + data


def discover(timeout: float = 2.0, broadcast: str = "255.255.255.255") -> list[tuple[str, str]]:
    """LAN 内の PT10 を探し、(IP, 名前) のリストを返す。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    found: dict[str, str] = {}
    try:
        deadline = time.monotonic() + timeout
        s.sendto(_cmd(0xE200), (broadcast, UDP_PORT))
        while time.monotonic() < deadline:
            try:
                data, (ip, _) = s.recvfrom(256)
            except socket.timeout:
                continue
            if data[:2] == b"\xc4\x00":
                found[ip] = data[8:].decode("ascii", "replace")
    finally:
        s.close()
    return sorted(found.items())


def mono_to_rows(mono) -> list[bytes]:
    """PIL の白黒画像 (幅 384) を PT10 のラスタ行 (LSB 左) に変換する。"""
    if mono.width != WIDTH:
        raise ValueError(f"width must be {WIDTH}")
    # PIL の "1" モードは MSB 左・1=白 なので、反転してビット順を入れ替える
    raw = mono.convert("1").tobytes()
    rev = bytes(int(f"{b:08b}"[::-1], 2) ^ 0xFF for b in range(256))
    stride = (mono.width + 7) // 8
    return [raw[y * stride:(y + 1) * stride].translate(rev) for y in range(mono.height)]


class RoltoPT10:
    def __init__(self, host: str, timeout: float = 5.0) -> None:
        self.host = host
        self.timeout = timeout

    def status(self) -> dict[str, object]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.timeout / 2)
        try:
            s.sendto(_cmd(0xE106), (self.host, UDP_PORT))
            data, _ = s.recvfrom(64)
        except socket.timeout as e:
            raise RoltoError(f"PT10 ({self.host}) から状態の応答がありません") from e
        finally:
            s.close()
        if data[:2] != b"\xc3\x06" or len(data) < 16:
            raise RoltoError(f"想定外の状態応答: {data.hex()}")
        busy, a, b, value = struct.unpack(">IHHI", data[4:16])
        return {"busy": bool(busy), "value": value, "raw": data.hex()}

    def print_rows(self, rows: list[bytes]) -> None:
        if not rows:
            return
        job = _cmd(0xE102, struct.pack(">HHI", 0x6000, WIDTH, len(rows)))
        for i in range(0, len(rows), ROWS_PER_BLOCK):
            block = rows[i:i + ROWS_PER_BLOCK]
            more = 1 if i + ROWS_PER_BLOCK < len(rows) else 0
            body = b"".join(block).ljust(BYTES_PER_ROW * ROWS_PER_BLOCK, b"\0")
            job += _cmd(0xE103, struct.pack(">HH", len(block), more) + body)
        job += _cmd(0xE104)

        try:
            sock = socket.create_connection((self.host, TCP_PORT), timeout=self.timeout)
        except OSError as e:
            raise RoltoError(f"PT10 ({self.host}:{TCP_PORT}) に接続できません: {e}") from e
        with sock:
            sock.settimeout(self.timeout)
            sock.sendall(_cmd(0xE101, b"\0" * 4))
            ack = sock.recv(64)
            if ack[:2] != b"\xc3\x01":
                raise RoltoError(f"開始コマンドへの応答が想定外: {ack.hex() or '(なし)'}")
            sock.sendall(job)
            self._wait_until_printed(len(rows))

    def _wait_until_printed(self, rows: int) -> None:
        """印字が終わるまで待つ (この間は接続を保つ)。

        状態の BUSY が 1 → 0 になったら完了。BUSY が一度も立たない場合に備えて
        行数から見積もった時間 (実機で 92 行は 2 秒で足り、110 行は足りなかった) も待つ。
        """
        estimate = 2.0 + rows * 0.03
        start = time.monotonic()
        seen_busy = False
        while time.monotonic() - start < estimate + 30:
            try:
                busy = self.status()["busy"]
            except RoltoError:
                busy = None
            if busy:
                seen_busy = True
            elif busy is False and (seen_busy or time.monotonic() - start >= estimate):
                break
            time.sleep(0.3)
        time.sleep(0.5)

    def feed(self, dots: int) -> None:
        """白紙を印字して紙を送る (8 ドット ≒ 1mm)。"""
        self.print_rows([bytes(BYTES_PER_ROW)] * max(0, dots))

    def print_mono_with_margin(self, mono, margin: int) -> None:
        extra = margin + TEAR_OFFSET_DOTS if margin > 0 else 0
        self.print_rows(mono_to_rows(mono) + [bytes(BYTES_PER_ROW)] * extra)
