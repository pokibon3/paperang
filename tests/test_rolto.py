import unittest

from PIL import Image

from paperang.rolto import BYTES_PER_ROW, WIDTH, mono_to_rows


class RoltoTest(unittest.TestCase):
    def test_rows_are_lsb_first_and_black_is_one(self):
        img = Image.new("1", (WIDTH, 2), 1)  # 白
        img.putpixel((0, 0), 0)    # 1 行目の左端を黒
        img.putpixel((15, 1), 0)   # 2 行目の 16 ドット目を黒
        rows = mono_to_rows(img)
        self.assertEqual([len(r) for r in rows], [BYTES_PER_ROW] * 2)
        self.assertEqual(rows[0][0], 0x01)
        self.assertEqual(rows[1][1], 0x80)
        self.assertEqual(sum(rows[0]) + sum(rows[1]), 0x81)

    def test_width_check(self):
        with self.assertRaises(ValueError):
            mono_to_rows(Image.new("1", (100, 1), 1))


if __name__ == "__main__":
    unittest.main()
