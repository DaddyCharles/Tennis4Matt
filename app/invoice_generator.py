"""PDF invoice generation for the Invoicing module (reportlab).

build_invoice_pdf(invoice, settings) returns the invoice as PDF bytes. Layout is
a single A4 page: dark navy header band, billing details, a line-item table, a
totals block, payment details, and a thank-you footer. Helvetica only (built in).
"""

import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

NAVY = HexColor("#0a1628")
TEAL = HexColor("#00c88a")
INK = HexColor("#1a2230")
MUTED = HexColor("#6b7785")
LINE = HexColor("#d9dee6")
WHITE = HexColor("#ffffff")


def _fmt_date(value: str) -> str:
    """Render a 'YYYY-MM-DD' string as '20 Jan 2025'."""
    if not value:
        return ""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except (ValueError, TypeError):
        return value


def _to12h(hhmm: str) -> str:
    if not hhmm:
        return ""
    try:
        h, m = [int(x) for x in hhmm.split(":")[:2]]
    except (ValueError, AttributeError):
        return hhmm
    ap = "am" if h < 12 else "pm"
    hr = ((h + 11) % 12) + 1
    return f"{hr}:{m:02d}{ap}"


def _money(amount) -> str:
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def build_invoice_pdf(invoice: dict, settings: dict) -> bytes:
    """Return a one-page A4 PDF invoice as bytes."""
    settings = settings or {}
    inv_cfg = settings.get("invoicing", {}) or {}
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    left = 20 * mm
    right = page_w - 20 * mm

    # ---- Header band ----------------------------------------------------
    band_h = 42 * mm
    c.setFillColor(NAVY)
    c.rect(0, page_h - band_h, page_w, band_h, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.rect(0, page_h - band_h, page_w, 2.2 * mm, fill=1, stroke=0)

    coach_name = settings.get("coach_name", "") or "Tennis Coach"
    court_name = settings.get("court_name", "") or ""
    address = inv_cfg.get("coach_address", "") or settings.get("court_address", "") or ""
    abn = inv_cfg.get("coach_abn", "") or ""

    y = page_h - 16 * mm
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, y, coach_name)
    y -= 6 * mm
    c.setFont("Helvetica", 9.5)
    c.setFillColor(HexColor("#9fb0c3"))
    if court_name:
        c.drawString(left, y, court_name)
        y -= 5 * mm
    if address:
        c.drawString(left, y, address)
        y -= 5 * mm
    if abn:
        c.drawString(left, y, f"ABN: {abn}")

    # Right side: INVOICE title + meta
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(right, page_h - 17 * mm, "INVOICE")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 9.5)
    meta_y = page_h - 24 * mm
    for label, value in (
        (invoice.get("invoice_number", ""), None),
        ("Issued", _fmt_date(invoice.get("issue_date", ""))),
        ("Due", _fmt_date(invoice.get("due_date", ""))),
    ):
        if value is None:
            c.setFont("Helvetica-Bold", 10)
            c.drawRightString(right, meta_y, str(label))
            c.setFont("Helvetica", 9.5)
        else:
            c.setFillColor(HexColor("#9fb0c3"))
            c.drawRightString(right, meta_y, f"{label}: {value}")
            c.setFillColor(WHITE)
        meta_y -= 5 * mm

    # ---- Bill to --------------------------------------------------------
    y = page_h - band_h - 14 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, y, "BILL TO")
    y -= 6 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, invoice.get("student_name", "") or "Student")
    phone = invoice.get("student_phone", "") or ""
    if phone:
        y -= 5 * mm
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9.5)
        c.drawString(left, y, phone)

    # ---- Line item table ------------------------------------------------
    table_top = y - 12 * mm
    row_h = 9 * mm
    c.setFillColor(NAVY)
    c.rect(left, table_top - row_h, right - left, row_h, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(left + 4 * mm, table_top - row_h + 3 * mm, "DESCRIPTION")
    c.drawRightString(right - 4 * mm, table_top - row_h + 3 * mm, "AMOUNT")

    # Description rows
    desc = invoice.get("description", "Tennis coaching session")
    detail_bits = []
    if invoice.get("lesson_date"):
        detail_bits.append(_fmt_date(invoice.get("lesson_date")))
    if invoice.get("lesson_start"):
        detail_bits.append(_to12h(invoice.get("lesson_start")))
    dur = invoice.get("lesson_duration_minutes")
    if dur:
        detail_bits.append(f"{dur} minutes")
    detail = "  -  ".join(detail_bits)

    body_y = table_top - row_h - 8 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica", 10.5)
    c.drawString(left + 4 * mm, body_y, desc)
    c.drawRightString(right - 4 * mm, body_y, _money(invoice.get("amount_ex_gst", invoice.get("amount_total", 0))))
    if detail:
        body_y -= 5.5 * mm
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(left + 4 * mm, body_y, detail)

    # Divider
    body_y -= 7 * mm
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(left, body_y, right, body_y)

    # ---- Totals ---------------------------------------------------------
    gst = float(invoice.get("gst_amount", 0) or 0)
    ty = body_y - 8 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawRightString(right - 38 * mm, ty, "Subtotal")
    c.setFillColor(INK)
    c.drawRightString(right - 4 * mm, ty, _money(invoice.get("amount_ex_gst", invoice.get("amount_total", 0))))
    ty -= 6 * mm
    c.setFillColor(MUTED)
    c.drawRightString(right - 38 * mm, ty, "GST")
    c.setFillColor(INK)
    c.drawRightString(right - 4 * mm, ty, _money(gst))

    ty -= 9 * mm
    c.setFillColor(NAVY)
    c.rect(right - 78 * mm, ty - 2 * mm, 78 * mm, 11 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(right - 74 * mm, ty + 1.5 * mm, "TOTAL")
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(right - 4 * mm, ty + 1 * mm, _money(invoice.get("amount_total", 0)))

    # ---- Payment details ------------------------------------------------
    py = ty - 22 * mm
    c.setStrokeColor(LINE)
    c.line(left, py + 8 * mm, right, py + 8 * mm)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, py, "PAYMENT DETAILS")
    py -= 6 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica", 9.5)
    bank = inv_cfg.get("bank_name", "") or ""
    bsb = inv_cfg.get("bank_bsb", "") or ""
    acct = inv_cfg.get("bank_account", "") or ""
    rows = []
    if bank:
        rows.append(f"Bank: {bank}")
    if bsb:
        rows.append(f"BSB: {bsb}")
    if acct:
        rows.append(f"Account: {acct}")
    rows.append(f"Reference: {invoice.get('invoice_number', '')}")
    if not (bank or bsb or acct):
        rows.insert(0, "Add your bank details in Settings > Invoicing.")
    for line_text in rows:
        c.drawString(left, py, line_text)
        py -= 5 * mm

    # ---- Footer ---------------------------------------------------------
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(page_w / 2, 18 * mm, "Thank you for your business!")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    terms = inv_cfg.get("payment_terms_days", 7)
    c.drawCentredString(page_w / 2, 13 * mm, f"Payment due within {terms} days of the issue date.")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()
