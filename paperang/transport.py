"""プリンタとの通信路。

- SerialTransport : macOS でペアリング済みの Paperang は /dev/cu.Paperang-XXXX のような
                    シリアルポート (Bluetooth SPP) として現れる。いちばん確実な経路。
- BleTransport    : BLE (Microchip ISSC 透過 UART サービス) 経由。機種/ファームにより
                    非対応の場合あり。bleak が必要。
- DryRunTransport : 実機なし。送信パケットを記録するだけ (デバッグ・テスト用)。
"""

from __future__ import annotations

import asyncio
import glob
import threading
import time
from typing import Protocol


class Transport(Protocol):
    def write(self, data: bytes) -> None: ...
    def read(self, timeout: float) -> bytes: ...
    def close(self) -> None: ...


def find_serial_ports() -> list[str]:
    ports = glob.glob("/dev/cu.*")
    return sorted(p for p in ports if "paperang" in p.lower() or "mia" in p.lower())


class SerialTransport:
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        import serial  # pyserial

        self._ser = serial.Serial(port, baudrate=baudrate, timeout=0.1, write_timeout=10)

    def write(self, data: bytes) -> None:
        self._ser.write(data)
        self._ser.flush()

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            n = self._ser.in_waiting
            if n:
                buf += self._ser.read(n)
                # まとまって届くことが多いので少しだけ追加で待つ
                time.sleep(0.05)
                if not self._ser.in_waiting:
                    break
            else:
                time.sleep(0.02)
        return bytes(buf)

    def close(self) -> None:
        self._ser.close()


# Microchip ISSC Transparent UART
BLE_SERVICE = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
BLE_WRITE_CHAR = "49535343-8841-43f4-a8d4-ecbe34729bb3"
BLE_NOTIFY_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"
CONFIRM_EVERY = 16  # BLE: この個数ごとに応答付き書き込みを挟む


class BleTransport:
    """bleak (asyncio) を専用スレッドで回し、同期 API として見せる。"""

    def __init__(self, address: str | None = None, name_prefix: str = "Paperang",
                 scan_timeout: float = 8.0) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._rx = bytearray()
        self._rx_lock = threading.Lock()
        self._client = self._run(self._connect(address, name_prefix, scan_timeout))

    def _run(self, coro, timeout: float | None = 60):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _connect(self, address, name_prefix, scan_timeout):
        from bleak import BleakClient, BleakScanner

        if address is None:
            dev = await BleakScanner.find_device_by_filter(
                lambda d, adv: bool(d.name) and d.name.lower().startswith(name_prefix.lower()),
                timeout=scan_timeout,
            )
            if dev is None:
                raise RuntimeError(f"BLE デバイス '{name_prefix}*' が見つかりません")
        else:
            dev = address
        client = BleakClient(dev)
        await client.connect()
        await client.start_notify(BLE_NOTIFY_CHAR, self._on_notify)
        char = client.services.get_characteristic(BLE_WRITE_CHAR)
        self._mtu = max(20, char.max_write_without_response_size if char else 20)
        return client

    def _on_notify(self, _sender, data: bytearray) -> None:
        with self._rx_lock:
            self._rx += data

    def write(self, data: bytes) -> None:
        # 応答なし書き込みだけで流すとプリンタ側であふれてデータが欠ける。
        # CONFIRM_EVERY 個ごと (と最後) を応答付きにして流量を抑える。
        # 実機で 1=8.2秒 / 4=5.7秒 / 16=4.6秒 (約 58mm)、いずれも欠けなし。
        chunks = [data[i:i + self._mtu] for i in range(0, len(data), self._mtu)]

        async def _w():
            for k, chunk in enumerate(chunks):
                confirm = k % CONFIRM_EVERY == CONFIRM_EVERY - 1 or k == len(chunks) - 1
                await self._client.write_gatt_char(BLE_WRITE_CHAR, chunk, response=confirm)
        self._run(_w(), timeout=None)

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._rx_lock:
                if self._rx:
                    time.sleep(0.05)
                    out = bytes(self._rx)
                    self._rx.clear()
                    return out
            time.sleep(0.02)
        return b""

    def close(self) -> None:
        try:
            self._run(self._client.disconnect(), timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)


class DryRunTransport:
    """実機の代わり。送られたパケットを記録し、各パケットに ACK を返す。

    ack: 返す ACK コード (0x00=OK, 0x03=エラー)。None なら何も返さない。
    """

    def __init__(self, ack: int | None = 0x00) -> None:
        from .protocol import DEVICE_CRC_KEY, PacketParser

        self.sent: list[bytes] = []
        self._ack = ack
        self._key = DEVICE_CRC_KEY
        self._parser = PacketParser()
        self._rx = bytearray()

    def write(self, data: bytes) -> None:
        from .protocol import build_packet

        self.sent.append(bytes(data))
        if self._ack is None:
            return
        for p in self._parser.feed(data):
            self._rx += build_packet(p.cmd, bytes([self._ack]), key=self._key)

    def read(self, timeout: float) -> bytes:
        out = bytes(self._rx)
        self._rx.clear()
        return out

    def close(self) -> None:
        pass
