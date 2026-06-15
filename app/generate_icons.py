"""Generate Ivan PWA icons + favicon from dashboard/static/ivan-logo.png.

The source logo sits on a solid white background; we knock the white out to
transparency and centre the logo on a dark (#0a1628) rounded square with
safe-zone padding so it survives PWA maskable cropping. Run automatically by
setup.bat; safe to re-run.
"""
import os

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "dashboard", "static")
LOGO_PATH = os.path.join(STATIC_DIR, "ivan-logo.png")

BG = (10, 22, 40)  # #0a1628


def _knockout_white(im, thresh=238):
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if r >= thresh and g >= thresh and b >= thresh:
                px[x, y] = (r, g, b, 0)
    return im


def _on_bg(src, size, pad_ratio=0.14):
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(size * 0.22)
    ImageDraw.Draw(canvas).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=BG + (255,))
    inner = int(size * (1 - 2 * pad_ratio))
    logo = src.resize((inner, inner), Image.LANCZOS)
    off = (size - inner) // 2
    canvas.alpha_composite(logo, (off, off))
    return canvas


def generate():
    os.makedirs(STATIC_DIR, exist_ok=True)
    if not os.path.exists(LOGO_PATH):
        return False
    src = Image.open(LOGO_PATH)
    if src.mode != "RGBA" or src.getextrema()[3][0] == 255:
        src = _knockout_white(src)
    for size in (192, 512):
        _on_bg(src, size).save(os.path.join(STATIC_DIR, f"icon-{size}.png"), "PNG")
    fav = _on_bg(src, 64, pad_ratio=0.06)
    fav.save(os.path.join(STATIC_DIR, "favicon.ico"),
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return True


if __name__ == "__main__":
    ok = generate()
    print("Ivan icons generated." if ok else "ivan-logo.png not found; skipped.")
