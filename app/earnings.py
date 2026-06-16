"""Earnings calculations derived on-the-fly from data/lessons.json.

Only lessons with status 'completed' or 'scheduled' count toward earnings;
prices are read from each lesson's own price field (set at booking time), so
changing default prices never rewrites history.
"""

import calendar
import csv
import io
from datetime import date, datetime, timedelta

from bot.logger import load_json, load_settings, save_json
from app import LESSONS_PATH, STUDENTS_PATH, now_sydney

COUNTED_STATUSES = {"completed", "scheduled"}


def _lessons() -> list:
    data = load_json(LESSONS_PATH, {"lessons": []})
    items = data.get("lessons", [])
    return items if isinstance(items, list) else []


def _students() -> list:
    data = load_json(STUDENTS_PATH, {"students": []})
    items = data.get("students", [])
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


def get_price_for_minutes(minutes: int) -> float:
    """Price for a lesson length. Single source of truth: pricing.duration_prices.

    Uses the exact configured price if present, otherwise the nearest mapped
    duration's per-minute rate, then a sensible default rate.
    """
    settings = load_settings()
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        mins = 60
    pricing = settings.get("pricing", {}) or {}
    prices = pricing.get("duration_prices", {}) or {}
    exact = prices.get(str(mins))
    if exact is not None:
        return float(exact)
    if prices:
        ref = min(prices.keys(), key=lambda k: abs(int(k) - mins))
        per_min = float(prices[ref]) / max(1, int(ref))
        return round(per_min * mins, 2)
    return round((80 / 60) * mins, 2)


def get_price_for_blocks(blocks: int) -> float:
    """Price for N 30-minute blocks. Delegates to the single pricing source."""
    try:
        n = int(blocks)
    except (TypeError, ValueError):
        n = 1
    return get_price_for_minutes(max(1, n) * 30)


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


def _days_since(date_str: str) -> int:
    d = _parse_date(date_str)
    if d is None:
        return 0
    return max(0, (now_sydney().date() - d).days)


def get_money_owed() -> list:
    """Students with outstanding balances from completed-unpaid lessons.

    Returns one dict per student, sorted by days_outstanding descending.
    """
    students = {s.get("id"): s for s in _students()}
    by_student = {}
    for l in _lessons():
        if l.get("status") != "completed" or l.get("payment_status") != "unpaid":
            continue
        sid = l.get("student_id")
        if not sid:
            continue
        entry = by_student.setdefault(sid, [])
        entry.append(l)

    result = []
    for sid, lessons in by_student.items():
        student = students.get(sid, {})
        lessons_sorted = sorted(lessons, key=lambda l: l.get("date", ""))
        amount = round(sum(_price(l) for l in lessons_sorted), 2)
        if amount <= 0:
            continue
        oldest = lessons_sorted[0].get("date", "")
        result.append({
            "student_id": sid,
            "student_name": student.get("name", lessons_sorted[0].get("student_name", "Unknown")),
            "student_phone": student.get("phone", ""),
            "lessons_unpaid": len(lessons_sorted),
            "amount_owed": amount,
            "oldest_unpaid_date": oldest,
            "days_outstanding": _days_since(oldest),
            "lessons": lessons_sorted,
        })

    result.sort(key=lambda r: r["days_outstanding"], reverse=True)
    return result


def get_total_owed() -> float:
    """Sum of all outstanding amounts across students."""
    return round(sum(r["amount_owed"] for r in get_money_owed()), 2)


def mark_student_paid(student_id: str) -> int:
    """Mark all unpaid completed lessons for a student as paid. Returns count."""
    data = load_json(LESSONS_PATH, {"lessons": []})
    lessons = data.get("lessons", [])
    if not isinstance(lessons, list):
        return 0
    changed = 0
    for l in lessons:
        if (l.get("student_id") == student_id
                and l.get("status") == "completed"
                and l.get("payment_status") == "unpaid"):
            l["payment_status"] = "paid"
            changed += 1
    if changed:
        save_json(LESSONS_PATH, {"lessons": lessons})
    return changed


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
