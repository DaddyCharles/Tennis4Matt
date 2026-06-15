"""Scan loop and lead extraction for Ivan.

The scan loop runs in a daemon thread (started from main.py) and must never
crash the process — every level catches and logs its own errors. Browser work
is async (Playwright); each group is scanned inside its own asyncio.run call so
the synchronous loop stays simple.
"""

import asyncio
import os
import re
import uuid
from datetime import datetime, time as dt_time

from bot import browser, notifier
from bot.logger import (
    CONFIG_DIR,
    DATA_DIR,
    human_delay,
    load_json,
    load_settings,
    log_error,
    log_info,
    log_success,
    log_warning,
    save_json,
    update_bot_status,
)

KEYWORDS_PATH = os.path.join(CONFIG_DIR, 'keywords.json')
GROUPS_PATH = os.path.join(CONFIG_DIR, 'groups.json')
REPLIED_POSTS_PATH = os.path.join(CONFIG_DIR, 'replied_posts.json')
LEADS_PATH = os.path.join(DATA_DIR, 'leads.json')

_MAX_REPLIED_IDS = 2000
_SCROLL_COUNT = 3


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_scan_loop() -> None:
    """Background loop: scan active groups on an interval while the bot is running.

    Never raises — all errors are caught and logged so the thread stays alive.
    """
    log_info("Scan loop started.")
    while True:
        try:
            settings = load_settings()
            running = bool(settings.get('bot_running', False))
            update_bot_status(running=running)

            if not running:
                _sleep(30)
                continue

            if not is_within_active_hours():
                log_info("Outside active hours — skipping scan.")
                _sleep(60)
                continue

            today_count = get_today_lead_count()
            update_bot_status(today_count=today_count)
            daily_limit = int(settings.get('daily_limit', 20))
            if today_count >= daily_limit:
                log_warning(
                    f"Daily limit reached ({today_count}/{daily_limit}) — pausing scans."
                )
                _sleep(60)
                continue

            log_info("Starting scan cycle.")
            found = scan_all_groups()
            update_bot_status(
                last_scan=datetime.now().isoformat(),
                today_count=get_today_lead_count(),
            )
            log_success(f"Scan cycle complete — {found} new lead(s) found.")

            interval_minutes = float(settings.get('scan_interval_minutes', 15))
            _sleep(max(1, int(interval_minutes * 60)))
        except Exception as e:
            log_error(f"Scan loop error (continuing): {e}")
            _sleep(30)


def _sleep(total_seconds: int) -> None:
    """Sleep in 1s steps so a running-flag change is noticed reasonably fast."""
    for _ in range(int(total_seconds)):
        import time
        time.sleep(1)


def scan_all_groups() -> int:
    """Scan every active group, returning the total number of new leads found."""
    total = 0
    try:
        data = load_json(GROUPS_PATH, {"groups": []})
        groups = [g for g in data.get('groups', []) if g.get('active')]
        if not groups:
            log_info("No active groups configured.")
            return 0
        for index, group in enumerate(groups):
            leads = scan_group(group)
            total += len(leads)
            if index < len(groups) - 1:
                human_delay()
    except Exception as e:
        log_error(f"Error in scan_all_groups: {e}")
    return total


def scan_group(group: dict) -> list[dict]:
    """Scan a single group and return the list of new leads found.

    Opens a headless browser with the saved session, navigates to the group,
    scrolls to load posts, extracts and processes each, then closes the browser.
    """
    try:
        return asyncio.run(_scan_group_async(group))
    except Exception as e:
        log_error(f"Error scanning group {group.get('name', '?')}: {e}")
        return []


async def _scan_group_async(group: dict) -> list[dict]:
    """Async worker for scan_group()."""
    leads: list[dict] = []
    playwright, browser_obj, page = await browser.load_session()
    if page is None:
        log_warning(
            f"Skipping group '{group.get('name', '?')}' — no valid session. "
            "Log in to Facebook from the Settings page."
        )
        return leads
    try:
        log_info(f"Scanning group: {group.get('name', '?')}")
        await page.goto(group['url'], wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(4)

        for _ in range(_SCROLL_COUNT):
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(3)

        posts = await extract_posts(page)
        log_info(f"Extracted {len(posts)} post(s) from {group.get('name', '?')}.")

        for post in posts:
            # Inject group context so process_post can build a full lead.
            post['group_id'] = group.get('id', '')
            post['group_name'] = group.get('name', '')
            post['group_location'] = group.get('location', '')
            lead = process_post(post)
            if lead:
                leads.append(lead)
    except Exception as e:
        log_error(f"Error scanning group {group.get('name', '?')}: {e}")
    finally:
        await browser.close_browser(playwright, browser_obj)
    return leads


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

async def extract_posts(page) -> list[dict]:
    """Extract visible posts from a Facebook group page.

    Facebook's DOM is heavily obfuscated, so this is intentionally best-effort:
    it reads role='article' containers, grabs their text, and pulls the first
    permalink-style anchor to derive a post_id and post_url. Posts without a
    usable permalink are skipped.
    Returns dicts: { post_id, poster_name, post_text, post_url, extracted_at }.
    """
    results: list[dict] = []
    try:
        articles = await page.query_selector_all("div[role='article']")
        for article in articles:
            try:
                text = (await article.inner_text()) or ""
                text = text.strip()
                if not text:
                    continue

                post_url = await _find_permalink(article, page)
                if not post_url:
                    continue
                post_id = _extract_post_id(post_url)
                if not post_id:
                    continue

                poster_name = await _find_poster_name(article)

                results.append({
                    "post_id": post_id,
                    "poster_name": poster_name,
                    "post_text": text[:500],
                    "post_url": post_url,
                    "extracted_at": datetime.now().isoformat(),
                })
            except Exception:
                # One bad post element must not abort the whole extraction.
                continue
    except Exception as e:
        log_error(f"Error extracting posts: {e}")
    # De-duplicate by post_id within this page load.
    seen = set()
    unique = []
    for post in results:
        if post['post_id'] in seen:
            continue
        seen.add(post['post_id'])
        unique.append(post)
    return unique


async def _find_permalink(article, page) -> str:
    """Find the first permalink-style URL inside a post article element."""
    selectors = [
        "a[href*='/posts/']",
        "a[href*='/permalink/']",
        "a[href*='story_fbid=']",
        "a[href*='/groups/'][href*='/posts/']",
    ]
    for selector in selectors:
        try:
            anchor = await article.query_selector(selector)
            if anchor:
                href = await anchor.get_attribute('href')
                if href:
                    return _absolute_url(href)
        except Exception:
            continue
    return ""


async def _find_poster_name(article) -> str:
    """Best-effort extraction of the poster's display name from a post element."""
    selectors = [
        "h3 a", "h4 a", "strong a", "h2 a",
        "[role='link'] strong span", "a[role='link'] strong",
    ]
    for selector in selectors:
        try:
            element = await article.query_selector(selector)
            if element:
                name = (await element.inner_text() or "").strip()
                if name and len(name) < 80:
                    return name
        except Exception:
            continue
    return "Unknown"


def _absolute_url(href: str) -> str:
    """Turn a relative Facebook href into an absolute URL."""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.facebook.com" + href
    return "https://www.facebook.com/" + href


def _extract_post_id(url: str) -> str:
    """Derive a stable post id from a Facebook permalink URL."""
    patterns = [
        r"/posts/([A-Za-z0-9]+)",
        r"/permalink/(\d+)",
        r"story_fbid=([A-Za-z0-9]+)",
        r"/videos/(\d+)",
        r"/photos/[^/]+/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_post(post: dict) -> dict | None:
    """Turn an extracted post into a saved lead, or return None if it should be skipped.

    Skips posts already actioned or that do not match any keyword category.
    On a match: saves the lead, marks the post actioned, and notifies the user.
    """
    try:
        post_id = post.get('post_id', '')
        if not post_id or is_already_actioned(post_id):
            return None

        match = match_keywords(post.get('post_text', ''))
        if not match:
            return None

        lead = {
            "post_id": post_id,
            "poster_name": post.get('poster_name', 'Unknown'),
            "post_text": post.get('post_text', '')[:500],
            "post_url": post.get('post_url', ''),
            "group_id": post.get('group_id', ''),
            "group_name": post.get('group_name', ''),
            "group_location": post.get('group_location', ''),
            "category_id": match['category_id'],
            "category_label": match['category_label'],
            "matched_keyword": match['matched_keyword'],
            "status": "new",
            "reply_sent": False,
            "reply_text": "",
            "notes": "",
        }

        lead_id = save_lead(lead)
        lead['id'] = lead_id
        mark_actioned(post_id)

        log_success(
            f"New lead: {lead['poster_name']} in {lead['group_name']} "
            f"(matched '{match['matched_keyword']}')"
        )
        notifier.notify_desktop(
            "New Tennis Lead!",
            f"{lead['poster_name']} in {lead['group_name']}",
        )
        notifier.notify_email(lead)
        return lead
    except Exception as e:
        log_error(f"Error processing post: {e}")
        return None


def match_keywords(text: str) -> dict | None:
    """Match post text against keyword categories, honouring exclusions.

    Returns { category_id, category_label, matched_keyword } on the first match,
    or None if an exclusion phrase is present or nothing matches.
    """
    if not text:
        return None
    lowered = text.lower()
    data = load_json(KEYWORDS_PATH, {"categories": [], "exclusions": []})

    for exclusion in data.get('exclusions', []):
        if exclusion and exclusion.lower() in lowered:
            return None

    for category in data.get('categories', []):
        for keyword in category.get('keywords', []):
            if keyword and keyword.lower() in lowered:
                return {
                    "category_id": category.get('id', ''),
                    "category_label": category.get('label', ''),
                    "matched_keyword": keyword,
                }
    return None


def is_already_actioned(post_id: str) -> bool:
    """Return True if this post id has already been actioned."""
    data = load_json(REPLIED_POSTS_PATH, {"post_ids": []})
    return post_id in data.get('post_ids', [])


def mark_actioned(post_id: str) -> None:
    """Record a post id as actioned, trimming the list to the most recent 2000."""
    data = load_json(REPLIED_POSTS_PATH, {"post_ids": []})
    post_ids = data.get('post_ids', [])
    if post_id not in post_ids:
        post_ids.append(post_id)
    if len(post_ids) > _MAX_REPLIED_IDS:
        post_ids = post_ids[-_MAX_REPLIED_IDS:]
    data['post_ids'] = post_ids
    save_json(REPLIED_POSTS_PATH, data)


def save_lead(lead: dict) -> str:
    """Append a lead to leads.json with a generated id and created_at; return the id."""
    lead_id = str(uuid.uuid4())
    record = dict(lead)
    record['id'] = lead_id
    record['created_at'] = datetime.now().isoformat()
    record.setdefault('status', 'new')
    record.setdefault('reply_sent', False)
    record.setdefault('reply_text', '')
    record.setdefault('notes', '')

    data = load_json(LEADS_PATH, {"leads": []})
    if 'leads' not in data or not isinstance(data.get('leads'), list):
        data = {"leads": []}
    data['leads'].append(record)
    save_json(LEADS_PATH, data)
    return lead_id


def get_today_lead_count() -> int:
    """Count leads created today (by created_at date)."""
    data = load_json(LEADS_PATH, {"leads": []})
    today = datetime.now().date().isoformat()
    count = 0
    for lead in data.get('leads', []):
        created = lead.get('created_at', '')
        if created[:10] == today:
            count += 1
    return count


def is_within_active_hours() -> bool:
    """Return True if the current local time falls within the configured active window."""
    settings = load_settings()
    start = _parse_hhmm(settings.get('active_hours_start', '07:00'), dt_time(7, 0))
    end = _parse_hhmm(settings.get('active_hours_end', '21:00'), dt_time(21, 0))
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    # Overnight window (e.g. 21:00 -> 06:00).
    return now >= start or now <= end


def _parse_hhmm(value: str, fallback: dt_time) -> dt_time:
    """Parse an 'HH:MM' string into a time, returning fallback on bad input."""
    try:
        hour, minute = value.split(':')
        return dt_time(int(hour), int(minute))
    except Exception:
        return fallback
