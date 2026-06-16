"""Lead intelligence: quality scoring, suggested replies, and pipeline stats.

Human-in-the-loop only — suggested replies are display-only and never auto-posted,
keeping the workflow within Facebook's terms of service.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

try:
    from app import ai_helper
except Exception:  # pragma: no cover - ai is optional
    ai_helper = None

try:
    from bot.logger import load_settings
except Exception:  # pragma: no cover
    def load_settings() -> dict:
        return {}


# High-intent phrases — strong buying signals (5★ territory)
_STRONG_PHRASES = [
    "looking for a coach", "looking for a tennis coach", "need a coach",
    "private lesson", "private lessons", "private coaching",
    "kids coaching", "kids lessons", "children's coaching", "junior coaching",
    "want to learn", "after a coach", "recommend a coach", "any coaches",
    "dm me", "pm me", "message me", "please dm", "get in touch",
]

# Medium-intent phrases (3★ territory)
_MEDIUM_PHRASES = [
    "tennis lessons", "tennis coaching", "learn tennis", "tennis coach",
    "lessons", "coaching", "enquiry", "enquiring", "interested in",
]

_PHONE_RE = re.compile(r"(?:\+?61|0)[\s-]?\d(?:[\s-]?\d){7,9}")


def _location_terms(settings: dict) -> list[str]:
    loc = (settings or {}).get("location", "") or ""
    terms = []
    for part in re.split(r"[,\n]", loc):
        part = part.strip()
        if len(part) >= 3 and part.lower() not in ("australia", "nsw", "new south wales"):
            terms.append(part.lower())
    return terms


def score_lead(post_text: str, post_location: str | None = None, settings: dict | None = None) -> dict:
    """Score a lead 1-5 based on intent strength, location match, and contact details.

    Returns {score: 1-5, reasons: [...], intent: 'high'|'medium'|'low'}.
    """
    settings = settings if settings is not None else load_settings()
    text = (post_text or "").lower()
    reasons: list[str] = []
    points = 0

    strong_hits = [p for p in _STRONG_PHRASES if p in text]
    if strong_hits:
        points += 3
        reasons.append(f"High-intent phrase: “{strong_hits[0]}”")

    medium_hits = [p for p in _MEDIUM_PHRASES if p in text]
    if medium_hits and not strong_hits:
        points += 2
        reasons.append("Mentions tennis lessons/coaching")
    elif medium_hits:
        points += 1

    # Location match (post location field or text mentions a local term)
    loc_terms = _location_terms(settings)
    loc_blob = f"{text} {(post_location or '').lower()}"
    matched_loc = next((t for t in loc_terms if t in loc_blob), None)
    if matched_loc:
        points += 1
        reasons.append(f"Near you ({matched_loc.title()})")

    # Contact details present
    if _PHONE_RE.search(post_text or ""):
        points += 1
        reasons.append("Phone number included")
    if any(k in text for k in ("dm me", "pm me", "message me", "please dm")):
        if "Phone number included" not in reasons:
            reasons.append("Asked to be contacted")

    if not reasons:
        reasons.append("Mentions tennis, intent unclear")

    score = max(1, min(5, points + 1))  # baseline 1★, capped at 5★

    if score >= 4:
        intent = "high"
    elif score >= 3:
        intent = "medium"
    else:
        intent = "low"

    return {"score": score, "reasons": reasons, "intent": intent}


def _template_reply(lead: dict, settings: dict) -> str:
    name = (lead.get("poster_name") or "there").split(" ")[0]
    coach = settings.get("coach_name", "Matt") or "Matt"
    court = settings.get("court_name", "") or "my local courts"
    return (
        f"Hi {name}! I'm {coach}, a local tennis coach. I'd love to help you out. "
        f"I run lessons at {court} and offer a free first assessment so we can find "
        f"the right plan for you. Feel free to send me a message and we can sort out "
        f"a time that suits. Looking forward to hearing from you!"
    )


def suggest_lead_reply(lead: dict, settings: dict | None = None) -> dict:
    """Draft a friendly reply for a lead. Display-only — never auto-posted.

    Uses GroqCloud when the AI module is on; otherwise returns a template with
    the lead's name slotted in. Returns {text, source: 'ai'|'template'}.
    """
    settings = settings if settings is not None else load_settings()

    if ai_helper is not None and ai_helper.ai_available():
        coach = settings.get("coach_name", "Matt") or "Matt"
        court = settings.get("court_name", "") or "local courts"
        prompt = (
            "Write a warm, brief, friendly reply (2-3 sentences) to this Facebook "
            "post from someone interested in tennis coaching. Offer a free first "
            "assessment and invite them to message back. Do not use hashtags or "
            "emoji. Sound like a real local coach, not a sales pitch.\n\n"
            f"Coach name: {coach}\n"
            f"Court: {court}\n"
            f"Poster name: {lead.get('poster_name', 'there')}\n"
            f"Their post: \"{lead.get('post_text', '')}\""
        )
        result = ai_helper.ai_generate(
            prompt,
            system="You are a friendly local tennis coach replying to leads on Facebook.",
        )
        if result.get("success") and result.get("text"):
            return {"text": result["text"].strip(), "source": "ai"}

    return {"text": _template_reply(lead, settings), "source": "template"}


def _parse_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None


# Statuses that count as "actively pursued / converted"
_BOOKED = {"booked", "won"}
_CONTACTED = {"contacted", "replied"}
_DISMISSED = {"dismissed", "ignored", "lost"}


def lead_stats(leads: list[dict]) -> dict:
    """Compute pipeline + performance stats for the Lead Monitor header."""
    now = datetime.now()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    new_today = 0
    new_week = 0
    booked_month = 0
    counts = {"new": 0, "contacted": 0, "booked": 0, "dismissed": 0}
    group_tally: dict[str, int] = {}
    keyword_tally: dict[str, int] = {}

    for l in leads:
        created = _parse_dt(l.get("created_at", ""))
        status = (l.get("status") or "new").lower()

        # Normalise legacy statuses into the four-stage pipeline for counting
        if status in _BOOKED:
            counts["booked"] += 1
        elif status in _CONTACTED:
            counts["contacted"] += 1
        elif status in _DISMISSED:
            counts["dismissed"] += 1
        else:
            counts["new"] += 1

        if created:
            if created.date() == today:
                new_today += 1
            if created >= week_ago:
                new_week += 1
            if status in _BOOKED and created >= month_start:
                booked_month += 1

        g = l.get("group_name") or ""
        if g:
            group_tally[g] = group_tally.get(g, 0) + 1
        k = l.get("matched_keyword") or ""
        if k:
            keyword_tally[k] = keyword_tally.get(k, 0) + 1

    total = len(leads)
    conversion_pct = round((counts["booked"] / total) * 100) if total else 0
    best_group = max(group_tally.items(), key=lambda x: x[1])[0] if group_tally else None
    best_keyword = max(keyword_tally.items(), key=lambda x: x[1])[0] if keyword_tally else None

    return {
        "new_today": new_today,
        "new_week": new_week,
        "booked_month": booked_month,
        "conversion_pct": conversion_pct,
        "counts": counts,
        "best_group": best_group,
        "best_keyword": best_keyword,
        "total": total,
    }


def ensure_score(lead: dict, settings: dict | None = None) -> dict:
    """Backfill score fields on a lead in place if missing; return the lead."""
    if not lead.get("score"):
        result = score_lead(lead.get("post_text", ""), lead.get("group_location"), settings)
        lead["score"] = result["score"]
        lead["score_reasons"] = result["reasons"]
        lead["intent"] = result["intent"]
    return lead
