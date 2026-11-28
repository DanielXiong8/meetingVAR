"""把联络表帧拼成一张网格图，带时间码标签。

用法: python -I tile_sheet.py <frames_dir> <out.png> [cols] [thumb_w]
"""
import glob
import os
import sys

from PIL import Image, ImageDraw

frames = sorted(glob.glob(os.path.join(sys.argv[1], "f_*.png")))
out = sys.argv[2]
cols = int(sys.argv[3]) if len(sys.argv) > 3 else 6
tw = int(sys.argv[4]) if len(sys.argv) > 4 else 460

th = int(tw * 9 / 16)
rows = (len(frames) + cols - 1) // cols
sheet = Image.new("RGB", (cols * tw, rows * th), (26, 26, 28))
draw = ImageDraw.Draw(sheet)

for i, f in enumerate(frames):
    im = Image.open(f).convert("RGB").resize((tw, th), Image.LANCZOS)
    x, y = (i % cols) * tw, (i // cols) * th
    sheet.paste(im, (x, y))
    label = os.path.basename(f)[2:-4].lstrip("0") or "0"
    draw.rectangle([x, y, x + 54, y + 20], fill=(0, 0, 0))
    draw.text((x + 6, y + 5), f"{label}s", fill=(255, 255, 255))

sheet.save(out)
print(out, sheet.size, len(frames), "frames")