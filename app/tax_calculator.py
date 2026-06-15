"""Australian sole-trader tax estimation (2025-26).

Pure calculation helpers used by the Tax Estimator module. All amounts are in
AUD. These are estimates only — not tax advice. Brackets, Medicare levy, LITO,
PAYG quarterly dates, and the GST threshold follow ATO 2025-26 figures.
"""

from datetime import date


def calculate_income_tax(taxable_income: float) -> float:
    """Calculate Australian individual income tax for 2025-26."""
    if taxable_income <= 18200:
        return 0
    elif taxable_income <= 45750:
        return (taxable_income - 18200) * 0.19
    elif taxable_income <= 120000:
        return 5397 + (taxable_income - 45750) * 0.325
    elif taxable_income <= 180000:
        return 29467 + (taxable_income - 120000) * 0.37
    else:
        return 51667 + (taxable_income - 180000) * 0.45


def calculate_medicare_levy(taxable_income: float) -> float:
    """2% Medicare levy."""
    if taxable_income <= 26000:
        return 0
    return taxable_income * 0.02


def get_lito(taxable_income: float) -> float:
    """Low Income Tax Offset."""
    if taxable_income <= 37500:
        return 700
    elif taxable_income <= 45000:
        return 700 - ((taxable_income - 37500) * 0.05)
    elif taxable_income <= 66667:
        return 325 - ((taxable_income - 45000) * 0.015)
    return 0


def calculate_full_tax(gross_income: float, expenses: float, other_income: float = 0) -> dict:
    """Full tax calculation for a sole trader. Returns a complete breakdown."""
    gross_income = float(gross_income or 0)
    expenses = float(expenses or 0)
    other_income = float(other_income or 0)

    net_business_profit = max(0, gross_income - expenses)
    taxable_income = net_business_profit + other_income

    income_tax = calculate_income_tax(taxable_income)
    lito = get_lito(taxable_income)
    medicare = calculate_medicare_levy(taxable_income)

    tax_payable = max(0, income_tax - lito + medicare)
    quarterly_payg = tax_payable / 4

    return {
        "gross_income": round(gross_income, 2),
        "total_expenses": round(expenses, 2),
        "net_business_profit": round(net_business_profit, 2),
        "other_income": round(other_income, 2),
        "taxable_income": round(taxable_income, 2),
        "income_tax": round(income_tax, 2),
        "lito_offset": round(lito, 2),
        "medicare_levy": round(medicare, 2),
        "estimated_tax_payable": round(tax_payable, 2),
        "quarterly_payg_estimate": round(quarterly_payg, 2),
        "effective_rate": round((tax_payable / taxable_income * 100) if taxable_income > 0 else 0, 1),
        "take_home_estimate": round(gross_income - expenses - tax_payable, 2),
        "gst_warning": gross_income >= 67500,  # warn at 90% of threshold
        "gst_required": gross_income >= 75000,
    }


def get_fy_dates():
    """Return current Australian financial year start and end dates."""
    today = date.today()
    if today.month >= 7:
        return date(today.year, 7, 1), date(today.year + 1, 6, 30)
    return date(today.year - 1, 7, 1), date(today.year, 6, 30)


def get_next_payg_date():
    """Return next PAYG quarterly due date and quarter label.

    Quarterly instalment due dates for sole traders:
      Q1 (Jul-Sep) -> 28 Oct
      Q2 (Oct-Dec) -> 28 Feb
      Q3 (Jan-Mar) -> 28 Apr
      Q4 (Apr-Jun) -> 28 Jun
    """
    today = date.today()
    year = today.year
    # Candidate due dates ordered through the calendar year.
    candidates = [
        ("Q2", date(year, 2, 28)),
        ("Q3", date(year, 4, 28)),
        ("Q4", date(year, 6, 28)),
        ("Q1", date(year, 10, 28)),
        ("Q2", date(year + 1, 2, 28)),
    ]
    for label, due in candidates:
        if due >= today:
            return {
                "quarter": label,
                "due_date": due.isoformat(),
                "due_label": due.strftime("%d %B %Y"),
            }
    # Fallback (should not be reached).
    due = date(year + 1, 2, 28)
    return {"quarter": "Q2", "due_date": due.isoformat(), "due_label": due.strftime("%d %B %Y")}


def get_days_in_fy_elapsed():
    """Return (days_elapsed, total_days) for a financial-year progress bar."""
    start, end = get_fy_dates()
    today = date.today()
    total_days = (end - start).days + 1
    elapsed = (today - start).days + 1
    elapsed = max(0, min(elapsed, total_days))
    return elapsed, total_days
