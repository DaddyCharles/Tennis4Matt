"""Run Ivan as a background app with a system tray icon.

Launched via pythonw (no console window). Starts the Flask dashboard and
the scan loop in background threads, draws a green tray icon, and opens the
dashboard in the browser. Right-click the tray icon for controls.
"""

import os
import threading
import time
import webbrowser

import pystray
from PIL import Image, ImageDraw

from bot.logger import log_info, update_bot_status
from bot.scanner import run_scan_loop
from app import notifications as coach_notifications
from main import run_flask, run_weather_loop, PORT

DASHBOARD_URL = f"http://127.0.0.1:{PORT}"
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(_BASE_DIR, "dashboard", "static", "ivan-logo.png")


def make_icon_image() -> Image.Image:
    """Use the Ivan logo for the tray icon, or fall back to a green circle."""
    try:
        if os.path.exists(LOGO_PATH):
            img = Image.open(LOGO_PATH).convert("RGBA")
            return img.resize((64, 64), Image.LANCZOS)
    except Exception:
        pass
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, size - 6, size - 6), fill=(0, 212, 170, 255))
    return image


def open_dashboard(icon=None, item=None) -> None:
    webbrowser.open(DASHBOARD_URL)


def start_bot(icon=None, item=None) -> None:
    update_bot_status(running=True)
    log_info("Bot started from tray.")


def stop_bot(icon=None, item=None) -> None:
    update_bot_status(running=False)
    log_info("Bot stopped from tray.")


def quit_app(icon, item) -> None:
    log_info("Ivan shutting down from tray.")
    icon.stop()


def start_background_services() -> None:
    """Start Flask + scan loop in daemon threads (same as main.py)."""
    log_info("Ivan starting (tray mode)...")
    flask_thread = threading.Thread(target=run_flask, daemon=True, name="flask")
    scan_thread = threading.Thread(target=run_scan_loop, daemon=True, name="scan")
    weather_thread = threading.Thread(target=run_weather_loop, daemon=True, name="weather")
    notify_thread = threading.Thread(
        target=coach_notifications.run_notification_loop, daemon=True, name="notify"
    )
    flask_thread.start()
    scan_thread.start()
    weather_thread.start()
    notify_thread.start()
    log_info(f"Dashboard available at {DASHBOARD_URL}")


def delayed_open_browser() -> None:
    time.sleep(3)
    open_dashboard()


def main() -> None:
    start_background_services()

    threading.Thread(target=delayed_open_browser, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Open Ivan", open_dashboard, default=True),
        pystray.MenuItem("Start Bot", start_bot),
        pystray.MenuItem("Stop Bot", stop_bot),
        pystray.MenuItem("Quit", quit_app),
    )

    icon = pystray.Icon(
        "ivan",
        make_icon_image(),
        "Ivan",
        menu,
    )

    def on_ready(ic):
        ic.visible = True
        try:
            ic.notify("Ivan is running", "Ivan")
        except Exception:
            pass

    icon.run(setup=on_ready)


if __name__ == "__main__":
    main()
