# news_agent Agent Guide

This file mirrors the project-specific workflow guidance from `CLAUDE.md` and adds extra guardrails for working inside the larger Git repository at `D:\代码项目`.

## Repository Scope

This project lives inside a larger Git repository at `D:\代码项目`.

When working in this folder:

- Treat `D:\代码项目\news_agent` as the application root.
- Do not stage or commit files outside `news_agent` unless the user explicitly asks.
- For Git commands, use the repository root `D:\代码项目`, but pass explicit `news_agent/...` pathspecs.
- Prefer staging only intentional source files, templates, scripts, config examples, and docs.
- Do not stage runtime files such as `.env`, `news.db`, `*.db`, `logs/`, `audio_cache/`, cookies, browser profiles, generated patch files, or temporary test artifacts.
- Before committing, run at least a Python compile check for touched Python modules.
- If GitHub push fails because of network issues, retry push first; do not create server-only commits unless necessary.

## Running The App

```bash
# Web UI (development) - requires Python 3.11+ (.venv)
.venv\Scripts\python.exe app.py      # serves on http://localhost:5000

# CLI (fetch + summarize + email digest)
.venv\Scripts\python.exe main.py

# Reset a source's articles for re-testing
.venv\Scripts\python.exe scripts\reset_source.py "semi analysis"
```

## Deployment

Server: `47.239.66.248` (Alibaba Cloud ECS, Hong Kong)
App path: `/opt/tofuhouse/news_agent`
Service: `news-agent` (systemd + gunicorn)

Standard deploy:

```bash
git push
bash /opt/tofuhouse/news_agent/scripts/deploy.sh
```

See `DEPLOY.md` for full deployment reference.

## Session Workflow

- At session start: read `STATUS.md`; treat it as the source of truth for current state.
- After completing a major feature or meaningful fix: update `STATUS.md` before stopping.
- Do not update `STATUS.md` for small edits, typo fixes, or minor refactors.

## Git

- Always push immediately after committing.
- Because the Git root is larger than this app, never use broad staging such as `git add .` from `D:\代码项目`.
- Stage explicit `news_agent/...` paths only.
- Keep unrelated dirty files in the larger repo untouched.

Useful command patterns:

```powershell
git -C "D:\代码项目" status --short -- news_agent
git -C "D:\代码项目" add news_agent/app.py news_agent/templates/example.html
git -C "D:\代码项目" commit -m "Message"
git -C "D:\代码项目" push
```

## Shell, SSH, Encoding, And Remote Diagnostics

This project is commonly operated from Windows PowerShell against a Linux server. Avoid fragile nested one-line commands that pass through PowerShell, SSH, bash, Python, and SQL at the same time.

Rules:

- Prefer UTF-8-safe files and commands. Keep dependency/config/script files ASCII when possible, especially `requirements.txt`, `.env.example`, `.ps1`, `.bat`, and deploy scripts.
- Avoid special Unicode punctuation in machine-read files, such as em dashes, arrows, smart quotes, and emoji.
- Prefer `python -m pip` over direct `pip`.
- In PowerShell, avoid complex one-line commands with nested quotes, `$()`, SQL strings, or Python snippets.
- For remote server diagnostics, prefer SSH single-quoted here-docs or temporary scripts.
- For SQLite queries, prefer Python `sqlite3` parameterized queries instead of shell-quoted SQL.
- Do not write diagnostic scripts into the server repo unless needed. Prefer stdin scripts or `/tmp`, and clean up temporary files afterward.
- Do not use `git add .` from the larger repo root. Stage explicit `news_agent/...` paths only.

Recommended local encoding setup:

[Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User')
[Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
chcp 65001
$OutputEncoding = [System.Text.UTF8Encoding]::new()

Preferred remote diagnostic pattern:

ssh -p 2222 -i "$env:USERPROFILE\.ssh\id_ed25519" root@47.239.66.248 @'
cd /opt/tofuhouse/news_agent || exit 1
.venv/bin/python - <<'PY'
import sqlite3

conn = sqlite3.connect("news.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, title, url FROM articles WHERE instr(lower(content), ?) > 0 LIMIT 20",
    ("anthropic",),
).fetchall()

for row in rows:
    print(dict(row))
PY
'@

Preferred compile check pattern:

.\.venv\Scripts\python.exe -B -c "import pathlib; files=['app.py','topic_workflow.py','db/core.py']; [compile(pathlib.Path(f).read_text(encoding='utf-8'), f, 'exec') for f in files]; print('compile_ok')"

If local `.venv` is broken, use the bundled/runtime Python only as a fallback, and note that project dependencies may be missing unless `.venv\Lib\site-packages` is added to `PYTHONPATH`.

## Conventions

- DB access always through `db/` layer; no raw SQL in routes or pipeline code unless there is a strong local precedent.
- Config values always from `config.py`; no hardcoded strings, keys, or thresholds.
- Background tasks follow the existing `threading.Thread(target=fn, daemon=True).start()` pattern.
- New background jobs that need to survive restarts go in SQLite, following `transcript_jobs`.
- New background jobs that do not need persistence go in an in-memory dict, following `_digest_jobs`.
- New LLM calls use `QWEN_SUMMARY_MODEL` (`qwen-plus`) unless vision is required (`QWEN_VISION_MODEL`).
- Chinese UI strings are intentional; do not translate or change them unless requested.
- Do not add type hints to existing files unless explicitly asked.
- Do not refactor code outside the scope of the current task; ask first.
- If something is ambiguous, ask one specific question before proceeding.
