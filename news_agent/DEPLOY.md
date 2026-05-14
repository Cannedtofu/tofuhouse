# Deploying Updates to the Cloud Server

**Server:** 47.239.66.248 (Alibaba Cloud ECS, Hong Kong)
**App path:** `/opt/tofuhouse/news_agent`
**Service:** `news-agent` (systemd + gunicorn)

---

## Standard update (code changes only)

SSH into the server, then run:

```bash
cd /opt/tofuhouse/news_agent
git pull
systemctl restart news-agent
systemctl status news-agent
```

If you set up the deploy alias (see below), this is just:

```bash
deploy
```

---

## When you also changed `requirements.txt`

```bash
cd /opt/tofuhouse/news_agent
git pull
.venv/bin/pip install -r requirements.txt
systemctl restart news-agent
systemctl status news-agent
```

---

## When you added a new `.env` variable

The `.env` file on the server is **not** tracked by git — you must edit it manually each time you add a new config variable.

```bash
nano /opt/tofuhouse/news_agent/.env
# Add the new variable, save with Ctrl+O, exit with Ctrl+X
systemctl restart news-agent
```

Check `.env.example` in the repo to see what variables exist and what they do.

---

## Database schema changes

No action needed. `db.init_db()` runs automatically on every app startup and applies any new `ALTER TABLE` migrations. The database file (`news.db`) lives on the server and is never overwritten by `git pull`.

---

## Checking if something went wrong

```bash
# Last 50 lines of service logs
journalctl -u news-agent -n 50

# Follow live logs
journalctl -u news-agent -f

# App-level log file
tail -100 /opt/tofuhouse/news_agent/logs/app.log
```

Common issues:
- **Service fails to start** → usually a syntax error or missing env variable; check `journalctl`
- **502 Bad Gateway** → gunicorn is not running; check service status and restart
- **New feature broken but service is up** → check `app.log` for Python exceptions

---

## One-time setup: deploy alias

Add this to `~/.bashrc` on the server so you can deploy with a single word:

```bash
echo "alias deploy='cd /opt/tofuhouse/news_agent && git pull && systemctl restart news-agent && systemctl status news-agent'" >> ~/.bashrc
source ~/.bashrc
```

---

## Full reference: what each component does

| Component | What it is | How to restart |
|---|---|---|
| `news-agent` | Gunicorn serving the Flask app | `systemctl restart news-agent` |
| `xvfb` | Virtual display for headed Playwright | `systemctl restart xvfb` |
| `nginx` | Reverse proxy (port 80 → 5000) | `systemctl reload nginx` |

Nginx config: `/etc/nginx/sites-available/news-agent`
Systemd service: `/etc/systemd/system/news-agent.service`
