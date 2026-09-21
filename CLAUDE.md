# CLAUDE.md — invariants for this repo

Project-specific rules. These are not style preferences; each one below exists
because getting it wrong has already produced a wrong number in a published
report. Read this before touching `scripts/pull.py` or anything under `reports/`.

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
| 310 | Conversation Log Form_B2C | the form that drives stage automations |
| 328 | Aavataar ai LC Calling Activity | **bot** calling |
| 330 | Futwork LC Lead Qualification | vendor calling |
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
  (`INTERNSHALA_DATA_DIR`, `INTERNSHALA_OUT_DIR`, `INTERNSHALA_WINDOW_START`,
  `INTERNSHALA_AS_OF`, `ROUTING_WORKBOOK`).
- Large raw API pulls and generated HTML are **not** committed. Only code is.

## 8. Publishing artifacts

A report that already exists as a published artifact must be **republished to the
same URL**, not published fresh — a new publish creates a second artifact and the
shared link goes stale.

| Report | Artifact URL |
|---|---|
| Internshala funnel | https://claude.ai/artifact/FvCLwTGY61fKQhzUEoAnB9 |
| LSQ routing map | https://claude.ai/artifact/3ghUWW1wFjdfm6FMVb9gco |

Do not put PII in an artifact — no learner names, phone numbers or email
addresses. Counsellor and owner names are fine; they are internal staff.

## 9. `data/universe.json` is machine-written — take the remote copy on conflict

The daily GitHub Action (`.github/workflows/refresh.yml`, 07:00 IST) runs
`scripts/pull.py`, rewrites `data/universe.json` and pushes. Vercel is
git-connected and redeploys on that push.

This means a local push is often rejected as behind. Rebase, and resolve
`data/universe.json` in favour of the **remote's** newer data:

```bash
git pull --rebase origin main
git checkout --ours data/universe.json   # during a rebase, "ours" = upstream
git add data/universe.json && git rebase --continue
```

Then re-apply any local schema change (e.g. re-flagging pool owners) as a fresh
commit rather than keeping the stale local file.

`universe.json` is compact **arrays, not objects** — 21 columns per lead, order
defined by the header list in `pull.py`. If you add a column, append it at the end
and update both the header list and `index.html`'s index constants together.

## 10. Do not auto-commit

Commit only when asked. This repo pushes to a git-connected Vercel project, so a
commit is a deploy.
