# CampusFlow

A college planner for keeping every course, class, and deadline in one place: what's overdue, what's due today, where you need to be this week, and which plans clash, all in your own time zone, with reminders that arrive before work is due.

Built as a full-stack portfolio project: a React + TypeScript frontend, a FastAPI backend, PostgreSQL, and a separate worker process that delivers durable reminders.

**Try it:** [campusflow-zgs3.onrender.com](https://campusflow-zgs3.onrender.com), then press **Try the demo**. The free server sleeps when idle, so the first load can take about a minute. No sign-up needed. ([What the demo is](#the-shared-demo))

![Today view](docs/screenshots/today-light.png)

## Status

| Milestone | Scope | State |
| --- | --- | --- |
| 1 | Accounts, courses, assignments, persistent storage | **Done** |
| 2 | Weekly calendar, recurring classes, conflict detection | **Done** |
| 3 | Durable reminders and notification settings | **Done** |
| 4 | Demo account, deployment, usability, portfolio docs | **Done** |

## The problem

Students juggle deadlines from four to six courses, each with its own syllabus, LMS page, and habits. Deadlines get missed less from forgetting than from not seeing them in one place at the right time. CampusFlow's target workflows:

1. **Morning check:** open Today and see what's overdue, what's due today, and the week ahead.
2. **Capture fast:** a professor announces a deadline, so press <kbd>N</kbd>, type a title, and save. The last course you used is remembered.
3. **Find and filter:** search assignments, filter by course, status, overdue, or date range, and sort by deadline.
4. **Finish:** check an assignment off. It's saved before it's shown as done, and it can be undone.
5. **Plan the week:** see classes, meetings, and deadlines on one week grid, and get warned before booking two things at once.
6. **Get reminded:** each assignment's reminder arrives in Notifications, and by email if you opt in, even if nobody has the app open.
7. **End the semester:** archive a course to keep its history without cluttering the lists.

## Screenshots

| | |
| --- | --- |
| ![Week grid](docs/screenshots/week-light.png) | ![Agenda view](docs/screenshots/agenda-light.png) |
| ![Conflict warning](docs/screenshots/event-conflict-light.png) | ![Editing a weekly class](docs/screenshots/class-dialog-light.png) |
| ![Assignments](docs/screenshots/assignments-light.png) | ![New assignment](docs/screenshots/dialog-light.png) |
| ![Courses](docs/screenshots/courses-light.png) | ![Settings](docs/screenshots/settings-light.png) |
| ![Notifications](docs/screenshots/notifications-light.png) | ![Reminder history in the edit dialog](docs/screenshots/reminder-history-light.png) |
| ![Week in dark mode](docs/screenshots/week-dark.png) | ![Today in dark mode](docs/screenshots/today-dark.png) |
| ![Sign in, with the demo entry](docs/screenshots/login.png) | ![The shared demo](docs/screenshots/demo-today-light.png) |

<img src="docs/screenshots/today-phone.png" alt="Today on a phone" width="300"> <img src="docs/screenshots/agenda-phone.png" alt="The week as an agenda on a phone" width="300"> <img src="docs/screenshots/notifications-phone.png" alt="Notifications on a phone" width="300"> <img src="docs/screenshots/demo-phone.png" alt="The shared demo on a phone" width="300">

## The shared demo

**Try the demo** on the sign-in page signs you in to one shared sample account: five courses (one archived), fifteen assignments (two overdue, two due today), a weekly schedule with a deliberate clash (a Thursday lab overlapping a lecture), and an overnight study session. Every date is relative to the day it was built, so it never looks stale.

- **It's a normal account.** `users.is_demo` grants nothing; the tests check the demo can't see anyone else's data. It only *removes* the abilities that would let one visitor spoil the demo for the next: changing the password, deleting the account, and turning on email.
- **It's shared.** Everyone who tries it sees the same data and can change it, and a banner on every page says so.
- **It resets every night** at 08:00 UTC (4:00 AM in New York during daylight time), when `python -m app.demo` deletes the account and everything in it and builds a fresh one. The banner shows the next reset in your time zone. A reset signs everyone out of it. If someone presses the button during the few seconds of a reset, they're asked to try again in a minute.
- **It never sends email.** Its address uses the reserved `.invalid` domain, which can't receive mail and which sign-up rejects, and reminder planning never schedules email for it.
- **One-click login is rate limited** (30 per hour per IP) so it can't be used to flood the sessions table.

## Architecture

```mermaid
flowchart LR
  B[Browser<br/>React + TypeScript] -- "same origin: the app and /api/*<br/>cookie + X-CSRF-Token" --> A[FastAPI<br/>serves the API and the built frontend]
  A -- SQLAlchemy 2.x<br/>psycopg 3 --> D[(PostgreSQL 18)]
  W[Reminder worker<br/>python -m app.worker] -- "claims due jobs<br/>FOR UPDATE SKIP LOCKED" --> D
  W -- "HTTPS + Idempotency-Key<br/>(or local capture file)" --> R[Resend]
```

One frontend, one API, one database, and one worker. The reminder queue is a PostgreSQL table, so there's no message broker to run. In development the Vite dev server serves the frontend and proxies `/api`; in production FastAPI serves the built files itself. Either way the browser sees one origin.

**In production** (all free tiers, see [Deployment](#deployment)):

```mermaid
flowchart LR
  B[Browser] -- HTTPS --> RW["Render web service (free, Ohio)<br/>Docker: migrate, ensure demo, uvicorn"]
  RW --> N[(Neon PostgreSQL 18<br/>us-east-2)]
  GA["GitHub Actions schedule<br/>worker --once every 30 min<br/>demo reset nightly"] --> N
  GA -- reminder email --> R[Resend]
```

**How a reminder flows:** saving an assignment (or completing it, archiving its course, changing your time zone or email setting) recalculates its reminder rows in the same transaction. The worker, a separate process, polls for rows whose time has come, claims them, rechecks the assignment, then writes an in-app notification or calls the email adapter, and records what happened. The browser polls Notifications once a minute.

**How a request flows:** the browser calls `/api/...` with the session cookie. FastAPI loads the session (dependency `require_auth`), checks the CSRF header on writes, and runs the handler in its threadpool. The handler reads or writes with SQLAlchemy, filtering every query by the signed-in user's ID, then commits. Pydantic validates the input on the way in and shapes the output on the way out. The frontend caches responses with TanStack Query and refetches the affected views after every write.

### Database

```mermaid
erDiagram
  users ||--o{ sessions : "signs in with"
  users ||--o{ courses : owns
  users ||--o{ assignments : owns
  courses ||--o{ assignments : "contains (same user)"
  users ||--o{ events : owns
  courses |o--o{ events : "optionally linked (same user)"
  assignments ||--o{ reminders : "scheduled for (same user)"
  users ||--o{ notifications : receives
  reminders |o--o| notifications : "delivered as"
  users {
    bigint id PK
    varchar email "unique, lowercase"
    varchar password_hash "argon2id"
    varchar time_zone "IANA, e.g. America/New_York"
    bool email_reminders "opt-in"
  }
  sessions {
    bigint id PK
    bigint user_id FK
    bytea token_hash "sha256 of cookie token"
    varchar csrf_token
    timestamptz expires_at
  }
  courses {
    bigint id PK
    bigint user_id FK
    varchar name
    varchar code "optional"
    varchar instructor "optional"
    varchar color "#rrggbb from a fixed palette"
    timestamptz archived_at "null = active"
    uuid idempotency_key "unique per user"
  }
  assignments {
    bigint id PK
    bigint user_id FK
    bigint course_id FK "composite FK (user_id, course_id)"
    varchar title
    text description
    date due_date "date-only deadline"
    timestamptz due_at "exact deadline (UTC)"
    timestamptz completed_at "null = open"
    int reminder_minutes "null = none"
    uuid idempotency_key "unique per user"
  }
  events {
    bigint id PK
    bigint user_id FK
    bigint course_id FK "nullable, composite FK (user_id, course_id)"
    varchar title
    varchar kind "class, study, meeting, other"
    timestamptz starts_at "one-time only (UTC)"
    timestamptz ends_at "one-time only (UTC)"
    smallint_array weekdays "weekly only, 0 = Monday"
    time start_time "weekly only, local wall time"
    int duration_minutes "weekly only"
    varchar time_zone "weekly only, IANA zone the class is defined in"
    date first_date "weekly only"
    date last_date "weekly only"
    uuid idempotency_key "unique per user"
  }
  reminders {
    bigint id PK
    bigint assignment_id FK "composite FK (user_id, assignment_id)"
    varchar channel "in_app or email"
    timestamptz deadline_at "rechecked before delivery"
    int lead_minutes
    timestamptz send_at
    varchar status "pending, processing, captured, accepted, delivered, skipped, cancelled, failed"
    smallint attempts
    timestamptz next_attempt_at "backoff"
    timestamptz locked_until "worker lease"
    date due_on "date-only due date: part of the reminder's identity"
    bool legacy_identity "pre-upgrade reminder whose due date couldn't be verified"
    varchar detail "why cancelled, skipped, or failed"
    varchar provider_message_id
    varchar provider_status "Resend last_event"
  }
  notifications {
    bigint id PK
    bigint user_id FK
    bigint assignment_id FK "null after the assignment is deleted"
    bigint reminder_id FK "unique: one notification per reminder"
    varchar title
    varchar body
    timestamptz read_at
  }
  rate_limit_events {
    bigint id PK
    varchar bucket "e.g. login:email:..."
    timestamptz created_at
  }
```

Exactly one of `due_date` or `due_at` is set; a check constraint enforces it. Likewise an event is either one-time (`starts_at`/`ends_at`) or weekly (the rule columns), never a mix. Weekly occurrences aren't stored: they're computed from the rule for whatever window is asked for (see [Calendar](#calendar-rules-are-stored-occurrences-are-computed)).

## Tech stack and versions

| Layer | Choice | Version |
| --- | --- | --- |
| Frontend | React, TypeScript, Vite | 19.3, 6.0, 8.3 |
| Data fetching | TanStack Query | 5.104 |
| Routing | React Router | 8.5 |
| UI details | lucide-react icons, Sonner toasts, Instrument Sans (self-hosted) | 1.61, 2.0, 5.x |
| Backend | Python, FastAPI, Uvicorn | 3.12+, 0.159, 0.54 |
| Database | PostgreSQL, SQLAlchemy, Alembic, psycopg | 18, 2.1, 1.20, 3.3 |
| Validation | Pydantic, pydantic-settings | 2.14, 2.16 |
| Passwords | argon2-cffi (Argon2id) | 25.1 |
| Tests | pytest, Playwright, axe-core (`@axe-core/playwright`) | 9.1, 1.64, 4.13 |
| Tooling | uv, ruff, mypy (strict), oxlint, Prettier | 0.11, 0.17, 2.4, 1.x, 3.x |

Exact versions are pinned in `backend/uv.lock` and `frontend/package-lock.json`. Node is pinned to 24 in `frontend/.nvmrc`. Node 24 is the newest LTS line today; Node 26 becomes Active LTS on October 27, 2026, and moving to it is a one-line change.

## Running it locally

Prerequisites: PostgreSQL 18, [uv](https://docs.astral.sh/uv/), and Node 24.

```bash
# 1. Database role (once). CREATEDB lets the tests create throwaway databases.
createuser --createdb --pwprompt campusflow      # password: campusflow (dev only)

# 2. Backend
cd backend
cp .env.example .env                             # adjust if your database differs
uv sync                                          # installs exact locked versions
uv run python -m scripts.ensure_db               # creates the database if missing
uv run alembic upgrade head                      # applies migrations
uv run uvicorn app.main:app --reload --port 8000

# 3. Reminder worker (another terminal). Email is captured to backend/.captured-email/ by default.
cd backend
uv run python -m app.worker                      # add --once to process what's due and exit
# After upgrading an existing database, run once (safe to repeat):  uv run python -m app.worker --resync

# 4. Frontend (another terminal)
cd frontend
npm ci
npm run dev                                      # http://localhost:5173

# Optional: the shared demo account (also what the nightly job runs)
cd backend && uv run python -m app.demo
```

The Vite dev server proxies `/api` to the API on port 8000, so the browser sees a single origin.

To run it the way production does, one process serving everything:

```bash
cd frontend && npm run build
cd ../backend && CAMPUSFLOW_FRONTEND_DIST=../frontend/dist uv run uvicorn app.main:app --port 8000
# or the production image:  docker build -t campusflow . && docker run -p 8000:8000 -e CAMPUSFLOW_DATABASE_URL=... campusflow
```

### Environment variables

All backend settings use the `CAMPUSFLOW_` prefix. See [`backend/.env.example`](backend/.env.example). The important ones:

| Variable | Default | Notes |
| --- | --- | --- |
| `CAMPUSFLOW_DATABASE_URL` | local `campusflow` DB | SQLAlchemy URL with the `postgresql+psycopg://` driver |
| `CAMPUSFLOW_ALLOWED_ORIGINS` | Vite dev origins | JSON list. Writes from any other origin are rejected |
| `CAMPUSFLOW_SESSION_COOKIE_SECURE` | `false` | **Must be `true` in production** (HTTPS-only cookie) |
| `CAMPUSFLOW_SESSION_COOKIE_NAME` | `cf_session` | Use `__Host-cf_session` in production |
| `CAMPUSFLOW_SESSION_IDLE_DAYS` / `_ABSOLUTE_DAYS` | 7 / 30 | Sliding and absolute session lifetimes |
| `CAMPUSFLOW_LOGIN_MAX_FAILURES_PER_EMAIL` | 5 per 15 min | Also a per-IP limit (30) |
| `CAMPUSFLOW_EMAIL_MODE` | `capture` | `capture` writes emails to a local file and sends nothing; `resend` sends |
| `CAMPUSFLOW_RESEND_API_KEY` | none | Secret. Only needed with `EMAIL_MODE=resend` |
| `CAMPUSFLOW_EMAIL_FROM` | `CampusFlow <onboarding@resend.dev>` | Resend's test sender works without a domain |
| `CAMPUSFLOW_EMAIL_ALLOWED_RECIPIENTS` | empty (anyone) | JSON list or comma-separated. If set, email only goes to these addresses; others stay in-app, and Settings tells them so |
| `CAMPUSFLOW_REMINDER_MAX_ATTEMPTS` / `_LEASE_MINUTES` | 5 / 5 | Retry limit, and how long a claimed job waits before another worker may take it |
| `CAMPUSFLOW_FRONTEND_DIST` | unset | Path to the built frontend. When set, the API serves it (production) |
| `CAMPUSFLOW_TRUSTED_PROXY_HOPS` | `0` | Proxies in front of the app that append to `X-Forwarded-For` (Render: 3, measured from the live logs). Rate limits use the entry the first of them added |
| `CAMPUSFLOW_DEMO_ENABLED` / `_RESET_UTC_HOUR` | `true` / `8` | The shared demo, and the hour its nightly reset is shown for |

No secrets are committed. `.env` files are git-ignored. A host-style `postgres://` or `postgresql://` URL (as Neon and Render hand out) is accepted and given the psycopg driver automatically.

## Testing and checks

```bash
# Backend: each run creates its own PostgreSQL database and drops it afterwards
cd backend
uv run ruff format --check . && uv run ruff check . && uv run mypy app tests scripts
uv run pytest --cov=app

# Frontend
cd frontend
npm run format:check && npm run lint && npm run typecheck && npm run build
npm test                          # calendar layout unit tests (Node's built-in runner)

# Browser tests: build the frontend and serve it from FastAPI, as production does,
# on a separate campusflow_e2e database
npx playwright install chromium   # once
npm run test:e2e
```

GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs four jobs on every push and pull request, with a PostgreSQL 18 service container: backend checks, frontend checks, the browser tests, and a Docker job that builds the production image, starts it with production settings, and smoke-tests it (health, the app shell on a deep link, security headers, and a demo sign-in).

What the 162 backend tests cover (97% line coverage):

- **Auth:** HTTP-only cookie, email normalization, generic login errors, server-side logout (a replayed cookie fails), idle and absolute expiry, CSRF header required for writes, cross-origin writes rejected, login throttling that survives across clients, password change signing out other devices, account deletion.
- **Isolation:** another user's records return 404 by ID for read, edit, complete, delete, archive, and deletion-impact. An assignment can't be attached to another user's course, and the database itself rejects such a row. Idempotency keys are scoped per user.
- **Assignments:** validation, lifecycle, every filter, title search with literal `%` and `_`, both sort orders, the Today grouping, idempotent create (including six concurrent double-submits), and the likely-duplicate warning and its exact matching rule.
- **Courses:** validation, idempotent create, archive keeps data, and delete requires the confirmed count and cascades in one transaction.
- **Time:** spring-forward gaps, fall-back overlaps, overdue exactly after 11:59 PM local, time zone changes, and "today" computed in the user's zone rather than UTC.
- **Scheduling rules (pure functions, no database):** adjacent events don't conflict, a one-minute overlap does, overnight events clash with the next morning, weekly expansion stays inside both the window and the series dates, classes keep their local time through daylight saving, a class start in the spring-forward gap moves forward, a repeated fall-back time uses the first one, and a class defined in another zone keeps that zone's wall time.
- **Reminders (53 tests):** each reminder delivered at most once: no second delivery after editing the title or description, saving unchanged settings, resyncing repeatedly before and after delivery, syncing while the worker holds the job, completing and reopening, a time zone change, or turning email on late, while a new deadline or lead time still gets its replacement; when reminders fire (9:00 AM for day-based leads on date-only deadlines, counting back from midnight for hour-based ones, immediately if the time has passed but the deadline hasn't); rescheduling on edit, completion, reopening, archiving, time zone changes, and email opt-in; four concurrent resyncs leaving exactly one pending reminder; three concurrent workers claiming 30 jobs with no job claimed twice; a crashed worker's job reclaimed after its lease, with the late worker unable to deliver it again; a job that keeps crashing its worker failing after 5 attempts; backoff of 1 then 4 minutes with a stable idempotency key across retries; permanent errors failing without retries; the outage policy; rechecking a reminder completed or rescheduled after it was claimed; provider confirmation moving `accepted` to `delivered` or `failed`; capture mode; the recipient allowlist; Resend request shape and error classification (with simulated HTTP); log masking; isolation between users; and the worker's command line.
- **Migrations (7 tests, each on its own disposable database):** a database populated at the revision before reminder identity is upgraded with Alembic and then exercised with the real sync and worker code, with the clock at 2026-10-16 12:00 UTC: a deadline moved from Oct 15 (reminder sent) to Oct 20 keeps its Oct 20 replacement through repeated resyncs and delivers it exactly once, keeping delivery ids and notification links, both on a straight upgrade and on a database where the faulty backfill had already run; an unchanged deadline is never delivered again; a replacement the faulty identity already cancelled is restored by `--resync`; unverifiable records are flagged and kept; and a fresh install migrates up, down, and up again with no drift from the models.
- **Demo account (8 tests):** the reset builds the documented data relative to today, wipes visitors' changes and signs everyone out, 503 while no demo exists, the per-IP rate limit, no password change, deletion, or email for the demo, no access to anyone else's data, no email even if the flag is forced on, and the reserved address can't be registered.
- **Production plumbing:** host database URLs, the recipient allowlist format, rate limits that ignore `X-Forwarded-For` entries a visitor wrote, security headers on every response, serving the built frontend (deep links, immutable asset caching, `/api` 404s staying JSON, no path traversal), creating the demo only when missing, and Settings saying when this server can't email you.
- **Calendar API:** validation (end after start, unique weekdays, series at most a year), conflict warnings for one-time and recurring events with **Save anyway**, deadlines that never block time, overnight events, editing a whole series, idempotent create, bounded windows, archived courses' events hidden and ignored for conflicts, events on Today, deleting a course with its events, and isolation between users.

Nine frontend unit tests cover the week grid's geometry (overnight events split across two days, overlapping events side by side, adjacent ones not, the visible hour range) and when a time-based label must next update.

The Playwright test walks one student through the app: register, add a course, add an overdue assignment, trigger the duplicate warning and the discard prompt, add a weekly class and a clashing meeting on the calendar, save past the conflict warning and see both flagged in the agenda, search, complete, reload, sign out, and sign back in. A second test checks the wrong-password error. A third runs the whole reminder path: it creates an assignment whose reminder is due, runs the real worker process against the test database, finds the reminder in Notifications, opens it to see its delivery history, and checks that a second worker run delivers nothing twice. A fourth uses Playwright's controlled clock: it leaves Today open, moves time past an event's end, and checks that "Ended" appears without any request to the API. A demo test signs in with one click, checks the banner and the disabled settings, and scans it with axe. Two keyboard tests do the four [usability-test](docs/usability-test.md) tasks (create a course, add an assignment, schedule a conflicting event, find an overdue task) with the keyboard alone, checking that every control they reach shows a focus ring, and that Escape returns focus to whatever opened a dialog. Writing them found three real bugs, all fixed: the course color picker had nine Tab stops and no arrow keys, and closing a dialog or an inline form dropped focus to the top of the page.

An automated accessibility scan ([`frontend/e2e/accessibility.spec.ts`](frontend/e2e/accessibility.spec.ts)) runs [axe-core](https://github.com/dequelabs/axe-core) against WCAG 2.2 AA on every page and dialog (sign-in and registration, Today, the week grid and agenda, assignments, courses, notifications, settings, the assignment dialog, and the event dialog showing a conflict warning) in light and dark mode, at desktop and phone widths: 48 scans per run. Any violation fails CI, and so does anything the Content-Security-Policy blocks, since these tests run against the production build with its real headers. Its first run found three real problems, all fixed: finished classes on Today were faded with opacity, which dropped their text below 4.5:1 contrast; deadline chips were smaller than the 24px minimum touch target; and the phone's "New" button had no clear name for screen readers. A scanner can't judge everything (whether a label makes sense, or how a screen reader announces a flow), so that still needs a manual pass.

## Deployment

Everything runs on free tiers. Nothing here needs a credit card.

| Piece | Where | How |
| --- | --- | --- |
| Web app and API | [Render](https://render.com) free web service, Ohio | [`Dockerfile`](Dockerfile), described by [`render.yaml`](render.yaml) |
| Database | [Neon](https://neon.tech) free PostgreSQL 18, us-east-2 | Direct (unpooled) connection string |
| Reminder worker | GitHub Actions, every 30 minutes | [`.github/workflows/scheduled.yml`](.github/workflows/scheduled.yml) runs `python -m app.worker --once` |
| Nightly demo reset | GitHub Actions, 08:00 UTC | Same workflow, `python -m app.demo` |
| Email | [Resend](https://resend.com) free plan | Test sender; delivers only to the account owner |

**The image** builds the frontend in a Node stage, then installs the locked Python dependencies with uv and copies the built files in. On start it runs `alembic upgrade head`, creates the demo account if it doesn't exist yet (`python -m app.demo --if-missing`), and starts uvicorn as a non-root user. Migrations run at start rather than as a separate release step so it works the same on any host. That's safe here because the free plan runs a single instance.

**First-time setup:**

1. **Neon:** create a project with PostgreSQL 18 in us-east-2, and copy the *direct* connection string. Turn pooling off: the app sets the session time zone to UTC as a connection option, which a transaction pooler can drop.
2. **Render:** go to New → Blueprint and pick this repository. Render reads `render.yaml` and asks for the two values that are kept out of git: `CAMPUSFLOW_DATABASE_URL` (the Neon string) and `CAMPUSFLOW_EMAIL_ALLOWED_RECIPIENTS` (the address you signed up to Resend with).
3. **GitHub:** under Settings → Secrets and variables → Actions, add the secrets `CAMPUSFLOW_DATABASE_URL`, `CAMPUSFLOW_RESEND_API_KEY`, and `CAMPUSFLOW_EMAIL_ALLOWED_RECIPIENTS`, and the variable `CAMPUSFLOW_APP_URL` (the Render URL, used for links in emails).

**Deploys:** every push to `main` builds and deploys on Render. Render waits for `/api/health`, which runs `SELECT 1` against the database, before switching traffic to the new version. To roll back, redeploy an earlier deploy from the Render dashboard. Migrations so far only add columns and tables, so the previous version still runs against the newer schema.

**Logs:** the Render dashboard shows the app's logs. Each request is one line (`GET /api/today 200 12.3ms`), with the path only: no query strings, cookies, or bodies. Worker and demo-reset runs log to their GitHub Actions run, with each job's id, its outcome, and a masked address (`a***@example.edu`). To run either job right away, go to Actions → Scheduled jobs → Run workflow.

**Limits of the free setup (read before relying on it):**

- **The web service sleeps after 15 idle minutes.** The next visit wakes it, which takes about a minute.
- **Reminders can be up to about 30 minutes late**, because the worker runs every 30 minutes instead of continuously, and GitHub can start scheduled runs later than planned. A late run still sends everything that came due, and the [outage policy](#durable-reminders) applies. The 30-minute interval is a deliberate budget choice. Each run wakes the database, which then stays up about 5 minutes, and Neon's free plan includes 100 compute-hours a month per project. At the smallest compute size, waking every 15 minutes would use about 60 of those just for the worker, and every 30 minutes about 30. (That's an estimate, not a measurement.)
- **GitHub turns off scheduled workflows in a public repository after 60 days without activity.** Any push turns them back on.
- **Email only reaches the Resend account owner** until a domain is verified. Everyone else's reminders stay in the app, and Settings tells them so instead of offering a switch that can't work.
- **The demo is shared:** visitors can see each other's changes until the nightly reset.

## Performance and usability

- [`docs/performance.md`](docs/performance.md): the nine most common reads, timed against a synthetic dataset of 301 users and 46,500 assignments, with each query's plan. Every statement uses an index and runs in under 4 ms. The time per request (15–32 ms median) goes to Python, not the database.
- [`docs/usability-test.md`](docs/usability-test.md): a 15-minute script for testing with classmates, covering creating a course, adding an assignment, scheduling a conflicting event, and finding an overdue task.
- [`docs/feedback-log.md`](docs/feedback-log.md): the template for recording those sessions. It's empty because no sessions have been run yet.

## Key decisions

### One origin in production

FastAPI serves the built frontend as well as the API: real files as they are, any other path as `index.html` so the client-side router can handle deep links, and unknown `/api/...` paths stay JSON 404s. Fingerprinted files under `/assets` are cached for a year; everything else revalidates. One origin means the session cookie stays first-party (`SameSite=Lax`, `__Host-` prefix), there's no CORS, and the free plan only has to run one service. The tradeoff: static files are served by Python instead of a CDN, which is plenty for a planner and would be the first thing to move behind a CDN under real traffic.

Every response carries security headers. The Content-Security-Policy allows scripts, fonts, and styles only from the same origin, plus inline styles, which the toast library injects. HSTS is sent in production. The browser tests run under this exact policy and fail on anything it blocks.

### Client IPs behind a proxy

Rate limits count per IP, but behind Render's proxies the TCP peer is a proxy, so every visitor would share one limit. The obvious fix, trusting `X-Forwarded-For`, is spoofable: a client can send its own header, and the proxies append to it rather than replacing it. So the app takes the entry exactly `CAMPUSFLOW_TRUSTED_PROXY_HOPS` places from the right, which is the one the platform's own proxy wrote. Anything further left is ignored. A test sends thirty demo logins with a different made-up address each time and still hits the limit. If the header ever has fewer entries than expected, the app logs a warning and falls back to one shared bucket, so the limit is never skipped.

### Why a scheduled job instead of a worker service

Render's free plan has no background workers, and a worker that polls all day would keep the free database awake around the clock. The worker was already built to run as `--once` (claim everything due, process it, exit) with leases that survive crashes. So a GitHub Actions schedule can run it with no code changes, and a paid worker service would run the same command in a loop. The cost is lateness (see [Deployment](#deployment)), not correctness: a reminder is still delivered at most once, and still not after its deadline.

### Synchronous SQLAlchemy with `def` route handlers

Every handler is a plain `def`, not `async def`. FastAPI runs `def` handlers in a threadpool, so a blocking database call ties up one worker thread while the event loop keeps serving other requests. Mixing `async def` with a blocking driver would freeze the loop for every request during each query. Sync SQLAlchemy is also simpler to read, debug, and test. The tradeoff is that concurrency is bounded by the threadpool size (40 by default), which is far beyond what this app needs. A busier deployment would add API instances before needing async.

### Sessions instead of JWTs

Login creates a row in `sessions`, and the cookie holds a random 256-bit token. Only its SHA-256 is stored, so a leaked database can't be replayed as cookies. Logging out deletes the row, which ends the session immediately; a JWT can't be revoked that simply. The cookie is `HttpOnly` (unreadable by JavaScript), `SameSite=Lax`, and `Secure` in production. Sessions slide for 7 days of inactivity and end after 30 days regardless. Changing your password deletes every other session.

### CSRF and origins

Two layers:

1. Each session has its own CSRF token. The frontend gets it from `/api/auth/session` and sends it as `X-CSRF-Token` on every write. A malicious site can make the browser send the cookie, but it can't read the token.
2. Middleware rejects any `POST`, `PUT`, `PATCH`, or `DELETE` whose `Origin` header isn't allow-listed. This covers login and registration, which happen before a session exists.

CORS is configured explicitly for the same allow-list. Production serves the frontend and API from one origin, so the allow-list there is empty and CORS never comes into play.

### Login rate limiting in PostgreSQL

Failed logins are rows in `rate_limit_events`, keyed by email and by IP: 5 failures per email or 30 per IP in 15 minutes. Because the counter is in the database, the limit holds across multiple API instances and restarts. A transaction-scoped advisory lock per key stops parallel requests from all slipping under the limit at once. A successful login clears that email's failures, and old rows are pruned.

### Ownership is enforced twice

Every query filters by the user ID taken from the session, never from the request. Another user's record looks exactly like a missing one (404), so IDs can't be probed. As a backstop, assignments reference courses through a composite foreign key on `(user_id, course_id)` → `courses(user_id, id)`. Even a bug in the API couldn't link an assignment to someone else's course; PostgreSQL would refuse the row.

### Duplicates: two different problems

- **Double-clicks and retries** create true duplicates. The form generates a UUID when it opens and sends it as `Idempotency-Key`. It's stored on the row under a unique `(user_id, idempotency_key)` constraint, and the insert uses `ON CONFLICT DO NOTHING`. A repeat returns the original record (`200`, `Idempotent-Replayed: true`) even when both requests arrive at the same moment. The test fires six concurrent identical requests and expects exactly one row.
- **Likely duplicates** are the user's call. If the same course already has an assignment with the same title (trimmed, case-insensitive) on the same due date, the API answers `409 likely_duplicate` with the matches. The form shows them, and **Save anyway** resends with `allow_duplicate: true`.

### Archive vs. delete

Archiving sets `archived_at`. Every assignment stays, but the course is hidden from Today, lists, and pickers, and can't get new work until it's restored. Deleting first shows how many assignments and calendar events will go with it. The `DELETE` request must include those confirmed numbers (`?expected_assignments=7&expected_events=2`). The API locks the course row, recounts, and refuses with `409` if the number changed, so you never delete more than you agreed to. The cascade happens in the same transaction.

### Time zones

Each account has an IANA time zone, set from the browser at sign-up and changeable in Settings.

| Kind of deadline | Stored as | Example |
| --- | --- | --- |
| Date-only ("due Friday") | `due_date DATE` | `2026-10-23` |
| Exact time ("due Friday 5 PM") | `due_at TIMESTAMPTZ` in UTC | `2026-10-23 21:00:00+00` |

Rules, all computed on the server so every view agrees:

- **A date-only deadline never moves.** Due Oct 23 stays Oct 23 whatever your zone. It becomes overdue once Oct 23 has fully passed in your current zone, at local midnight after 11:59 PM. Example: due Oct 14 in New York is still on time at 11:59:59 PM EDT and overdue at 12:00 AM. If you switch to Los Angeles, it's overdue at midnight Pacific instead.
- **An exact deadline keeps its moment.** 9:00 AM in New York is 13:00 UTC. Switch to Los Angeles and it shows as 6:00 AM, the same instant. Switch to London and a 11:30 PM New York deadline shows as 4:30 AM the next day.
- **Times that don't exist are rejected.** On March 8, 2026, New York clocks jump from 2:00 to 3:00 AM, so "due 2:30 AM" gets a clear error.
- **Times that happen twice are flagged.** On November 1, 2026, 1:30 AM happens twice. CampusFlow uses the first one (EDT) and tells you so after saving.
- **Today and "next 7 days"** use your zone's calendar date, not UTC. At 10 PM in New York it's still today, even though it's already tomorrow in UTC.

Settings shows a live preview of what a time zone change will do before you save.

### Calendar: rules are stored, occurrences are computed

A weekly class is stored once, as a rule: days of the week, local start time, duration, its IANA zone, and first and last class dates. The API turns rules into concrete occurrences only for the window being viewed (at most 42 days), in [`backend/app/scheduling.py`](backend/app/scheduling.py), a module of pure functions with no database or web code. One-time events are stored as exact UTC instants.

The tradeoff: storing every occurrence as its own row would make queries trivial and allow cancelling one meeting, but editing a class would mean rewriting a semester of rows and keeping them in sync. Computing on read keeps one source of truth and makes "edit the series" a single update. The cost is CPU on every read (small: a week of a full schedule is a few dozen occurrences) and no per-occurrence exceptions yet. The edit dialog says so: "Changes apply to every meeting in this series."

Scheduling rules:

- **An event occupies [start, end).** A class ending at 10:00 and one starting at 10:00 don't conflict. Deadlines are markers and never occupy time.
- **Conflicts are a warning, not a block.** Saving an overlapping event returns `409 schedule_conflict` listing what it overlaps (including every future meeting of a weekly class). The form shows them, and **Save anyway** resends with `allow_conflicts: true`. Overlaps stay flagged on the grid, in the agenda, and on Today.
- **Overnight events work.** A 10 PM to 12:45 AM event ends the next day. The week grid draws it on both days, and it conflicts with anything early the next morning.
- **End must be after start.** For a one-time event that's checked on the exact instants. For a weekly class, an end time earlier than the start means "ends the next day".
- **Archived courses' events are hidden** and ignored for conflicts, and come back when the course is restored.

Time zones for events:

- **A class keeps its local time.** A Friday 9:00 class in New York is 13:00 UTC in October and 14:00 UTC after clocks fall back on November 1, 2026, and it shows as 9:00 both weeks.
- **A class keeps its own zone.** It's defined in the zone you were in when you created it. If you move to Los Angeles, that 9:00 New York class shows at 6:00 AM, which is when it actually happens. A one-time event keeps its instant, like an exact deadline.
- **Skipped times move forward for classes.** If a weekly class starts at 2:30 AM, the meeting on March 8, 2026 (when New York skips from 2:00 to 3:00) happens at 3:30 AM. For a single event, a time that doesn't exist is rejected with an explanation instead, since there's no series to keep consistent.
- **Repeated times use the first one.** On November 1, 2026, 1:30 AM happens twice; CampusFlow uses the first (EDT) and says so after saving a one-time event.

### Durable reminders

**The queue is a PostgreSQL table.** Each `reminders` row is one reminder on one channel (in-app or email) and is also the job the worker processes. A broker such as Redis or RabbitMQ would be one more service to run, secure, and back up, and wouldn't remove the need to keep scheduling state in the database. At this scale (a few jobs per student per week) PostgreSQL's row locks are enough. The tradeoff: workers poll (every 30 seconds by default) instead of being pushed work, so a reminder can arrive up to one poll interval late.

**Scheduling stays in sync with the data.** Every change that could affect a reminder (creating, editing, completing, reopening, deleting, archiving a course, changing time zone or email opt-in) recalculates that assignment's reminders in the same transaction, so the queue can't disagree with the assignment. The assignment row and its reminder rows are locked first: simultaneous edits wait their turn, and the worker, which claims with `SKIP LOCKED`, passes over them until the change commits. A partial unique index enforces at most one pending reminder per assignment and channel.

**Reminders from before the identity column.** A reminder records the deadline instant it was scheduled for, but not the time zone it was computed in or whether the deadline was date-only at the time. On upgrade, a reminder gets a due date only when two records agree: its recorded deadline is exactly the deadline the assignment's *current* due date produces in the user's *current* zone. One that matches the current exact-time deadline is identified by that instant. Anything else (the deadline moved, the zone changed, or the deadline switched between date-only and exact time) can't be reconstructed reliably: the same instant is local midnight for different dates in different zones, and could have been an exact-time deadline. Those reminders are kept unchanged as history, flagged `legacy_identity`, and matched only by their exact recorded instant, so they never block the current schedule. The tradeoff, chosen deliberately: if a date-only reminder was sent and the user then changed time zone *before* upgrading, that one reminder may be sent once more. Guessing instead could silently cancel a reminder the student needs.

**Each reminder is delivered at most once.** A reminder is identified by its channel, its lead time, and the due date (or the exact due instant for timed deadlines), not by when it fires. Once one has been delivered, captured, accepted, failed, or skipped, or while a worker is sending it, it is never scheduled again. Only a cancelled one, which was never attempted, can come back.

| Change | What happens to the reminder |
| --- | --- |
| Edit the title, description, or course; save unchanged settings; run `--resync` | Nothing. A waiting reminder keeps its row, a sent one stays sent |
| Change the due date, due time, or lead time | It's a different reminder: the waiting one is cancelled and the new one scheduled (even if the old one was already sent) |
| Change time zone | A waiting date-only reminder moves to 9:00 AM in the new zone; one already sent isn't sent again |
| Complete, then reopen | Completing cancels waiting reminders. Reopening brings back ones that were never sent; ones already sent stay sent |
| Turn email off, then on | Off cancels waiting emails. On adds email only to reminders that haven't fired yet, never a late copy of one already shown in-app |

**When a reminder fires:**

| Deadline | Reminder | Fires |
| --- | --- | --- |
| Fri 5:00 PM (exact time) | 3 hours before | Fri 2:00 PM |
| Friday (date only) | 1 day before | Thu 9:00 AM, your time |
| Friday (date only) | 1 hour before | Fri 11:00 PM (an hour before it becomes overdue) |
| Due tomorrow | 1 week before | As soon as it's saved, since the time has passed but the deadline hasn't |

Date-only reminders follow your current time zone, like the overdue rule, so changing zones reschedules them. Exact-time reminders keep their instant.

**How the worker processes a job:**

1. **Claim.** One short transaction selects due rows with `FOR UPDATE SKIP LOCKED`, marks them `processing`, stamps a lease (`locked_until`, 5 minutes) and the worker's id, and counts the attempt. Concurrent workers skip rows another worker has locked, so no job is claimed twice.
2. **Recheck.** The worker reloads the assignment. If it was completed or archived, rescheduled since, or email was turned off, the reminder is `cancelled` (so reopening can bring it back). If the deadline has already passed, it is `skipped`, which is final.
3. **Deliver, outside any transaction.** In-app: insert the notification (unique per reminder, so a repeat is a no-op) in the same transaction that marks the job delivered. Email: call the adapter with `Idempotency-Key: campusflow-reminder-<id>`.
4. **Record**, but only if this worker still holds the lease. If another worker took over, the result is discarded (and an in-app write rolled back).

**Retries.** Rate limits, 5xx responses, and network errors are retried after 1, 4, 16, and 64 minutes (plus up to 20% jitter), up to 5 attempts. Errors that retrying can't fix, such as an invalid API key or Resend's test-mode recipient restriction, fail immediately with the provider's message.

**Crash recovery.** If a worker dies mid-job, the lease expires and another worker reclaims the job. Each claim counts as an attempt, so a job that crashes every worker that touches it fails after 5 attempts instead of looping.

**Outage policy.** After downtime, reminders whose time has passed are still sent if their deadline hasn't passed. Once the deadline has passed, the reminder is skipped and recorded as missed: a late "due soon" message would be wrong, and the assignment already shows as overdue.

**Honest statuses.** `cancelled` means no longer wanted before it was sent; `skipped` means a final policy decision (the deadline passed first, or the address isn't allowed to receive email on this server). `captured` means saved to a local file and never sent. `accepted` means Resend took the message, which isn't proof of delivery. `delivered` requires evidence: the in-app notification exists, or Resend reports `delivered` when the worker asks for the message's last event. Bounces and complaints move the job to `failed`. The edit dialog shows these in plain words.

**Logs** record job ids, outcomes, and masked addresses (`a***@example.edu`); the API key never appears in logs or stored errors.

**Remaining risks:**

- If a worker stalls past its lease *while* Resend is processing its request, a second worker may send the same email. Resend's idempotency key drops the duplicate if it arrives within 24 hours.
- If the assignment changes in the moment between the recheck and the send, one stale email can go out. The new reminder is still scheduled correctly.
- If the time zone changes while an email is waiting between retries, the replacement email gets a new idempotency key. If Resend had in fact accepted the earlier attempt (a timeout after sending), that one email could arrive twice.
- Email reminders only reach the account owner until a domain is verified in Resend; others' email reminders fail with the provider's explanation, or are skipped by the allowlist. In-app reminders are unaffected.

## Design

The interface follows three design skills: Impeccable's rules for product UI, Emil Kowalski's design-engineering and motion guidance, and the Taste skill's app-screen study. Concretely:

- **Layout:** one typeface (Instrument Sans, chosen because it has true tabular figures for dates and counts) and flat surfaces split by 1px lines.
- **Color:** reserved for course identity, overdue status, and the single near-black primary action. Light and dark themes follow the OS.
- **Motion:** feedback only: buttons press to 0.97 scale, dialogs enter in 180ms with a strong ease-out, and everything respects reduced-motion settings.
- **States:** every block has real loading skeletons, empty states that say what to do next, and in-place errors with Retry.
- **Accessibility:** visible focus rings, labeled fields, a skip link, keyboard-reachable everything, 4.5:1 text contrast in both themes, and 24px minimum touch targets, checked automatically by axe on every push.
- **Forms never lose input:** a failed save keeps every field, and closing a form with edits asks first.

The week grid follows the same rules: course colors appear only as event tints and deadline dots, conflicts get a red edge and a warning icon (never color alone), and phones get the agenda, a list that works with screen readers, instead of a cramped grid.

The Taste inspector reports 0 failures on every screen. The main remaining warning is deliberate: calendar axis labels and event details use 12px text, because a week of classes has to fit on one screen. Editable designs (components, color variables, and the main screens in light and dark, including the week grid, the calendar event component, the conflict dialog, Today's schedule, the shared-demo banner, and the demo versions of Today, Settings, and sign-in) live in a Figma file in the project owner's drafts.

## Known limitations

- Editing a weekly class changes every meeting, past and future. Skipping or moving a single meeting (a holiday, a moved lab) isn't supported.
- Only weekly repetition. A series can run at most one year, and a one-time event at most 14 days.
- Events of archived courses are hidden from the calendar until the course is restored.
- Phones show the week as an agenda list only; the grid needs a wider screen.
- Accessibility is scanned automatically and the main tasks are tested by keyboard, but it hasn't had a manual pass with a screen reader (VoiceOver, NVDA).
- No usability sessions have been run yet. The script and log are ready, but there are no findings to report.
- On the free deployment: cold starts after 15 idle minutes, reminders up to about 30 minutes late, and email only to the owner's address (see [Deployment](#deployment)).
- One reminder per assignment, at fixed choices (1 hour to 1 week before). Emails are plain text.
- Notifications refresh once a minute in the browser rather than being pushed.
- Delivery confirmation is checked for 3 days after sending; later bounces aren't recorded.
- If Resend's daily quota is used up, retries run out before the quota resets at midnight UTC, and the email is marked failed.
- The "last course used" memory is per device (browser storage), not per account.
- Registering with an email that already exists says so. This lets someone check whether an address has an account, a tradeoff accepted for clearer sign-up errors. Registration is rate limited per IP.
- The assignment list returns at most 500 rows with no pagination. That's fast at the [measured](docs/performance.md) size, but the 500-row response is the slowest request.
- If the session expires while a form is open, saving shows a "sign in again" message and keeps the form, but signing in happens in a separate step.
- Real reminder email through Resend is set up on the live site but hasn't been confirmed end to end yet. The adapter is tested against simulated responses, and the scheduled worker runs successfully against the production database.
- The shared demo can be changed by anyone until the nightly reset, so a visitor may find it in an odd state.

## Project layout

```
backend/
  app/            FastAPI app: config, db, models, schemas, security, routers/
                  scheduling.py (pure recurrence and overlap rules), calendar.py (loading and shaping)
                  reminders.py (when reminders fire, keeping them in sync), worker.py (the worker process),
                  email.py (capture and Resend adapters)
  alembic/        migrations
                  demo.py (the shared demo account)
  scripts/        ensure_db.py, perf.py (synthetic dataset and timings)
  tests/          pytest suite (disposable PostgreSQL per run)
frontend/
  src/            React app: api.ts, pages/, components/, styles.css
                  calendarLayout.ts (+ .test.ts): week grid geometry
  e2e/            Playwright tests (against the production build)
docs/             performance.md, usability-test.md, feedback-log.md,
                  interview-walkthrough.md, screenshots/
Dockerfile        production image (frontend build + API)
render.yaml       Render Blueprint
.github/workflows/ci.yml          checks, browser tests, Docker smoke test
.github/workflows/scheduled.yml   reminder worker and nightly demo reset
```
