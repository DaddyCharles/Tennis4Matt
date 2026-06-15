#!/bin/bash
# Ivan - One-Time Setup (macOS / Linux)
cd "$(dirname "$0")"

echo ""
echo "  ============================================================"
echo "    Ivan - One-Time Setup"
echo "  ============================================================"
echo ""
echo "  This will get everything ready. It can take a few minutes."
echo "  Please leave this window open until you see 'Setup complete!'"
echo ""

# --- 1. Check that Python 3 is installed --------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "  [!] Python 3 is not installed on this computer."
    echo ""
    echo "      We are opening the Python download page for you now."
    echo "      1. Download and run the installer."
    echo "      2. After it finishes, run this setup again."
    echo ""
    open "https://www.python.org/downloads/" 2>/dev/null || xdg-open "https://www.python.org/downloads/" 2>/dev/null
    read -r -p "  Press ENTER to close this window..."
    exit 1
fi
echo "  [1/5] Python found."

# --- 2. Create the virtual environment ---------------------------
if [ ! -x "venv/bin/python" ]; then
    echo "  [2/5] Creating a private environment for the app..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo ""
        echo "  [!] Could not create the environment."
        echo "      Please make sure Python installed correctly, then try again."
        read -r -p "  Press ENTER to close this window..."
        exit 1
    fi
else
    echo "  [2/5] Environment already exists - reusing it."
fi

# --- 3. Install the required packages -----------------------------
echo "  [3/5] Installing required packages (this is the slow part)..."
./venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
./venv/bin/python -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo ""
    echo "  [!] Could not install the required packages."
    echo "      Please check your internet connection and try again."
    read -r -p "  Press ENTER to close this window..."
    exit 1
fi

# --- 4. Install the browser the bot uses --------------------------
echo "  [4/5] Installing the browser the bot uses..."
./venv/bin/python -m playwright install chromium
if [ $? -ne 0 ]; then
    echo ""
    echo "  [!] Could not install the browser component."
    echo "      Please check your internet connection and try again."
    read -r -p "  Press ENTER to close this window..."
    exit 1
fi

# --- 5. Generate the app (PWA) icons -----------------------------
echo "  [5/5] Generating app icons..."
./venv/bin/python app/generate_icons.py || echo "  [!] Could not generate app icons (non-critical). Continuing..."

# --- Make the run launchers executable ---------------------------
chmod +x run.command run.sh 2>/dev/null

echo ""
echo "  ============================================================"
echo "    Setup complete!"
echo "  ============================================================"
echo ""
echo "  To start the app, double-click 'run.command'."
echo ""
read -r -p "  Press ENTER to close this window..."
exit 0
