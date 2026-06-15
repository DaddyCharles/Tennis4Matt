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
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
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
        user_title=settings.get('user_title', 'Mr'),
        user_name=settings.get('user_name', 'Scheers'),
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
    """Leads table page with filters."""
    leads = _recent_leads(_load_leads(), MAX_LEADS_RETURNED)
    groups = load_json(GROUPS_PATH, {"groups": []}).get('groups', [])
    return render_template('leads.html', leads=leads, groups=groups)


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
    return render_template(
        'settings.html',
        settings=settings,
        session_status=_session_status(),
        session_created_at=created,
        session_expiry=expiry,
        startup_enabled=_startup_enabled(),
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

        settings['user_title'] = (body.get('user_title', settings.get('user_title', 'Mr')) or '').strip()
        settings['user_name'] = (body.get('user_name', settings.get('user_name', 'Scheers')) or '').strip()
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

@app.route('/coach')
def coach_index():
    """Home (Today) view: weather strip, today's lessons, quick stats."""
    settings = load_settings()
    return render_template(
        'coach_index.html',
        settings=settings,
        coach_title=settings.get('coach_title', 'Mr'),
        coach_name=settings.get('coach_name', 'Matt'),
        user_title=settings.get('user_title', 'Mr'),
        user_name=settings.get('user_name', 'Scheers'),
        greeting=_time_greeting(),
        today=today_str(),
        today_long=now_sydney().strftime('%A, %-d %B %Y'),
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
        student = _student_by_id(student_id)
        blocks = int(body.get('blocks', 2) or 2)
        price = body.get('price')
        if price in (None, ''):
            price = coach_earnings.get_price_for_blocks(blocks)
        base = {
            'id': uuid.uuid4().hex,
            'student_id': student_id,
            'student_name': (student or {}).get('name', body.get('student_name', '')),
            'date': body.get('date', today_str()),
            'start_time': body.get('start_time', '09:00'),
            'blocks': blocks,
            'duration_minutes': blocks_to_minutes(blocks),
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
        if base['recurring'] and body.get('recurring_rule'):
            new_items = _generate_recurring(base, body.get('recurring_rule'))
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
            'phone': body.get('phone', ''),
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
                        student[key] = body[key]
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


@app.route('/api/earnings/prices', methods=['POST'])
def api_earnings_prices():
    try:
        body = request.get_json(silent=True) or {}
        settings = load_settings()
        prices = settings.get('lesson_prices', {}) or {}
        for key in ('30min', '60min', '90min', '120min'):
            if key in body:
                try:
                    prices[key] = float(body[key])
                except (TypeError, ValueError):
                    pass
        settings['lesson_prices'] = prices
        save_json(SETTINGS_PATH, settings)
        log_info("Lesson prices updated.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
            'anthropic_api_key', 'working_hours_start', 'working_hours_end',
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
        if isinstance(body.get('lesson_prices'), dict):
            prices = settings.get('lesson_prices', {}) or {}
            for key, val in body['lesson_prices'].items():
                try:
                    prices[key] = float(val)
                except (TypeError, ValueError):
                    pass
            settings['lesson_prices'] = prices
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
        save_json(SETTINGS_PATH, settings)
        log_info("Coach settings saved.")
        return jsonify({'status': 'ok'})
    except Exception as e:
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


@app.route('/api/settings/test-ai', methods=['POST'])
def api_settings_test_ai():
    if not ai_helper.ai_available():
        return jsonify({'status': 'error', 'message': 'No API key set.'}), 400
    try:
        reply = ai_helper._call("Reply with exactly: OK")
        if reply:
            return jsonify({'status': 'ok', 'message': 'AI connection works.'})
        return jsonify({'status': 'error', 'message': 'AI did not respond. Check your API key.'}), 502
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
    """Expose settings + business badges to every template (incl. base.html)."""
    try:
        return {
            'settings': load_settings(),
            'invoice_stats': _invoice_summary(),
            'package_alerts': len(_package_alerts()),
            'sms_stats': {'unread': 0},
        }
    except Exception:
        return {
            'settings': {},
            'invoice_stats': {'overdue_count': 0},
            'package_alerts': 0,
            'sms_stats': {'unread': 0},
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


@app.route('/api/availability/save', methods=['POST'])
def api_availability_save():
    try:
        body = request.get_json(silent=True) or {}
        availability = body.get('availability', body)
        settings = load_settings()
        days = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
        current = settings.get('availability', {}) or {}
        for day in days:
            if day in availability:
                slots = availability[day]
                if isinstance(slots, list):
                    current[day] = slots
        settings['availability'] = current
        save_json(SETTINGS_PATH, settings)
        log_info("Availability saved.")
        return jsonify({'status': 'ok', 'availability': current})
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
