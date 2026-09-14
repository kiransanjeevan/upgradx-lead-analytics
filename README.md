# upGradX Lead Analytics

A password-protected dashboard for the upGradX (Learning Centre / offline) vertical.
It's a static single-page app that **fetches its data** from `data/universe.json`, which a
**daily GitHub Action** regenerates from the LeadSquared API. Vercel is git-connected, so
each refresh redeploys automatically.

```
GitHub Action (07:00 IST daily)  ->  scripts/pull.py  ->  data/universe.json  ->  git push
                                                                                      |
                                                              Vercel redeploy (git-connected)
                                                                                      |
                                          index.html  fetch('data/universe.json')  ->  dashboard
                                          (whole site gated by Basic Auth via middleware.js)
```

## What's here
| File | Purpose |
|---|---|
| `index.html` | the dashboard (home KPIs + 7 reports), fetches `data/universe.json` |
| `data/universe.json` | the data snapshot (refreshed daily by CI) |
| `middleware.js` | Basic-Auth gate for the whole site (user/pass from Vercel env) |
| `scripts/pull.py` | LeadSquared pull → writes `data/universe.json` (stdlib only) |
| `.github/workflows/refresh.yml` | daily cron + manual trigger |
| `vercel.json`, `package.json` | Vercel config |

## One-time setup

### 1. Push to GitHub
```bash
cd upgradx-lead-analytics
git init && git add -A && git commit -m "init: upGradX Lead Analytics"
git branch -M main
git remote add origin git@github.com:<you>/upgradx-lead-analytics.git
git push -u origin main
```

### 2. Connect Vercel
- vercel.com → **Add New… → Project** → import this repo → **Deploy** (framework preset: *Other*).
- Vercel → Project → **Settings → Environment Variables**, add (all environments):
  - `DASHBOARD_USER` — a username you choose
  - `DASHBOARD_PASS` — a password you choose (avoid `:`)
- **Redeploy** so the middleware picks up the vars. Opening the URL now prompts for user/pass.

### 3. Enable the daily refresh (GitHub)
- Repo → **Settings → Secrets and variables → Actions → New repository secret**, add:
  - `LSQ_HOST`  = `https://api-in21.leadsquared.com/v2`
  - `LSQ_ACCESS` = your LeadSquared access key
  - `LSQ_SECRET` = your LeadSquared secret key
- (Optional) add a repository **Variable** `LSQ_START` = `2026-09-01` (widen later to pull more history).
- The workflow runs daily at 07:00 IST. To run it now: repo → **Actions → Daily LSQ refresh → Run workflow**.

## Refreshing manually / locally
```bash
export LSQ_HOST=... LSQ_ACCESS=... LSQ_SECRET=...
python scripts/pull.py          # rewrites data/universe.json
git add data/universe.json && git commit -m "data refresh" && git push
```

## Troubleshooting the daily refresh

### How to tell it failed

The dashboard says so — you should not need the Actions tab. The chip in the top bar
reads `Updated Xh ago · next ~Yh` in green, turns **amber past 30h** and **red past
54h**, and the banner under the title names the run that did not land.

The thresholds are deliberately loose (a cycle plus slack) because GitHub's scheduler
drifts by hours; a tight window would cry wolf most mornings. The cost is that a
*silently skipped* day looks healthy until it is ~30h stale.

### Why a scheduled run does not fire

On 13-09-2026 the 07:00 IST run never ran. Everything that could be checked was ruled
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
gh workflow run refresh.yml -R kiransanjeevan/upgradx-lead-analytics   # ~10 min
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
