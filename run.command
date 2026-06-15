#!/bin/bash
# Ivan - Double-click to start (macOS)
cd "$(dirname "$0")"
source venv/bin/activate
python3 main.py &
sleep 3
open http://127.0.0.1:9999
