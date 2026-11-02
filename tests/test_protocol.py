import struct
import unittest

from paperang.printer import Paperang, PaperangError
from paperang.protocol import (
    DEFAULT_SESSION_KEY,
    DEVICE_CRC_KEY,
    MAX_DATA_PER_PACKET,
    STANDARD_CRC_KEY,
    Cmd,
    PacketParser,
    build_packet,
    build_packets,
    crc32,
)
from paperang.transport import DryRunTransport


class ProtocolTest(unittest.TestCase):
    def test_packet_layout(self):
        pkt = build_packet(Cmd.FEED_LINE, struct.pack("<H", 80), index=0)
        self.assertEqual(pkt[0], 0x02)
        self.assertEqual(pkt[1], Cmd.FEED_LINE)
        self.assertEqual(pkt[2], 0)
        self.assertEqual(struct.unpack_from("<H", pkt, 3)[0], 2)
        self.assertEqual(pkt[5:7], b"\x50\x00")
        self.assertEqual(struct.unpack_from("<I", pkt, 7)[0], crc32(b"\x50\x00", STANDARD_CRC_KEY))
        self.assertEqual(pkt[-1], 0x03)
        self.assertEqual(len(pkt), 12)

    def test_split(self):
        data = bytes(range(256)) * 13  # 3328 bytes -> 1536 + 1536 + 256
        pkts = build_packets(Cmd.PRINT_DATA, data)
        self.assertEqual([p[2] for p in pkts], [0, 1, 2])
        self.assertEqual([struct.unpack_from("<H", p, 3)[0] for p in pkts],
                         [MAX_DATA_PER_PACKET, MAX_DATA_PER_PACKET, 256])

    def test_parser_roundtrip_with_noise_and_fragments(self):
        a = build_packet(Cmd.SENT_MODEL, b"P1\x00", key=DEFAULT_SESSION_KEY)
        b = build_packet(Cmd.SENT_BAT_STATUS, b"\x55", key=DEFAULT_SESSION_KEY)
        stream = b"\xff\x00" + a + b
        parser = PacketParser()
        got = []
        for i in range(0, len(stream), 3):  # 3 バイトずつ到着
            got += parser.feed(stream[i:i + 3])
        self.assertEqual([p.cmd for p in got], [Cmd.SENT_MODEL, Cmd.SENT_BAT_STATUS])
        self.assertEqual(got[0].data, b"P1\x00")
        self.assertTrue(all(p.crc_ok(DEFAULT_SESSION_KEY) for p in got))

    def test_without_handshake_uses_device_key(self):
        t = DryRunTransport()
        Paperang(t, session_key=None).feed_line(10)
        (pkt,) = PacketParser().feed(b"".join(t.sent))
        self.assertEqual(pkt.cmd, Cmd.FEED_LINE)
        self.assertTrue(pkt.crc_ok(DEVICE_CRC_KEY))

    def test_known_device_packets(self):
        # 実機が受け付けたパケット (診断ログより)
        self.assertEqual(build_packet(Cmd.GET_MODEL, b"\x01", key=DEVICE_CRC_KEY).hex(),
                         "0206000100016fe8e8fb03")
        self.assertEqual(build_packet(Cmd.FEED_LINE, struct.pack("<H", 16), key=DEVICE_CRC_KEY).hex(),
                         "021a0002001000bc582d5c03")

    def test_parse_real_reply(self):
        # 実機の GET_MODEL 応答: SENT_MODEL "Paperang" + ACK 00
        raw = bytes.fromhex("02070008005061706572616e67025d701d03020600010000f9d8ef8c03")
        model, ack = PacketParser().feed(raw)
        self.assertEqual((model.cmd, model.data), (Cmd.SENT_MODEL, b"Paperang"))
        self.assertEqual((ack.cmd, ack.data), (Cmd.GET_MODEL, b"\x00"))
        self.assertTrue(model.crc_ok(DEVICE_CRC_KEY) and ack.crc_ok(DEVICE_CRC_KEY))

    def test_session_key_handshake(self):
        t = DryRunTransport()
        Paperang(t).feed_line(10)
        first, second = PacketParser().feed(b"".join(t.sent))
        # 既定では標準キーで鍵を登録し、以降は実機の鍵 0x33CE7A78 を使う
        self.assertEqual(first.cmd, Cmd.SET_CRC_KEY)
        self.assertEqual(first.data, struct.pack("<I", DEFAULT_SESSION_KEY))
        self.assertTrue(first.crc_ok(STANDARD_CRC_KEY))
        self.assertTrue(second.crc_ok(DEVICE_CRC_KEY))

    def test_error_ack_raises(self):
        with self.assertRaises(PaperangError):
            Paperang(DryRunTransport(ack=0x03)).feed_line(10)

    def test_print_without_reply_raises(self):
        # 応答が無いのに「印刷しました」と返さない
        with self.assertRaises(PaperangError):
            Paperang(DryRunTransport(ack=None)).print_bitmap(b"\xff" * 48)

    def test_info_without_reply_raises(self):
        with self.assertRaises(PaperangError):
            Paperang(DryRunTransport(ack=None)).get_info()

    def test_bitmap_length_check(self):
        p = Paperang(DryRunTransport())
        with self.assertRaises(ValueError):
            p.print_bitmap(b"\x00" * 47)
        p.print_bitmap(b"\xff" * 48 * 2)

    def _print_data(self, t):
        pkts = PacketParser().feed(b"".join(t.sent))
        return b"".join(x.data for x in pkts if x.cmd == Cmd.PRINT_DATA)

    def test_feed_prints_blank_rows(self):
        t = DryRunTransport()
        Paperang(t).feed(80)
        self.assertEqual(self._print_data(t), bytes(48 * 80))

    def test_margin_adds_tear_offset(self):
        from paperang.printer import TEAR_OFFSET_DOTS
        t = DryRunTransport()
        Paperang(t).print_bitmap_with_margin(b"\xff" * 48, 80)
        self.assertEqual(self._print_data(t), b"\xff" * 48 + bytes(48 * (80 + TEAR_OFFSET_DOTS)))
        t = DryRunTransport()
        Paperang(t).print_bitmap_with_margin(b"\xff" * 48, 0)
        self.assertEqual(self._print_data(t), b"\xff" * 48)


if __name__ == "__main__":
    unittest.main()
