# Deploying Updates to the Cloud Server

**Server:** 47.239.66.248 (Alibaba Cloud ECS, Hong Kong)  
**App path:** `/opt/tofuhouse/news_agent`  
**Service:** `news-agent` (systemd + gunicorn)  
**Nitter:** Docker Compose in `/opt/tofuhouse/news_agent/nitter/`

---

## Standard update

From your local machine, push changes:
```bash
git push
```

Then on the server, run the deploy script:
```bash
bash /opt/tofuhouse/news_agent/scripts/deploy.sh
```

The script handles everything automatically:
- Detects if `nitter/` files changed → restarts Docker containers only when needed
- Detects if `requirements.txt` changed → runs pip install only when needed
- Checks for missing `.env` variables and adds defaults
- Restarts `news-agent`
- Reports status at each step

---

## One-time server setup (already done — for reference)

### Protect nitter.conf from git overwrites

The server's `nitter.conf` has a custom `hmacKey`. Tell git to never overwrite it on this machine:

```bash
cd /opt/tofuhouse/news_agent
git update-index --skip-worktree news_agent/nitter/nitter.conf
```

After this, `git pull` will never touch `nitter.conf`, even if the committed version changes.

### Make deploy a one-word command

```bash
echo "alias deploy='bash /opt/tofuhouse/news_agent/scripts/deploy.sh'" >> ~/.bashrc
source ~/.bashrc
```

Then just run:
```bash
deploy
```

---

## When you add a new `.env` variable

The `.env` file is not tracked by git. The deploy script automatically adds known variables if missing. For anything new, add it manually:

```bash
echo "NEW_VAR=value" >> /opt/tofuhouse/news_agent/.env
systemctl restart news-agent
```

Check `.env.example` in the repo for all documented variables.

---

## Nitter-specific operations

### Check Nitter status
```bash
cd /opt/tofuhouse/news_agent/nitter
docker compose ps
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/about   # expect 200
```

### View Nitter logs
```bash
cd /opt/tofuhouse/news_agent/nitter
docker compose logs --tail=30
```

### Restart Nitter manually
```bash
cd /opt/tofuhouse/news_agent/nitter
docker compose down && docker compose up -d
```

### Fetch historical tweets (admin only)
```bash
cd /opt/tofuhouse/news_agent
.venv/bin/python scripts/fetch_history.py @handle --from 2025-01-01 --to 2025-06-01
# --delay 120 (seconds between pages, default)
# --dry-run   (preview without writing to DB)
```

---

## Database schema changes

No action needed. `db.init_db()` runs automatically on startup and applies any new migrations.

---

## Troubleshooting

```bash
# Service logs (last 50 lines)
journalctl -u news-agent -n 50

# Follow live logs
journalctl -u news-agent -f

# App log file
tail -100 /opt/tofuhouse/news_agent/logs/app.log
```

| Symptom | Likely cause | Fix |
|---|---|---|
| Service fails to start | Syntax error or missing env var | `journalctl -u news-agent -n 30` |
| 502 Bad Gateway | Gunicorn not running | `systemctl restart news-agent` |
| Feature broken, service up | Python exception | Check `app.log` |
| Nitter returns errors | Session expired or X.com blocked | Re-extract cookies, update `sessions.jsonl` |
| Nitter HTTP 000 | Container not running | `docker compose up -d` in `nitter/` |

---

## Component reference

| Component | Role | Restart command |
|---|---|---|
| `news-agent` | Gunicorn / Flask app | `systemctl restart news-agent` |
| `nitter` (Docker) | X.com RSS proxy | `docker compose up -d` in `nitter/` |
| `nitter-redis` (Docker) | Nitter response cache | restarted together with nitter |
| `xvfb` | Virtual display for Playwright | `systemctl restart xvfb` |
| `nginx` | Reverse proxy (80 → 5000) | `systemctl reload nginx` |

Config files:  
- Nginx: `/etc/nginx/sites-available/news-agent`  
- Systemd: `/etc/systemd/system/news-agent.service`  
- Nitter: `/opt/tofuhouse/news_agent/nitter/nitter.conf` (skip-worktree protected)
