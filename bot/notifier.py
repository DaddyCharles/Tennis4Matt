"""Desktop and email notifications for Ivan.

plyer provides cross-platform desktop notifications (Windows + macOS + Linux)
and is imported lazily so this module loads fine even if it is not installed.
Email is optional and gated by settings; both functions swallow their own errors
so they can never break a scan.
"""

import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from bot.logger import load_settings, log_error, log_info


def notify_desktop(title: str, message: str) -> None:
    """Send a cross-platform desktop notification; log instead if unavailable."""
    try:
        from plyer import notification  # type: ignore
        notification.notify(title=title, message=message, app_name="Ivan", timeout=10)
        log_info(f"Desktop notification sent: {title} — {message}")
    except ImportError:
        # plyer not installed — degrade gracefully to the activity log.
        log_info(f"[notify] {title}: {message}")
    except Exception as e:
        # Any platform/backend issue — never break the scan, just log it.
        log_error(f"Desktop notification failed (logged instead): {e}")
        log_info(f"[notify] {title}: {message}")


def notify_email(lead: dict) -> None:
    """Email a new lead if email notifications are enabled. Never raises."""
    settings = load_settings()
    if not settings.get('email_notifications', False):
        return

    address = settings.get('email_address', '')
    smtp_server = settings.get('email_smtp', '')
    password = settings.get('email_password', '')

    if not address or not smtp_server:
        log_error("Email notifications enabled but email_address/email_smtp not set.")
        return

    poster = lead.get('poster_name', 'Unknown')
    subject = f"New Tennis Lead — {poster}"
    body = (
        f"Poster: {poster}\n"
        f"Group: {lead.get('group_name', '')} ({lead.get('group_location', '')})\n"
        f"Matched: {lead.get('matched_keyword', '')} "
        f"[{lead.get('category_label', '')}]\n"
        f"Time: {lead.get('created_at', datetime.now().isoformat())}\n\n"
        f"Post preview:\n{lead.get('post_text', '')[:300]}\n\n"
        f"Post URL: {lead.get('post_url', '')}\n"
    )

    msg = MIMEText(body, _charset='utf-8')
    msg['Subject'] = subject
    msg['From'] = address
    msg['To'] = address

    try:
        host, _, port_str = smtp_server.partition(':')
        port = int(port_str) if port_str else 587
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass
            if password:
                server.login(address, password)
            server.sendmail(address, [address], msg.as_string())
        log_info(f"Email notification sent for lead: {poster}")
    except Exception as e:
        log_error(f"Email notification failed: {e}")
