"""生成应用图标（icon.ico 多尺寸 + logo.png）——深底 + 红色录制点 + 波形。"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
S = 256


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角深色底
    d.rounded_rectangle([8, 8, S - 8, S - 8], radius=52, fill=(43, 52, 64, 255))
    # 波形条
    bars = [(96, 128), (128, 88), (160, 152), (192, 108), (224, 136)]
    for x, h in bars:
        d.rounded_rectangle([x - 9, h - 18, x + 9, h + 34], radius=9, fill=(232, 236, 241, 255))
    # 红色录制点（漂浮在波形上）
    d.ellipse([60, 60, 118, 118], fill=(224, 67, 47, 255))
    return img


def main() -> None:
    img = draw()
    img.save(OUT / "logo.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    img.save(OUT / "icon.ico", sizes=[(s, s) for s in sizes])
    print("icon.ico / logo.png ->", OUT)


if __name__ == "__main__":
    main()
