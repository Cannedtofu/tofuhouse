# News Agent

News Agent is a personal research and monitoring assistant with a Flask web UI, feed/topic ingestion, transcript tools, digests, and notification integrations.

## Local Run

```powershell
.\.venv\Scripts\python.exe app.py
```

The development server listens on `http://localhost:5000`.

## Subprojects

- [Enterprise WeCom Assistant](docs/wecom-assistant/README.md): outbound WeCom active-push module, notification abstraction, deployment checklist, and future callback roadmap.

## Docker

Copy `.env.example` to `.env`, fill the required values, then run:

```bash
docker compose up -d --build
```

The container exposes the Flask app through Gunicorn on port `5000`. Runtime state is mounted for SQLite databases, logs, uploads, and audio cache.
