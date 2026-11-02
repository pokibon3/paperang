"""画像・テキストを 1bpp ラスタに変換する (Pillow 使用)。"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

JP_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
]


def load_font(size: int, path: str | None = None) -> ImageFont.FreeTypeFont:
    for p in ([path] if path else []) + JP_FONT_CANDIDATES:
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def fit_width(img: Image.Image, width: int) -> Image.Image:
    """幅をプリンタ幅に合わせる (縦横比維持、透過は白背景に合成)。"""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, "white")
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")
    h = max(1, round(img.height * width / img.width))
    return img.resize((width, h), Image.Resampling.LANCZOS)


def to_mono(img: Image.Image, width: int, dither: bool = True,
            threshold: int = 128) -> Image.Image:
    """プリンタ幅のモノクロ (mode '1') 画像にする。"""
    gray = fit_width(img, width)
    if dither:
        return gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    return gray.point(lambda v: 255 if v >= threshold else 0, mode="1")


def render_text(text: str, width: int, font_size: int = 28, font_path: str | None = None,
                margin: int = 8, line_spacing: int = 6) -> Image.Image:
    """テキストを折り返して描画する (日本語は 1 文字単位で折り返し)。"""
    font = load_font(font_size, font_path)
    max_w = width - margin * 2
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    lines: list[str] = []
    for para in text.splitlines() or [""]:
        cur = ""
        for ch in para:
            if probe.textlength(cur + ch, font=font) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)

    ascent, descent = font.getmetrics()
    lh = ascent + descent + line_spacing
    img = Image.new("L", (width, margin * 2 + lh * len(lines)), 255)
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((margin, margin + i * lh), line, font=font, fill=0)
    return img


def mono_to_bitmap(mono: Image.Image) -> bytes:
    """mode '1' 画像 → プリンタ用バイト列 (1=黒)。"""
    if mono.mode != "1":
        raise ValueError("image must be mode '1'")
    # PIL の '1' は 1=白 なので反転してからパック
    return ImageOps.invert(mono.convert("L")).convert("1").tobytes()
