# Interview walkthrough

How to present CampusFlow in an interview, and how to answer the questions it tends to raise. Each topic links to the code, so you can open the file and walk through it on screen.

## Before the interview

- **Know how it was built, and say it plainly.** CampusFlow was built with an AI coding assistant, working from a written spec, milestone by milestone, with every change reviewed and tested. Interviewers increasingly ask about this. The strong answer is specific: what you decided (scope, stack, the tradeoffs below), what you checked, and what you'd now do yourself. Don't claim you typed every line if you didn't.
- **Be able to run it.** Know how to open the live demo, run the tests, and find a reminder in the database. Practice once with [the exercises](#exercises-to-do-before-the-interview).
- **Only quote numbers from this repo:** 162 backend tests at 97% line coverage, 15 browser tests plus 48 automated accessibility scans, and the timings in [`performance.md`](performance.md). No usability sessions have been run yet, so don't describe any results from them.

## The 30-second version

> CampusFlow is a planner for college students: courses, assignments, a weekly class schedule, and reminders before things are due, all in the student's own time zone. It's React and TypeScript on the front, FastAPI and PostgreSQL on the back, and a separate worker that delivers reminders. The interesting parts are the edges: deadlines that stay correct across time zones and daylight saving, recurring classes that are computed instead of stored, and a reminder queue in PostgreSQL that delivers each reminder at most once, even if a worker crashes. It's deployed for free, with a one-click demo account.

## The two-minute architecture tour

Open the README's architecture diagrams.

1. **One origin.** In production FastAPI serves both the API and the built React app, so the session cookie is first-party and there's no CORS ([`main.py`](../backend/app/main.py)).
2. **Every request:** the cookie gives a session token. Only its SHA-256 is stored, so a leaked database can't be replayed as cookies. `require_auth` loads the user, writes need a CSRF header, and every query filters by that user's id ([`deps.py`](../backend/app/deps.py)).
3. **Data:** users own courses, assignments, events, and reminders. Assignments point to courses with a composite foreign key on `(user_id, course_id)`, so even a buggy query can't attach your assignment to someone else's course: the database refuses.
4. **Reminders:** saving an assignment recalculates its reminder rows in the same transaction. A worker claims due rows with `FOR UPDATE SKIP LOCKED`, rechecks the assignment, and delivers in-app or by email ([`reminders.py`](../backend/app/reminders.py), [`worker.py`](../backend/app/worker.py)).
5. **Deployment:** a Docker image on Render's free plan, PostgreSQL 18 on Neon, and a GitHub Actions schedule that runs the worker every 30 minutes and resets the demo nightly.

## One request, end to end: checking off an assignment

A good thing to trace live, from the click to the database and back:

1. **The checkbox** in [`AssignmentRow.tsx`](../frontend/src/components/AssignmentRow.tsx) runs the `useSetCompleted` mutation from [`queries.ts`](../frontend/src/queries.ts), which sends the request with the CSRF token. The UI shows it as done only after the server confirms, so nothing appears saved that wasn't.
2. **The middleware** in [`main.py`](../backend/app/main.py) rejects the request if its `Origin` isn't allowed. `require_auth` then loads the session (sliding expiry) and checks the CSRF token.
3. **The handler** in [`routers/assignments.py`](../backend/app/routers/assignments.py) loads the assignment with `user_id = <session user>`, so another user's id looks exactly like a missing one (404). It sets `completed_at` and calls `sync_assignment`, which cancels the waiting reminders, all in one transaction.
4. **TanStack Query** refetches the views that changed (Today, the list, the counts).

## Deep dives: likely questions and how to answer them

### "How do you handle time zones?"

- Two kinds of deadline are stored differently. Date-only is a `DATE`, so "due Friday" is Friday wherever you are. Exact-time is a UTC `TIMESTAMPTZ`, so it keeps its instant.
- A date-only deadline becomes overdue at local midnight after the date, in the user's *current* zone. The tests cover 11:59 PM versus midnight, the spring-forward gap (a time that doesn't exist is rejected), and the fall-back overlap (a time that happens twice uses the first and says so).
- **A bug worth telling:** on PostgreSQL 18 six tests failed, because timestamps came back in the server's default zone. The fix pins every database session to UTC ([`db.py`](../backend/app/db.py)). The tests caught it on upgrade, before anyone else did.

### "Why compute recurring classes instead of storing each meeting?"

A weekly class is one row holding a rule. Occurrences are computed for the window being viewed, by pure functions in [`scheduling.py`](../backend/app/scheduling.py) that are tested without a database. One source of truth makes "edit the series" a single update. The cost is no per-meeting exceptions yet, and the UI says so. Classes keep their local wall time across daylight saving.

### "How does conflict detection work?"

Events occupy [start, end), so back-to-back classes don't conflict. Saving an overlapping event returns `409 schedule_conflict` with what it overlaps, including every future meeting of a weekly class. The user can choose **Save anyway**. It's a warning, not a block, because students do double-book on purpose.

### "Walk me through the reminder system." (the strongest topic)

- **The queue is a table.** No Redis or RabbitMQ: at a few jobs per student per week, PostgreSQL row locks are enough, and scheduling state has to live in the database anyway.
- **Claiming:** `SELECT … FOR UPDATE SKIP LOCKED`, then mark the rows `processing` with a 5-minute lease. Concurrent workers skip each other's rows. A test runs three workers over 30 jobs, and none is claimed twice.
- **Crash recovery:** if a worker dies, its lease expires and another worker reclaims the job. A late worker that comes back can't record its result, because it no longer holds the lease.
- **Email idempotency:** each send uses `Idempotency-Key: campusflow-reminder-<id>`, so if a retry follows a timeout after the email actually went out, Resend drops the duplicate.
- **At most once, by identity:** a reminder is identified by channel, lead time, and due date (or the exact instant for timed deadlines). Once it has fired, an edit to the title can't make it fire again, but moving the deadline creates a new reminder.
- **Honest statuses:** `accepted` (Resend took it) isn't `delivered` (Resend confirmed it). After an outage, a reminder whose deadline has passed is skipped rather than sent late.
- **A bug worth telling:** a reviewer found that a database migration backfilled each reminder's due date from the assignment's *current* date, so a reminder for a moved deadline could be suppressed. The fix fills it only where two records agree, flags the rest as legacy, and comes with a regression test that upgrades a real database at the old revision ([`test_migrations.py`](../backend/tests/test_migrations.py)).

### "How is it secured?"

- Argon2id password hashing. Server-side sessions that log out instantly. The `HttpOnly`, `Secure`, `SameSite=Lax` cookie with the `__Host-` prefix.
- Per-session CSRF tokens plus an `Origin` check, which also covers login and sign-up before a session exists.
- Rate limits stored in PostgreSQL, with advisory locks so parallel requests can't all slip under the limit.
- **Behind a proxy:** taking client IPs from `X-Forwarded-For` naively is spoofable, so the app trusts only the entry its own proxy added. There's a test for exactly that.
- A Content-Security-Policy that the browser tests run under.

### "How did you test it?"

Unit tests for the pure scheduling rules. API tests against a real, disposable PostgreSQL (concurrency included: six identical requests at once create one row). Migration tests on real upgrades. Playwright tests against the production build. axe accessibility scans in light and dark mode at two widths. A keyboard-only run of the usability tasks. CI also builds the Docker image and smoke-tests it. The keyboard test found three real focus bugs the scanner had missed, which makes a good story about why automated scans aren't enough.

### "Did you measure performance?"

Yes, against 46,500 synthetic assignments ([`performance.md`](performance.md)). Every query uses an index and runs in under 4 ms. Requests take 15–32 ms median in-process, and the time goes to Python, not the database. So no new index was justified, and the doc names the one change to make if that ever changes. Be clear that these are in-process numbers, not production latency.

## Tradeoffs to bring up yourself

| Decision | Gave up | Why it was worth it |
| --- | --- | --- |
| Sync SQLAlchemy, `def` handlers | Async concurrency | Simpler, and correct by default with a blocking driver. 40 threads is plenty |
| Sessions over JWTs | Stateless auth | Instant logout, and nothing to steal from the database |
| Queue in PostgreSQL | Push delivery | One fewer service, and transactional consistency with the data |
| Occurrences computed on read | Per-meeting edits | One source of truth |
| Free hosting, scheduled worker | Prompt reminders (up to ~30 min late), cold starts | $0, and the worker code is identical to the paid setup |
| One origin | CDN for static files | First-party cookies, no CORS, one service |

## What you'd do next

- Per-meeting exceptions for recurring classes (a holiday or a moved lab).
- A verified email domain, so reminders reach every user, not just the owner.
- Run the usability sessions and act on what they find.
- A screen-reader pass with VoiceOver and NVDA.
- Pagination for the assignment list.

## Exercises to do before the interview

1. Run the backend tests, then break the "overdue at midnight" rule in [`timeutil.py`](../backend/app/timeutil.py) and watch which tests fail.
2. In the demo, schedule something that clashes with the Thursday lab, then find the 409 response in the browser's network tab.
3. Locally, create an assignment due tomorrow with a 1-day reminder, run `uv run python -m app.worker --once`, and find the row in `reminders` with its status.
4. Read `claim()` in [`worker.py`](../backend/app/worker.py) and explain out loud what `SKIP LOCKED` prevents.
5. Explain in two sentences why `X-Forwarded-For` can't simply be trusted.
