"""Optional AI features via the Anthropic Claude API.

Every function returns None silently if no API key is configured, and all API
calls are wrapped so a failure never crashes a request or background thread.
"""

import json

from bot.logger import log_error, load_settings

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 500
TEMPERATURE = 0.7


def get_api_key() -> str:
    return (load_settings().get("anthropic_api_key", "") or "").strip()


def ai_available() -> bool:
    """True if an Anthropic API key is configured."""
    return bool(get_api_key())


def _call(prompt: str, system: str = None):
    """Send a single-turn message to Claude and return the text, or None."""
    key = get_api_key()
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        kwargs = {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts).strip()
    except Exception as e:
        log_error(f"AI call failed: {e}")
        return None


def summarise_student_progress(student: dict, lessons: list):
    """Return a short coaching progress summary for a student, or None."""
    if not ai_available():
        return None
    history = [
        f"- {l.get('date', '')} {l.get('start_time', '')} "
        f"({l.get('duration_minutes', 0)}min, {l.get('status', '')}): "
        f"{l.get('lesson_summary') or l.get('notes') or 'no notes'}"
        for l in lessons[-20:]
    ]
    prompt = (
        f"Student: {student.get('name', 'Unknown')}\n"
        f"Level: {student.get('level', 'Unknown')}\n"
        f"Coach notes: {student.get('notes', 'none')}\n\n"
        f"Lesson history:\n" + ("\n".join(history) if history else "No lessons yet.")
    )
    system = (
        "You are an experienced tennis coach. Summarise this student's progress "
        "based on their lesson history and notes. Be specific and practical. "
        "Return 3-4 sentences max."
    )
    return _call(prompt, system)


def get_earnings_insight(lessons: list, settings: dict):
    """Return {busiest_day, top_student, trend, recommendation} or None."""
    if not ai_available():
        return None
    rows = [
        f"- {l.get('date', '')} {l.get('student_name', '')} ${l.get('price', 0)} "
        f"({l.get('status', '')})"
        for l in lessons[-120:]
    ]
    prompt = (
        "Here are recent tennis lessons:\n" + "\n".join(rows) + "\n\n"
        "Analyse this data and respond with ONLY a JSON object with keys: "
        "busiest_day, top_student, trend, recommendation. "
        "trend is one of up/down/stable. recommendation is one actionable sentence."
    )
    raw = _call(prompt, "You are a business analyst. Respond with valid JSON only.")
    if not raw:
        return None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"busiest_day": "", "top_student": "", "trend": "stable", "recommendation": raw}


def draft_cancellation_message(lesson: dict, reason: str = "weather"):
    """Return a short SMS-friendly cancellation message, or None."""
    if not ai_available():
        return None
    prompt = (
        f"Draft a polite SMS to cancel a tennis lesson.\n"
        f"Student: {lesson.get('student_name', 'there')}\n"
        f"Lesson time: {lesson.get('date', '')} {lesson.get('start_time', '')}\n"
        f"Reason: {reason}\n"
        "Include an offer to reschedule. Keep under 160 characters. Return only the message."
    )
    return _call(prompt, "You write friendly, professional, concise SMS messages.")


def parse_natural_language_booking(text: str, students: list):
    """Parse a free-text booking request into {student_id, date, start_time, blocks}, or None."""
    if not ai_available():
        return None
    roster = [f"{s.get('id')}: {s.get('name')}" for s in students]
    prompt = (
        f"Students (id: name):\n" + "\n".join(roster) + "\n\n"
        f"Request: \"{text}\"\n\n"
        "Return ONLY a JSON object with keys student_id, date (YYYY-MM-DD), "
        "start_time (HH:MM 24hr), blocks (integer, 1 block = 30 min). "
        "Pick the best matching student_id from the list."
    )
    raw = _call(prompt, "You parse booking requests. Respond with valid JSON only.")
    if not raw:
        return None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None


def suggest_reschedule(lesson: dict, weather_data: dict):
    """Suggest a better time this week given weather, or None."""
    if not ai_available():
        return None
    prompt = (
        f"A tennis lesson on {lesson.get('date', '')} at {lesson.get('start_time', '')} "
        f"may be affected by weather: {json.dumps(weather_data or {}, default=str)[:600]}.\n"
        "Suggest the best alternative day/time this week in one short sentence."
    )
    return _call(prompt, "You are a helpful tennis coaching assistant.")
