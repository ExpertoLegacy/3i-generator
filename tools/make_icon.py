"""Draw the app icon: a report sheet with binder holes, in the template's blues."""
from pathlib import Path

from PIL import Image, ImageDraw

TARGET = Path(__file__).resolve().parent.parent / "packaging" / "app.ico"
SCALE = 8
SIZE = 64 * SCALE


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((3 * SCALE, 2 * SCALE, 61 * SCALE, 62 * SCALE), radius=6 * SCALE, fill="#1F3D63")
    pen.rectangle((13 * SCALE, 9 * SCALE, 54 * SCALE, 55 * SCALE), fill="#FFFFFF")
    for y in (16, 32, 48):
        pen.ellipse((6 * SCALE, (y - 3) * SCALE, 12 * SCALE, (y + 3) * SCALE), fill="#CDD8E4")
    for y, end in ((18, 48), (26, 48), (34, 48), (42, 38)):
        pen.rectangle((20 * SCALE, y * SCALE, end * SCALE, (y + 3) * SCALE), fill="#2E5C8A")
    pen.rectangle((20 * SCALE, 11 * SCALE, 40 * SCALE, 14 * SCALE), fill="#E8A90C")
    image.save(TARGET, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
