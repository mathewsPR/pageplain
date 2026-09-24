#!/usr/bin/env bash
# Fetch Camoufox + Playwright once into local cache (avoids repeated GitHub hits)
set -euo pipefail
echo "Installing Python deps if needed..."
pip install -q -r requirements.txt || true
echo "Fetching Camoufox browser into ~/.cache/camoufox ..."
python -m camoufox fetch
echo "Installing Playwright Chromium fallback..."
python -m playwright install chromium
echo "Done. Camoufox cache: ${HOME}/.cache/camoufox"
python -m camoufox version || true
