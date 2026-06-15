"""SMS contact manager — bulk SMS to students/groups via Twilio.

Reads Twilio credentials from settings.json. All contact, group, message-log and
template data lives in data/sms_contacts.json. Phone numbers are normalised to
E.164 (+61...) for Australian mobiles before sending. The twilio package is
imported lazily so the rest of the app keeps working if it is not installed.
"""

import os
import re
import time
import uuid
from datetime import datetime

from bot.logger import DATA_DIR, load_json, load_settings, log_error, log_info, save_json

SMS_CONTACTS_PATH = os.path.join(DATA_DIR, 'sms_contacts.json')

# Approximate Twilio cost per SMS segment to Australia, in AUD.
COST_PER_SMS_AUD = 0.08

_DEFAULT_TEMPLATES = [
    {"id": "rain_cancel", "label": "Rain Cancellation",
     "text": "Hi {name}, today's lesson at {time} has been cancelled due to weather. I'll be in touch to reschedule. Apologies for the inconvenience!"},
    {"id": "reschedule", "label": "Reschedule",
     "text": "Hi {name}, can we reschedule your lesson to {date} at {time}? Please reply to confirm."},
    {"id": "reminder", "label": "Lesson Reminder",
     "text": "Hi {name}, just a reminder of your lesson tomorrow at {time}. See you on court!"},
    {"id": "christmas", "label": "Christmas Message",
     "text": "Merry Christmas {name}! Thank you for a wonderful year of tennis. Wishing you and your family a happy and healthy holiday season. See you in the new year!"},
    {"id": "payment", "label": "Payment Reminder",
     "text": "Hi {name}, just a friendly reminder that your lesson payment of ${amount} is outstanding. Please transfer to BSB {bsb} Acc {account}. Thanks!"},
    {"id": "custom", "label": "Custom Message", "text": ""},
]


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def _default_data() -> dict:
    return {"contacts": [], "groups": [], "messages": [], "templates": list(_DEFAULT_TEMPLATES)}


def load_data() -> dict:
    """Return the full SMS data store, healing missing keys."""
    data = load_json(SMS_CONTACTS_PATH, _default_data())
    for key in ("contacts", "groups", "messages"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not data.get("templates"):
        data["templates"] = list(_DEFAULT_TEMPLATES)
    return data


def save_data(data: dict) -> bool:
    return save_json(SMS_CONTACTS_PATH, data)


# ---------------------------------------------------------------------------
# Phone / name helpers
# ---------------------------------------------------------------------------

def format_phone_au(phone: str) -> str:
    """Convert an Australian phone number to E.164 (+61...) format.

    0412 345 678 -> +61412345678. Handles spaces, dashes, brackets and numbers
    already in +61 / 61 form. Returns the cleaned string; non-AU input is
    returned best-effort with a leading +.
    """
    if not phone:
        return ""
    raw = str(phone).strip()
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("+"):
        return "+" + re.sub(r"\D", "", digits[1:])
    digits = re.sub(r"\D", "", digits)
    if digits.startswith("61"):
        return "+" + digits
    if digits.startswith("0"):
        return "+61" + digits[1:]
    if len(digits) == 9:  # 412345678 (missing leading 0)
        return "+61" + digits
    return "+" + digits


def get_contact_first_name(name: str) -> str:
    """Extract the first name from a full name for personalisation."""
    if not name:
        return "there"
    return str(name).strip().split()[0]


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------

def get_twilio_client():
    """Return a Twilio client from settings, or None if not configured."""
    settings = load_settings()
    twilio = settings.get('twilio', {}) or {}
    if not twilio.get('account_sid') or not twilio.get('auth_token'):
        return None
    try:
        from twilio.rest import Client
    except ImportError:
        log_error("Twilio package not installed. Run: pip install twilio")
        return None
    try:
        return Client(twilio['account_sid'], twilio['auth_token'])
    except Exception as e:
        log_error(f"Could not create Twilio client: {e}")
        return None


def _from_number() -> str:
    settings = load_settings()
    return (settings.get('twilio', {}) or {}).get('from_number', '')


def send_sms(to_number: str, message: str) -> dict:
    """Send a single SMS via Twilio. Returns {success, sid, error}."""
    client = get_twilio_client()
    if client is None:
        return {"success": False, "sid": "", "error": "Twilio not configured"}
    from_number = _from_number()
    if not from_number:
        return {"success": False, "sid": "", "error": "No Twilio from-number set"}
    to = format_phone_au(to_number)
    if not to:
        return {"success": False, "sid": "", "error": "Invalid phone number"}
    try:
        msg = client.messages.create(body=message, from_=from_number, to=to)
        return {"success": True, "sid": msg.sid, "error": ""}
    except Exception as e:
        return {"success": False, "sid": "", "error": str(e)}


def _apply_variables(text: str, contact: dict, extra: dict) -> str:
    """Replace {name} and other {placeholders} in a message body."""
    settings = load_settings()
    inv = settings.get('invoicing', {}) or {}
    values = {
        "name": get_contact_first_name(contact.get('name', '')),
        "time": extra.get('time', ''),
        "date": extra.get('date', ''),
        "amount": extra.get('amount', ''),
        "bsb": inv.get('bank_bsb', ''),
        "account": inv.get('bank_account', ''),
    }
    out = text
    for key, val in values.items():
        out = out.replace('{' + key + '}', str(val))
    return out


def send_bulk_sms(contacts: list, message_text: str, personalise: bool = True,
                  extra: dict = None) -> dict:
    """Send an SMS to multiple contacts.

    personalise=True replaces {name} with each contact's first name and fills
    {time}/{date}/{amount}/{bsb}/{account}. Rate-limited to 1 msg/sec (Twilio).
    Returns {sent, failed, sids, errors}.
    """
    extra = extra or {}
    sent, failed, sids, errors = 0, 0, [], []
    for i, contact in enumerate(contacts):
        body = _apply_variables(message_text, contact, extra) if personalise else message_text
        result = send_sms(contact.get('phone', ''), body)
        if result['success']:
            sent += 1
            sids.append(result['sid'])
        else:
            failed += 1
            errors.append({"name": contact.get('name', ''), "error": result['error']})
        if i < len(contacts) - 1:
            time.sleep(1)  # Twilio rate limit: max 1/sec
    return {"sent": sent, "failed": failed, "sids": sids, "errors": errors}


def test_twilio_connection() -> dict:
    """Validate the configured Twilio credentials. Returns {success, message}."""
    settings = load_settings()
    twilio = settings.get('twilio', {}) or {}
    if not twilio.get('account_sid') or not twilio.get('auth_token'):
        return {"success": False, "message": "Account SID and Auth Token required."}
    client = get_twilio_client()
    if client is None:
        return {"success": False, "message": "Could not create Twilio client (is the twilio package installed?)."}
    try:
        account = client.api.accounts(twilio['account_sid']).fetch()
        return {"success": True, "message": f"Connected to Twilio account: {account.friendly_name} ({account.status})."}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Contacts / groups / messages CRUD
# ---------------------------------------------------------------------------

def add_contact(name: str, phone: str, tags=None, student_id=None, notes="") -> dict:
    data = load_data()
    contact = {
        "id": uuid.uuid4().hex,
        "name": (name or '').strip(),
        "phone": format_phone_au(phone),
        "tags": tags or [],
        "student_id": student_id,
        "notes": notes or "",
        "active": True,
        "created_at": datetime.now().isoformat(),
    }
    data['contacts'].append(contact)
    save_data(data)
    return contact


def log_message(message_text: str, recipient_type: str, recipient_id, recipient_name: str,
                recipients_count: int, status: str, twilio_sids: list, template_used=None) -> dict:
    data = load_data()
    entry = {
        "id": uuid.uuid4().hex,
        "sent_at": datetime.now().isoformat(),
        "message_text": message_text,
        "recipient_type": recipient_type,
        "recipient_id": recipient_id,
        "recipient_name": recipient_name,
        "recipients_count": recipients_count,
        "status": status,
        "twilio_sids": twilio_sids or [],
        "template_used": template_used,
    }
    data['messages'].insert(0, entry)
    data['messages'] = data['messages'][:200]
    save_data(data)
    return entry


def resolve_recipients(data: dict, recipient_type: str, recipient_id=None) -> list:
    """Return the list of contact dicts targeted by a send request."""
    contacts = [c for c in data.get('contacts', []) if c.get('active', True)]
    if recipient_type == 'all':
        return contacts
    if recipient_type == 'group':
        group = next((g for g in data.get('groups', []) if g.get('id') == recipient_id), None)
        if not group:
            return []
        ids = set(group.get('contact_ids', []))
        return [c for c in contacts if c.get('id') in ids]
    if recipient_type == 'contact':
        ids = recipient_id if isinstance(recipient_id, list) else [recipient_id]
        idset = set(ids)
        return [c for c in contacts if c.get('id') in idset]
    return []
