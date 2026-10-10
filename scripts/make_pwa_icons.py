"""Рисует PNG-иконки PWA по тому же знаку, что и frontend/public/icon.svg.

Запуск из корня репозитория: python scripts/make_pwa_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icons"
BACKGROUND = (23, 41, 35, 255)  # #172923
GLYPH = (185, 237, 120, 255)  # #b9ed78
SUPERSAMPLE = 4

# Знак в координатах viewBox 128x128: прямоугольники осевых штрихов шириной 10.
STROKES = [
    (27, 27, 37, 96),
    (27, 27, 101, 37),
    (91, 27, 101, 96),
    (43, 43, 53, 80),
    (43, 43, 85, 53),
    (75, 43, 85, 80),
    (59, 48, 69, 96),
]


def render(size: int, glyph_scale: float, corner: float) -> Image.Image:
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if corner:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * corner, fill=BACKGROUND)
    else:
        draw.rectangle((0, 0, big, big), fill=BACKGROUND)
    unit = big / 128 * glyph_scale
    shift = (big - 128 * unit) / 2
    for x0, y0, x1, y1 in STROKES:
        draw.rectangle(
            (shift + x0 * unit, shift + y0 * unit, shift + x1 * unit, shift + y1 * unit),
            fill=GLYPH,
        )
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # purpose "any": скруглённый квадрат, как у icon.svg
    render(192, 1.0, 28 / 128).save(OUT / "icon-192.png")
    render(512, 1.0, 28 / 128).save(OUT / "icon-512.png")
    # purpose "maskable": фон на всю площадь, знак внутри безопасной зоны (80%)
    render(512, 0.6, 0).save(OUT / "icon-maskable-512.png")
    # iOS сам скругляет иконку, поэтому фон непрозрачный и без скруглений
    render(180, 0.75, 0).save(OUT / "apple-touch-icon.png")


if __name__ == "__main__":
    main()
