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

## Notes
- **Security:** the LSQ key is read-only-used but is a *read/write* CRM key — keep it only in
  GitHub Secrets / your shell, never in the repo, and rotate it periodically.
- **Scale:** fine to ~1M leads on this setup. `data/universe.json` is committed each day; if the
  git history grows large over time, switch to Vercel Blob or a deploy-token push instead of committing.
- **Data:** contains no customer PII (source, dates, counsellor names, stage, counts only).
