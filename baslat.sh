#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Ilk kurulum yapiliyor, biraz bekle..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python uyan.py "$@"
