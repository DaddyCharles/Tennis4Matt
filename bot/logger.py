"""Logging, shared state, and JSON/file helpers for Ivan.

Everything else in the project depends on this module. It owns:
  - The in-memory log buffer (read by the dashboard's live log feed)
  - The shared BOT_STATUS dict (read by the dashboard's status feed)
  - Safe JSON load/save helpers used everywhere
  - Settings loading with defaults merged in
  - human_delay() for human-like pacing between browser actions
"""

import json
import logging
import os
import random
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, 'config')
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
SESSION_DIR = os.path.join(BASE_DIR, 'session')

SETTINGS_PATH = os.path.join(CONFIG_DIR, 'settings.json')
ACTIVITY_LOG_PATH = os.path.join(LOGS_DIR, 'activity.log')

# Default settings — merged under whatever is on disk so missing keys never crash.
DEFAULT_SETTINGS = {
    "scan_interval_minutes": 15,
    "active_hours_start": "07:00",
    "active_hours_end": "21:00",
    "daily_limit": 20,
    "min_delay_seconds": 8,
    "max_delay_seconds": 25,
    "headless_mode": True,
    "email_notifications": False,
    "email_address": "",
    "email_smtp": "",
    "email_password": "",
    "bot_running": False,
    "session_created_at": "",
    # --- Coach Pro settings ----------------------------------------------
    "coach_name": "Matt",
    "coach_title": "Mr",
    "location": "Panania, NSW, Australia",
    "latitude": -33.9522,
    "longitude": 151.0286,
    "currency": "AUD",
    "default_lesson_price": 80,
    "pricing": {
        "duration_prices": {"30": 45, "45": 65, "60": 80, "90": 110, "120": 140},
        "presets": [
            {"name": "Standard", "amount": 80},
            {"name": "Junior", "amount": 60},
            {"name": "Group", "amount": 40},
        ],
    },
    "court_name": "",
    "court_address": "",
    "lights_warning_minutes": 45,
    "anthropic_api_key": "",
    "push_notifications_enabled": False,
    "vapid_public_key": "",
    "vapid_private_key": "",
    "vapid_claim_email": "mailto:coach@example.com",
    "working_hours_start": "07:00",
    "working_hours_end": "20:00",
    "timezone": "Australia/Sydney",
    # --- Sole trader business modules ------------------------------------
    "modules": {
        "invoicing": True,
        "expense_tracker": True,
        "tax_estimator": True,
        "waitlist": True,
        "lesson_packages": True,
    },
    "invoicing": {
        "coach_abn": "",
        "coach_address": "",
        "bank_name": "",
        "bank_bsb": "",
        "bank_account": "",
        "invoice_prefix": "INV",
        "next_invoice_number": 1,
        "payment_terms_days": 7,
        "gst_registered": False,
    },
    "tax": {
        "financial_year_start": "2025-07-01",
        "other_income": 0,
        "has_help_debt": False,
        "has_private_health": False,
        "quarterly_payg_rate": None,
    },
    "availability": {
        "monday": {"open": True, "start": "07:00", "end": "19:00"},
        "tuesday": {"open": True, "start": "07:00", "end": "19:00"},
        "wednesday": {"open": True, "start": "07:00", "end": "19:00"},
        "thursday": {"open": True, "start": "07:00", "end": "19:00"},
        "friday": {"open": True, "start": "07:00", "end": "19:00"},
        "saturday": {"open": True, "start": "08:00", "end": "16:00"},
        "sunday": {"open": False, "start": "08:00", "end": "16:00"},
        "custom_presets": {},
    },
    "help": {
        "show_help_button": True,
        "show_feature_tips": True,
        "completed_tours": [],
        "dismissed_tips": [],
    },
    "ai": {
        "provider": "groq",
        "groq_api_key": "",
        "model": "llama-3.3-70b-versatile",
        "enabled": False,
    },
}

# ---------------------------------------------------------------------------
# Shared in-memory state
# ---------------------------------------------------------------------------

LOG_BUFFER: list[str] = []          # Last 100 log lines
LOG_LOCK = threading.Lock()         # Protects LOG_BUFFER
_MAX_BUFFER = 100

BOT_STATUS: dict = {
    "running": False,
    "last_scan": None,              # ISO timestamp string or None
    "today_count": 0,               # Leads found today
    "last_error": None,             # Last error message or None
}
_STATUS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# File logger setup (rotating at 5MB)
# ---------------------------------------------------------------------------

def _build_file_logger() -> logging.Logger:
    """Create a rotating file logger for logs/activity.log."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    logger = logging.getLogger('fb_lead_monitor')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = RotatingFileHandler(
            ACTIVITY_LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=2,
            encoding='utf-8'
        )
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    return logger


_FILE_LOGGER = _build_file_logger()


def _emit(level: str, message: str) -> None:
    """Format, store, persist, and print a single log line."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"
    with LOG_LOCK:
        LOG_BUFFER.append(line)
        if len(LOG_BUFFER) > _MAX_BUFFER:
            del LOG_BUFFER[:len(LOG_BUFFER) - _MAX_BUFFER]
    try:
        _FILE_LOGGER.info(line)
    except Exception:
        pass
    # This is the one allowed console output point — the standard says route all
    # logging through here rather than calling print() directly elsewhere.
    print(line)


def log_info(message: str) -> None:
    """Log an informational message."""
    _emit("INFO", message)


def log_success(message: str) -> None:
    """Log a success message."""
    _emit("SUCCESS", message)


def log_warning(message: str) -> None:
    """Log a warning message."""
    _emit("WARNING", message)


def log_error(message: str) -> None:
    """Log an error message and record it as the last error in BOT_STATUS."""
    _emit("ERROR", message)
    with _STATUS_LOCK:
        BOT_STATUS["last_error"] = message


def get_log_buffer(n: int = 50) -> list[str]:
    """Return the last n lines from the in-memory log buffer (thread-safe)."""
    with LOG_LOCK:
        return list(LOG_BUFFER[-n:])


def clear_log_buffer() -> None:
    """Empty the in-memory log buffer (used by the dashboard 'Clear Log' button)."""
    with LOG_LOCK:
        LOG_BUFFER.clear()


def get_bot_status() -> dict:
    """Return a copy of the shared BOT_STATUS dict."""
    with _STATUS_LOCK:
        return dict(BOT_STATUS)


def update_bot_status(**kwargs) -> None:
    """Update one or more fields of BOT_STATUS, e.g. update_bot_status(running=True)."""
    with _STATUS_LOCK:
        for key, value in kwargs.items():
            BOT_STATUS[key] = value


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def load_json(filepath: str, default=None):
    """Load a JSON file safely; return default if missing or corrupt."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(filepath: str, data) -> bool:
    """Save data to a JSON file safely. Returns True on success."""
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log_error(f"Failed to save {filepath}: {e}")
        return False


def load_settings() -> dict:
    """Load settings.json with all default keys merged in."""
    raw = load_json(SETTINGS_PATH, {})
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(raw, dict):
        settings.update(raw)
    return settings


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------

def human_delay(min_s: float = None, max_s: float = None) -> None:
    """Sleep for a random human-like duration drawn from settings (or args)."""
    settings = load_settings()
    lo = min_s if min_s is not None else settings.get('min_delay_seconds', 5)
    hi = max_s if max_s is not None else settings.get('max_delay_seconds', 15)
    if hi < lo:
        lo, hi = hi, lo
    time.sleep(random.uniform(lo, hi))
