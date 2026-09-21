#!/usr/bin/env python3
"""
Build the Internshala funnel report (the published artifact).

WHAT IT DOES
    Reads three JSON files produced by fetch_data.py, flattens every lead into a
    compact row array, and injects the whole dataset into template.html in place
    of the __DATA__ placeholder. The result is a single self-contained HTML file
    whose toggles recompute client-side — no server, no API calls at view time.

THE THREE BASES (the page lets the reader switch between them)
    C  Created        CreatedOn >= window start. The acquisition cohort.
    I  Interested-in  mx_Interested_In_Date >= window start. Arrivals PLUS older
                      leads that re-expressed interest. Still an arrival basis —
                      it is NOT a measure of work done.
    A  Worked         >= 1 contact attempt inside the window, taken from the
                      activity log. The only basis that measures effort; most of
                      its leads arrived before the window.

ACTIVITY EVENT CODES (see fetch_data.py)
    22   Outbound Phone Call Activity      human counsellor call
    328  Aavataar ai LC Calling Activity   AI voice bot (created by "System")
    330  Futwork LC Lead Qualification     Futwork vendor tele-qualification
    Neither 328 nor 330 writes the lead-level call fields, which is why a
    bot-called lead still looks "never called" in any lead-field cut.

CONFIG (all optional env vars)
    INTERNSHALA_WINDOW_START  window start, YYYY-MM-DD   (default 2026-09-01)
    INTERNSHALA_AS_OF         "today" for cohort ageing  (default: system date)
    INTERNSHALA_DATA_DIR      input JSON folder          (default ./data)
    INTERNSHALA_OUT_DIR       output folder              (default ./out)

USAGE
    python3 fetch_data.py      # refresh the three JSON inputs (costs API calls)
    python3 build.py           # regenerate out/internshala-funnel.html
    # then publish out/internshala-funnel.html as the artifact
"""

import json, collections, datetime as dt, os
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("INTERNSHALA_DATA_DIR") or BASE / "data")
OUT = Path(os.environ.get("INTERNSHALA_OUT_DIR") or BASE / "out")
OUT.mkdir(parents=True, exist_ok=True)

# Window start. Everything on the page is relative to this date.
WIN = dt.date.fromisoformat(os.environ.get("INTERNSHALA_WINDOW_START") or "2026-09-01")
# "Today" for cohort ageing / the partial-cohort fade. Override to reproduce an
# older build exactly (the published v6 used 2026-09-19).
TODAY = dt.date.fromisoformat(os.environ["INTERNSHALA_AS_OF"]) if os.environ.get("INTERNSHALA_AS_OF") else dt.date.today()

POOLS = {"Avtar LCoffline", "Futworks Lc"}   # bulk holding accounts, not counsellors

raw = json.load(open(DATA / "internshala_raw.json"))          # all-time Internshala leads
hum = json.load(open(DATA / "act_sep.json"))["internshala"]   # event 22, per lead
bf = json.load(open(DATA / "act_sep_botfut.json"))            # events 328 / 330, per lead
bot, fut = bf.get("bot", {}), bf.get("futwork", {})


def pd(s):
    """Parse the several datetime shapes LeadSquared returns; None if absent."""
    s = (s or "").strip()
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f)
        except Exception:
            pass
    return None


SPEED = ['&lt;1h', '1–4h', '4–24h', '1–3d', '3d+']
ATT = ['0', '1', '2', '3', '4–5', '6+']


def sb(anchor, call):
    """Speed-to-lead bucket: minutes from anchor date to first call. -1 = never/before."""
    if not anchor or not call:
        return -1
    m = (call - anchor).total_seconds() / 60
    return -1 if m < 0 else 0 if m < 60 else 1 if m < 240 else 2 if m < 1440 else 3 if m < 4320 else 4


wk = lambda d: ((d - WIN).days) // 7 if d and d >= WIN else -1
dy = lambda d: (d - WIN).days if d and d >= WIN else -1
norm = lambda x: (x or '').strip().lower().title() or '(not set)'

rows = []
cens = {}
prgs = {}
for x in raw:
    pid = x["ProspectID"]
    c = pd(x.get('CreatedOn'))
    i = pd(x.get('mx_Interested_In_Date'))
    h, b, f = hum.get(pid), bot.get(pid), fut.get(pid)
    afirst = min([pd(z["first"]) for z in (h, b, f) if z and z.get("first")], default=None)
    cd = c.date() if c else None
    idt = i.date() if i else None
    ad = afirst.date() if afirst else None
    # a lead is in scope if ANY of the three bases puts it inside the window
    if not any(d and d >= WIN for d in (cd, idt, ad)):
        continue
    call = pd(x.get('mx_First_Call_Date_and_Time'))
    conn = pd(x.get('mx_First_Interaction_Date'))
    cons = pd(x.get('mx_First_Counselling_Date'))
    pay = pd(x.get('mx_First_Transaction_Date'))
    att = int(float(x.get('mx_Reached_Out_Attempts') or 0))
    inter = int(float(x.get('mx_Interacted_Count') or 0))
    consf = 1 if (str(x.get('mx_Is_Counselled_Lead')).lower() in ('true', '1', 'yes') or cons) else 0
    inwin = lambda d: 1 if (d and d.date() >= WIN) else 0
    cn = norm(x.get('mx_Offline_Centre_Name'))
    cn = '(not set)' if cn in ('—', '-', '(Not Set)') else cn.replace(' - Upgrad Learning Support Centre', '')
    pr = (x.get('mx_Program') or '(not set)').strip() or '(not set)'
    cens.setdefault(cn, len(cens))
    prgs.setdefault(pr, len(prgs))
    rows.append([
        wk(cd), dy(cd), wk(idt), dy(idt), wk(ad), dy(ad),
        1 if (x.get('OwnerIdName') or '') in POOLS else 0,
        1 if (call or att > 0 or inter > 0) else 0,
        1 if call else 0, 1 if conn else 0, consf, 1 if pay else 0,
        inwin(conn), inwin(cons), inwin(pay),
        sb(c, call), sb(i, call),
        0 if att == 0 else 1 if att == 1 else 2 if att == 2 else 3 if att == 3 else 4 if att <= 5 else 5,
        cens[cn], prgs[pr],
        (h or {}).get("n", 0), (b or {}).get("n", 0), (f or {}).get("n", 0)])

# column index map, mirrored in the template's JS
IDX = dict(wkC=0, dyC=1, wkI=2, dyI=3, wkA=4, dyA=5, pool=6, worked=7, called=8, conn=9,
           couns=10, paid=11, connW=12, counsW=13, paidW=14, spC=15, spI=16, att=17,
           cen=18, prog=19, hcalls=20, bcalls=21, fcalls=22)

mk = lambda key, step: [dict(label=(WIN + dt.timedelta(days=i * step)).strftime('%d %b'),
                             dow=(WIN + dt.timedelta(days=i * step)).strftime('%a'),
                             age=(TODAY - (WIN + dt.timedelta(days=i * step))).days,
                             partial=(TODAY - (WIN + dt.timedelta(days=i * step))).days < 14)
                        for i in range(max(r[key] for r in rows) + 1)]

cenL = [k for k, _ in sorted(cens.items(), key=lambda kv: kv[1])]
prgL = [(k[:46] + '…') if len(k) > 47 else k for k, _ in sorted(prgs.items(), key=lambda kv: kv[1])]
nC = sum(1 for r in rows if r[0] >= 0)
nI = sum(1 for r in rows if r[2] >= 0)
nA = sum(1 for r in rows if r[4] >= 0)
hc = sum(r[20] for r in rows if r[4] >= 0)
bc = sum(r[21] for r in rows if r[4] >= 0)

# Every label in the copy is derived from WIN / TODAY, so nothing goes stale when
# the window moves. (%-d is not portable, hence the explicit .day.)
WINLBL = f"{WIN.day} {WIN:%B %Y}"       # 1 September 2026
WINSHORT = f"{WIN.day} {WIN:%b}"        # 1 Sep
ASOF = f"{TODAY.day} {TODAY:%b}"        # 19 Sep
SAME = (WIN.year, WIN.month) == (TODAY.year, TODAY.month)
RANGE = f"{WIN.day}–{ASOF}" if SAME else f"{WINSHORT}–{ASOF}"   # 1–19 Sep
PERIOD = WIN.strftime('%B') if SAME else "the window"           # September
OLDPCT = round(100 * sum(1 for r in rows if r[4] >= 0 and r[0] < 0) / max(nA, 1))

D = dict(
    rows=rows, IDX=IDX, cenLabels=cenL, progLabels=prgL, speedLabels=SPEED, attLabels=ATT,
    weeksC=mk(0, 7), weeksI=mk(2, 7), weeksA=mk(4, 7), daysC=mk(1, 1), daysI=mk(3, 1), daysA=mk(5, 1),
    totals=dict(C=nC, I=nI, A=nA), calls=dict(human=hc, bot=bc),
    standfirst=f"Internshala leads, window from {WINLBL}. Three ways to decide which leads belong in the window: "
               f"<b>created</b> ({nC:,}), <b>interested-in</b> ({nI:,}) and <b>worked</b> ({nA:,} leads with a contact attempt since {WINSHORT}). "
               f"Pick a basis below — every cut follows it.",
    meta=[["Source", "Internshala only"], ["Window", f"from {WINSHORT} {WIN.year} (IST)"],
          ["Bases", "Created / Interested-in / Worked"],
          ["Lead data", f"Leads.Get, read-only, {ASOF}"],
          ["Activity data", f"Activity log {RANGE} (human call 22, bot 328, Futwork 330)"]],
    bases=[dict(id='C', label=f'Created ≥ {WINSHORT}', note=f"Leads whose <code>CreatedOn</code> falls in the window — the acquisition cohort. {nC:,} leads."),
           dict(id='I', label=f'Interested-in ≥ {WINSHORT}', note=f"Leads whose <code>mx_Interested_In_Date</code> falls in the window — new arrivals plus older leads that re-expressed interest. {nI:,} leads."),
           dict(id='A', label=f'Worked ≥ {WINSHORT}', note=f"Leads with at least one contact attempt logged in the window — a human call, a bot call or a Futwork qualification — whenever the lead arrived. {nA:,} leads, {hc:,} human calls and {bc:,} bot calls.")],
    defs=[
        [f"Created ≥ {WINSHORT}", "<code>CreatedOn</code> in the window. Answers “how did the leads we acquired this month perform?”"],
        [f"Interested-in ≥ {WINSHORT}", "<code>mx_Interested_In_Date</code> in the window. Still an arrival basis — it just also counts older leads that re-signalled interest."],
        [f"Worked ≥ {WINSHORT}", f"At least one contact attempt in the window, from the activity log. This is the only basis that measures effort rather than arrival, and {OLDPCT}% of its leads arrived before {PERIOD}."],
        ["Human vs bot call", "<b>Human</b> = <code>Outbound Phone Call Activity</code> (event 22), logged by a named counsellor. <b>Bot</b> = <code>Aavataar ai LC Calling Activity</code> (event 328), created by System. Neither writes the lead call fields, which is why the bot is invisible in the funnel."],
        ["Pooled", "Owner is <code>Avtar LCoffline</code> (bot queue) or <code>Futworks Lc</code> (vendor) — not a counsellor."],
        ["Worked (lead-field)", "A first-call date, ≥1 logged attempt, or ≥1 interaction — human effort only, since neither bot writes these."],
    ],
    outcomeNote=f"<b>Outcome counting:</b> Connected, Counselled and Paid are shown as <b>ever</b> (the lead has reached that state at any time) with an <b>in-window</b> figure beside it (the first connect / counselling / payment happened on or after {WINSHORT}). "
                f"“Ever” keeps the funnel comparable across bases; “in window” is the honest measure of what {PERIOD} produced. They differ most on the interested-in and worked bases, which both contain older leads carrying outcomes earned earlier.",
    speedNote=f"Speed to lead is hidden on the <b>Worked</b> basis on purpose. It measures the gap between a lead arriving and the first call — but {OLDPCT}% of leads on this basis arrived before {PERIOD}, so the gap would be months and would say nothing about responsiveness. It is shown on the two arrival bases, where it is meaningful.",
    popNotes=dict(all="Everything in the window on the selected basis.",
                  unpool="Owned by a named counsellor or centre mailbox.",
                  pool="Parked in Avtar LCoffline or Futworks Lc. On the Worked basis this is nearly empty — pooled leads get almost no human calling.",
                  worked="Call, attempt or interaction recorded on the lead record.",
                  untouch="No logged activity on the lead record. Empty by definition on the Worked basis."),
    caveats=[
        ["Three bases, three questions", "Created = acquisition. Interested-in = acquisition + reactivation. Worked = effort. They are not interchangeable and the totals differ by design."],
        ["Activity data is a snapshot", f"The Worked basis is built from an activity-log pull covering {RANGE}. Nothing refreshes it automatically, unlike the lead fields — rerun fetch_data.py to update it."],
        ["Bot calls are invisible in the funnel", "Neither the Aavataar bot nor Futwork writes <code>mx_First_Call_Date_and_Time</code> or the attempt counter, so a bot-called lead still shows as never called in the lead-field cuts."],
        ["Attempts under-logged", "The attempts field reflects human dialling only, and even then is incomplete."],
        ["Cohort maturity", "Cohorts under two weeks old are faded and will still improve."],
    ],
    footer=f"upGrad X product analytics · Internshala · window from {WINSHORT} {WIN.year} · leads: LeadSquared Leads.Get ({ASOF}) · "
           f"activity: events 22/328/330, {RANGE} · {nC:,} created · {nI:,} interested-in · {nA:,} worked")

tpl = open(BASE / "template.html", encoding='utf-8').read()
dest = OUT / "internshala-funnel.html"
dest.write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False, separators=(',', ':'))), encoding='utf-8')

print(f"rows {len(rows):,} | created {nC:,} | interested {nI:,} | worked {nA:,} | human calls {hc:,} | bot calls {bc:,}")
w = [r for r in rows if r[4] >= 0]
print(f"  worked basis: pooled {sum(r[6] for r in w):,} | created-in-window {sum(1 for r in w if r[0] >= 0):,} | older {sum(1 for r in w if r[0] < 0):,}")
print(f"  in-window outcomes (worked basis): conn {sum(r[12] for r in w):,} couns {sum(r[13] for r in w):,} paid {sum(r[14] for r in w):,}")
print(f"  wrote {dest} ({dest.stat().st_size // 1024} KB)")
