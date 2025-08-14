# TikTok Automation — Max Client Edition

**Production-focused**: API + Worker + Scheduler + Polished GUI, scrcpy integration, pipelines/schedules, logs, video preview, and templates.

## Quick start
```powershell
.\install.ps1
.\.venv\Scripts\Activate.ps1
# Optional: set a local API token to require auth on the API
$env:API_TOKEN = "your-strong-token"
python main.py
```

- The app binds to 127.0.0.1 by default. If you set API_TOKEN, the GUI/Worker/Scheduler automatically include it in requests.

## Highlights
- One-click **launcher** (auto port + device)
- **FastAPI** with jobs/runs/cancel/retry + logs
- **Scheduler** (APScheduler) reads `config.yaml` schedules and enqueues jobs
- **Worker** executes warmup/pipeline with recovery hooks
- **GUI**: Overview · Quick Run · Pipelines · Schedules · Devices · Jobs/Runs · Logs · Settings
- **scrcpy**: open/close, auto-open on runs, version-aware flags
- **Video**: 9:16 repurpose helper (ffmpeg; optional)
- **Theming**: dark/light QSS, icons, tooltips, shortcuts

### Dependencies
- Required: adb, scrcpy (for device mirroring)
- Optional: ffmpeg (only needed for future “repurpose to 9:16” helper)

### Security Note
- By default, the API is intended for localhost-only. If you expose it beyond localhost, set `API_TOKEN` and forward the header `X-API-Token` with each request.
- The launcher and components pass `API_TOKEN` from your environment automatically.

See `docs/UX_NOTES.md` for UX decisions.
