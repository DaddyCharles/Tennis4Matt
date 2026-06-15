# Ivan — Tennis Coach Business Manager

A complete business management app for tennis coaches.

Built for Matt. Runs locally on Windows or Mac.

---

## What Ivan Does

- Daily schedule with weather and court conditions
- Student management and lesson booking
- Earnings tracking with charts
- Invoicing with PDF generation
- Expense tracking with ATO categories
- Australian tax estimator (GST, PAYG)
- Lesson packages
- Bulk SMS to students and groups
- Facebook lead monitoring for new students

---

## For Matt (Windows)

1. Download and extract this ZIP
2. Open the `FOR_MATT` folder
3. Follow `MATT_START_HERE.txt`

---

## For Developers

**Requirements:** Python 3.10+

**Setup:**

```bash
git clone https://github.com/YOURUSERNAME/ivan
cd ivan
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
pip install -r requirements.txt
playwright install chromium
python3 main.py
```

Open http://127.0.0.1:9999

On first run, Ivan copies clean starting files from `data/defaults/` and
`config/defaults/` into place, so a fresh clone works out of the box.

---

## Tech Stack

Python, Flask, Playwright, Chart.js, Open-Meteo API, Twilio, Anthropic Claude API
