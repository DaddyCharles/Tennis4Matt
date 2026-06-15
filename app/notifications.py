"""Web Push notifications (lesson reminders, lights + weather alerts).

VAPID keys are generated once and stored in settings. Subscriptions from the
browser are saved to data/notifications.json. A background loop checks every
5 minutes for things worth notifying about and de-duplicates via a 'sent' log.
"""

import base64
import time
from datetime import datetime

from bot.logger import (
    SETTINGS_PATH,
    load_json,
    load_settings,
    log_error,
    log_info,
    save_json,
)
from app import NOTIFICATIONS_PATH, LESSONS_PATH, now_sydney, today_str


def generate_vapid_keys() -> dict:
    """Generate a new VAPID key pair as base64url strings."""
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01

        vapid = Vapid01()
        vapid.generate_keys()
        raw_pub = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        raw_priv = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        return {
            "public_key": base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode(),
            "private_key": base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode(),
        }
    except Exception as e:
        log_error(f"VAPID key generation failed: {e}")
        return {"public_key": "", "private_key": ""}


def ensure_vapid_keys() -> dict:
    """Return existing VAPID keys, generating and persisting them on first run."""
    settings = load_settings()
    pub = settings.get("vapid_public_key", "")
    priv = settings.get("vapid_private_key", "")
    if pub and priv:
        return {"public_key": pub, "private_key": priv}
    keys = generate_vapid_keys()
    if keys["public_key"]:
        settings["vapid_public_key"] = keys["public_key"]
        settings["vapid_private_key"] = keys["private_key"]
        save_json(SETTINGS_PATH, settings)
        log_info("Generated VAPID keys for push notifications.")
    return keys


def _load() -> dict:
    data = load_json(NOTIFICATIONS_PATH, {"subscriptions": [], "sent": []})
    data.setdefault("subscriptions", [])
    data.setdefault("sent", [])
    return data


def save_subscription(subscription: dict) -> bool:
    """Persist a browser push subscription (de-duplicated by endpoint)."""
    if not subscription or not subscription.get("endpoint"):
        return False
    data = _load()
    endpoint = subscription.get("endpoint")
    data["subscriptions"] = [
        s for s in data["subscriptions"] if s.get("endpoint") != endpoint
    ]
    data["subscriptions"].append(subscription)
    return save_json(NOTIFICATIONS_PATH, data)


def _mark_sent(key: str) -> None:
    data = _load()
    today = today_str()
    data["sent"] = [s for s in data["sent"] if s.get("date") == today]
    data["sent"].append({"key": key, "date": today})
    save_json(NOTIFICATIONS_PATH, data)


def _already_sent(key: str) -> bool:
    data = _load()
    today = today_str()
    return any(s.get("key") == key and s.get("date") == today for s in data["sent"])


def send_notification(title: str, body: str, url: str = "/") -> bool:
    """Send a push notification to every subscribed device. Returns True if any sent."""
    settings = load_settings()
    priv = settings.get("vapid_private_key", "")
    if not priv:
        log_error("No VAPID private key — cannot send push notification.")
        return False
    data = _load()
    subs = data["subscriptions"]
    if not subs:
        return False
    try:
        from pywebpush import webpush, WebPushException
    except Exception as e:
        log_error(f"pywebpush unavailable: {e}")
        return False

    import json as _json
    payload = _json.dumps({"title": title, "body": body, "url": url})
    claims = {"sub": settings.get("vapid_claim_email", "mailto:coach@example.com")}
    sent_any = False
    survivors = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=priv,
                vapid_claims=dict(claims),
            )
            sent_any = True
            survivors.append(sub)
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                log_info("Dropping expired push subscription.")
            else:
                log_error(f"Push send failed: {e}")
                survivors.append(sub)
        except Exception as e:
            log_error(f"Push send error: {e}")
            survivors.append(sub)
    if len(survivors) != len(subs):
        data["subscriptions"] = survivors
        save_json(NOTIFICATIONS_PATH, data)
    return sent_any


def _todays_lessons() -> list:
    lessons = load_json(LESSONS_PATH, {"lessons": []}).get("lessons", [])
    today = today_str()
    return [
        l for l in lessons
        if l.get("date") == today and l.get("status") == "scheduled"
    ]


def _minutes_until(start_time: str):
    try:
        hh, mm = start_time.split(":")[:2]
        now = now_sydney()
        when = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        return int((when - now).total_seconds() // 60)
    except (ValueError, TypeError):
        return None


def check_lesson_reminders() -> None:
    """Send 60-minute and 15-minute reminders for today's scheduled lessons."""
    for lesson in _todays_lessons():
        mins = _minutes_until(lesson.get("start_time", ""))
        if mins is None:
            continue
        lid = lesson.get("id", "")
        name = lesson.get("student_name", "Student")
        duration = lesson.get("duration_minutes", 0)
        if 55 <= mins <= 65 and not _already_sent(f"60-{lid}"):
            send_notification(
                "Lesson in 1 hour",
                f"{name} - {duration} min",
                "/coach",
            )
            _mark_sent(f"60-{lid}")
        elif 10 <= mins <= 20 and not _already_sent(f"15-{lid}"):
            send_notification(
                "Starting soon",
                f"{name} - court ready?",
                "/coach",
            )
            _mark_sent(f"15-{lid}")


def check_lights_warning(weather: dict) -> None:
    """Notify when sunset is within the warning window and lessons are running."""
    if not weather:
        return
    warning = weather.get("lights_warning")
    if not warning:
        return
    if not _todays_lessons():
        return
    key = f"lights-{warning.get('sunset_time', '')}"
    if _already_sent(key):
        return
    send_notification(
        f"Lights on in {warning.get('minutes_until', 0)} minutes",
        f"Sunset at {warning.get('sunset_time', '')}",
        "/coach",
    )
    _mark_sent(key)


def check_weather_alerts(weather: dict, lessons_today: list) -> None:
    """Notify if rain probability is high during lesson hours."""
    if not weather or not lessons_today:
        return
    if int(weather.get("rain_prob", 0)) < 60:
        return
    key = f"rain-{today_str()}"
    if _already_sent(key):
        return
    send_notification(
        "Rain likely today",
        f"{len(lessons_today)} lesson(s) may be affected",
        "/coach",
    )
    _mark_sent(key)


def run_notification_loop() -> None:
    """Background thread: run all checks every 5 minutes, never crashing."""
    from app.weather import get_cached_weather

    log_info("Notification scheduler started.")
    while True:
        try:
            settings = load_settings()
            if settings.get("push_notifications_enabled"):
                weather = get_cached_weather()
                check_lesson_reminders()
                check_lights_warning(weather)
                check_weather_alerts(weather, _todays_lessons())
        except Exception as e:
            log_error(f"Notification loop error: {e}")
        time.sleep(300)
