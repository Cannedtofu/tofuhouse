#!/usr/bin/env bash
# deploy.sh — pull latest code and restart services on the server.
# Run from anywhere: bash /opt/tofuhouse/news_agent/scripts/deploy.sh

set -e

APP=/opt/tofuhouse/news_agent
NITTER=$APP/nitter

echo "=== News Agent Deploy ==="
cd "$APP"

# ---------------------------------------------------------------
# 1. Detect what's changing before the pull
# ---------------------------------------------------------------
git fetch origin main -q

NITTER_CHANGED=$(git diff HEAD origin/main --name-only | grep -c '^news_agent/nitter/' || true)
REQS_CHANGED=$(git diff HEAD origin/main --name-only | grep -c 'requirements.txt' || true)

# ---------------------------------------------------------------
# 2. Pull
# ---------------------------------------------------------------
echo "→ Pulling latest code…"
git pull

# ---------------------------------------------------------------
# 3. Check required .env variables
# ---------------------------------------------------------------
echo "→ Checking .env…"
MISSING=0

add_if_missing() {
  local key=$1 val=$2
  if ! grep -q "^${key}=" "$APP/.env" 2>/dev/null; then
    echo "  [+] Adding ${key}=${val}"
    echo "${key}=${val}" >> "$APP/.env"
    MISSING=$((MISSING + 1))
  fi
}

add_if_missing NITTER_LOCAL_URL   "http://127.0.0.1:8080"
add_if_missing NITTER_FETCH_PERIOD_HOURS "24"
add_if_missing NITTER_PAGE_DELAY  "120"

if [ "$MISSING" -gt 0 ]; then
  echo "  Added $MISSING missing variable(s) to .env"
else
  echo "  .env OK"
fi

# ---------------------------------------------------------------
# 4. Install new Python dependencies if requirements.txt changed
# ---------------------------------------------------------------
if [ "$REQS_CHANGED" -gt 0 ]; then
  echo "→ requirements.txt changed — installing packages…"
  "$APP/.venv/bin/pip" install -r "$APP/requirements.txt" -q
fi

# ---------------------------------------------------------------
# 5. Restart Nitter if nitter/ files changed
# ---------------------------------------------------------------
if [ "$NITTER_CHANGED" -gt 0 ]; then
  echo "→ Nitter files changed — restarting containers…"
  cd "$NITTER"
  docker compose down
  docker compose up -d
  sleep 5
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/about)
  if [ "$STATUS" = "200" ]; then
    echo "  Nitter OK (HTTP 200)"
  else
    echo "  WARNING: Nitter returned HTTP $STATUS — check: docker compose logs --tail=20"
  fi
  cd "$APP"
else
  echo "→ No Nitter changes, skipping container restart"
fi

# ---------------------------------------------------------------
# 6. Restart news-agent
# ---------------------------------------------------------------
echo "→ Restarting news-agent…"
systemctl restart news-agent
sleep 3
systemctl is-active --quiet news-agent && echo "  news-agent OK" || echo "  ERROR: news-agent failed to start"

echo "=== Deploy complete ==="
