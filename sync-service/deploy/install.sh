#!/bin/bash
# App-side setup for the Pocket Casts sync service.
# Run this from the sync-service directory: bash deploy/install.sh
# It creates the venv, installs deps, and smoke-tests the app. It does NOT
# touch systemd or nginx (those need sudo and are documented in README.md).
set -e

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
echo "==> sync-service dir: $HERE"

# 1) Python check
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found. Install it (e.g. sudo apt install python3 python3-venv)."
    exit 1
fi
echo "==> $(python3 --version)"

# 2) venv (needs the venv module; on Debian: sudo apt install python3-venv)
if [ ! -d .venv ]; then
    echo "==> creating virtualenv"
    python3 -m venv .venv
fi
echo "==> installing requirements"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# 3) import smoke test (verifies the sibling `pocketcasts` package is importable)
echo "==> import check"
.venv/bin/python -c "import sync_service; print('    sync_service imports OK')"

# 4) boot gunicorn briefly and hit /sync/health
echo "==> booting gunicorn on 127.0.0.1:8001 for a health check"
.venv/bin/gunicorn --workers 1 --bind 127.0.0.1:8001 sync_service:app >/tmp/pcsync-install.log 2>&1 &
PID=$!
sleep 3
if curl -fsS http://127.0.0.1:8001/sync/health >/dev/null 2>&1; then
    echo "    /sync/health OK"
    RESULT=0
else
    echo "    /sync/health FAILED — see /tmp/pcsync-install.log"
    RESULT=1
fi
kill $PID 2>/dev/null || true

echo
if [ $RESULT -eq 0 ]; then
    echo "App is ready. Next (need sudo):"
    echo "  1. sudo cp deploy/pocketcasts-sync.service /etc/systemd/system/"
    echo "     (adjust User/paths inside if this isn't at /var/www/podcasts/Pocket-Casts)"
    echo "  2. sudo systemctl daemon-reload && sudo systemctl enable --now pocketcasts-sync"
    echo "  3. Add deploy/nginx.conf.snippet to the site's server{} block, then:"
    echo "     sudo nginx -t && sudo systemctl reload nginx"
    echo "  4. curl https://podcasts.webosarchive.org/sync/health   # expect {\"status\":\"ok\"}"
fi
exit $RESULT
