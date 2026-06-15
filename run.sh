#!/bin/bash
# Ivan - Start the app (macOS / Linux)
cd "$(dirname "$0")"

# --- Make sure setup has been run --------------------------------
if [ ! -x "venv/bin/python" ]; then
    echo ""
    echo "  It looks like setup has not finished yet."
    echo "  Please run setup first (setup.sh / setup.command), then try again."
    echo ""
    read -r -p "  Press ENTER to close this window..."
    exit 1
fi

echo ""
echo "  ============================================================"
echo "    Ivan is starting..."
echo "  ============================================================"
echo ""
echo "  Your browser will open automatically in a few seconds."
echo "  Keep this window open while you use the app."
echo "  To stop the app, just close this window (or press Ctrl+C)."
echo ""

# --- Open the dashboard in the browser after a short delay -------
( sleep 3; open "http://127.0.0.1:9999" 2>/dev/null || xdg-open "http://127.0.0.1:9999" 2>/dev/null ) &

# --- Start the app ----------------------------------------------
./venv/bin/python main.py
EXIT_CODE=$?

# --- If we get here, the app stopped or crashed -----------------
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  ============================================================"
    echo "    Something went wrong - please contact support."
    echo "  ============================================================"
    echo ""
    echo "  The app has stopped. The details above may help support."
    echo ""
    read -r -p "  Press ENTER to close this window..."
fi
exit $EXIT_CODE
