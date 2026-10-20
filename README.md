# upGradX Lead Analytics

A password-protected dashboard for the upGradX (Learning Centre / offline) vertical.
It's a static single-page app that **fetches its data** from `data/*.json`, which GitHub Actions
regenerate from the LeadSquared API (read-only calls). Vercel is git-connected, so each data
commit redeploys automatically.

```
refresh.yml  (01:11 IST nightly, 07:53 backup)  ->  pull.py          ->  data/universe.json
                                                ->  pull_talktime.py ->  data/talktime.json
today.yml    (dispatched by an external timer)  ->  pull_today.py    ->  data/today.json
        -> git push -> Vercel redeploy -> index.html fetches data/*.json (Basic Auth via middleware.js)
```

## Reports
Overview · Speed to Lead · Talk Time · Lead Routing · Funnel & Lead Stage · Counsellor Scorecard ·
Centre hygiene (Daily shares). Effort & Connects, Lead Ageing and Internshala were retired on
19–20 Oct 2026 (folded into Overview / Speed to Lead; Internshala = the Sources filter); their old
`#hash` links still open the report that absorbed them.

## What's here
| File | Purpose |
|---|---|
| `index.html` | the whole dashboard |
| `data/universe.json` | one row per lead (35 columns) + `talk_by_day`; every report except Talk Time |
| `data/talktime.json` | per counsellor × IST day, last 14 complete days; Talk Time report |
| `data/today.json` | today's activity by centre; Centre hygiene's "today" (past days come from `universe.json`) |
| `scripts/pull.py` | full LeadSquared pull → `universe.json` (stdlib only) |
| `scripts/pull_talktime.py` | dialler call pull → `talktime.json` |
| `scripts/pull_today.py` | today-only pull (~10 s) → `today.json`; reuses `pull.py`'s rules |
| `.github/workflows/refresh.yml` | nightly cron (01:11 IST, backup 07:53 IST) + manual run |
| `.github/workflows/today.yml` | manual / API dispatch only (see below) |
| `middleware.js` | Basic-Auth gate for the whole site (user/pass from Vercel env) |
| `vercel.json`, `package.json` | Vercel config |

## Schedule
- **Nightly:** `refresh.yml` at 01:11 IST, with a 07:53 IST backup that no-ops if the primary landed.
  GitHub starts this repo's scheduled runs 4.5–5.5 h late, so data usually lands ~06:30 IST.
- **Intraday (Centre hygiene shares at 12pm / 3pm / 6pm):** `today.yml` has no cron (it would be
  hours late). An external timer (e.g. cron-job.org) dispatches it at 11:40 / 14:40 / 17:40 IST:
  `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/today.yml/dispatches`,
  body `{"ref":"main"}`, with a fine-grained token limited to this repo (Actions: read & write).
  You can also click **Run workflow** in the Actions tab before a share.
- Both workflows `git pull --rebase` before pushing, so they don't reject each other.

## One-time setup
1. Push the repo to GitHub, import it in Vercel (framework preset *Other*), and set Vercel env vars
   `DASHBOARD_USER` / `DASHBOARD_PASS` (avoid `:`); redeploy.
2. GitHub → **Settings → Secrets and variables → Actions**: secrets `LSQ_HOST`
   (`https://api-in21.leadsquared.com/v2`), `LSQ_ACCESS`, `LSQ_SECRET`. Optional variables:
   `LSQ_START` (default `2026-09-01`), `LSQ_POOLS` (default `Avtar LCoffline,Futworks Lc`).
3. Set up the external timer for `today.yml` (above).

## Running the pulls locally
```bash
export LSQ_HOST=... LSQ_ACCESS=... LSQ_SECRET=...
python scripts/pull.py            # ~25 min → data/universe.json
python scripts/pull_talktime.py   # → data/talktime.json  (TT_DAYS, TT_AS_OF optional)
python scripts/pull_today.py      # ~10 s → data/today.json
```
Data files are normally committed by the workflows; commit local data only if you mean to deploy it.

## Troubleshooting the daily refresh

### How to tell it failed

The dashboard says so — you should not need the Actions tab. The chip in the top bar
reads `Updated Xh ago · next ~Yh` in green, turns **amber past 30h** and **red past
54h**, and the banner under the title names the run that did not land.

The thresholds are deliberately loose (a cycle plus slack) because GitHub's scheduler
drifts by hours; a tight window would cry wolf most mornings. The cost is that a
*silently skipped* day looks healthy until it is ~30h stale.

### Why a scheduled run does not fire

On 13-09-2026 the scheduled run (then at 07:00 IST) never ran. Everything that could be checked was ruled
out, in this order:

| Checked | Result |
|---|---|
| Workflow disabled? | `state=active` |
| Workflow on the default branch? | yes — schedules only run on the default branch |
| 60-day inactivity auto-disable? | repo pushed the same day |
| Actions minutes exhausted? | a manual run succeeded hours later, so minutes were available |
| Bad cron syntax? | the same cron had fired the previous day |

What is left is GitHub's documented behaviour: **scheduled workflows run on a
best-effort basis and can be delayed or dropped entirely under load.** The evidence
fits — of the two scheduled opportunities this workflow has had, one started **4h31m
late** and one never ran. Nothing in this repo was wrong either time.

Two mitigations are in `refresh.yml`:

1. **The schedule absorbs the delay.** Primary is `41 19 * * *` (19:41 UTC = **01:11
   IST**), chosen so that even a 5-hour slip lands by ~06:30 IST. Measured delays on
   this repo: 4h32m, 5h21m, 5h24m — and the queue delay is *zero* every time, meaning
   GitHub creates the run late rather than waiting on a runner. Moving to an odd
   minute alone did not help.
2. **Running overnight is safe.** Only ~1% of assignments and last-activity events,
   and ~5% of lead creation, fall between 01:00 and 07:00 IST; the evening peak (62%
   of assignments, 20:00 IST) is long finished.
3. **A backup run** at `23 2 * * *` (07:53 IST) no-ops when the primary already
   landed, so a normal day is still one pull and one commit. Whichever lands first
   wins and the other stands down, so order does not matter if both slip.
4. **The pull fails loudly rather than short.** A page returning an error object, or
   the page cap being hit with a full final page, raises instead of silently writing
   a truncated file — a stale dashboard is safer than a quietly wrong one. Volume
   swings only *warn* (louder past 25%), because a real bulk reassignment moved 6,236
   leads on 14-09-2026 and must not break the pipeline.

### What to do when it is stale

```bash
gh workflow run refresh.yml -R kiransanjeevan/upgradx-lead-analytics   # ~25 min
gh run list -R kiransanjeevan/upgradx-lead-analytics --workflow refresh.yml --limit 3
```

A manual dispatch always runs, ignoring the freshness guard.

### Other things worth knowing

- **Always a day behind.** The newest complete day is yesterday, because today is
  partial at pull time. A 30-day window ending 12-09 on the 13th is correct.
- **Unmapped-source warnings** in the pull log list raw `Source` values landing in
  "Other" with real volume or money. They are a prompt to extend `grp()`, not errors.
- **Nobody is notified on failure.** The dashboard shows staleness but sends nothing.
  A `if: failure()` notification step is still worth adding.

## Notes
- **Security:** the LSQ key is read-only-used but is a *read/write* CRM key — keep it only in
  GitHub Secrets / your shell, never in the repo, and rotate it periodically.
- **Scale:** fine to ~1M leads on this setup. `data/universe.json` is committed each day; if the
  git history grows large over time, switch to Vercel Blob or a deploy-token push instead of committing.
- **Data:** contains no customer PII (source, dates, counsellor names, stage, counts only).
