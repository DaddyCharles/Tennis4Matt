"""Flask dashboard for Ivan.

Serves the web UI at http://localhost:9999 and exposes JSON APIs for the live
log/status feeds, bot control, lead management, and config editing. Config and
data are read/written through the JSON helpers in bot.logger so the scan loop
and the dashboard always agree on state.
"""

import csv
import io
import os
import random
import re
import socket
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timedelta

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from bot import browser
from bot.logger import (
    BASE_DIR,
    CONFIG_DIR,
    DATA_DIR,
    clear_log_buffer,
    get_bot_status,
    get_log_buffer,
    load_json,
    load_settings,
    log_error,
    log_info,
    save_json,
    update_bot_status,
)
from app import (
    LESSONS_PATH,
    STUDENTS_PATH,
    DAY_NAMES,
    blocks_to_minutes,
    format_currency,
    now_sydney,
    today_str,
)
from app import ai_helper
from app import earnings as coach_earnings
from app import notifications as coach_notifications
from app import weather as coach_weather
from app import tax_calculator as coach_tax
from app import invoice_generator as coach_invoices
from app import sms_manager as coach_sms

ONBOARDING_PATH = os.path.join(CONFIG_DIR, 'onboarding.json')
KEYWORDS_PATH = os.path.join(CONFIG_DIR, 'keywords.json')
GROUPS_PATH = os.path.join(CONFIG_DIR, 'groups.json')
REPLIES_PATH = os.path.join(CONFIG_DIR, 'replies.json')
SETTINGS_PATH = os.path.join(CONFIG_DIR, 'settings.json')
LEADS_PATH = os.path.join(DATA_DIR, 'leads.json')
SESSION_PATH = os.path.join(BASE_DIR, 'session', 'session.json')
INVOICES_PATH = os.path.join(DATA_DIR, 'invoices.json')
EXPENSES_PATH = os.path.join(DATA_DIR, 'expenses.json')
PACKAGES_PATH = os.path.join(DATA_DIR, 'packages.json')

MAX_LEADS_RETURNED = 500

app = Flask(__name__)
app.secret_key = 'fb-lead-monitor-local-secret'


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

@app.template_filter('timeago')
def timeago_filter(iso_string: str) -> str:
    """Render an ISO timestamp as a human 'x ago' string."""
    if not iso_string:
        return "never"
    try:
        then = datetime.fromisoformat(iso_string)
    except (ValueError, TypeError):
        return iso_string
    seconds = (datetime.now() - then).total_seconds()
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(hours // 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


@app.template_filter('datetimefmt')
def datetimefmt_filter(iso_string: str) -> str:
    """Render an ISO timestamp as 'YYYY-MM-DD HH:MM'."""
    if not iso_string:
        return ""
    try:
        return datetime.fromisoformat(iso_string).strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return iso_string


@app.template_filter('dateshort')
def dateshort_filter(date_string: str) -> str:
    """Render a 'YYYY-MM-DD' date as '12 Jan'."""
    if not date_string:
        return ""
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').strftime('%-d %b')
    except (ValueError, TypeError):
        return date_string


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------

def _load_leads() -> list[dict]:
    """Return the leads list from data/leads.json."""
    data = load_json(LEADS_PATH, {"leads": []})
    leads = data.get('leads', [])
    return leads if isinstance(leads, list) else []


def _save_leads(leads: list[dict]) -> bool:
    """Persist the leads list to data/leads.json."""
    return save_json(LEADS_PATH, {"leads": leads})


def _recent_leads(leads: list[dict], n: int) -> list[dict]:
    """Return the n most recent leads (newest first)."""
    ordered = sorted(
        leads, key=lambda l: l.get('created_at', ''), reverse=True
    )
    return ordered[:n]


def _session_status() -> str:
    """Return 'not_set' or 'set' depending on whether a session file exists."""
    return 'set' if os.path.exists(SESSION_PATH) else 'not_set'


def _startup_shortcut_path() -> str:
    """Path of the Windows Startup-folder launcher (.bat) for this app."""
    appdata = os.environ.get('APPDATA', '')
    return os.path.join(
        appdata, 'Microsoft', 'Windows', 'Start Menu', 'Programs',
        'Startup', 'Ivan.bat',
    )


def _startup_enabled() -> bool:
    """Return True if the app is set to launch on Windows startup."""
    if os.name != 'nt':
        return False
    return os.path.exists(_startup_shortcut_path())


def _next_scan_minutes(last_scan: str, interval_minutes: int, running: bool):
    """Minutes until the next scan, or None if the bot is paused/unknown.

    Returns 0 if a scan is already due. None means 'Paused' (bot stopped) or
    there is no prior scan to extrapolate from.
    """
    if not running:
        return None
    if not last_scan:
        return 0
    try:
        last = datetime.fromisoformat(last_scan)
    except (ValueError, TypeError):
        return 0
    due = last + timedelta(minutes=max(1, int(interval_minutes or 1)))
    remaining = (due - datetime.now()).total_seconds()
    if remaining <= 0:
        return 0
    return int(remaining // 60) + (1 if remaining % 60 else 0)


def _time_greeting() -> dict:
    """Return a time-of-day greeting label for the dashboard welcome."""
    hour = datetime.now().hour
    if hour < 12:
        return {'text': 'Good morning'}
    if hour < 18:
        return {'text': 'Good afternoon'}
    return {'text': 'Good evening'}


def _nav_status() -> dict:
    """Sidebar nav badge state: new-lead count and whether groups/keywords exist."""
    leads = _load_leads()
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    keywords = load_json(KEYWORDS_PATH, {"categories": []}).get('categories', [])
    return {
        'nav_new_count': sum(1 for l in leads if l.get('status') == 'new'),
        'nav_groups_count': len(groups),
        'nav_keywords_count': len(keywords),
    }


@app.context_processor
def inject_nav_status():
    """Make sidebar badge data available to every template (incl. base.html)."""
    try:
        return _nav_status()
    except Exception:
        return {'nav_new_count': 0, 'nav_groups_count': 0, 'nav_keywords_count': 0}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    """Root: send users to the Today (home) page once onboarding is done."""
    onboarding = load_json(ONBOARDING_PATH, {"completed": False})
    if not onboarding.get("completed"):
        return redirect(url_for('onboarding'))
    return redirect(url_for('coach_index'))


@app.route('/dashboard')
def dashboard_page():
    """Lead Monitor: status, stats, recent logs, recent leads."""
    onboarding = load_json(ONBOARDING_PATH, {"completed": False})
    if not onboarding.get("completed"):
        return redirect(url_for('onboarding'))
    leads = _load_leads()
    settings = load_settings()
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    keywords = load_json(KEYWORDS_PATH, {"categories": []}).get('categories', [])
    status = get_bot_status()
    today = datetime.now().date().isoformat()
    today_count = sum(1 for l in leads if l.get('created_at', '')[:10] == today)
    new_count = sum(1 for l in leads if l.get('status') == 'new')
    running = bool(settings.get('bot_running', False))
    interval = settings.get('scan_interval_minutes', 15)
    return render_template(
        'index.html',
        status=status,
        bot_running=running,
        greeting=_time_greeting(),
        facebook_connected=(_session_status() == 'set'),
        today_count=today_count,
        total_leads=len(leads),
        new_count=new_count,
        last_scan=status.get('last_scan'),
        groups_active=sum(1 for g in groups if g.get('active')),
        groups_count=len(groups),
        keywords_count=len(keywords),
        scan_interval=interval,
        next_scan_minutes=_next_scan_minutes(status.get('last_scan'), interval, running),
        recent_leads=_recent_leads(leads, 5),
        logs=get_log_buffer(50),
    )


@app.route('/leads')
def leads_page():
    """Lead Monitor page: stats header, scored cards, and pipeline."""
    from app.leads import ensure_score, lead_stats
    settings = load_settings()
    all_leads = _load_leads()
    stats = lead_stats(all_leads)
    leads = _recent_leads(all_leads, MAX_LEADS_RETURNED)
    for lead in leads:
        ensure_score(lead, settings)
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    return render_template('leads.html', leads=leads, groups=groups, stats=stats, settings=settings)


@app.route('/keywords')
def keywords_page():
    """Keyword category editor page (Presets + Custom tabs)."""
    keywords = load_json(KEYWORDS_PATH, {"categories": [], "exclusions": []})
    replies = load_json(REPLIES_PATH, {"templates": []})
    presets = keywords.get('presets', [])
    active_preset = keywords.get('active_preset')
    return render_template(
        'keywords.html',
        keywords=keywords,
        replies=replies,
        presets=presets,
        active_preset=active_preset,
    )


@app.route('/groups')
def groups_page():
    """Group list + add form page."""
    groups = load_json(GROUPS_PATH, {"groups": []})
    return render_template('groups.html', groups=groups.get('groups', []))


@app.route('/replies')
def replies_page():
    """Reply template editor page."""
    replies = load_json(REPLIES_PATH, {"templates": []})
    return render_template('replies.html', replies=replies)


@app.route('/settings')
def settings_page():
    """Settings page including Facebook session controls."""
    settings = load_settings()
    created = settings.get('session_created_at', '')
    expiry = ''
    if created:
        try:
            expiry = (datetime.fromisoformat(created) + timedelta(days=30)).isoformat()
        except (ValueError, TypeError):
            expiry = ''
    avail_ranges, custom_presets = _get_availability(settings)
    return render_template(
        'settings.html',
        settings=settings,
        session_status=_session_status(),
        session_created_at=created,
        session_expiry=expiry,
        startup_enabled=_startup_enabled(),
        avail_ranges=avail_ranges,
        custom_presets=custom_presets,
    )


# ---------------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------------

_ONBOARDING_DEFAULT = {
    "completed": False,
    "current_step": 1,
    "completed_steps": [],
    "facebook_connected": False,
    "groups_added": False,
    "keywords_configured": False,
    "replies_configured": False,
}

# Which onboarding flag each step sets when completed.
_STEP_FLAG = {
    2: "facebook_connected",
    3: "groups_added",
    4: "keywords_configured",
    5: "replies_configured",
}


@app.route('/onboarding')
def onboarding():
    """First-run setup wizard (full-screen, no sidebar)."""
    state = load_json(ONBOARDING_PATH, dict(_ONBOARDING_DEFAULT))
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    replies = load_json(REPLIES_PATH, {"templates": []})
    return render_template(
        'onboarding.html',
        state=state,
        groups=groups,
        replies=replies,
        session_status=_session_status(),
    )


@app.route('/onboarding/status')
def onboarding_status():
    """Return current onboarding state as JSON."""
    return jsonify(load_json(ONBOARDING_PATH, dict(_ONBOARDING_DEFAULT)))


@app.route('/onboarding/step/<int:n>', methods=['POST'])
def onboarding_step(n):
    """Record completion of step n. Returns next_step."""
    try:
        state = load_json(ONBOARDING_PATH, dict(_ONBOARDING_DEFAULT))
        # Step 3 requires at least one group before advancing.
        if n == 3:
            groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
            if not groups:
                return jsonify({
                    'status': 'error',
                    'message': 'Add at least one group to continue.',
                }), 400
        completed = state.get('completed_steps', [])
        if n not in completed:
            completed.append(n)
        state['completed_steps'] = sorted(completed)
        state['current_step'] = n + 1
        if n in _STEP_FLAG:
            state[_STEP_FLAG[n]] = True
        save_json(ONBOARDING_PATH, state)
        return jsonify({'status': 'ok', 'next_step': n + 1})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/onboarding/complete', methods=['POST'])
def onboarding_complete():
    """Mark onboarding complete; client then navigates to the dashboard."""
    try:
        state = load_json(ONBOARDING_PATH, dict(_ONBOARDING_DEFAULT))
        state['completed'] = True
        state['replies_configured'] = True
        completed = state.get('completed_steps', [])
        if 5 not in completed:
            completed.append(5)
        state['completed_steps'] = sorted(completed)
        state['current_step'] = 5
        save_json(ONBOARDING_PATH, state)
        log_info("Onboarding completed.")
        return jsonify({'status': 'ok', 'redirect': url_for('index')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/onboarding/reset', methods=['POST'])
def onboarding_reset():
    """Reset onboarding state so the wizard runs again."""
    try:
        save_json(ONBOARDING_PATH, dict(_ONBOARDING_DEFAULT))
        log_info("Onboarding reset.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# API: status + logs
# ---------------------------------------------------------------------------

@app.route('/api/status')
def api_status():
    """Return current bot status as JSON."""
    status = get_bot_status()
    settings = load_settings()
    leads = _load_leads()
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    keywords = load_json(KEYWORDS_PATH, {"categories": []}).get('categories', [])
    today = datetime.now().date().isoformat()
    today_count = sum(1 for l in leads if l.get('created_at', '')[:10] == today)
    new_count = sum(1 for l in leads if l.get('status') == 'new')
    running = bool(settings.get('bot_running', False))
    interval = settings.get('scan_interval_minutes', 15)
    return jsonify({
        'running': running,
        'facebook_connected': _session_status() == 'set',
        'last_scan': status.get('last_scan'),
        'next_scan_minutes': _next_scan_minutes(status.get('last_scan'), interval, running),
        'today_count': today_count,
        'new_count': new_count,
        'last_error': status.get('last_error'),
        'total_leads': len(leads),
        'groups_count': len(groups),
        'keywords_count': len(keywords),
    })


@app.route('/api/logs')
def api_logs():
    """Return the last 50 log lines as JSON."""
    return jsonify({'logs': get_log_buffer(50)})


@app.route('/api/logs/clear', methods=['POST'])
def api_logs_clear():
    """Clear the in-memory live activity log buffer."""
    try:
        clear_log_buffer()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Bot control
# ---------------------------------------------------------------------------

@app.route('/bot/start', methods=['POST'])
def bot_start():
    """Set bot_running=true in settings.json."""
    try:
        settings = load_settings()
        settings['bot_running'] = True
        save_json(SETTINGS_PATH, settings)
        update_bot_status(running=True)
        log_info("Bot started by user.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/bot/stop', methods=['POST'])
def bot_stop():
    """Set bot_running=false in settings.json."""
    try:
        settings = load_settings()
        settings['bot_running'] = False
        save_json(SETTINGS_PATH, settings)
        update_bot_status(running=False)
        log_info("Bot stopped by user.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Lead management
# ---------------------------------------------------------------------------

@app.route('/leads/<lead_id>/status', methods=['POST'])
def lead_status(lead_id):
    """Update a lead's status. Body: { status: 'won'|'lost'|'ignored'|'replied' }."""
    try:
        body = request.get_json(silent=True) or {}
        new_status = body.get('status', '')
        allowed = {'new', 'replied', 'ignored', 'won', 'lost'}
        if new_status not in allowed:
            return jsonify({'status': 'error', 'message': 'Invalid status'}), 400
        leads = _load_leads()
        found = False
        for lead in leads:
            if lead.get('id') == lead_id:
                lead['status'] = new_status
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        _save_leads(leads)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/leads/<lead_id>/reply', methods=['POST'])
def lead_reply(lead_id):
    """Mark a lead replied and store the reply text used. Body: { reply_text }."""
    try:
        body = request.get_json(silent=True) or {}
        reply_text = body.get('reply_text', '')
        leads = _load_leads()
        found = False
        for lead in leads:
            if lead.get('id') == lead_id:
                lead['status'] = 'replied'
                lead['reply_sent'] = True
                lead['reply_text'] = reply_text
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        _save_leads(leads)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/leads/<lead_id>/delete', methods=['POST'])
def lead_delete(lead_id):
    """Delete a lead from leads.json."""
    try:
        leads = _load_leads()
        new_leads = [l for l in leads if l.get('id') != lead_id]
        if len(new_leads) == len(leads):
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        _save_leads(new_leads)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


_LEAD_PIPELINE = {'new', 'contacted', 'booked', 'dismissed'}


@app.route('/api/leads/<lead_id>/status', methods=['POST'])
def api_lead_status(lead_id):
    """Set a lead's pipeline status: new | contacted | booked | dismissed."""
    try:
        body = request.get_json(silent=True) or {}
        new_status = (body.get('status', '') or '').lower()
        if new_status not in _LEAD_PIPELINE:
            return jsonify({'status': 'error', 'message': 'Invalid status'}), 400
        leads = _load_leads()
        found = None
        for lead in leads:
            if lead.get('id') == lead_id:
                lead['status'] = new_status
                if new_status == 'contacted' and not lead.get('contacted_at'):
                    lead['contacted_at'] = now_sydney().isoformat()
                found = lead
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        _save_leads(leads)
        return jsonify({'status': 'ok', 'lead_status': new_status})
    except Exception as e:
        log_error(f"Lead status error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/leads/<lead_id>/notes', methods=['POST'])
def api_lead_notes(lead_id):
    """Save a free-text note on a lead."""
    try:
        body = request.get_json(silent=True) or {}
        note = body.get('notes', '')
        leads = _load_leads()
        found = False
        for lead in leads:
            if lead.get('id') == lead_id:
                lead['notes'] = note
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        _save_leads(leads)
        return jsonify({'status': 'ok'})
    except Exception as e:
        log_error(f"Lead notes error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/leads/<lead_id>/suggest-reply', methods=['POST'])
def api_lead_suggest_reply(lead_id):
    """Return a suggested reply (Groq if on, else template). Display-only, never auto-posts."""
    try:
        from app.leads import suggest_lead_reply
        leads = _load_leads()
        lead = next((l for l in leads if l.get('id') == lead_id), None)
        if not lead:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404
        result = suggest_lead_reply(lead, load_settings())
        return jsonify({'status': 'ok', 'text': result['text'], 'source': result['source']})
    except Exception as e:
        log_error(f"Lead suggest-reply error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/leads/<lead_id>/convert-to-student', methods=['POST'])
def api_lead_convert_to_student(lead_id):
    """Create a student from a lead, mark the lead booked, and link them."""
    try:
        leads = _load_leads()
        lead = next((l for l in leads if l.get('id') == lead_id), None)
        if not lead:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404

        name = (lead.get('poster_name') or '').strip() or 'New Student'
        settings = load_settings()
        student = {
            'id': uuid.uuid4().hex,
            'name': name,
            'phone': lead.get('poster_phone', '') or '',
            'email': '',
            'level': 'Beginner',
            'age_group': 'Adult',
            'default_duration': 60,
            'default_price': float(settings.get('default_lesson_price', 80) or 80),
            'notes': f"Converted from Facebook lead ({lead.get('group_name', 'unknown group')}).",
            'created_at': now_sydney().isoformat(),
            'active': True,
            'from_lead_id': lead_id,
        }
        students = _load_students()
        students.append(student)
        _save_students(students)

        lead['status'] = 'booked'
        lead['converted_student_id'] = student['id']
        _save_leads(leads)

        log_info(f"Lead converted to student: {name}")
        return jsonify({'status': 'ok', 'student_id': student['id']})
    except Exception as e:
        log_error(f"Lead convert error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/leads/stats')
def api_lead_stats():
    """Pipeline + performance stats for the Lead Monitor."""
    try:
        from app.leads import lead_stats
        return jsonify({'status': 'ok', **lead_stats(_load_leads())})
    except Exception as e:
        log_error(f"Lead stats error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/leads/export')
def leads_export():
    """Download all leads as a CSV file."""
    leads = _load_leads()
    columns = [
        'id', 'created_at', 'poster_name', 'post_text', 'post_url',
        'group_name', 'group_location', 'category_label', 'matched_keyword',
        'status', 'reply_sent', 'reply_text', 'notes', 'post_id',
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    for lead in _recent_leads(leads, len(leads)):
        writer.writerow({c: lead.get(c, '') for c in columns})
    output = buffer.getvalue()
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=leads.csv'},
    )


@app.route('/leads/<lead_id>/reply-text')
def lead_reply_text(lead_id):
    """Return a random reply message for this lead's category, with {name} filled in."""
    try:
        leads = _load_leads()
        lead = next((l for l in leads if l.get('id') == lead_id), None)
        if not lead:
            return jsonify({'status': 'error', 'message': 'Lead not found'}), 404

        category_id = lead.get('category_id', '')
        keywords = load_json(KEYWORDS_PATH, {"categories": []})
        template_id = category_id
        for category in keywords.get('categories', []):
            if category.get('id') == category_id:
                template_id = category.get('reply_template_id', category_id)
                break

        replies = load_json(REPLIES_PATH, {"templates": []})
        messages = []
        for template in replies.get('templates', []):
            if template.get('id') == template_id:
                messages = template.get('messages', [])
                break

        if not messages:
            return jsonify({'reply_text': ''})

        text = random.choice(messages)
        first_name = (lead.get('poster_name', '') or '').split(' ')[0]
        text = text.replace('{name}', first_name)
        return jsonify({'reply_text': text})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Config saves
# ---------------------------------------------------------------------------

@app.route('/keywords/save', methods=['POST'])
def keywords_save():
    """Save categories + exclusions, preserving the presets bundle on disk."""
    try:
        body = request.get_json(silent=True) or {}
        existing = load_json(KEYWORDS_PATH, {})
        existing['categories'] = body.get('categories', [])
        existing['exclusions'] = body.get('exclusions', [])
        existing.setdefault('presets', [])
        # A manual edit means the active config no longer matches a preset.
        existing['active_preset'] = None
        save_json(KEYWORDS_PATH, existing)
        log_info("Keywords saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/keywords/presets')
def keywords_presets():
    """Return the list of available keyword presets as JSON."""
    keywords = load_json(KEYWORDS_PATH, {})
    return jsonify({'presets': keywords.get('presets', [])})


@app.route('/keywords/load-preset/<preset_id>', methods=['POST'])
def keywords_load_preset(preset_id):
    """Load a preset's categories + exclusions into the active config."""
    try:
        keywords = load_json(KEYWORDS_PATH, {})
        presets = keywords.get('presets', [])
        preset = next((p for p in presets if p.get('id') == preset_id), None)
        if preset is None:
            return jsonify({'status': 'error', 'message': 'Preset not found'}), 404
        categories = preset.get('categories', [])
        keywords['categories'] = categories
        keywords['exclusions'] = preset.get('exclusions', [])
        keywords['active_preset'] = preset_id
        save_json(KEYWORDS_PATH, keywords)
        log_info("Loaded keyword preset: %s" % preset.get('label', preset_id))
        return jsonify({'status': 'ok', 'categories_loaded': len(categories)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/groups/save', methods=['POST'])
def groups_save():
    """Save the full groups.json from posted JSON."""
    try:
        data = request.get_json(silent=True) or {}
        data.setdefault('groups', [])
        save_json(GROUPS_PATH, data)
        log_info("Groups saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/groups/add', methods=['POST'])
def groups_add():
    """Add a single group. Body: { name, url, location }."""
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get('name', '') or '').strip()
        url = (body.get('url', '') or '').strip()
        location = (body.get('location', '') or '').strip()
        if not name or not url:
            return jsonify({'status': 'error', 'message': 'Name and URL required'}), 400
        data = load_json(GROUPS_PATH, {"groups": []})
        groups = data.get('groups', [])
        group = {
            'id': f"grp{int(datetime.now().timestamp() * 1000)}",
            'name': name,
            'url': url,
            'location': location,
            'active': True,
        }
        groups.append(group)
        data['groups'] = groups
        save_json(GROUPS_PATH, data)
        log_info(f"Group added: {name}")
        return jsonify({'status': 'ok', 'group': group})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/groups/<group_id>/delete', methods=['POST'])
def groups_delete(group_id):
    """Remove a group by id."""
    try:
        data = load_json(GROUPS_PATH, {"groups": []})
        groups = data.get('groups', [])
        new_groups = [g for g in groups if g.get('id') != group_id]
        if len(new_groups) == len(groups):
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        data['groups'] = new_groups
        save_json(GROUPS_PATH, data)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/groups/<group_id>/toggle', methods=['POST'])
def groups_toggle(group_id):
    """Toggle a group's active flag."""
    try:
        data = load_json(GROUPS_PATH, {"groups": []})
        groups = data.get('groups', [])
        new_active = None
        for group in groups:
            if group.get('id') == group_id:
                new_active = not group.get('active', False)
                group['active'] = new_active
                break
        if new_active is None:
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        save_json(GROUPS_PATH, data)
        return jsonify({'status': 'ok', 'active': new_active})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/replies/save', methods=['POST'])
def replies_save():
    """Save the full replies.json from posted JSON."""
    try:
        data = request.get_json(silent=True) or {}
        data.setdefault('templates', [])
        save_json(REPLIES_PATH, data)
        log_info("Reply templates saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/settings/save', methods=['POST'])
def settings_save():
    """Save settings.json, preserving the current bot_running flag."""
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        # bot_running is controlled via /bot/start and /bot/stop only.
        running = settings.get('bot_running', False)

        def as_int(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def as_float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        settings['scan_interval_minutes'] = as_int(
            body.get('scan_interval_minutes'), settings.get('scan_interval_minutes', 15)
        )
        settings['active_hours_start'] = body.get(
            'active_hours_start', settings.get('active_hours_start', '07:00')
        )
        settings['active_hours_end'] = body.get(
            'active_hours_end', settings.get('active_hours_end', '21:00')
        )
        settings['daily_limit'] = as_int(
            body.get('daily_limit'), settings.get('daily_limit', 20)
        )
        settings['min_delay_seconds'] = as_float(
            body.get('min_delay_seconds'), settings.get('min_delay_seconds', 8)
        )
        settings['max_delay_seconds'] = as_float(
            body.get('max_delay_seconds'), settings.get('max_delay_seconds', 25)
        )
        settings['headless_mode'] = bool(body.get('headless_mode', settings.get('headless_mode', True)))
        settings['email_notifications'] = bool(
            body.get('email_notifications', settings.get('email_notifications', False))
        )
        settings['email_address'] = body.get('email_address', settings.get('email_address', ''))
        settings['email_smtp'] = body.get('email_smtp', settings.get('email_smtp', ''))
        settings['email_password'] = body.get('email_password', settings.get('email_password', ''))
        settings['bot_running'] = running

        save_json(SETTINGS_PATH, settings)
        log_info("Settings saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/settings/login', methods=['POST'])
def settings_login():
    """Launch the manual Facebook login flow in a separate process/console."""
    try:
        cmd = [sys.executable, '-m', 'bot.browser', 'login']
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
        subprocess.Popen(cmd, cwd=BASE_DIR, creationflags=creationflags)
        log_info("Facebook login flow launched in a new window.")
        return jsonify({
            'status': 'ok',
            'message': 'A browser/console window is opening. Log in, then press ENTER in that window.',
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/settings/test-session', methods=['POST'])
def settings_test_session():
    """Check whether the saved Facebook session is still valid."""
    try:
        if not os.path.exists(SESSION_PATH):
            return jsonify({'status': 'invalid', 'message': 'No session saved yet. Click Login to Facebook first.'})
        valid = browser.is_session_valid()
        if valid:
            return jsonify({'status': 'ok', 'message': 'Session is valid and logged in.'})
        return jsonify({'status': 'invalid', 'message': 'Session is not logged in. Please log in again.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Windows auto-startup
# ---------------------------------------------------------------------------

@app.route('/settings/startup/enable', methods=['POST'])
def startup_enable():
    """Add a launcher to the Windows Startup folder so the app runs at login."""
    try:
        if os.name != 'nt':
            return jsonify({
                'status': 'error',
                'message': 'Auto-start is only available on Windows.',
            }), 400
        path = _startup_shortcut_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        run_bat = os.path.join(BASE_DIR, 'run.bat')
        contents = (
            "@echo off\r\n"
            f'cd /d "{BASE_DIR}"\r\n'
            f'start "" "{run_bat}"\r\n'
        )
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(contents)
        log_info("Auto-start on Windows enabled.")
        return jsonify({'status': 'ok', 'enabled': True})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/settings/startup/disable', methods=['POST'])
def startup_disable():
    """Remove the app's launcher from the Windows Startup folder."""
    try:
        path = _startup_shortcut_path()
        if os.path.exists(path):
            os.remove(path)
        log_info("Auto-start on Windows disabled.")
        return jsonify({'status': 'ok', 'enabled': False})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===========================================================================
# COACH PRO — tennis coaching management (lessons, students, weather, earnings)
# ===========================================================================

@app.template_filter('currency')
def currency_filter(amount) -> str:
    """Render a number as an AUD dollar string."""
    return format_currency(amount)


def get_local_ip() -> str:
    """Best-effort local network IP so the phone can reach the app."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# Lessons / students data access
# ---------------------------------------------------------------------------

def _load_lessons() -> list:
    data = load_json(LESSONS_PATH, {"lessons": []})
    items = data.get('lessons', [])
    return items if isinstance(items, list) else []


def _save_lessons(lessons: list) -> bool:
    return save_json(LESSONS_PATH, {"lessons": lessons})


def _load_students() -> list:
    data = load_json(STUDENTS_PATH, {"students": []})
    items = data.get('students', [])
    return items if isinstance(items, list) else []


def _save_students(students: list) -> bool:
    return save_json(STUDENTS_PATH, {"students": students})


def _student_by_id(student_id: str):
    return next((s for s in _load_students() if s.get('id') == student_id), None)


def _lesson_counts_by_student() -> dict:
    counts = {}
    for lesson in _load_lessons():
        sid = lesson.get('student_id')
        if sid:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Coach pages
# ---------------------------------------------------------------------------

def _weather_icon(condition) -> str:
    """Map a condition string to a Tabler icon name."""
    c = (condition or '').lower()
    if 'thunder' in c or 'storm' in c:
        return 'ti-storm'
    if 'rain' in c or 'shower' in c or 'drizzle' in c:
        return 'ti-cloud-rain'
    if 'fog' in c or 'mist' in c or 'haze' in c:
        return 'ti-mist'
    if 'snow' in c:
        return 'ti-snowflake'
    if 'partly' in c:
        return 'ti-cloud-sun'
    if 'cloud' in c or 'overcast' in c:
        return 'ti-cloud'
    if 'sun' in c or 'clear' in c:
        return 'ti-sun'
    return 'ti-cloud'


def _hhmm_to_12h(value) -> str:
    """Format an 'HH:MM' (or hour int) string as '8:00 PM'."""
    try:
        if isinstance(value, int):
            h, m = value, 0
        else:
            h, m = (int(p) for p in str(value).split(':')[:2])
    except (ValueError, TypeError):
        return str(value or '')
    period = 'AM' if h < 12 else 'PM'
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"


def _is_after_sunset(weather) -> bool:
    if not weather:
        return False
    sunset = weather.get('sunset_time')
    if not sunset:
        return False
    try:
        sh, sm = (int(p) for p in str(sunset).split(':')[:2])
    except (ValueError, TypeError):
        return False
    now = now_sydney()
    return (now.hour, now.minute) >= (sh, sm)


def get_dashboard_mood(weather, current_hour) -> str:
    """Return one of: 'sunny', 'cloud', 'rain', 'evening'."""
    if current_hour >= 18 or _is_after_sunset(weather):
        return 'evening'
    if not weather:
        return 'cloud'
    cond = (weather.get('condition') or '').lower()
    rain_prob = weather.get('rain_prob', 0) or 0
    if 'rain' in cond or 'shower' in cond or 'storm' in cond or 'thunder' in cond or rain_prob >= 50:
        return 'rain'
    if 'cloud' in cond or 'overcast' in cond or 'fog' in cond:
        return 'cloud'
    return 'sunny'


def get_weather_callout(weather, lessons_today) -> dict:
    """Plain-English weather summary {icon, title, text, link, link_text}."""
    if not weather:
        return {'icon': 'ti-cloud-off', 'title': 'Weather unavailable',
                'text': "Couldn't load the forecast right now.", 'link': '', 'link_text': ''}
    sunset_12 = _hhmm_to_12h(weather.get('sunset_time')) if weather.get('sunset_time') else ''
    scheduled = [l for l in lessons_today if l.get('status') == 'scheduled']
    cond = (weather.get('condition') or '').lower()
    rain_prob = weather.get('rain_prob', 0) or 0
    is_rain = 'rain' in cond or 'shower' in cond or 'storm' in cond or 'thunder' in cond or rain_prob >= 50

    if _is_after_sunset(weather):
        text = 'Sun has set. Court lights are active.'
        if scheduled:
            nxt = scheduled[0]
            text = f"Sun has set. Court lights active. {len(scheduled)} lesson{'s' if len(scheduled) != 1 else ''} left, next at {_hhmm_to_12h(nxt.get('start_time'))}."
        return {'icon': 'ti-bulb', 'title': 'Lights are on', 'text': text, 'link': '', 'link_text': ''}

    if is_rain:
        if scheduled:
            nxt = scheduled[0]
            text = f"{nxt.get('student_name', 'A')}'s {_hhmm_to_12h(nxt.get('start_time'))} lesson may be affected. Tap to send a heads-up."
            return {'icon': 'ti-umbrella', 'title': f'Rain likely today ({rain_prob}%)', 'text': text,
                    'link': '/sms?template=rain_cancel&tab=contacts', 'link_text': 'Warn students'}
        return {'icon': 'ti-umbrella', 'title': f'Rain likely today ({rain_prob}%)',
                'text': 'No lessons booked today — nothing to reschedule.', 'link': '', 'link_text': ''}

    # Dry day
    tail = f" Court lights on at {sunset_12}." if sunset_12 else ''
    if scheduled:
        text = f"Clear through all your lessons.{tail}"
    else:
        text = f"Clear and dry today.{tail}"
    return {'icon': 'ti-droplet-off', 'title': 'No rain today', 'text': text, 'link': '', 'link_text': ''}


def _build_timeline(lessons, settings) -> list:
    """Build hourly timeline rows mixing lesson cards and free slots."""
    start = settings.get('working_hours_start', '07:00') or '07:00'
    end = settings.get('working_hours_end', '20:00') or '20:00'
    try:
        sh = int(str(start).split(':')[0])
        eh = int(str(end).split(':')[0])
    except (ValueError, TypeError):
        sh, eh = 7, 20
    by_hour = {}
    for l in lessons:
        try:
            h = int(str(l.get('start_time') or '07:00').split(':')[0])
        except (ValueError, TypeError):
            h = sh
        by_hour.setdefault(h, []).append(l)
    rows = []
    for h in range(sh, eh + 1):
        if by_hour.get(h):
            for l in sorted(by_hour[h], key=lambda x: x.get('start_time', '')):
                rows.append({'time': _hhmm_to_12h(l.get('start_time')), 'type': 'lesson', 'lesson': l})
        else:
            rows.append({'time': _hhmm_to_12h(h), 'type': 'free', 'hour24': f'{h:02d}:00'})
    return rows


def _min_to_hhmm(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def get_schedule_summary(lessons, settings) -> dict:
    """Compact summary: booked totals + human-readable free blocks within working hours."""
    start = settings.get('working_hours_start', '07:00') or '07:00'
    end = settings.get('working_hours_end', '20:00') or '20:00'

    def to_min(t):
        parts = str(t).split(':')
        try:
            return int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, TypeError):
            return 0

    ws, we = to_min(start), to_min(end)
    booked = []
    total_minutes = 0
    total_earnings = 0.0
    for l in lessons:
        if l.get('status') == 'cancelled':
            continue
        st = l.get('start_time')
        if not st:
            continue
        s = to_min(st)
        dur = int(l.get('duration_minutes') or 0)
        booked.append((s, s + dur))
        total_minutes += dur
        total_earnings += float(l.get('price') or 0)
    booked.sort()

    free = []
    cursor = ws
    for s, e in booked:
        if s > cursor:
            free.append((cursor, min(s, we)))
        cursor = max(cursor, e)
    if cursor < we:
        free.append((cursor, we))

    free_blocks = []
    for s, e in free:
        if e - s < 30:  # skip gaps shorter than 30 min — not worth slotting a lesson into
            continue
        if e >= we:
            free_blocks.append(f"{_hhmm_to_12h(_min_to_hhmm(s))} onwards")
        else:
            free_blocks.append(f"{_hhmm_to_12h(_min_to_hhmm(s))}–{_hhmm_to_12h(_min_to_hhmm(e))}")

    return {
        'total_hours': round(total_minutes / 60, 1),
        'total_earnings': total_earnings,
        'free_blocks': free_blocks,
    }


def _enrich_lesson(l: dict) -> dict:
    """Add display fields used by the agenda template."""
    out = dict(l)
    dur = l.get('duration_minutes') or 0
    try:
        sh, sm = (int(p) for p in str(l.get('start_time') or '0:0').split(':')[:2])
        total = sh * 60 + sm + int(dur)
        out['end_time'] = f"{(total // 60) % 24:02d}:{total % 60:02d}"
    except (ValueError, TypeError):
        out['end_time'] = ''
    out['start_12h'] = _hhmm_to_12h(l.get('start_time'))
    out['end_12h'] = _hhmm_to_12h(out['end_time']) if out['end_time'] else ''
    return out


@app.route('/coach')
def coach_index():
    """Home (Today) view: agenda timeline + adaptive weather rail."""
    settings = load_settings()
    now = now_sydney()
    weather = coach_weather.get_cached_weather()
    week = coach_weather.get_cached_week_forecast() or {'days': []}

    today = today_str()
    todays = [_enrich_lesson(l) for l in _load_lessons()
              if l.get('date') == today and l.get('status') != 'cancelled']
    todays.sort(key=lambda l: l.get('start_time', ''))

    total_minutes = sum(int(l.get('duration_minutes') or 0) for l in todays)
    total_value = sum(float(l.get('price') or 0) for l in todays)
    hours = round(total_minutes / 60, 1)
    hours_label = (f"{int(hours)}" if hours == int(hours) else f"{hours}") + (' hour' if hours == 1 else ' hours')

    scheduled = [l for l in todays if l.get('status') == 'scheduled']
    next_lesson = None
    for l in scheduled:
        try:
            sh, sm = (int(p) for p in str(l.get('start_time')).split(':')[:2])
        except (ValueError, TypeError):
            continue
        if (sh, sm) >= (now.hour, now.minute):
            next_lesson = l
            break
    if next_lesson is None and scheduled:
        next_lesson = scheduled[0]

    summary = coach_earnings.get_earnings_summary()
    total_owed = coach_earnings.get_total_owed()
    mood = get_dashboard_mood(weather, now.hour)
    callout = get_weather_callout(weather, todays)

    week_days = week.get('days', [])
    for d in week_days:
        d['icon'] = _weather_icon(d.get('condition'))
    sunset_12 = _hhmm_to_12h(weather.get('sunset_time')) if weather and weather.get('sunset_time') else '—'

    return render_template(
        'coach_index.html',
        settings=settings,
        coach_title=settings.get('coach_title', 'Mr'),
        coach_name=settings.get('coach_name', 'Matt'),
        greeting=_time_greeting(),
        today=today,
        today_long=now.strftime('%A, %-d %B %Y'),
        mood=mood,
        weather=weather,
        weather_icon=_weather_icon(weather.get('condition') if weather else ''),
        callout=callout,
        week_days=week_days,
        sunset_12=sunset_12,
        timeline=_build_timeline(todays, settings),
        schedule_summary=get_schedule_summary(todays, settings),
        lessons_today=todays,
        lesson_count=len(todays),
        hours_label=hours_label,
        total_value=total_value,
        earnings_week=summary.get('week', 0),
        earnings_month=summary.get('month', 0),
        total_owed=total_owed,
        next_lesson=next_lesson,
    )


@app.route('/calendar')
def calendar_page():
    settings = load_settings()
    return render_template('calendar.html', settings=settings)


@app.route('/students')
def students_page():
    settings = load_settings()
    return render_template('students.html', settings=settings)


@app.route('/students/<student_id>')
def student_detail_page(student_id):
    student = _student_by_id(student_id)
    if not student:
        return redirect(url_for('students_page'))
    settings = load_settings()
    return render_template('student_detail.html', settings=settings, student=student)


@app.route('/earnings')
def earnings_page():
    settings = load_settings()
    return render_template('earnings.html', settings=settings)


@app.route('/money-owed')
def money_owed_page():
    settings = load_settings()
    owed = coach_earnings.get_money_owed()
    total = coach_earnings.get_total_owed()
    return render_template(
        'money_owed.html',
        settings=settings,
        owed=owed,
        total=total,
        active_page='money-owed',
    )


@app.route('/api/money-owed/mark-paid/<student_id>', methods=['POST'])
def api_money_owed_mark_paid(student_id):
    try:
        count = coach_earnings.mark_student_paid(student_id)
        return jsonify({'status': 'ok', 'marked': count})
    except Exception as e:
        log_error(f"Mark paid error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Lessons API
# ---------------------------------------------------------------------------

def _generate_recurring(base: dict, rule: dict) -> list:
    """Build weekly recurring lesson instances from a base lesson + rule."""
    instances = []
    try:
        start = datetime.strptime(base['date'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        return [base]
    end = None
    end_raw = (rule or {}).get('end_date')
    if end_raw:
        try:
            end = datetime.strptime(end_raw, '%Y-%m-%d').date()
        except ValueError:
            end = None
    max_weeks = 52
    for week in range(max_weeks):
        instance_date = start + timedelta(weeks=week)
        if end and instance_date > end:
            break
        instance = dict(base)
        instance['id'] = uuid.uuid4().hex
        instance['date'] = instance_date.isoformat()
        instance['recurring'] = True
        instance['recurring_rule'] = rule
        instances.append(instance)
    return instances


@app.route('/api/lessons')
def api_lessons():
    """All lessons, optionally filtered by ?date=, ?student=, ?status=."""
    lessons = _load_lessons()
    date_f = request.args.get('date')
    student_f = request.args.get('student')
    status_f = request.args.get('status')
    if date_f:
        lessons = [l for l in lessons if l.get('date') == date_f]
    if student_f:
        lessons = [l for l in lessons if l.get('student_id') == student_f]
    if status_f:
        lessons = [l for l in lessons if l.get('status') == status_f]
    lessons = sorted(lessons, key=lambda l: (l.get('date', ''), l.get('start_time', '')))
    return jsonify({'lessons': lessons})


@app.route('/api/lessons/today')
def api_lessons_today():
    today = today_str()
    lessons = [l for l in _load_lessons() if l.get('date') == today]
    lessons = sorted(lessons, key=lambda l: l.get('start_time', ''))
    return jsonify({'lessons': lessons, 'count': len(lessons)})


@app.route('/api/lessons/week')
def api_lessons_week():
    ref = now_sydney().date()
    start = ref - timedelta(days=ref.weekday())
    end = start + timedelta(days=6)
    by_day = {DAY_NAMES[i]: [] for i in range(7)}
    week = []
    for lesson in _load_lessons():
        try:
            d = datetime.strptime(lesson.get('date', ''), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        if start <= d <= end:
            week.append(lesson)
            by_day[DAY_NAMES[d.weekday()]].append(lesson)
    week = sorted(week, key=lambda l: (l.get('date', ''), l.get('start_time', '')))
    return jsonify({
        'lessons': week,
        'by_day': by_day,
        'week_start': start.isoformat(),
        'week_end': end.isoformat(),
    })


@app.route('/api/lessons/add', methods=['POST'])
def api_lessons_add():
    try:
        body = request.get_json(silent=True) or {}
        student_id = body.get('student_id', '')

        # Create a new student inline if the modal sent the "+ Add new student" option
        if student_id == '__new__':
            new_name = (body.get('new_student_name', '') or '').strip()
            if not new_name:
                return jsonify({'status': 'error', 'message': 'New student name required'}), 400
            new_student = {
                'id': uuid.uuid4().hex,
                'name': new_name,
                'phone': coach_sms.format_phone_au(body.get('new_student_phone', '')),
                'email': '',
                'level': body.get('new_student_level', 'Beginner') or 'Beginner',
                'age_group': 'Adult',
                'default_duration': 60,
                'default_price': float(body.get('price') or 80),
                'notes': '',
                'created_at': now_sydney().isoformat(),
                'active': True,
            }
            students = _load_students()
            students.append(new_student)
            _save_students(students)
            log_info(f"Student added (from lesson modal): {new_name}")
            student_id = new_student['id']
            student = new_student
        else:
            student = _student_by_id(student_id)

        # Prefer explicit duration_minutes (15-min increments); fall back to legacy blocks
        dur_raw = body.get('duration_minutes')
        if dur_raw not in (None, ''):
            duration_minutes = max(15, int(dur_raw))
            blocks = max(1, round(duration_minutes / 30))
        else:
            blocks = int(body.get('blocks', 2) or 2)
            duration_minutes = blocks_to_minutes(blocks)
        price = body.get('price')
        if price in (None, ''):
            price = coach_earnings.get_price_for_minutes(duration_minutes)
        base = {
            'id': uuid.uuid4().hex,
            'student_id': student_id,
            'student_name': (student or {}).get('name', body.get('student_name', '')),
            'date': body.get('date', today_str()),
            'start_time': body.get('start_time', '09:00'),
            'blocks': blocks,
            'duration_minutes': duration_minutes,
            'price': float(price),
            'status': 'scheduled',
            'recurring': bool(body.get('recurring', False)),
            'recurring_rule': body.get('recurring_rule'),
            'notes': body.get('notes', ''),
            'lesson_summary': '',
            'payment_status': 'unpaid',
            'created_at': now_sydney().isoformat(),
        }
        lessons = _load_lessons()
        recur_weeks = body.get('recur_weeks')
        rule = body.get('recurring_rule')
        if base['recurring'] and recur_weeks is not None:
            try:
                weeks = int(recur_weeks)
            except (TypeError, ValueError):
                weeks = 1
            weeks = 52 if weeks <= 0 else max(1, min(52, weeks))
            rule = rule or {'frequency': 'weekly'}
            try:
                start = datetime.strptime(base['date'], '%Y-%m-%d').date()
                rule['end_date'] = (start + timedelta(weeks=weeks - 1)).isoformat()
            except (KeyError, ValueError):
                pass
            rule['weeks'] = weeks
            base['recurring_rule'] = rule
            new_items = _generate_recurring(base, rule)
        elif base['recurring'] and rule:
            new_items = _generate_recurring(base, rule)
        else:
            base['recurring'] = False
            base['recurring_rule'] = None
            new_items = [base]
        lessons.extend(new_items)
        _save_lessons(lessons)
        log_info(f"Lesson(s) added: {len(new_items)} for {base['student_name']}")
        return jsonify({
            'status': 'ok',
            'lesson_id': new_items[0]['id'],
            'created_count': len(new_items),
        })
    except Exception as e:
        log_error(f"Add lesson error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _update_lesson(lesson_id: str, changes: dict) -> bool:
    lessons = _load_lessons()
    found = False
    for lesson in lessons:
        if lesson.get('id') == lesson_id:
            lesson.update(changes)
            found = True
            break
    if found:
        _save_lessons(lessons)
    return found


@app.route('/api/lessons/<lesson_id>/complete', methods=['POST'])
def api_lesson_complete(lesson_id):
    try:
        if not _update_lesson(lesson_id, {'status': 'completed'}):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/cancel', methods=['POST'])
def api_lesson_cancel(lesson_id):
    try:
        if not _update_lesson(lesson_id, {'status': 'cancelled'}):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/notes', methods=['POST'])
def api_lesson_notes(lesson_id):
    try:
        body = request.get_json(silent=True) or {}
        changes = {
            'notes': body.get('notes', ''),
            'lesson_summary': body.get('lesson_summary', ''),
        }
        if not _update_lesson(lesson_id, changes):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/pay', methods=['POST'])
def api_lesson_pay(lesson_id):
    try:
        if not _update_lesson(lesson_id, {'payment_status': 'paid'}):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/coach-notes', methods=['POST'])
def api_lesson_coach_notes(lesson_id):
    try:
        body = request.get_json(silent=True) or {}
        if not _update_lesson(lesson_id, {'coach_notes': body.get('coach_notes', '')}):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/reschedule-data')
def api_lesson_reschedule_data(lesson_id):
    lesson = next((l for l in _load_lessons() if l.get('id') == lesson_id), None)
    if not lesson:
        return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
    return jsonify({
        'student_id': lesson.get('student_id', ''),
        'student_name': lesson.get('student_name', ''),
        'blocks': lesson.get('blocks', 2),
        'price': lesson.get('price', 0),
        'original_date': lesson.get('date', ''),
        'original_time': lesson.get('start_time', ''),
    })


@app.route('/api/lessons/<lesson_id>', methods=['DELETE'])
def api_lesson_delete(lesson_id):
    try:
        lessons = _load_lessons()
        remaining = [l for l in lessons if l.get('id') != lesson_id]
        if len(remaining) == len(lessons):
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        _save_lessons(remaining)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Students API
# ---------------------------------------------------------------------------

@app.route('/api/students')
def api_students():
    students = _load_students()
    counts = _lesson_counts_by_student()
    for student in students:
        student['lesson_count'] = counts.get(student.get('id'), 0)
    students = sorted(students, key=lambda s: s.get('name', '').lower())
    return jsonify({'students': students})


@app.route('/api/students/add', methods=['POST'])
def api_students_add():
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get('name', '') or '').strip()
        if not name:
            return jsonify({'status': 'error', 'message': 'Name required'}), 400
        student = {
            'id': uuid.uuid4().hex,
            'name': name,
            'phone': coach_sms.format_phone_au(body.get('phone', '')),
            'email': body.get('email', ''),
            'level': body.get('level', 'Beginner'),
            'age_group': body.get('age_group', 'Adult'),
            'default_duration': int(body.get('default_duration', 60) or 60),
            'default_price': float(body.get('default_price', 80) or 80),
            'notes': body.get('notes', ''),
            'created_at': now_sydney().isoformat(),
            'active': True,
        }
        students = _load_students()
        students.append(student)
        _save_students(students)
        log_info(f"Student added: {name}")
        return jsonify({'status': 'ok', 'student_id': student['id']})
    except Exception as e:
        log_error(f"Add student error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/students/<student_id>/edit', methods=['POST'])
def api_students_edit(student_id):
    try:
        body = request.get_json(silent=True) or {}
        students = _load_students()
        found = False
        editable = ('name', 'phone', 'email', 'level', 'age_group', 'notes')
        for student in students:
            if student.get('id') == student_id:
                for key in editable:
                    if key in body:
                        student[key] = coach_sms.format_phone_au(body[key]) if key == 'phone' else body[key]
                if 'default_duration' in body:
                    student['default_duration'] = int(body['default_duration'] or 60)
                if 'default_price' in body:
                    student['default_price'] = float(body['default_price'] or 80)
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        _save_students(students)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/students/<student_id>/deactivate', methods=['POST'])
def api_students_deactivate(student_id):
    try:
        students = _load_students()
        found = False
        for student in students:
            if student.get('id') == student_id:
                student['active'] = not student.get('active', True)
                found = True
                new_state = student['active']
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        _save_students(students)
        return jsonify({'status': 'ok', 'active': new_state})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/students/<student_id>/lessons')
def api_student_lessons(student_id):
    lessons = [l for l in _load_lessons() if l.get('student_id') == student_id]
    lessons = sorted(lessons, key=lambda l: (l.get('date', ''), l.get('start_time', '')), reverse=True)
    return jsonify({'lessons': lessons})


@app.route('/api/students/<student_id>/coaching-profile', methods=['GET', 'POST'])
def api_student_coaching_profile(student_id):
    students = _load_students()
    student = next((s for s in students if s.get('id') == student_id), None)
    if not student:
        return jsonify({'status': 'error', 'message': 'Student not found'}), 404
    if request.method == 'GET':
        return jsonify({'coaching_profile': student.get('coaching_profile', {})})
    try:
        body = request.get_json(silent=True) or {}
        profile = student.get('coaching_profile', {}) or {}
        for key in ('current_focus', 'goals'):
            if key in body:
                profile[key] = body.get(key, '')
        for key in ('strengths', 'areas_to_improve'):
            if key in body:
                items = body.get(key) or []
                profile[key] = [str(x).strip() for x in items if str(x).strip()]
        profile['updated_at'] = now_sydney().isoformat()
        student['coaching_profile'] = profile
        _save_students(students)
        return jsonify({'status': 'ok', 'coaching_profile': profile})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Calendar API
# ---------------------------------------------------------------------------

def _week_rain_map() -> dict:
    """Map of date string -> rain probability from the cached week forecast."""
    rain = {}
    try:
        data = coach_weather.get_cached_week_forecast() or {}
        for day in data.get('days', []):
            if day.get('date') is not None:
                rain[day['date']] = day.get('rain_prob', 0)
    except Exception:
        pass
    return rain


@app.route('/api/calendar/day')
def api_calendar_day():
    date_f = request.args.get('date') or today_str()
    lessons = [l for l in _load_lessons() if l.get('date') == date_f]
    lessons = sorted(lessons, key=lambda l: l.get('start_time', ''))
    earnings = round(sum(
        float(l.get('price') or 0) for l in lessons
        if l.get('status') in ('completed', 'scheduled')
    ), 2)
    return jsonify({
        'date': date_f,
        'lessons': lessons,
        'earnings': earnings,
        'rain_prob': _week_rain_map().get(date_f, 0),
    })


@app.route('/api/calendar/month')
def api_calendar_month():
    now = now_sydney()
    try:
        year = int(request.args.get('year') or now.year)
        month = int(request.args.get('month') or now.month)
    except (TypeError, ValueError):
        year, month = now.year, now.month
    rain_map = _week_rain_map()
    by_day = {}
    for l in _load_lessons():
        d = l.get('date') or ''
        try:
            dt = datetime.strptime(d, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            continue
        if dt.year != year or dt.month != month:
            continue
        entry = by_day.setdefault(d, {'lesson_count': 0, 'earnings': 0.0})
        if l.get('status') != 'cancelled':
            entry['lesson_count'] += 1
        if l.get('status') in ('completed', 'scheduled'):
            entry['earnings'] += float(l.get('price') or 0)
    days = []
    for d, info in by_day.items():
        days.append({
            'date': d,
            'lesson_count': info['lesson_count'],
            'earnings': round(info['earnings'], 2),
            'rain_prob': rain_map.get(d, 0),
        })
    days.sort(key=lambda x: x['date'])
    return jsonify({'year': year, 'month': month, 'days': days})


# ---------------------------------------------------------------------------
# Weather API
# ---------------------------------------------------------------------------

@app.route('/api/weather')
def api_weather():
    data = coach_weather.get_cached_weather()
    return jsonify(data or {})


@app.route('/api/weather/hourly')
def api_weather_hourly():
    data = coach_weather.get_cached_weather()
    return jsonify({'hourly': (data or {}).get('hourly', [])})


@app.route('/api/weather/week')
def api_weather_week():
    data = coach_weather.get_cached_week_forecast()
    return jsonify(data or {'days': []})


@app.route('/api/geocode')
def api_geocode():
    query = request.args.get('q', '')
    return jsonify(coach_weather.geocode_search(query))


# ---------------------------------------------------------------------------
# Earnings API
# ---------------------------------------------------------------------------

@app.route('/api/earnings/summary')
def api_earnings_summary():
    return jsonify(coach_earnings.get_earnings_summary())


@app.route('/api/earnings/chart/weekly')
def api_earnings_weekly():
    return jsonify(coach_earnings.get_weekly_chart_data(8))


@app.route('/api/earnings/chart/monthly')
def api_earnings_monthly():
    return jsonify(coach_earnings.get_monthly_chart_data(12))


@app.route('/api/earnings/chart/daily')
def api_earnings_daily():
    return jsonify(coach_earnings.get_daily_chart_data())


@app.route('/api/earnings/export')
def api_earnings_export():
    output = coach_earnings.export_csv()
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=earnings.csv'},
    )


# ---------------------------------------------------------------------------
# AI API
# ---------------------------------------------------------------------------

@app.route('/api/ai/student-summary', methods=['POST'])
def api_ai_student_summary():
    if not ai_helper.ai_available():
        return jsonify({'status': 'error', 'message': 'AI not configured. Add API key in Settings.'}), 400
    try:
        body = request.get_json(silent=True) or {}
        student = _student_by_id(body.get('student_id', ''))
        if not student:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        lessons = [l for l in _load_lessons() if l.get('student_id') == student['id']]
        summary = ai_helper.summarise_student_progress(student, lessons)
        return jsonify({'status': 'ok', 'summary': summary or ''})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/ai/earnings-insight', methods=['POST'])
def api_ai_earnings_insight():
    if not ai_helper.ai_available():
        return jsonify({'status': 'error', 'message': 'AI not configured. Add API key in Settings.'}), 400
    try:
        cutoff = (now_sydney().date() - timedelta(days=30)).isoformat()
        lessons = [l for l in _load_lessons() if l.get('date', '') >= cutoff]
        insight = ai_helper.get_earnings_insight(lessons, load_settings())
        return jsonify({'status': 'ok', 'insight': insight or {}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/ai/cancel-message', methods=['POST'])
def api_ai_cancel_message():
    if not ai_helper.ai_available():
        return jsonify({'status': 'error', 'message': 'AI not configured. Add API key in Settings.'}), 400
    try:
        body = request.get_json(silent=True) or {}
        lesson = next((l for l in _load_lessons() if l.get('id') == body.get('lesson_id')), body)
        message = ai_helper.draft_cancellation_message(lesson, body.get('reason', 'weather'))
        return jsonify({'status': 'ok', 'message': message or ''})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/ai/parse-booking', methods=['POST'])
def api_ai_parse_booking():
    if not ai_helper.ai_available():
        return jsonify({'status': 'error', 'message': 'AI not configured. Add API key in Settings.'}), 400
    try:
        body = request.get_json(silent=True) or {}
        booking = ai_helper.parse_natural_language_booking(body.get('text', ''), _load_students())
        return jsonify({'status': 'ok', 'booking': booking or {}})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Coach settings API
# ---------------------------------------------------------------------------

@app.route('/api/settings')
def api_settings():
    settings = load_settings()
    safe = dict(settings)
    safe['local_ip'] = get_local_ip()
    return jsonify(safe)


@app.route('/api/settings/save', methods=['POST'])
def api_settings_save():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        str_fields = (
            'coach_name', 'coach_title', 'location', 'court_name', 'court_address',
            'working_hours_start', 'working_hours_end',
            'vapid_claim_email',
        )
        for key in str_fields:
            if key in body:
                settings[key] = (body[key] or '').strip() if isinstance(body[key], str) else body[key]
        for key in ('latitude', 'longitude'):
            if key in body:
                try:
                    settings[key] = float(body[key])
                except (TypeError, ValueError):
                    pass
        if 'lights_warning_minutes' in body:
            try:
                settings['lights_warning_minutes'] = int(body['lights_warning_minutes'])
            except (TypeError, ValueError):
                pass
        if 'push_notifications_enabled' in body:
            settings['push_notifications_enabled'] = bool(body['push_notifications_enabled'])
        if isinstance(body.get('pricing'), dict):
            pricing = settings.get('pricing', {}) or {}
            pricing_in = body['pricing']
            if isinstance(pricing_in.get('duration_prices'), dict):
                dp = pricing.get('duration_prices', {}) or {}
                for key, val in pricing_in['duration_prices'].items():
                    try:
                        dp[str(key)] = float(val)
                    except (TypeError, ValueError):
                        pass
                pricing['duration_prices'] = dp
            if isinstance(pricing_in.get('presets'), list):
                pricing['presets'] = _clean_presets(pricing_in['presets'])
            settings['pricing'] = pricing
        if isinstance(body.get('invoicing'), dict):
            inv = settings.get('invoicing', {}) or {}
            inv_in = body['invoicing']
            for key in ('coach_abn', 'coach_address', 'bank_name', 'bank_bsb',
                        'bank_account', 'invoice_prefix'):
                if key in inv_in:
                    inv[key] = (inv_in[key] or '').strip() if isinstance(inv_in[key], str) else inv_in[key]
            if 'payment_terms_days' in inv_in:
                try:
                    inv['payment_terms_days'] = int(inv_in['payment_terms_days'])
                except (TypeError, ValueError):
                    pass
            if 'gst_registered' in inv_in:
                inv['gst_registered'] = bool(inv_in['gst_registered'])
            settings['invoicing'] = inv
        if isinstance(body.get('twilio'), dict):
            tw = settings.get('twilio', {}) or {}
            tw_in = body['twilio']
            for key in ('account_sid', 'auth_token', 'from_number'):
                if key in tw_in:
                    tw[key] = (tw_in[key] or '').strip() if isinstance(tw_in[key], str) else tw_in[key]
            if 'enabled' in tw_in:
                tw['enabled'] = bool(tw_in['enabled'])
            settings['twilio'] = tw
        if isinstance(body.get('ai'), dict):
            ai = settings.get('ai', {}) or {}
            ai_in = body['ai']
            ai.setdefault('provider', 'groq')
            if 'groq_api_key' in ai_in:
                ai['groq_api_key'] = (ai_in['groq_api_key'] or '').strip() if isinstance(ai_in['groq_api_key'], str) else ai_in['groq_api_key']
            if 'model' in ai_in and ai_in['model']:
                ai['model'] = (ai_in['model'] or '').strip()
            if 'enabled' in ai_in:
                ai['enabled'] = bool(ai_in['enabled'])
            settings['ai'] = ai
        if isinstance(body.get('tax'), dict):
            tax = settings.get('tax', {}) or {}
            tax_in = body['tax']
            if 'other_income' in tax_in:
                try:
                    tax['other_income'] = float(tax_in['other_income'])
                except (TypeError, ValueError):
                    pass
            for key in ('has_help_debt', 'has_private_health'):
                if key in tax_in:
                    tax[key] = bool(tax_in[key])
            if 'financial_year_start' in tax_in:
                tax['financial_year_start'] = (tax_in['financial_year_start'] or '').strip()
            settings['tax'] = tax
        save_json(SETTINGS_PATH, settings)
        log_info("Coach settings saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def _clean_presets(raw) -> list:
    """Normalise a list of {name, amount} price presets, dropping invalid ones."""
    cleaned = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = (item.get('name', '') or '').strip()
        if not name:
            continue
        try:
            amount = float(item.get('amount', 0) or 0)
        except (TypeError, ValueError):
            continue
        cleaned.append({'name': name, 'amount': amount})
    return cleaned


@app.route('/api/settings/price-presets', methods=['POST'])
def api_settings_price_presets():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        pricing = settings.get('pricing', {}) or {}
        pricing['presets'] = _clean_presets(body.get('presets', []))
        settings['pricing'] = pricing
        save_json(SETTINGS_PATH, settings)
        log_info("Price presets saved.")
        return jsonify({'status': 'ok', 'presets': pricing['presets']})
    except Exception as e:
        log_error(f"Save price presets error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/test-weather', methods=['POST'])
def api_settings_test_weather():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        lat = float(body.get('latitude', settings.get('latitude', -33.8688)))
        lon = float(body.get('longitude', settings.get('longitude', 151.2093)))
        data = coach_weather.get_weather(lat, lon)
        if not data:
            return jsonify({'status': 'error', 'message': 'Could not fetch weather. Check connection.'}), 502
        return jsonify({'status': 'ok', 'weather': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/location')
def api_settings_location_get():
    settings = load_settings()
    return jsonify({
        'latitude': settings.get('latitude', -33.8688),
        'longitude': settings.get('longitude', 151.2093),
        'location': settings.get('location', ''),
    })


@app.route('/api/settings/location', methods=['POST'])
def api_settings_location_save():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        if 'location' in body:
            settings['location'] = (body['location'] or '').strip()
        for key in ('latitude', 'longitude'):
            if key in body:
                settings[key] = float(body[key])
        save_json(SETTINGS_PATH, settings)
        coach_weather.clear_weather_cache()
        log_info(f"Location updated: {settings.get('location', '')}")
        return jsonify({
            'status': 'ok',
            'location': settings.get('location', ''),
            'latitude': settings.get('latitude'),
            'longitude': settings.get('longitude'),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/ai', methods=['POST'])
def api_settings_ai_save():
    """Save the GroqCloud AI configuration block."""
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        ai = settings.get('ai', {}) or {}
        ai.setdefault('provider', 'groq')
        if 'groq_api_key' in body:
            ai['groq_api_key'] = (body['groq_api_key'] or '').strip() if isinstance(body['groq_api_key'], str) else body['groq_api_key']
        if body.get('model'):
            ai['model'] = (body['model'] or '').strip()
        if 'enabled' in body:
            ai['enabled'] = bool(body['enabled'])
        settings['ai'] = ai
        save_json(SETTINGS_PATH, settings)
        log_info("AI (GroqCloud) settings saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/ai/test', methods=['POST'])
def api_ai_test():
    """Test the GroqCloud connection with the saved (or supplied) key."""
    try:
        body = request.get_json(silent=True) or {}
        key = (body.get('groq_api_key') or '').strip()
        if key:
            settings = load_settings()
            ai = settings.get('ai', {}) or {}
            ai['groq_api_key'] = key
            ai.setdefault('provider', 'groq')
            if body.get('model'):
                ai['model'] = (body['model'] or '').strip()
            settings['ai'] = ai
            save_json(SETTINGS_PATH, settings)
        result = ai_helper.test_groq_connection()
        if result.get('success'):
            return jsonify({'status': 'ok', 'message': result.get('message', 'AI connected')})
        return jsonify({'status': 'error', 'message': result.get('message', 'AI request failed')}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Back-compat alias for the older test-ai endpoint.
@app.route('/api/settings/test-ai', methods=['POST'])
def api_settings_test_ai():
    result = ai_helper.test_groq_connection()
    if result.get('success'):
        return jsonify({'status': 'ok', 'message': result.get('message', 'AI connected')})
    return jsonify({'status': 'error', 'message': result.get('message', 'AI request failed')}), 400


# ---------------------------------------------------------------------------
# Help & Guided Tours API
# ---------------------------------------------------------------------------

def _help_settings(settings):
    """Return the help block with all defaults present."""
    h = settings.get('help') or {}
    return {
        'show_help_button': bool(h.get('show_help_button', True)),
        'show_feature_tips': bool(h.get('show_feature_tips', True)),
        'completed_tours': list(h.get('completed_tours', []) or []),
        'dismissed_tips': list(h.get('dismissed_tips', []) or []),
    }


@app.route('/api/settings/help')
def api_settings_help_get():
    return jsonify(_help_settings(load_settings()))


@app.route('/api/settings/help', methods=['POST'])
def api_settings_help_save():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        h = _help_settings(settings)
        if 'show_help_button' in body:
            h['show_help_button'] = bool(body['show_help_button'])
        if 'show_feature_tips' in body:
            h['show_feature_tips'] = bool(body['show_feature_tips'])
        settings['help'] = h
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'help': h})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/complete-tour', methods=['POST'])
def api_settings_complete_tour():
    try:
        body = request.get_json(silent=True) or {}
        tour_id = (body.get('tour_id') or '').strip()
        if not tour_id:
            return jsonify({'status': 'error', 'message': 'tour_id required'}), 400
        settings = load_settings()
        h = _help_settings(settings)
        if tour_id not in h['completed_tours']:
            h['completed_tours'].append(tour_id)
        settings['help'] = h
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'completed_tours': h['completed_tours']})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/reset-tours', methods=['POST'])
def api_settings_reset_tours():
    try:
        settings = load_settings()
        h = _help_settings(settings)
        h['completed_tours'] = []
        settings['help'] = h
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/dismiss-tip', methods=['POST'])
def api_settings_dismiss_tip():
    try:
        body = request.get_json(silent=True) or {}
        tip_id = (body.get('tip_id') or '').strip()
        if not tip_id:
            return jsonify({'status': 'error', 'message': 'tip_id required'}), 400
        settings = load_settings()
        h = _help_settings(settings)
        if tip_id not in h['dismissed_tips']:
            h['dismissed_tips'].append(tip_id)
        settings['help'] = h
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'dismissed_tips': h['dismissed_tips']})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Notifications API
# ---------------------------------------------------------------------------

@app.route('/api/notifications/vapid-key')
def api_vapid_key():
    keys = coach_notifications.ensure_vapid_keys()
    return jsonify({'public_key': keys.get('public_key', '')})


@app.route('/api/notifications/subscribe', methods=['POST'])
def api_notifications_subscribe():
    try:
        body = request.get_json(silent=True) or {}
        ok = coach_notifications.save_subscription(body)
        if not ok:
            return jsonify({'status': 'error', 'message': 'Invalid subscription'}), 400
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/notifications/test', methods=['POST'])
def api_notifications_test():
    try:
        coach_notifications.ensure_vapid_keys()
        sent = coach_notifications.send_notification(
            "Ivan", "Test notification - all working!", "/coach"
        )
        if sent:
            return jsonify({'status': 'ok'})
        return jsonify({'status': 'error', 'message': 'No subscribed devices yet.'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===========================================================================
# SOLE TRADER BUSINESS MODULES — invoicing, expenses, tax, waitlist, packages
# ===========================================================================

VEHICLE_RATE = 0.88        # ATO 2025-26 cents-per-km (dollars)
VEHICLE_MAX_KM = 5000      # cap for cents-per-km method
HOME_OFFICE_RATE = 0.70    # ATO fixed-rate per hour

EXPENSE_CATEGORIES = [
    {"id": "equipment", "label": "Equipment & Supplies", "examples": "Tennis balls, rackets, ball machine maintenance", "gst_common": True},
    {"id": "court_hire", "label": "Court Hire", "examples": "Court booking fees", "gst_common": True},
    {"id": "vehicle", "label": "Vehicle - Km Rate", "examples": "Travel to courts, student locations", "gst_common": False, "km_based": True},
    {"id": "phone", "label": "Phone & Internet", "examples": "Business portion of phone/internet bill", "gst_common": True},
    {"id": "insurance", "label": "Insurance", "examples": "Public liability, professional indemnity", "gst_common": True},
    {"id": "professional_dev", "label": "Professional Development", "examples": "Coaching courses, certifications, Tennis Australia fees", "gst_common": True},
    {"id": "uniform", "label": "Uniform & Clothing", "examples": "Branded shirts, caps (must be distinctive uniform)", "gst_common": True},
    {"id": "marketing", "label": "Marketing", "examples": "Facebook ads, business cards, website", "gst_common": True},
    {"id": "software", "label": "Software & Subscriptions", "examples": "Apps, this software, scheduling tools", "gst_common": True},
    {"id": "home_office", "label": "Home Office", "examples": "70c/hour for time spent on admin at home", "gst_common": False, "hourly_based": True},
    {"id": "other", "label": "Other Business Expense", "examples": "Any other legitimate business cost", "gst_common": False},
]

PACKAGE_TYPES = [
    {"sessions": 5, "label": "5-Lesson Pack", "discount_pct": 5},
    {"sessions": 10, "label": "10-Lesson Pack", "discount_pct": 10},
    {"sessions": 20, "label": "20-Lesson Pack", "discount_pct": 15},
]

_CATEGORY_LABELS = {c["id"]: c["label"] for c in EXPENSE_CATEGORIES}


# ---------------------------------------------------------------------------
# Data access — invoices / expenses / packages
# ---------------------------------------------------------------------------

def _load_invoices() -> list:
    data = load_json(INVOICES_PATH, {"invoices": []})
    items = data.get('invoices', [])
    return items if isinstance(items, list) else []


def _save_invoices(invoices: list) -> bool:
    return save_json(INVOICES_PATH, {"invoices": invoices})


def _load_expenses() -> list:
    data = load_json(EXPENSES_PATH, {"expenses": []})
    items = data.get('expenses', [])
    return items if isinstance(items, list) else []


def _save_expenses(expenses: list) -> bool:
    return save_json(EXPENSES_PATH, {"expenses": expenses})


def _load_packages() -> list:
    data = load_json(PACKAGES_PATH, {"packages": []})
    items = data.get('packages', [])
    return items if isinstance(items, list) else []


def _save_packages(packages: list) -> bool:
    return save_json(PACKAGES_PATH, {"packages": packages})


def _fy_bounds() -> tuple:
    """Current financial year start/end as 'YYYY-MM-DD' strings."""
    start, end = coach_tax.get_fy_dates()
    return start.isoformat(), end.isoformat()


def _fy_lesson_income() -> float:
    start, end = _fy_bounds()
    total = 0.0
    for l in _load_lessons():
        if l.get('status') in ('completed', 'scheduled'):
            d = l.get('date') or ''
            if start <= d <= end:
                try:
                    total += float(l.get('price') or 0)
                except (TypeError, ValueError):
                    pass
    return round(total, 2)


def _fy_package_income() -> float:
    start, end = _fy_bounds()
    total = 0.0
    for p in _load_packages():
        d = p.get('purchase_date') or ''
        if start <= d <= end:
            try:
                total += float(p.get('amount_paid') or 0)
            except (TypeError, ValueError):
                pass
    return round(total, 2)


def _fy_gross_income() -> float:
    return round(_fy_lesson_income() + _fy_package_income(), 2)


# ---------------------------------------------------------------------------
# Expenses summary helpers
# ---------------------------------------------------------------------------

def _expense_summary() -> dict:
    start, end = _fy_bounds()
    by_category = {}
    total = 0.0
    vehicle_km = 0.0
    home_hours = 0.0
    gst_total = 0.0
    for e in _load_expenses():
        d = e.get('date') or ''
        if not (start <= d <= end):
            continue
        amount = float(e.get('amount') or 0)
        total += amount
        cat = e.get('category', 'other')
        by_category[cat] = round(by_category.get(cat, 0.0) + amount, 2)
        if cat == 'vehicle':
            vehicle_km += float(e.get('km_trip') or 0)
        if cat == 'home_office':
            home_hours += float(e.get('hours') or 0)
        if e.get('gst_claimable'):
            gst_total += amount
    breakdown = []
    for cat_id, amt in sorted(by_category.items(), key=lambda kv: kv[1], reverse=True):
        breakdown.append({
            'category': cat_id,
            'label': _CATEGORY_LABELS.get(cat_id, cat_id.title()),
            'amount': round(amt, 2),
            'pct': round((amt / total * 100) if total > 0 else 0, 1),
        })
    return {
        'total_expenses': round(total, 2),
        'total_deductions': round(total, 2),
        'vehicle_km_used': round(vehicle_km, 1),
        'vehicle_km_max': VEHICLE_MAX_KM,
        'home_office_hours': round(home_hours, 1),
        'gst_claimable_total': round(gst_total, 2),
        'by_category': breakdown,
    }


# ---------------------------------------------------------------------------
# Invoice summary helpers
# ---------------------------------------------------------------------------

def _invoice_is_overdue(inv: dict) -> bool:
    if inv.get('status') == 'paid':
        return False
    due = inv.get('due_date') or ''
    return bool(due and due < today_str())


def _invoice_display_status(inv: dict) -> str:
    if inv.get('status') == 'paid':
        return 'paid'
    if _invoice_is_overdue(inv):
        return 'overdue'
    return inv.get('status', 'unpaid')


def _invoice_summary() -> dict:
    invoices = _load_invoices()
    total_invoiced = 0.0
    total_paid = 0.0
    total_outstanding = 0.0
    overdue_count = 0
    for inv in invoices:
        amt = float(inv.get('amount_total') or 0)
        total_invoiced += amt
        if inv.get('status') == 'paid':
            total_paid += amt
        else:
            total_outstanding += amt
            if _invoice_is_overdue(inv):
                overdue_count += 1
    return {
        'total_invoiced': round(total_invoiced, 2),
        'total_paid': round(total_paid, 2),
        'total_outstanding': round(total_outstanding, 2),
        'overdue_count': overdue_count,
    }


def _package_alerts() -> list:
    alerts = []
    for p in _load_packages():
        if p.get('active') and 1 <= int(p.get('sessions_remaining') or 0) <= 2:
            alerts.append(p)
    return alerts


# ---------------------------------------------------------------------------
# Global template context — modules, sidebar badges (used by base.html)
# ---------------------------------------------------------------------------

@app.context_processor
def inject_business_context():
    """Expose settings + business badges + adaptive mood to every template (incl. base.html)."""
    try:
        owed = coach_earnings.get_money_owed()
        try:
            mood = get_dashboard_mood(coach_weather.get_cached_weather(), now_sydney().hour)
        except Exception:
            mood = 'cloud'
        return {
            'settings': load_settings(),
            'invoice_stats': _invoice_summary(),
            'package_alerts': len(_package_alerts()),
            'sms_stats': {'unread': 0},
            'total_owed': round(sum(r['amount_owed'] for r in owed), 2),
            'owed_count': len(owed),
            'mood': mood,
        }
    except Exception:
        return {
            'settings': {},
            'invoice_stats': {'overdue_count': 0},
            'package_alerts': 0,
            'sms_stats': {'unread': 0},
            'total_owed': 0,
            'owed_count': 0,
            'mood': 'cloud',
        }


# ---------------------------------------------------------------------------
# Module toggle save
# ---------------------------------------------------------------------------

@app.route('/api/settings/modules', methods=['POST'])
def api_settings_modules():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        modules = settings.get('modules', {}) or {}
        for key in ('invoicing', 'expense_tracker', 'tax_estimator', 'waitlist', 'lesson_packages', 'sms'):
            if key in body:
                modules[key] = bool(body[key])
        settings['modules'] = modules
        save_json(SETTINGS_PATH, settings)
        log_info("Module toggles saved.")
        return jsonify({'status': 'ok', 'modules': modules})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===========================================================================
# MODULE 1 — INVOICING
# ===========================================================================

@app.route('/invoices')
def invoices_page():
    settings = load_settings()
    return render_template('invoices.html', settings=settings, summary=_invoice_summary())


@app.route('/api/invoices')
def api_invoices():
    status_f = request.args.get('filter') or request.args.get('status')
    invoices = _load_invoices()
    for inv in invoices:
        inv['display_status'] = _invoice_display_status(inv)
    if status_f and status_f != 'all':
        invoices = [i for i in invoices if i.get('display_status') == status_f]
    invoices = sorted(invoices, key=lambda i: i.get('created_at', ''), reverse=True)
    return jsonify({'invoices': invoices, 'summary': _invoice_summary()})


@app.route('/api/invoices/summary')
def api_invoices_summary():
    return jsonify(_invoice_summary())


@app.route('/api/invoices/generate/<lesson_id>', methods=['POST'])
def api_invoice_generate(lesson_id):
    try:
        lesson = next((l for l in _load_lessons() if l.get('id') == lesson_id), None)
        if not lesson:
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        invoices = _load_invoices()
        existing = next((i for i in invoices if i.get('lesson_id') == lesson_id), None)
        if existing:
            return jsonify({'status': 'ok', 'invoice': existing, 'invoice_number': existing['invoice_number'], 'existing': True})

        settings = load_settings()
        inv_cfg = settings.get('invoicing', {}) or {}
        prefix = inv_cfg.get('invoice_prefix', 'INV') or 'INV'
        number = int(inv_cfg.get('next_invoice_number', 1) or 1)
        terms = int(inv_cfg.get('payment_terms_days', 7) or 7)
        gst_registered = bool(inv_cfg.get('gst_registered', False))

        issue = now_sydney().date()
        due = issue + timedelta(days=terms)
        invoice_number = f"{prefix}-{issue.year}-{number:03d}"

        amount_total = float(lesson.get('price') or 0)
        if gst_registered:
            gst_amount = round(amount_total / 11.0, 2)
            amount_ex_gst = round(amount_total - gst_amount, 2)
        else:
            gst_amount = 0.0
            amount_ex_gst = amount_total

        student = _student_by_id(lesson.get('student_id', '')) or {}
        invoice = {
            'id': uuid.uuid4().hex,
            'invoice_number': invoice_number,
            'lesson_id': lesson_id,
            'student_id': lesson.get('student_id', ''),
            'student_name': lesson.get('student_name', '') or student.get('name', ''),
            'student_phone': student.get('phone', ''),
            'issue_date': issue.isoformat(),
            'due_date': due.isoformat(),
            'lesson_date': lesson.get('date', ''),
            'lesson_start': lesson.get('start_time', ''),
            'lesson_duration_minutes': lesson.get('duration_minutes', 0),
            'description': f"Tennis coaching session - {lesson.get('duration_minutes', 0)} minutes",
            'amount_ex_gst': amount_ex_gst,
            'gst_amount': gst_amount,
            'amount_total': amount_total,
            'status': 'unpaid',
            'paid_date': None,
            'created_at': now_sydney().isoformat(),
        }
        invoices.append(invoice)
        _save_invoices(invoices)

        inv_cfg['next_invoice_number'] = number + 1
        settings['invoicing'] = inv_cfg
        save_json(SETTINGS_PATH, settings)
        log_info(f"Invoice generated: {invoice_number} for {invoice['student_name']}")
        return jsonify({'status': 'ok', 'invoice': invoice, 'invoice_number': invoice_number})
    except Exception as e:
        log_error(f"Invoice generate error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/invoices/<invoice_id>/mark-paid', methods=['POST'])
def api_invoice_mark_paid(invoice_id):
    try:
        invoices = _load_invoices()
        found = False
        for inv in invoices:
            if inv.get('id') == invoice_id:
                inv['status'] = 'paid'
                inv['paid_date'] = today_str()
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
        _save_invoices(invoices)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/invoices/<invoice_id>/send', methods=['POST'])
def api_invoice_send(invoice_id):
    try:
        invoices = _load_invoices()
        found = False
        for inv in invoices:
            if inv.get('id') == invoice_id:
                if inv.get('status') != 'paid':
                    inv['status'] = 'sent'
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
        _save_invoices(invoices)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/invoices/<invoice_id>/pdf')
def api_invoice_pdf(invoice_id):
    inv = next((i for i in _load_invoices() if i.get('id') == invoice_id), None)
    if not inv:
        return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
    pdf_bytes = coach_invoices.build_invoice_pdf(inv, load_settings())
    filename = f"{inv.get('invoice_number', 'invoice')}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )


@app.route('/api/invoices/<invoice_id>', methods=['DELETE'])
def api_invoice_delete(invoice_id):
    try:
        invoices = _load_invoices()
        remaining = [i for i in invoices if i.get('id') != invoice_id]
        if len(remaining) == len(invoices):
            return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
        _save_invoices(remaining)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/invoices/student/<student_id>')
def api_invoices_student(student_id):
    invoices = [i for i in _load_invoices() if i.get('student_id') == student_id]
    for inv in invoices:
        inv['display_status'] = _invoice_display_status(inv)
    invoices = sorted(invoices, key=lambda i: i.get('created_at', ''), reverse=True)
    outstanding = round(sum(float(i.get('amount_total') or 0) for i in invoices if i.get('status') != 'paid'), 2)
    return jsonify({'invoices': invoices, 'outstanding': outstanding})


# ===========================================================================
# MODULE 2 — EXPENSE TRACKER
# ===========================================================================

@app.route('/expenses')
def expenses_page():
    settings = load_settings()
    return render_template(
        'expenses.html', settings=settings,
        categories=EXPENSE_CATEGORIES, summary=_expense_summary(),
    )


@app.route('/api/expenses')
def api_expenses():
    date_f = request.args.get('date')
    cat_f = request.args.get('category')
    expenses = _load_expenses()
    if date_f:
        expenses = [e for e in expenses if e.get('date') == date_f]
    if cat_f:
        expenses = [e for e in expenses if e.get('category') == cat_f]
    for e in expenses:
        e['category_label'] = _CATEGORY_LABELS.get(e.get('category'), e.get('category', ''))
    expenses = sorted(expenses, key=lambda e: e.get('date', ''), reverse=True)
    return jsonify({'expenses': expenses, 'categories': EXPENSE_CATEGORIES})


@app.route('/api/expenses/summary')
def api_expenses_summary():
    return jsonify(_expense_summary())


@app.route('/api/expenses/add', methods=['POST'])
def api_expenses_add():
    try:
        body = request.get_json(silent=True) or {}
        category = body.get('category', 'other')
        km_trip = body.get('km_trip')
        hours = body.get('hours')
        amount = body.get('amount')

        if category == 'vehicle' and km_trip not in (None, ''):
            km_trip = float(km_trip)
            amount = round(km_trip * VEHICLE_RATE, 2)
        elif category == 'home_office' and hours not in (None, ''):
            hours = float(hours)
            amount = round(hours * HOME_OFFICE_RATE, 2)
        else:
            amount = float(amount or 0)
            km_trip = None
            hours = None

        expense = {
            'id': uuid.uuid4().hex,
            'date': body.get('date', today_str()),
            'category': category,
            'description': (body.get('description', '') or '').strip(),
            'amount': amount,
            'gst_claimable': bool(body.get('gst_claimable', False)),
            'km_trip': km_trip,
            'hours': hours,
            'receipt_note': body.get('receipt_note', ''),
            'created_at': now_sydney().isoformat(),
        }
        expenses = _load_expenses()
        expenses.append(expense)
        _save_expenses(expenses)
        log_info(f"Expense added: {category} {format_currency(amount)}")
        return jsonify({'status': 'ok', 'expense': expense})
    except Exception as e:
        log_error(f"Add expense error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/expenses/<expense_id>/edit', methods=['POST'])
def api_expenses_edit(expense_id):
    try:
        body = request.get_json(silent=True) or {}
        expenses = _load_expenses()
        found = False
        for e in expenses:
            if e.get('id') == expense_id:
                for key in ('date', 'category', 'description', 'receipt_note'):
                    if key in body:
                        e[key] = body[key]
                if 'gst_claimable' in body:
                    e['gst_claimable'] = bool(body['gst_claimable'])
                cat = e.get('category')
                if cat == 'vehicle' and body.get('km_trip') not in (None, ''):
                    e['km_trip'] = float(body['km_trip'])
                    e['amount'] = round(e['km_trip'] * VEHICLE_RATE, 2)
                    e['hours'] = None
                elif cat == 'home_office' and body.get('hours') not in (None, ''):
                    e['hours'] = float(body['hours'])
                    e['amount'] = round(e['hours'] * HOME_OFFICE_RATE, 2)
                elif 'amount' in body:
                    e['amount'] = float(body['amount'] or 0)
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Expense not found'}), 404
        _save_expenses(expenses)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/expenses/<expense_id>', methods=['DELETE'])
def api_expenses_delete(expense_id):
    try:
        expenses = _load_expenses()
        remaining = [e for e in expenses if e.get('id') != expense_id]
        if len(remaining) == len(expenses):
            return jsonify({'status': 'error', 'message': 'Expense not found'}), 404
        _save_expenses(remaining)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/expenses/export')
def api_expenses_export():
    columns = ['date', 'category', 'description', 'amount', 'gst_claimable', 'km_trip', 'hours', 'receipt_note']
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    ordered = sorted(_load_expenses(), key=lambda e: e.get('date', ''))
    for e in ordered:
        writer.writerow({c: e.get(c, '') for c in columns})
    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=expenses.csv'},
    )


# ===========================================================================
# MODULE 3 — TAX ESTIMATOR
# ===========================================================================

@app.route('/tax')
def tax_page():
    settings = load_settings()
    return render_template('tax.html', settings=settings)


@app.route('/api/tax/estimate')
def api_tax_estimate():
    settings = load_settings()
    tax_cfg = settings.get('tax', {}) or {}
    gross = _fy_gross_income()
    expenses = _expense_summary()['total_deductions']
    other = float(tax_cfg.get('other_income') or 0)
    result = coach_tax.calculate_full_tax(gross, expenses, other)

    elapsed, total_days = coach_tax.get_days_in_fy_elapsed()
    start, end = coach_tax.get_fy_dates()
    monthly_set_aside = round(result['estimated_tax_payable'] / 12.0, 2)
    weekly_set_aside = round(result['estimated_tax_payable'] / 52.0, 2)
    result.update({
        'fy_label': f"FY {start.year}-{str(end.year)[2:]}",
        'fy_days_elapsed': elapsed,
        'fy_days_total': total_days,
        'fy_progress_pct': round(elapsed / total_days * 100, 0) if total_days else 0,
        'monthly_set_aside': monthly_set_aside,
        'weekly_set_aside': weekly_set_aside,
        'next_payg': coach_tax.get_next_payg_date(),
    })
    return jsonify(result)


@app.route('/api/tax/payg-dates')
def api_tax_payg_dates():
    settings = load_settings()
    gross = _fy_gross_income()
    expenses = _expense_summary()['total_deductions']
    other = float((settings.get('tax', {}) or {}).get('other_income') or 0)
    result = coach_tax.calculate_full_tax(gross, expenses, other)
    payg = coach_tax.get_next_payg_date()
    payg['amount'] = result['quarterly_payg_estimate']
    return jsonify(payg)


@app.route('/api/tax/gst-status')
def api_tax_gst_status():
    gross = _fy_gross_income()
    threshold = 75000
    if gross < 60000:
        level, message = 'ok', 'Not required to register for GST.'
    elif gross < threshold:
        level, message = 'warn', 'Approaching threshold - consider registering for GST.'
    else:
        level, message = 'over', 'You must register for GST.'
    return jsonify({
        'turnover': gross,
        'threshold': threshold,
        'pct': round(gross / threshold * 100, 1) if threshold else 0,
        'level': level,
        'message': message,
    })


@app.route('/api/tax/save-assumptions', methods=['POST'])
def api_tax_save_assumptions():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        tax_cfg = settings.get('tax', {}) or {}
        if 'other_income' in body:
            try:
                tax_cfg['other_income'] = float(body['other_income'] or 0)
            except (TypeError, ValueError):
                pass
        if 'has_help_debt' in body:
            tax_cfg['has_help_debt'] = bool(body['has_help_debt'])
        if 'has_private_health' in body:
            tax_cfg['has_private_health'] = bool(body['has_private_health'])
        settings['tax'] = tax_cfg
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===========================================================================
# MODULE 4 — WAITLIST
# ===========================================================================

@app.route('/api/lessons/<lesson_id>/waitlist/add', methods=['POST'])
def api_waitlist_add(lesson_id):
    try:
        body = request.get_json(silent=True) or {}
        student_id = body.get('student_id', '')
        student = _student_by_id(student_id)
        if not student:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        lessons = _load_lessons()
        found = False
        for lesson in lessons:
            if lesson.get('id') == lesson_id:
                waitlist = lesson.get('waitlist') or []
                if any(w.get('student_id') == student_id for w in waitlist):
                    return jsonify({'status': 'error', 'message': 'Already on waitlist'}), 400
                waitlist.append({
                    'student_id': student_id,
                    'student_name': student.get('name', ''),
                    'added_at': now_sydney().isoformat(),
                    'notified': False,
                })
                lesson['waitlist'] = waitlist
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        _save_lessons(lessons)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/lessons/<lesson_id>/waitlist/remove', methods=['POST'])
def api_waitlist_remove(lesson_id):
    try:
        body = request.get_json(silent=True) or {}
        student_id = body.get('student_id', '')
        lessons = _load_lessons()
        found = False
        for lesson in lessons:
            if lesson.get('id') == lesson_id:
                waitlist = lesson.get('waitlist') or []
                lesson['waitlist'] = [w for w in waitlist if w.get('student_id') != student_id]
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Lesson not found'}), 404
        _save_lessons(lessons)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/waitlist/all')
def api_waitlist_all():
    entries = []
    for lesson in _load_lessons():
        for w in (lesson.get('waitlist') or []):
            entries.append({
                'lesson_id': lesson.get('id'),
                'date': lesson.get('date'),
                'start_time': lesson.get('start_time'),
                'student_id': w.get('student_id'),
                'student_name': w.get('student_name'),
                'added_at': w.get('added_at'),
                'notified': w.get('notified', False),
            })
    entries = sorted(entries, key=lambda e: (e.get('date') or '', e.get('start_time') or ''))
    return jsonify({'waitlist': entries})


_AVAIL_DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


_DEFAULT_RANGE = {"open": False, "start": "07:00", "end": "19:00"}


def _to_min(hhmm, fallback=0):
    """Parse 'HH:MM' into minutes-since-midnight; fallback on bad input."""
    try:
        h, m = [int(x) for x in str(hhmm).split(':')[:2]]
        return h * 60 + m
    except (ValueError, AttributeError, TypeError):
        return fallback


def _from_min(total):
    """Format minutes-since-midnight as 'HH:MM' (clamped to 00:00–23:59)."""
    total = max(0, min(23 * 60 + 59, int(total)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _coerce_range(val):
    """Coerce one day's value into {open, start, end}.

    Accepts the new dict form, or migrates the legacy slot-list form
    (['07:00','08:00',...]) into a min/max range.
    """
    if isinstance(val, dict) and ('open' in val or 'start' in val or 'end' in val):
        start = val.get('start') or _DEFAULT_RANGE['start']
        end = val.get('end') or _DEFAULT_RANGE['end']
        if _to_min(end) <= _to_min(start):
            end = _from_min(_to_min(start) + 60)
        return {'open': bool(val.get('open', False)), 'start': start, 'end': end}
    if isinstance(val, list) and val:
        mins = [_to_min(t) for t in val if str(t).strip()]
        if mins:
            lo, hi = min(mins), max(mins)
            # Legacy slots were start-of-block times; extend end by one hour
            # so the last slot stays inside the migrated working window.
            return {'open': True, 'start': _from_min(lo), 'end': _from_min(hi + 60)}
    return dict(_DEFAULT_RANGE)


def _normalise_ranges(raw):
    """Coerce an incoming availability dict into {day: {open,start,end}} for all 7 days."""
    raw = raw or {}
    slots = raw.get('slots') if isinstance(raw, dict) else None
    out = {}
    for day in _AVAIL_DAYS:
        if day in raw:
            out[day] = _coerce_range(raw.get(day))
        elif isinstance(slots, dict) and day in slots:
            out[day] = _coerce_range(slots.get(day))
        else:
            out[day] = dict(_DEFAULT_RANGE)
    return out


def _builtin_preset_ranges(preset_id):
    """Return a {day: {open,start,end}} dict for a built-in preset id, or None."""
    weekdays = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')
    weekend = ('saturday', 'sunday')
    presets = {
        'weekday_mornings': (weekdays, '07:00', '12:00'),
        'weekday_evenings': (weekdays, '16:00', '20:00'),
        'weekends': (weekend, '08:00', '16:00'),
        'every_day': (_AVAIL_DAYS, '07:00', '20:00'),
        'after_school': (weekdays, '15:30', '19:00'),
    }
    spec = presets.get(preset_id)
    if not spec:
        return None
    days, start, end = spec
    ranges = {}
    for day in _AVAIL_DAYS:
        if day in days:
            ranges[day] = {'open': True, 'start': start, 'end': end}
        else:
            ranges[day] = {'open': False, 'start': start, 'end': end}
    return ranges


def _get_availability(settings):
    """Return (ranges, custom_presets), migrating any legacy slot data."""
    avail = settings.get('availability', {}) or {}
    return _normalise_ranges(avail), (avail.get('custom_presets') or {})


def _store_ranges(settings, ranges):
    """Persist normalised ranges back onto settings['availability'] (drops legacy keys)."""
    avail = settings.get('availability', {}) or {}
    cleaned = {day: ranges[day] for day in _AVAIL_DAYS}
    cleaned['custom_presets'] = avail.get('custom_presets') or {}
    settings['availability'] = cleaned
    return cleaned


@app.route('/api/availability/save', methods=['POST'])
def api_availability_save():
    try:
        body = request.get_json(silent=True) or {}
        incoming = body.get('ranges', body.get('availability', body))
        settings = load_settings()
        ranges = _normalise_ranges(incoming)
        _store_ranges(settings, ranges)
        save_json(SETTINGS_PATH, settings)
        log_info("Availability saved.")
        return jsonify({'status': 'ok', 'ranges': ranges})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/availability/apply-preset', methods=['POST'])
def api_availability_apply_preset():
    try:
        body = request.get_json(silent=True) or {}
        preset_id = (body.get('preset_id') or body.get('preset') or '').strip()
        settings = load_settings()
        avail = settings.get('availability', {}) or {}
        ranges = _builtin_preset_ranges(preset_id)
        if ranges is None:
            entry = (avail.get('custom_presets') or {}).get(preset_id)
            if isinstance(entry, dict):
                source = entry.get('ranges', entry.get('slots', entry))
                ranges = _normalise_ranges(source)
        if ranges is None:
            return jsonify({'status': 'error', 'message': 'Unknown preset'}), 404
        ranges = _normalise_ranges(ranges)
        _store_ranges(settings, ranges)
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'ranges': ranges})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/availability/save-preset', methods=['POST'])
def api_availability_save_preset():
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        if not name:
            return jsonify({'status': 'error', 'message': 'Preset name required'}), 400
        settings = load_settings()
        avail = settings.get('availability', {}) or {}
        ranges = _normalise_ranges(body.get('ranges', avail))
        presets = avail.get('custom_presets')
        if not isinstance(presets, dict):
            presets = {}
        presets[name] = {'name': name, 'ranges': ranges}
        avail['custom_presets'] = presets
        settings['availability'] = avail
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'custom_presets': presets})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/availability/preset/<name>', methods=['DELETE'])
def api_availability_delete_preset(name):
    try:
        settings = load_settings()
        avail = settings.get('availability', {}) or {}
        presets = avail.get('custom_presets')
        if isinstance(presets, dict) and name in presets:
            del presets[name]
            avail['custom_presets'] = presets
            settings['availability'] = avail
            save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ===========================================================================
# MODULE 5 — LESSON PACKAGES
# ===========================================================================

@app.route('/packages')
def packages_page():
    settings = load_settings()
    return render_template('packages.html', settings=settings, package_types=PACKAGE_TYPES)


@app.route('/api/packages')
def api_packages():
    status_f = request.args.get('filter', 'all')
    packages = _load_packages()
    if status_f == 'active':
        packages = [p for p in packages if p.get('active')]
    elif status_f == 'completed':
        packages = [p for p in packages if not p.get('active')]
    packages = sorted(packages, key=lambda p: p.get('created_at', ''), reverse=True)
    return jsonify({'packages': packages, 'package_types': PACKAGE_TYPES})


@app.route('/api/packages/alerts')
def api_packages_alerts():
    return jsonify({'alerts': _package_alerts()})


@app.route('/api/packages/create', methods=['POST'])
def api_packages_create():
    try:
        body = request.get_json(silent=True) or {}
        student = _student_by_id(body.get('student_id', ''))
        if not student:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        total_sessions = int(body.get('total_sessions', 0) or 0)
        if total_sessions <= 0:
            return jsonify({'status': 'error', 'message': 'Sessions must be greater than zero'}), 400
        price_per_session = float(body.get('price_per_session', 0) or 0)
        total_price = float(body.get('total_price', round(price_per_session * total_sessions, 2)) or 0)
        amount_paid = float(body.get('amount_paid', total_price) or 0)
        package = {
            'id': uuid.uuid4().hex,
            'student_id': student['id'],
            'student_name': student.get('name', ''),
            'package_name': body.get('package_name', f"{total_sessions}-Lesson Pack"),
            'total_sessions': total_sessions,
            'sessions_used': 0,
            'sessions_remaining': total_sessions,
            'price_per_session': round(price_per_session, 2),
            'total_price': round(total_price, 2),
            'amount_paid': round(amount_paid, 2),
            'purchase_date': body.get('purchase_date', today_str()),
            'expiry_date': body.get('expiry_date') or None,
            'active': True,
            'notes': body.get('notes', ''),
            'created_at': now_sydney().isoformat(),
        }
        packages = _load_packages()
        packages.append(package)
        _save_packages(packages)
        log_info(f"Package created: {package['package_name']} for {package['student_name']}")
        return jsonify({'status': 'ok', 'package': package})
    except Exception as e:
        log_error(f"Create package error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/packages/<package_id>/use-session', methods=['POST'])
def api_packages_use_session(package_id):
    try:
        packages = _load_packages()
        found = None
        for p in packages:
            if p.get('id') == package_id:
                used = int(p.get('sessions_used', 0) or 0) + 1
                total = int(p.get('total_sessions', 0) or 0)
                remaining = max(0, total - used)
                p['sessions_used'] = used
                p['sessions_remaining'] = remaining
                if remaining <= 0:
                    p['active'] = False
                found = p
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Package not found'}), 404
        _save_packages(packages)
        return jsonify({'status': 'ok', 'package': found})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/packages/<package_id>/edit', methods=['POST'])
def api_packages_edit(package_id):
    try:
        body = request.get_json(silent=True) or {}
        packages = _load_packages()
        found = False
        for p in packages:
            if p.get('id') == package_id:
                for key in ('package_name', 'notes', 'purchase_date', 'expiry_date'):
                    if key in body:
                        p[key] = body[key]
                if 'total_sessions' in body:
                    p['total_sessions'] = int(body['total_sessions'] or 0)
                if 'sessions_used' in body:
                    p['sessions_used'] = int(body['sessions_used'] or 0)
                if 'price_per_session' in body:
                    p['price_per_session'] = float(body['price_per_session'] or 0)
                if 'total_price' in body:
                    p['total_price'] = float(body['total_price'] or 0)
                if 'amount_paid' in body:
                    p['amount_paid'] = float(body['amount_paid'] or 0)
                p['sessions_remaining'] = max(0, int(p.get('total_sessions', 0)) - int(p.get('sessions_used', 0)))
                p['active'] = p['sessions_remaining'] > 0 if 'active' not in body else bool(body['active'])
                found = True
                break
        if not found:
            return jsonify({'status': 'error', 'message': 'Package not found'}), 404
        _save_packages(packages)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/packages/<package_id>', methods=['DELETE'])
def api_packages_delete(package_id):
    try:
        packages = _load_packages()
        remaining = [p for p in packages if p.get('id') != package_id]
        if len(remaining) == len(packages):
            return jsonify({'status': 'error', 'message': 'Package not found'}), 404
        _save_packages(remaining)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/packages/student/<student_id>')
def api_packages_student(student_id):
    packages = [p for p in _load_packages() if p.get('student_id') == student_id]
    packages = sorted(packages, key=lambda p: p.get('created_at', ''), reverse=True)
    return jsonify({'packages': packages})


# ===========================================================================
# MODULE — SMS CONTACT MANAGER
# ===========================================================================

@app.route('/sms')
def sms_page():
    settings = load_settings()
    data = coach_sms.load_data()
    return render_template(
        'sms.html',
        settings=settings,
        contacts=data.get('contacts', []),
        groups=data.get('groups', []),
        templates=data.get('templates', []),
        twilio_ready=bool((settings.get('twilio', {}) or {}).get('account_sid')),
        active_page='sms',
    )


@app.route('/api/sms/contacts')
def api_sms_contacts():
    return jsonify({'contacts': coach_sms.load_data().get('contacts', [])})


@app.route('/api/sms/contacts/add', methods=['POST'])
def api_sms_contacts_add():
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        phone = (body.get('phone') or '').strip()
        if not name or not phone:
            return jsonify({'status': 'error', 'message': 'Name and phone required'}), 400
        contact = coach_sms.add_contact(
            name, phone,
            tags=body.get('tags') or [],
            student_id=body.get('student_id'),
            notes=body.get('notes', ''),
        )
        return jsonify({'status': 'ok', 'contact': contact})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/contacts/<contact_id>/edit', methods=['POST'])
def api_sms_contacts_edit(contact_id):
    try:
        body = request.get_json(silent=True) or {}
        data = coach_sms.load_data()
        contact = next((c for c in data['contacts'] if c.get('id') == contact_id), None)
        if not contact:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404
        if 'name' in body:
            contact['name'] = (body['name'] or '').strip()
        if 'phone' in body:
            contact['phone'] = coach_sms.format_phone_au(body['phone'])
        if 'tags' in body:
            contact['tags'] = body['tags'] or []
        if 'notes' in body:
            contact['notes'] = body['notes'] or ''
        if 'active' in body:
            contact['active'] = bool(body['active'])
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'contact': contact})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/contacts/<contact_id>', methods=['DELETE'])
def api_sms_contacts_delete(contact_id):
    try:
        data = coach_sms.load_data()
        before = len(data['contacts'])
        data['contacts'] = [c for c in data['contacts'] if c.get('id') != contact_id]
        if len(data['contacts']) == before:
            return jsonify({'status': 'error', 'message': 'Contact not found'}), 404
        for g in data['groups']:
            g['contact_ids'] = [cid for cid in g.get('contact_ids', []) if cid != contact_id]
        coach_sms.save_data(data)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/contacts/import-students', methods=['POST'])
def api_sms_import_students():
    try:
        data = coach_sms.load_data()
        existing_sids = {c.get('student_id') for c in data['contacts'] if c.get('student_id')}
        existing_phones = {c.get('phone') for c in data['contacts']}
        added = 0
        for s in _load_students():
            if not s.get('active', True):
                continue
            phone = (s.get('phone') or s.get('parent_phone') or '').strip()
            if not phone:
                continue
            sid = s.get('id')
            e164 = coach_sms.format_phone_au(phone)
            if sid in existing_sids or e164 in existing_phones:
                continue
            data['contacts'].append({
                'id': uuid.uuid4().hex,
                'name': s.get('name', 'Student'),
                'phone': e164,
                'tags': [s.get('level')] if s.get('level') else [],
                'student_id': sid,
                'notes': '',
                'active': True,
                'created_at': datetime.now().isoformat(),
            })
            existing_phones.add(e164)
            added += 1
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'added': added, 'contacts': data['contacts']})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/groups')
def api_sms_groups():
    return jsonify({'groups': coach_sms.load_data().get('groups', [])})


@app.route('/api/sms/groups/add', methods=['POST'])
def api_sms_groups_add():
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        if not name:
            return jsonify({'status': 'error', 'message': 'Group name required'}), 400
        data = coach_sms.load_data()
        group = {
            'id': uuid.uuid4().hex,
            'name': name,
            'description': body.get('description', ''),
            'colour': body.get('colour') or '#00c88a',
            'contact_ids': body.get('contact_ids') or [],
            'created_at': datetime.now().isoformat(),
        }
        data['groups'].append(group)
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'group': group})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/groups/<group_id>/edit', methods=['POST'])
def api_sms_groups_edit(group_id):
    try:
        body = request.get_json(silent=True) or {}
        data = coach_sms.load_data()
        group = next((g for g in data['groups'] if g.get('id') == group_id), None)
        if not group:
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        for key in ('name', 'description', 'colour'):
            if key in body:
                group[key] = body[key]
        if 'contact_ids' in body:
            group['contact_ids'] = body['contact_ids'] or []
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'group': group})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/groups/<group_id>', methods=['DELETE'])
def api_sms_groups_delete(group_id):
    try:
        data = coach_sms.load_data()
        before = len(data['groups'])
        data['groups'] = [g for g in data['groups'] if g.get('id') != group_id]
        if len(data['groups']) == before:
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        coach_sms.save_data(data)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/groups/<group_id>/add-contact', methods=['POST'])
def api_sms_groups_add_contact(group_id):
    try:
        body = request.get_json(silent=True) or {}
        cid = body.get('contact_id')
        data = coach_sms.load_data()
        group = next((g for g in data['groups'] if g.get('id') == group_id), None)
        if not group:
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        if cid and cid not in group.get('contact_ids', []):
            group.setdefault('contact_ids', []).append(cid)
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'group': group})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/groups/<group_id>/remove-contact', methods=['POST'])
def api_sms_groups_remove_contact(group_id):
    try:
        body = request.get_json(silent=True) or {}
        cid = body.get('contact_id')
        data = coach_sms.load_data()
        group = next((g for g in data['groups'] if g.get('id') == group_id), None)
        if not group:
            return jsonify({'status': 'error', 'message': 'Group not found'}), 404
        group['contact_ids'] = [c for c in group.get('contact_ids', []) if c != cid]
        coach_sms.save_data(data)
        return jsonify({'status': 'ok', 'group': group})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/send', methods=['POST'])
def api_sms_send():
    try:
        body = request.get_json(silent=True) or {}
        recipient_type = body.get('recipient_type', 'contact')
        recipient_id = body.get('recipient_id')
        message_text = (body.get('message_text') or '').strip()
        personalise = bool(body.get('personalise', True))
        template_id = body.get('template_id')
        if not message_text:
            return jsonify({'status': 'error', 'message': 'Message text required'}), 400
        data = coach_sms.load_data()
        recipients = coach_sms.resolve_recipients(data, recipient_type, recipient_id)
        if not recipients:
            return jsonify({'status': 'error', 'message': 'No recipients selected'}), 400
        result = coach_sms.send_bulk_sms(recipients, message_text, personalise,
                                         extra=body.get('variables') or {})
        if recipient_type == 'group':
            group = next((g for g in data['groups'] if g.get('id') == recipient_id), None)
            rname = group['name'] if group else 'Group'
        elif recipient_type == 'all':
            rname = 'All contacts'
        else:
            rname = ', '.join(coach_sms.get_contact_first_name(c.get('name', '')) for c in recipients)
        status = 'sent' if result['failed'] == 0 else ('partial' if result['sent'] else 'failed')
        coach_sms.log_message(
            message_text, recipient_type, recipient_id, rname,
            len(recipients), status, result['sids'], template_id,
        )
        log_info(f"SMS send: {result['sent']} sent, {result['failed']} failed to {rname}.")
        return jsonify({'status': 'ok', 'result': result})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/history')
def api_sms_history():
    messages = coach_sms.load_data().get('messages', [])
    return jsonify({'messages': messages[:50]})


@app.route('/api/sms/templates')
def api_sms_templates():
    return jsonify({'templates': coach_sms.load_data().get('templates', [])})


@app.route('/api/sms/templates/save', methods=['POST'])
def api_sms_templates_save():
    try:
        body = request.get_json(silent=True) or {}
        templates = body.get('templates')
        if not isinstance(templates, list):
            return jsonify({'status': 'error', 'message': 'templates must be a list'}), 400
        data = coach_sms.load_data()
        data['templates'] = templates
        coach_sms.save_data(data)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sms/test', methods=['POST'])
def api_sms_test():
    try:
        body = request.get_json(silent=True) or {}
        to = (body.get('to') or '').strip()
        if not to:
            return jsonify({'status': 'error', 'message': 'Recipient number required'}), 400
        result = coach_sms.send_sms(to, body.get('message') or 'Test message from Ivan. SMS is working!')
        if result['success']:
            return jsonify({'status': 'ok', 'sid': result['sid']})
        return jsonify({'status': 'error', 'message': result['error']}), 502
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/settings/twilio-test', methods=['POST'])
def api_settings_twilio_test():
    try:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict) and (body.get('account_sid') or body.get('auth_token') or body.get('from_number')):
            settings = load_settings()
            tw = settings.get('twilio', {}) or {}
            for key in ('account_sid', 'auth_token', 'from_number'):
                if body.get(key):
                    tw[key] = (body[key] or '').strip()
            settings['twilio'] = tw
            save_json(SETTINGS_PATH, settings)
        result = coach_sms.test_twilio_connection()
        code = 200 if result['success'] else 400
        return jsonify({'status': 'ok' if result['success'] else 'error',
                        'message': result['message']}), code
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

_BACKUP_DATA_FILES = [
    'students.json', 'lessons.json', 'invoices.json',
    'expenses.json', 'packages.json', 'sms_contacts.json',
    'sms_groups.json', 'sms_history.json',
]
_BACKUP_CONFIG_FILES = ['settings.json']


def _backup_meta() -> dict:
    settings = load_settings()
    backup = settings.get('backup') or {}
    last = backup.get('last_backup_at')
    remind = int(backup.get('remind_after_days', 30) or 30)
    days_since = None
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            days_since = (now_sydney().replace(tzinfo=None) - last_dt.replace(tzinfo=None)).days
        except (TypeError, ValueError):
            days_since = None
    snoozed_until = backup.get('snoozed_until')
    return {
        'last_backup': last,
        'days_since': days_since,
        'remind_after_days': remind,
        'snoozed_until': snoozed_until,
    }


@app.route('/api/backup/status')
def api_backup_status():
    meta = _backup_meta()
    days = meta['days_since']
    remind = meta['remind_after_days']
    snoozed_until = meta.get('snoozed_until')
    snoozed = bool(snoozed_until and snoozed_until >= today_str())
    needs_backup = (days is None) or (days >= remind)
    return jsonify({
        'last_backup': meta['last_backup'],
        'days_since': days,
        'remind_after_days': remind,
        'needs_backup': needs_backup and not snoozed,
    })


@app.route('/api/backup/download')
def api_backup_download():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in _BACKUP_DATA_FILES:
            path = os.path.join(DATA_DIR, name)
            if os.path.exists(path):
                zf.write(path, arcname=f'data/{name}')
        for name in _BACKUP_CONFIG_FILES:
            path = os.path.join(CONFIG_DIR, name)
            if os.path.exists(path):
                zf.write(path, arcname=f'config/{name}')
        invoices_dir = os.path.join(app.static_folder, 'invoices')
        if os.path.isdir(invoices_dir):
            for fname in os.listdir(invoices_dir):
                fpath = os.path.join(invoices_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=f'static/invoices/{fname}')
    buffer.seek(0)

    settings = load_settings()
    settings.setdefault('backup', {})
    settings['backup']['last_backup_at'] = now_sydney().isoformat()
    settings['backup'].pop('snoozed_until', None)
    save_json(SETTINGS_PATH, settings)

    fname = f"ivan_backup_{now_sydney().strftime('%Y-%m-%d')}.zip"
    return send_file(buffer, mimetype='application/zip',
                     as_attachment=True, download_name=fname)


@app.route('/api/backup/restore', methods=['POST'])
def api_backup_restore():
    try:
        upload = request.files.get('file')
        if not upload:
            return jsonify({'status': 'error', 'message': 'No file uploaded'}), 400
        restored = []
        with zipfile.ZipFile(upload.stream) as zf:
            for member in zf.namelist():
                if member.endswith('/') or '..' in member:
                    continue
                base = os.path.basename(member)
                if member.startswith('data/') and base in _BACKUP_DATA_FILES:
                    dest = os.path.join(DATA_DIR, base)
                elif member.startswith('config/') and base in _BACKUP_CONFIG_FILES:
                    dest = os.path.join(CONFIG_DIR, base)
                elif member.startswith('static/invoices/'):
                    inv_dir = os.path.join(app.static_folder, 'invoices')
                    os.makedirs(inv_dir, exist_ok=True)
                    dest = os.path.join(inv_dir, base)
                else:
                    continue
                with zf.open(member) as src, open(dest, 'wb') as out:
                    out.write(src.read())
                restored.append(member)
        return jsonify({'status': 'ok', 'restored': restored, 'count': len(restored)})
    except zipfile.BadZipFile:
        return jsonify({'status': 'error', 'message': 'Not a valid backup ZIP file'}), 400
    except Exception as e:
        log_error(f"Backup restore error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/backup/snooze', methods=['POST'])
def api_backup_snooze():
    try:
        settings = load_settings()
        settings.setdefault('backup', {})
        snooze_until = (now_sydney().date() + timedelta(days=7)).isoformat()
        settings['backup']['snoozed_until'] = snooze_until
        save_json(SETTINGS_PATH, settings)
        return jsonify({'status': 'ok', 'snoozed_until': snooze_until})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# PWA
# ---------------------------------------------------------------------------

@app.route('/manifest.json')
def pwa_manifest():
    return send_from_directory(app.static_folder, 'manifest.json', mimetype='application/manifest+json')


@app.route('/sw.js')
def pwa_service_worker():
    return send_from_directory(app.static_folder, 'sw.js', mimetype='application/javascript')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9999, debug=False)
