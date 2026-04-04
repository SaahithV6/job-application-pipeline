# Job Application Pipeline

Automated job application system powered by AI browser automation.

## Features
- **Auto-discovery**: Finds recommended jobs via LinkedIn + Gmail
- **Auto-apply**: Uses Browser Use AI to fill and submit applications
- **Email monitoring**: Scans Gmail for rejections, interviews, and offers
- **Calendar integration**: Creates Google Calendar events for interviews
- **Interview prep**: Auto-replies with confirmation and prep materials
- **Dashboard**: Track all applications and their statuses

## Quick Start (Replit)
1. Click **Run** — the dashboard starts on port 5000
2. Go to **Settings** to configure your name, email, LinkedIn URL, and resume
3. The pipeline runs automatically on weekdays at noon (configurable)

## Dashboard
- **Main view**: All applications with status filters and search
- **Detail view**: Full application info, activity log, notes
- **Settings**: Personal info, daily limits, timezone, auto-reply toggle
- **Self-Schedule**: Pick interview times when a company sends a scheduling link

## Status Flow
```
Applied → Interview → Offer
    ↓         ↓
 Ghosted   Self-Schedule
    ↓
 Rejected
```

## Architecture
- `db.py` — SQLite data layer
- `discover_jobs.py` — 3-strategy job discovery (Browser Use → LinkedIn Scraper → Gmail)
- `apply.py` — Browser Use job application engine
- `monitor.py` — Gmail email classifier
- `calendar_ops.py` — Google Calendar integration
- `interview_prep.py` — Auto-reply and prep generation
- `pipeline.py` — Main orchestrator
- `dashboard/` — Flask web app
