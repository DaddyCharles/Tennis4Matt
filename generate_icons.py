"""Generate Ivan's iOS (apple-touch-icon) and Windows icons from the bundled logos.

Step 1 (PWA icons 192/512 + favicon.ico) lives in app/generate_icons.py.
This script covers:
  - Step 2: iOS apple-touch-icon set + a 1024 App-Store size
  - Step 3: Windows tray .ico + tile (mstile) PNGs

Source logos live in dashboard/static/. Optional per-platform overrides
(ivan-logo-ios.png, ivan-logo-windows.png) are used when present; otherwise we
fall back to ivan-logo.png. Run automatically by setup; safe to re-run.
"""
import os
import sys

from PIL import Image, ImageDraw

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app.generate_icons import _knockout_white, STATIC_DIR, BG  # noqa: E402

ICONS_DIR = os.path.join(STATIC_DIR, "icons")

LOGO_DEFAULT = os.path.join(STATIC_DIR, "ivan-logo.png")
LOGO_IOS = os.path.join(STATIC_DIR, "ivan-logo-ios.png")
LOGO_WINDOWS = os.path.join(STATIC_DIR, "ivan-logo-windows.png")

# Apple recommends opaque, full-bleed squares (iOS applies its own mask).
IOS_SIZES = (120, 152, 167, 180)
IOS_APP_STORE = 1024

# Windows app/tray icon embeds several sizes in one .ico.
WIN_ICO_SIZES = (16, 32, 48, 64, 128, 256)
# Windows Start-menu / pinned tiles (logo on transparent, tile colour comes from theme).
MSTILE_SIZES = (70, 150, 310)


def _load_source(path):
    """Open a logo and knock out its white background if it isn't already transparent."""
    src = Image.open(path)
    if src.mode != "RGBA" or src.getextrema()[3][0] == 255:
        src = _knockout_white(src)
    return src


def _resolve(preferred, fallback=LOGO_DEFAULT):
    return preferred if os.path.exists(preferred) else fallback


def _square_opaque(src, size, pad_ratio=0.12, bg=BG):
    """Logo centred on a fully opaque square (for iOS — no transparent corners)."""
    canvas = Image.new("RGBA", (size, size), bg + (255,))
    inner = int(size * (1 - 2 * pad_ratio))
    logo = src.resize((inner, inner), Image.LANCZOS)
    off = (size - inner) // 2
    canvas.alpha_composite(logo, (off, off))
    return canvas


def _square_rounded(src, size, pad_ratio=0.14, bg=BG):
    """Logo on a dark rounded square with transparent corners (for the .ico)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(size * 0.22)
    ImageDraw.Draw(canvas).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=bg + (255,))
    inner = int(size * (1 - 2 * pad_ratio))
    logo = src.resize((inner, inner), Image.LANCZOS)
    off = (size - inner) // 2
    canvas.alpha_composite(logo, (off, off))
    return canvas


def _logo_only(src, size, pad_ratio=0.16):
    """Logo centred on a transparent square (for Windows tiles)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = int(size * (1 - 2 * pad_ratio))
    logo = src.resize((inner, inner), Image.LANCZOS)
    off = (size - inner) // 2
    canvas.alpha_composite(logo, (off, off))
    return canvas


def generate_ios():
    """Step 2: apple-touch-icon set + 1024 App-Store icon."""
    src = _load_source(_resolve(LOGO_IOS))
    for size in IOS_SIZES:
        _square_opaque(src, size).save(
            os.path.join(ICONS_DIR, f"apple-touch-icon-{size}.png"), "PNG")
    # Default apple-touch-icon (180 is the modern iPhone size).
    _square_opaque(src, 180).save(
        os.path.join(ICONS_DIR, "apple-touch-icon.png"), "PNG")
    _square_opaque(src, IOS_APP_STORE, pad_ratio=0.10).save(
        os.path.join(ICONS_DIR, "icon-1024.png"), "PNG")


def generate_windows():
    """Step 3: Windows tray .ico + Start-menu tile PNGs."""
    src = _load_source(_resolve(LOGO_WINDOWS))
    # Multi-size .ico for the system tray / app icon.
    base = _square_rounded(src, max(WIN_ICO_SIZES))
    base.save(os.path.join(ICONS_DIR, "ivan.ico"),
              sizes=[(s, s) for s in WIN_ICO_SIZES])
    # Square + wide tiles.
    for size in MSTILE_SIZES:
        _logo_only(src, size).save(
            os.path.join(ICONS_DIR, f"mstile-{size}x{size}.png"), "PNG")
    # Wide tile (310x150): logo on a transparent 310x150 canvas.
    wide = Image.new("RGBA", (310, 150), (0, 0, 0, 0))
    inner = int(150 * (1 - 2 * 0.16))
    logo = src.resize((inner, inner), Image.LANCZOS)
    wide.alpha_composite(logo, ((310 - inner) // 2, (150 - inner) // 2))
    wide.save(os.path.join(ICONS_DIR, "mstile-310x150.png"), "PNG")


def generate():
    if not os.path.exists(LOGO_DEFAULT):
        return False
    os.makedirs(ICONS_DIR, exist_ok=True)
    generate_ios()
    generate_windows()
    return True


if __name__ == "__main__":
    ok = generate()
    print("Ivan iOS + Windows icons generated."
          if ok else "ivan-logo.png not found; skipped.")
