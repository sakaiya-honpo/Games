"""Build-time script: generate launcher/icon.ico using Pillow."""
import os
from PIL import Image, ImageDraw

sizes = [256, 64, 48, 32, 16]
imgs = []
for sz in sizes:
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    p = max(1, sz // 8)
    d.rounded_rectangle([p, p, sz-p-1, sz-p-1], radius=sz//5, fill=(46, 160, 100, 255))
    m = sz // 4
    pts = [(m + sz//10, m), (m + sz//10, sz-m), (sz-m, sz//2)]
    d.polygon(pts, fill=(255, 255, 255, 230))
    imgs.append(img)

out = os.path.join("launcher", "icon.ico")
imgs[0].save(out, format="ICO", append_images=imgs[1:])
print(f"Generated {out}")
