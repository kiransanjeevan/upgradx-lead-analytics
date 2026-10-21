# CLAUDE.md — invariants for this repo

Project-specific rules. These are not style preferences; each one below exists
because getting it wrong has already produced a wrong number in a published
report. Read this before touching `scripts/`, `index.html` or anything under `reports/`.

---

## 1. Credentials and API safety

- Credentials come from the environment only: `LSQ_HOST`, `LSQ_ACCESS`, `LSQ_SECRET`.
  Never hardcode them, never write them into a file, never echo them into output.
  In CI they are GitHub Secrets.
- **Read-only calls only.** The LeadSquared endpoints used here are `Leads.Get`,
  `LeadsMetaData.Get`, `ProspectActivity.svc/Retrieve`,
  `ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent`,
  `ProspectActivity.svc/GetActivityDetails`, `ActivityTypes.Get`. Do not add a
  `Lead.Create`, `Lead.Update`, activity-post or any other write call. This
  instance is production; a write changes a real lead and can fire stage automations.
- `PageSize` maxes out at **1000**. `2000` returns HTTP 500. Don't "optimise" it upward.

## 2. Pool accounts — there are two, not one

```
Avtar LCoffline   the Aavataar bot queue
Futworks Lc       the Futwork vendor queue
```

Both are bulk holding pools; a lead sitting in either has **not** been worked by a
human counsellor. `Futworks Lc` was missed originally and every pool number in the
first analysis was wrong. The default lives in `scripts/pull.py`:

```python
POOL_OWNERS = {o.strip().lower() for o in
               (os.environ.get("LSQ_POOLS") or "Avtar LCoffline,Futworks Lc").split(",") if o.strip()}
```

Override with the `LSQ_POOLS` repo variable (also wired into `.github/workflows/refresh.yml`).
If a new bulk owner appears, add it there — `pull.py` prints a warning for
suspiciously large owners that aren't in the list.

## 3. "Pooled" and "worked" are independent flags

Do **not** treat "not pooled" as "worked", or "worked" as "not pooled". They are
two separate booleans and every segment table must be a 2×2, not a split.
Conflating them once reported a connect rate of 39.4% when the true rate among
genuinely worked leads was 68.9%.

Related: a **state field** on the lead (e.g. `mx_First_Call_Date_and_Time`) records
an *outcome*; the **activity log** records the *work*. Bot and vendor calling write
activities but leave the lead's call fields empty, so any "has this lead been
touched" question answered from lead fields alone will undercount. Proven with a
control group: 10 pooled leads, 20 logged bot calls, 0 call-field values.

## 4. Time zones

The API filters in **UTC**; the business reads everything in **IST**.

- When building a date filter, shift the IST window start back by 330 minutes.
- `mins()` in `pull.py` adds 330 and returns *minutes since IST midnight of `BASE_DATE`*.
  It is not a UTC timestamp. Don't compare its output to a raw API timestamp.
- `BASE_DATE` / `LSQ_START` is an **IST calendar date**, currently `2026-09-01`.
  Widening it multiplies both API calls and `universe.json` size — moving it from
  July to September cut the daily refresh from ~248 calls / 12.6 MB to ~86 calls / ~5 MB.

## 5. Activity event codes

| Code | Activity | Note |
|---|---|---|
| 22 | Outbound Phone Call | **human** calling |
| 21 | Inbound Phone Call | counts as attempted only once connected; never adds to the attempt count |
| 310 | Conversation Log Form_B2C | the form that drives stage automations |
| 328 | Aavataar ai LC Calling Activity | **bot** calling (not pulled since 20 Oct 2026) |
| 330 | Futwork LC Lead Qualification | vendor calling (not pulled since 20 Oct 2026) |
| 232 | Ownership Changed | how leads move between owners |
| 3002 | StageChange | system activity — **cannot** be bulk-retrieved |

Never report "calls" without saying whether you mean 22, 328, 330, or the union.

## 6. Never hardcode a number that appears in prose

Every figure written into report copy must be computed from the parsed data and
interpolated. A hardcoded `63%` survived in the Internshala artifact's narrative
after the underlying cohort changed to `57%` — the chart and the sentence next to
it disagreed for several versions.

When editing any `reports/*/template.html`, grep the prose for digits and confirm
each one is either interpolated or a genuinely fixed constant (a year, a code).
Counts of things parsed from a source file — centres, steps, paths, defects — are
always computed, never typed.

## 7. Report generators are the source of truth, not their HTML output

Each `reports/<name>/` holds `build.py` + `template.html` (committed) and writes to
`out/` from data in `data/` (both gitignored — see `.gitignore`).

- **Never hand-edit a generated HTML file.** Change the builder or its input and re-run.
- Paths resolve relative to the script via `Path(__file__).resolve().parent`, so the
  builder runs from any working directory.
- Inputs that live outside the repo are env-var overridable with a sensible default
  (e.g. `ROUTING_WORKBOOK`).
- Large raw API pulls and generated HTML are **not** committed. Only code is.
- The Internshala report was removed on 20 Oct 2026 (use the Sources filter on any report;
  `#internshala` opens the Overview). The old builder (`reports/internshala/`,
  `data/internshala-funnel.html`) is gone; any `reports/internshala/` folder left locally is
  gitignored leftovers. `pull.py bot_log()` is kept but no longer called (see §9, col 33).

## 8. Publishing artifacts

A report that already exists as a published artifact must be **republished to the
same URL**, not published fresh — a new publish creates a second artifact and the
shared link goes stale.

| Report | Artifact URL |
|---|---|
| Internshala funnel (retired 20 Oct 2026; don't republish) | https://claude.ai/artifact/FvCLwTGY61fKQhzUEoAnB9 |
| LSQ routing map | https://claude.ai/artifact/3ghUWW1wFjdfm6FMVb9gco |

Do not put PII in an artifact — no learner names, phone numbers or email
addresses. Counsellor and owner names are fine; they are internal staff.

## 9. `data/*.json` is machine-written — take the remote copy on conflict

Two workflows write data and push; Vercel is git-connected and redeploys on each push.

| Workflow | Trigger | Runs | Writes |
|---|---|---|---|
| `.github/workflows/refresh.yml` | cron 01:11 IST + backup 07:53 IST (backup no-ops if today's data landed) + manual | `scripts/pull.py`, then `scripts/pull_talktime.py` and `scripts/pull_naming.py` (both continue-on-error) | `data/universe.json`, `data/talktime.json`, `data/naming.json` |
| `.github/workflows/today.yml` | `workflow_dispatch` **only** — run by hand (Actions → Run workflow) before the 12pm / 3pm / 6pm shares. An external timer + fine-grained token was designed but Kiran declined it for now (20 Oct 2026) | `scripts/pull_today.py` (~10 s, today only) | `data/today.json` |

`today.json` feeds the Centre hygiene report's 12pm / 3pm / 6pm shares; past days come from
`universe.json`. There is no midday full pull any more (removed 20 Oct 2026) — GitHub starts this
repo's crons 4.5–5.5 h late, which is why the intraday run is not a cron. Both
workflows `git pull --rebase` before pushing so they don't reject each other.

This means a local push is often rejected as behind. Rebase, and resolve
`data/universe.json` in favour of the **remote's** newer data:

```bash
git pull --rebase origin main
git checkout --ours data/universe.json   # during a rebase, "ours" = upstream
git add data/universe.json && git rebase --continue
```

Then re-apply any local schema change (e.g. re-flagging pool owners) as a fresh
commit rather than keeping the stale local file.

`universe.json` is compact **arrays, not objects** — 35 columns per lead (0–34), order
defined by the `cols` list in `pull.py`. If you add a column, append it at the end
and update both the header list and `index.html`'s index constants together. Never
remove one: col 33 `bot_calls` is kept as an empty list (the bot pull stopped 20 Oct 2026)
so 34 `walkins` keeps its index. Top-level `talk_by_day` (talk seconds by IST day × the
caller's centre) feeds Centre hygiene.

`naming.json` (`scripts/pull_naming.py`, ~1.5 min): Source Campaigns of leads created in the last 7
days with owner centres, plus live Meta ad sets and lead-form centre options (Marketing API, read-only).
Its `RULES` list is a hand copy of LSQ's routing rules (export of 15 Oct 2026) and `PLANNED` holds the
agreed fixes (Mumbai, Kolkata, "Gurgaon", drop "bengal") — update both when Sales Ops changes LSQ.
The Meta part needs `META_ACCESS_TOKEN` / `META_AD_ACCOUNT_ID` GitHub secrets; without them the
previous Meta block is kept.

## 10. `index.html` conventions the code relies on

- **Reports:** Overview (`home`), Speed to Lead (`speed`), Talk Time (`talk`), Lead Routing
  (`routing`), Funnel & Lead Stage (`funnel`), Counsellor Scorecard (`owner`), Centre hygiene
  (`daily`, "Daily shares"), Campaign naming (`naming`, from `data/naming.json`). Retired hashes resolve through `ALIAS` (`attempts`/`connect` →
  Overview on Calling depth, `internshala` → Overview, `ageing` → Speed, `source` → Funnel).
  Keep old aliases; shared links depend on them.
- **One shared helper per idea — reuse, don't re-implement:** `tile(value,label,sub,cls,tip)`
  for every KPI tile; `srcGroupFilter()` for every Sources filter; `PG_NAME` / `progNameOf` for
  programme names; `INT_DEF` for the Interested definition; `CALL_RULE_ATT` / `CALL_RULE_CON`
  for the call-counting rule; `SMALL=5` small-sample rule. Use the shared label set (Attempted,
  % of leads, Uncalled, Attempts per lead, Connected, Connect %, Median 1st attempt, Within SLA,
  Overdue follow-ups, Counselled, Enrolled, Share of leads).
- **Every report's `render()` sets `window.__RERENDER=render`** — the global pool and
  business-hours switches call it to redraw in place. A new report must do the same.
- **No `//` comment on a line whose statement continues on the next line** (it comments out
  the rest of the expression). Put the comment on its own line.
- **Tooltip text keeps a literal `\n`** inside JS strings (the tip renderer splits on it);
  don't turn it into a real newline or `<br>`.

## 11. Do not auto-commit

Commit only when asked. This repo pushes to a git-connected Vercel project, so a
commit is a deploy.
