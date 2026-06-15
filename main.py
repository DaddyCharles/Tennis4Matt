"""Entry point for Ivan.

Starts two daemon threads inside one process:
  1. The Flask dashboard (http://localhost:9999)
  2. The background scan loop

Both are daemons, so they stop when the main thread exits (Ctrl+C).
"""

import os
import shutil
import sys
import threading
import time

# Ensure runtime data/config files exist before anything imports them.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_data_files() -> None:
    """Copy clean default JSON files into place on first run.

    The live data/config files are git-ignored, so a fresh clone starts with
    only the templates in data/defaults/ and config/defaults/. For each
    template, if the matching live file is missing, copy it across. Existing
    files are never overwritten.
    """
    pairs = [
        (os.path.join(_BASE_DIR, "data", "defaults"), os.path.join(_BASE_DIR, "data")),
        (os.path.join(_BASE_DIR, "config", "defaults"), os.path.join(_BASE_DIR, "config")),
    ]
    for defaults_dir, live_dir in pairs:
        if not os.path.isdir(defaults_dir):
            continue
        os.makedirs(live_dir, exist_ok=True)
        for name in os.listdir(defaults_dir):
            if not name.endswith(".json"):
                continue
            dest = os.path.join(live_dir, name)
            if not os.path.exists(dest):
                shutil.copyfile(os.path.join(defaults_dir, name), dest)
    # Make sure the logs/ and session/ folders exist too.
    os.makedirs(os.path.join(_BASE_DIR, "logs"), exist_ok=True)
    os.makedirs(os.path.join(_BASE_DIR, "session"), exist_ok=True)


ensure_data_files()

from bot.logger import log_error, log_info
from bot.scanner import run_scan_loop
from dashboard.app import app

from app import notifications as coach_notifications
from app import weather as coach_weather

HOST = "0.0.0.0"
PORT = 9999
WEATHER_REFRESH_SECONDS = 1800  # 30 minutes


def run_flask() -> None:
    """Run the Flask dashboard server (blocking)."""
    # use_reloader=False so we don't spawn a second process/thread set.
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


def run_weather_loop() -> None:
    """Refresh the cached weather every 30 minutes."""
    while True:
        try:
            coach_weather.refresh_weather_cache()
        except Exception as e:
            log_error(f"Weather loop error: {e}")
        time.sleep(WEATHER_REFRESH_SECONDS)


def main() -> None:
    """Launch the dashboard and scan loop, then keep the process alive."""
    log_info("Ivan starting...")

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

    print("=" * 60)
    print("  Ivan is running")
    print(f"  Open your dashboard at: http://localhost:{PORT}")
    print("  Press Ctrl+C in this window to stop.")
    print("=" * 60)
    log_info(f"Dashboard available at http://localhost:{PORT}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_info("Shutting down Ivan...")
        print("\nShutting down...")


def login_only() -> None:
    """Open a visible browser for one-time Facebook login, then exit."""
    from bot.browser import create_session

    log_info("Starting Facebook login...")
    print("=" * 60)
    print("  Facebook Login")
    print("  A browser window will open. Log in to Facebook, then")
    print("  return to this window.")
    print("=" * 60)
    ok = create_session()
    if ok:
        print("\nLogin saved. You can close this window.")
    else:
        print("\nLogin was not saved. Please try again.")


if __name__ == "__main__":
    if "--login-only" in sys.argv:
        login_only()
    else:
        main()
