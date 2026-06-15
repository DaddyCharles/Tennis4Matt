"""Ivan modules — integrated into Ivan.

Shared helpers (paths, Sydney-time, currency) live here so weather, earnings,
ai_helper, and notifications can all import them. Logging and JSON I/O are reused
from bot.logger so the whole app shares one settings file and one log buffer.
"""

import os
from datetime import datetime

import pytz

from bot.logger import BASE_DIR, DATA_DIR, SETTINGS_PATH, load_settings

LESSONS_PATH = os.path.join(DATA_DIR, 'lessons.json')
STUDENTS_PATH = os.path.join(DATA_DIR, 'students.json')
NOTIFICATIONS_PATH = os.path.join(DATA_DIR, 'notifications.json')

SYDNEY_TZ = pytz.timezone('Australia/Sydney')

DAY_NAMES = [
    'Monday', 'Tuesday', 'Wednesday', 'Thursday',
    'Friday', 'Saturday', 'Sunday',
]


def now_sydney() -> datetime:
    """Current time in the Australia/Sydney timezone."""
    return datetime.now(SYDNEY_TZ)


def today_str() -> str:
    """Today's date as 'YYYY-MM-DD' (Sydney)."""
    return now_sydney().strftime('%Y-%m-%d')


def time_str() -> str:
    """Current time as 'HH:MM' (Sydney, 24hr)."""
    return now_sydney().strftime('%H:%M')


def format_currency(amount) -> str:
    """Format a number as a dollar string, e.g. $80.00."""
    try:
        return f"${float(amount):.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def blocks_to_minutes(blocks) -> int:
    """Convert 30-minute blocks to total minutes."""
    try:
        return int(blocks) * 30
    except (TypeError, ValueError):
        return 0


def minutes_to_label(minutes: int) -> str:
    """Render minutes as 'X hour(s) Y minutes'."""
    hours, mins = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} minutes")
    return " ".join(parts) if parts else "0 minutes"
