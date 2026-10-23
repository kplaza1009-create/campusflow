# Usability test: script for classmates

A 15-minute session with one classmate at a time. The goal is to find where CampusFlow is confusing, not to grade the participant. Record what happens in [the feedback log](feedback-log.md).

## Before the session

- Use the live demo (**Try the demo** on the sign-in page) or a local copy with the demo loaded (`uv run python -m app.demo`). The demo has courses, classes, and overdue work already in it, so every task below makes sense.
- The demo is shared, and an earlier participant may have changed it. Reset it right before each session: from the Actions tab, run **Scheduled jobs** with **demo-reset**, or locally run `uv run python -m app.demo`.
- Open the app on the device the participant normally uses: their laptop or their phone. Note which one in the log.
- Have a timer and the feedback log ready.

## Introduction (read aloud)

> Thanks for helping. I'm testing the app, not you. If something is hard, that's the app's fault, and it's exactly what I need to find. Please think out loud as you go: what you're looking for, what you expect to happen, and what surprises you. I'll mostly stay quiet, and I can't help during a task, but you can stop anytime. I'm taking notes on what happens. I'm not recording your screen or your voice, and I won't write down your name. Is that OK?

Only continue if they say yes. Don't record audio or video unless they agree to that separately.

## Tasks

Read each task aloud exactly as written. Don't name buttons or pages. Start the timer when you finish reading and stop it when they say they're done or give up. A task fails if it takes longer than 3 minutes.

1. **Create a course.** "You just signed up for Organic Chemistry, course code CHEM 231. Add it to your courses."
   - Success: the course appears under Active courses with the right name and code.
2. **Add an assignment.** "Your first organic chemistry problem set is due this coming Friday at 11:59 PM. Add it."
   - Success: an assignment for CHEM 231, due Friday at 11:59 PM.
3. **Schedule a conflicting event.** "You're meeting your advisor this coming Wednesday from 10:15 to 10:45 AM. Put it on your calendar."
   - The demo has Calculus II on Mondays, Wednesdays, and Fridays from 10:00 to 10:50, so this clashes. Don't mention that.
   - Success: the event is saved, *and* the participant can say what it overlaps. Note whether they noticed the warning on their own.
4. **Find an overdue task.** "Is there anything you were supposed to have turned in already? Tell me what it is."
   - Success: they name at least one overdue assignment (the demo has two).

## After each task

Ask: "On a scale of 1 to 7, how easy or difficult was that, where 1 is very difficult and 7 is very easy?" Write down the number exactly as they say it.

## After all tasks

1. "What was the most confusing part?"
2. "Was anything missing that you expected to find?"
3. "Would you use something like this for your own classes? Why or why not?"

Thank them. Don't explain the "right" way to do a task until all their answers are written down.

## What to watch for

- Where they look first, and any wrong turns (a page they opened and left, a button they hovered but didn't press).
- Moments of hesitation longer than about 5 seconds.
- Anything they say out loud that shows a wrong expectation, written down word for word.
- Whether they read the conflict warning, or click past it.

## After the session

In the feedback log, record what you **saw**, not your interpretation. Fill in the issues table only from what actually happened in sessions, and mark how many participants hit each issue. Don't summarize results until at least three sessions are done, and don't report numbers from sessions that didn't happen.
