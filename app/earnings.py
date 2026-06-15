"""Earnings calculations derived on-the-fly from data/lessons.json.

Only lessons with status 'completed' or 'scheduled' count toward earnings;
prices are read from each lesson's own price field (set at booking time), so
changing default prices never rewrites history.
"""

import calendar
import csv
import io
from datetime import date, datetime, timedelta

from bot.logger import load_json, load_settings
from app import LESSONS_PATH, now_sydney

COUNTED_STATUSES = {"completed", "scheduled"}


def _lessons() -> list:
    data = load_json(LESSONS_PATH, {"lessons": []})
    items = data.get("lessons", [])
    return items if isinstance(items, list) else []


def _counts(lesson: dict) -> bool:
    return lesson.get("status") in COUNTED_STATUSES


def _price(lesson: dict) -> float:
    try:
        return float(lesson.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def get_price_for_blocks(blocks: int) -> float:
    """Price for N 30-minute blocks, using configured per-duration prices."""
    settings = load_settings()
    prices = settings.get("lesson_prices", {}) or {}
    try:
        n = int(blocks)
    except (TypeError, ValueError):
        n = 1
    key = {1: "30min", 2: "60min", 3: "90min", 4: "120min"}.get(n)
    if key and key in prices:
        return float(prices[key])
    per_block = float(prices.get("60min", 80)) / 2.0
    return round(per_block * n, 2)


def get_today_earnings() -> float:
    today = now_sydney().strftime("%Y-%m-%d")
    return round(sum(_price(l) for l in _lessons() if _counts(l) and l.get("date") == today), 2)


def get_week_earnings(date_str: str = None) -> float:
    ref = _parse_date(date_str) if date_str else now_sydney().date()
    if ref is None:
        ref = now_sydney().date()
    start = ref - timedelta(days=ref.weekday())
    end = start + timedelta(days=6)
    total = 0.0
    for l in _lessons():
        d = _parse_date(l.get("date", ""))
        if d and start <= d <= end and _counts(l):
            total += _price(l)
    return round(total, 2)


def get_month_earnings(year: int = None, month: int = None) -> float:
    now = now_sydney()
    year = year or now.year
    month = month or now.month
    total = 0.0
    for l in _lessons():
        d = _parse_date(l.get("date", ""))
        if d and d.year == year and d.month == month and _counts(l):
            total += _price(l)
    return round(total, 2)


def get_year_earnings(year: int = None) -> float:
    year = year or now_sydney().year
    total = 0.0
    for l in _lessons():
        d = _parse_date(l.get("date", ""))
        if d and d.year == year and _counts(l):
            total += _price(l)
    return round(total, 2)


def get_unpaid_total() -> float:
    total = 0.0
    for l in _lessons():
        if l.get("status") == "completed" and l.get("payment_status") == "unpaid":
            total += _price(l)
    return round(total, 2)


def get_projected_month() -> float:
    """Completed + still-scheduled lessons for the current month (a forecast)."""
    return get_month_earnings()


def get_weekly_chart_data(weeks: int = 8) -> dict:
    today = now_sydney().date()
    this_monday = today - timedelta(days=today.weekday())
    labels, data, counts = [], [], []
    for i in range(weeks - 1, -1, -1):
        start = this_monday - timedelta(weeks=i)
        end = start + timedelta(days=6)
        total = 0.0
        count = 0
        for l in _lessons():
            d = _parse_date(l.get("date", ""))
            if d and start <= d <= end and _counts(l):
                total += _price(l)
                count += 1
        labels.append(start.strftime("%b %d"))
        data.append(round(total, 2))
        counts.append(count)
    return {"labels": labels, "data": data, "lesson_counts": counts}


def get_monthly_chart_data(months: int = 12) -> dict:
    now = now_sydney()
    labels, data, counts = [], [], []
    year, month = now.year, now.month
    seq = []
    for _ in range(months):
        seq.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    for (y, m) in reversed(seq):
        total = 0.0
        count = 0
        for l in _lessons():
            d = _parse_date(l.get("date", ""))
            if d and d.year == y and d.month == m and _counts(l):
                total += _price(l)
                count += 1
        labels.append(date(y, m, 1).strftime("%b %y"))
        data.append(round(total, 2))
        counts.append(count)
    return {"labels": labels, "data": data, "lesson_counts": counts}


def get_daily_chart_data(year: int = None, month: int = None) -> dict:
    now = now_sydney()
    year = year or now.year
    month = month or now.month
    days_in_month = calendar.monthrange(year, month)[1]
    labels = [str(d) for d in range(1, days_in_month + 1)]
    data = [0.0] * days_in_month
    for l in _lessons():
        d = _parse_date(l.get("date", ""))
        if d and d.year == year and d.month == month and _counts(l):
            data[d.day - 1] += _price(l)
    data = [round(v, 2) for v in data]
    today_index = (now.day - 1) if (now.year == year and now.month == month) else -1
    return {"labels": labels, "data": data, "today_index": today_index}


def get_earnings_summary() -> dict:
    return {
        "today": get_today_earnings(),
        "week": get_week_earnings(),
        "month": get_month_earnings(),
        "year": get_year_earnings(),
        "unpaid": get_unpaid_total(),
        "projected": get_projected_month(),
    }


def export_csv() -> str:
    columns = [
        "date", "start_time", "student_name", "duration_minutes",
        "blocks", "price", "status", "payment_status", "notes",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    ordered = sorted(_lessons(), key=lambda l: (l.get("date", ""), l.get("start_time", "")))
    for lesson in ordered:
        writer.writerow({c: lesson.get(c, "") for c in columns})
    return buffer.getvalue()
