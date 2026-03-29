# HelmHub

A self-hosted personal command center PWA for tasks, notes, reminders, events, bookmarks, and daily focus. Built with Flask, HTMX, PostgreSQL, and Docker.

---

## Features

- **Dashboard** — unified summary view of tasks, reminders, events, and notes
- **Tasks** — create and manage to-dos with priority levels (low/medium/high), due dates, and pinning
- **Notes** — write and organize notes with tags and pinning; includes quick scratchpad
- **Reminders** — time-based alerts with snooze support
- **Events** — calendar events with start/end times and location
- **Bookmarks** — save and categorize URLs with descriptions
- **Focus Mode** — distraction-free view for deep work
- **TOTP 2FA** — optional two-factor authentication with recovery codes
- **PWA** — installable as a standalone app with offline support via service worker
- **Themes** — light, dark, or system preference
- **REST API** — JSON endpoints for dashboard data, tasks, reminders, and quick capture

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, Flask 3.0 |
| Database | PostgreSQL 16 (production), SQLite (development/testing) |
| ORM | SQLAlchemy 2.0 + Alembic migrations |
| Frontend | Jinja2 templates, HTMX 1.9, Vanilla JS |
| Auth | Flask-Login, bcrypt (work factor 12), PyOTP (TOTP) |
| Security | Flask-WTF (CSRF), Flask-Limiter (rate limiting) |
| Server | Gunicorn 22 (2 workers) |
| Deployment | Docker, Docker Compose |

---

## Quick Start (Docker)

### Prerequisites

- Docker and Docker Compose

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/whahn1983/helmhub.git
cd helmhub

# 2. Copy the example environment file
cp .env.example .env

# 3. Edit .env and set a strong SECRET_KEY and admin credentials
nano .env

# 4. Start the application
docker-compose up -d

# Application is available at http://localhost:8080
```

Log in with the admin credentials you set in `.env`. On first run, the admin account is created automatically if no users exist in the database.

---

## Manual Setup (Local Development)

### Prerequisites

- Python 3.12+
- PostgreSQL (or use the default SQLite for development)

### Steps

```bash
# 1. Clone and enter the repository
git clone https://github.com/whahn1983/helmhub.git
cd helmhub

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set environment variables (or create a .env file)
export SECRET_KEY="your-secret-key-here"
export DATABASE_URL="postgresql://user:password@localhost:5432/helmhub"
export FLASK_ENV="development"
export DEFAULT_ADMIN_USERNAME="admin"
export DEFAULT_ADMIN_PASSWORD="changeme"

# 5. Apply database migrations
flask db upgrade

# 6. Start the development server
flask run --port 8080
```

---

## Configuration

All configuration is done via environment variables. Copy `.env.example` to `.env` and fill in your values.

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Flask session and CSRF secret — **must be changed in production** | *(required)* |
| `DATABASE_URL` | Database connection URI | `sqlite:///helmhub.db` |
| `APP_PORT` | Host port mapped to the container | `8080` |
| `POSTGRES_PASSWORD` | PostgreSQL password (used by docker-compose) | `helmhub_secret` |
| `DEFAULT_ADMIN_USERNAME` | Username for the auto-created admin account | `admin` |
| `DEFAULT_ADMIN_PASSWORD` | Password for the auto-created admin account | `changeme` |
| `TZ` | Timezone for date/time display | `America/New_York` |
| `FLASK_ENV` | Runtime environment (`production`, `development`, `testing`) | `production` |
| `SESSION_COOKIE_SECURE` | Restrict session cookies to HTTPS | `False` |

**For production deployments**, always set:
- A long, random `SECRET_KEY`
- A strong `DEFAULT_ADMIN_PASSWORD` (or change the password immediately after first login)
- `SESSION_COOKIE_SECURE=True` if serving over HTTPS

---

## Project Structure

```
helmhub/
├── app/
│   ├── __init__.py          # Application factory (create_app)
│   ├── config.py            # Config classes (Dev / Prod / Test)
│   ├── extensions.py        # Flask extension instances
│   ├── models/              # SQLAlchemy models (user, task, note, reminder, event, bookmark, setting)
│   ├── routes/              # Flask blueprints per feature
│   │   ├── api.py           # JSON REST API
│   │   ├── auth.py          # Login, TOTP, logout
│   │   ├── dashboard.py     # Main dashboard
│   │   ├── tasks.py
│   │   ├── notes.py
│   │   ├── reminders.py
│   │   ├── events.py
│   │   ├── bookmarks.py
│   │   ├── focus.py
│   │   └── settings.py
│   ├── services/            # Auth and TOTP helpers
│   ├── static/              # CSS, JS, service worker, PWA manifest, icons
│   └── templates/           # Jinja2 HTML templates
├── migrations/              # Alembic migration versions
├── tests/                   # pytest test suite
├── docker-compose.yml
├── Dockerfile
├── gunicorn.conf.py
├── entrypoint.sh
├── requirements.txt
└── .env.example
```

---

## API Reference

All endpoints require an authenticated session. Responses are JSON.

### Dashboard

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/dashboard-data` | Summary of tasks, reminders, events, and notes |

### Tasks

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/tasks` | List tasks; supports `view`, `priority`, and `search` query params |

**`view` options:** `today`, `upcoming`, `overdue`, `completed`, `all`
**`priority` options:** `low`, `medium`, `high`

### Reminders

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/reminders/due` | List currently due or snoozed reminders |

### Quick Capture

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/quick-capture` | Create a task, note, reminder, or event from JSON |

**Example request body:**
```json
{
  "type": "task",
  "title": "Review pull request",
  "priority": "high",
  "due_at": "2025-01-15T17:00:00"
}
```

---

## Authentication & Security

- **Passwords** are hashed with bcrypt (work factor 12)
- **TOTP 2FA** uses PyOTP with standard 30-second TOTP tokens; compatible with any authenticator app (Google Authenticator, Authy, etc.)
- **Recovery codes** — 8 single-use codes are generated when TOTP is enabled; each is bcrypt-hashed and consumed on use
- **CSRF protection** on all HTML forms via Flask-WTF
- **Rate limiting** — login endpoint is limited to 10 requests/minute; default global limit is 200 requests/day
- **Session cookies** — HttpOnly, SameSite=Lax, 30-day lifetime; set `SESSION_COOKIE_SECURE=True` for HTTPS deployments

---

## Running Tests

```bash
# Install dependencies (if not already done)
pip install -r requirements.txt

# Run the full test suite
pytest tests/

# Verbose output
pytest tests/ -v

# Run a specific test file
pytest tests/test_tasks.py
```

Tests use an in-memory SQLite database with CSRF and rate limiting disabled. No external services are required.

---

## Installing as a PWA

HelmHub is a Progressive Web App and can be installed as a standalone application on desktop and mobile.

1. Open HelmHub in a supported browser (Chrome, Edge, Safari on iOS)
2. Look for the **Install** or **Add to Home Screen** prompt in the browser's address bar or share menu
3. The app will open in a standalone window without browser chrome

The service worker caches static assets for offline access and uses a network-first strategy for HTML and API responses.

---

## Deployment Notes

### Behind a Reverse Proxy

If running behind nginx or a similar proxy, set `SESSION_COOKIE_SECURE=True` and ensure the proxy forwards `X-Forwarded-For` and `X-Forwarded-Proto` headers so Flask can determine the correct scheme for CSRF and session security.

### Database Migrations

Migrations run automatically via `entrypoint.sh` when the Docker container starts. For manual deployments, run:

```bash
flask db upgrade
```

To create a new migration after changing models:

```bash
flask db migrate -m "describe the change"
flask db upgrade
```

### Gunicorn Configuration

Default settings (`gunicorn.conf.py`):
- 2 sync workers
- 120-second request timeout
- Max 1000 requests per worker before restart (with ±100 jitter)
- Logs to stdout

---

## License

[Apache 2.0](LICENSE)
