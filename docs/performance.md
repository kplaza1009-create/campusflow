# Performance

How fast CampusFlow's common reads are, measured against a synthetic dataset. Every number on this page comes from running `scripts/perf.py`. None are estimated.

## Dataset

`backend/scripts/perf.py` builds the dataset with a fixed random seed, and every date is relative to the day you run it.

| | Measured student | Each of 300 other students |
|---|---|---|
| Courses | 10 (2 archived) | 6 (1 archived) |
| Assignments | 1,500, due anywhere from a year ago to a year ahead; 40% have an exact time; 85% of past ones are completed | 150 |
| Weekly class series | 12 | 8 |
| One-time events | 300, within ±6 months | 20 |
| Notifications | 500 (5 unread) | 30 |
| Reminders | in-app, one for each open future assignment | none |

In total that's 301 users, 1,810 courses, 46,500 assignments, 8,712 events, 9,500 notifications, and 613 reminders. Most of the rows belong to the other students. That's deliberate: every query has to skip past them, so a missing per-user index would show up. A student who keeps 1,500 assignments has years of coursework in the app, so the measured student is a heavy user, not a typical one.

## Method

- Each request goes through the real FastAPI app in-process (Starlette's test client). The time includes session auth, routing, the SQL, Pydantic validation, and JSON serialization. It does **not** include network or TLS time.
- Each request gets 5 warm-up calls, then 100 timed calls. The table reports the median and the 95th percentile.
- `--explain` captures the SQL that each request runs and prints `EXPLAIN ANALYZE` for it.

To reproduce, run this from `backend/`. The script refuses any database whose name doesn't end in `_perf`, because it drops and rebuilds that database.

```bash
CAMPUSFLOW_LOG_LEVEL=WARNING \
CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@localhost:5432/campusflow_perf \
  uv run python -m scripts.perf --runs 100 --explain
```

## Environment

These measurements were taken on October 23, 2026, in a cloud development container, with the API and the database on the same machine:

- 2 vCPUs (Intel Xeon @ 2.80GHz), 7 GB RAM
- PostgreSQL 18.4 with default settings
- Python 3.12.3, FastAPI 0.159.1, SQLAlchemy 2.1.6, psycopg 3.3.6, Pydantic 2.14.1

## Results

| Request | Items returned | Median (ms) | p95 (ms) | SQL statements | Slowest statement's execution time (ms) |
|---|---:|---:|---:|---:|---:|
| Today (`/api/today`) | 109 | 20.6 | 27.7 | 3 | 0.73 |
| Assignments, open (default list, 500 cap) | 500 | 31.3 | 98.3 | 2 | 1.57 |
| Assignments, all, newest first | 500 | 31.8 | 102.5 | 2 | 3.99 |
| Assignments, overdue | 97 | 17.6 | 23.4 | 2 | 0.87 |
| Assignments, title search "lab" | 226 | 22.5 | 30.5 | 2 | 1.47 |
| Calendar, one week | 35 | 18.4 | 24.8 | 3 | 1.56 |
| Calendar, six weeks | 233 | 27.9 | 33.2 | 3 | 0.99 |
| Notifications | 50 | 15.0 | 22.6 | 3 | 0.05 |
| Courses | 8 | 15.0 | 18.5 | 3 | 0.40 |

An earlier run with 50 timed calls per request gave medians within 4 ms of these.

## What the numbers say

**The database isn't the bottleneck.** Every statement ran in under 4 ms, and most in under 1 ms. Courses returns 8 rows and runs 0.4 ms of SQL, yet takes 15 ms, so roughly 12–15 ms of each request is fixed overhead: the test client's thread hand-off, auth, and framework work. The rest scales with how many items come back. The two 500-item lists are the slowest, and their p95 jumps to about 100 ms, which is the cost of building and serializing 500 objects in Python, not the SQL.

**Every query uses an index**, and no plan does a sequential scan of a large table. These indexes were added in earlier milestones. This milestone checked their use with `EXPLAIN ANALYZE`:

| Query | Index used |
|---|---|
| Assignment lists, Today, calendar deadlines | `uq_courses_user_id_id` to find the user's courses, then `ix_assignments_dupe_lookup (user_id, course_id, lower(title))` per course |
| Calendar events | `ix_events_user_series`, on its leading `user_id` column: it fetches the user's 312 events, then filters them to the window (0.2 ms) |
| Notifications list | `ix_notifications_user_created` (scanned backward for newest first) |
| Unread count | `ix_notifications_user_unread` (partial index, index-only scan) |
| Session lookup | unique index on `sessions.token_hash` (the planner seq-scans a table this tiny; that's expected) |

**I didn't add any new indexes.** Nothing in these plans spends meaningful time outside an index. Two filters do scan all of one user's assignments instead of a date range: Today's "due within 7 days" and the calendar's deadline window. Both filter on the due date in the user's own time zone, which a plain index can't serve. At 1,500 assignments that scan costs 0.7–1.6 ms, about 5% of the request. Rewriting the filters to match ranges on `due_date` and `due_at` separately would let `ix_assignments_user_due_date` and `ix_assignments_user_due_at` serve them, but it isn't worth the extra code at this size. It's the first change to make if those requests get slow.

## Limits of these numbers

- **These are in-process numbers on a fast machine with a local database.** A real visitor also pays for the network round trip, TLS, and the hosting plan's CPU.
- The free hosting plan this project uses has much less CPU than this container. It also sleeps after 15 idle minutes, so the first visit afterwards waits for it to wake up. These numbers don't cover either effect.
- The measured student is a single, heavy user. Concurrent load wasn't tested.
