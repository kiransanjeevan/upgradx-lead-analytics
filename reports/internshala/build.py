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
    A  Touched        >= 1 outreach of any kind inside the window, taken from the
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

import json
import re, collections, datetime as dt, os
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
# Non-call touchpoints. Event 22 only fires for calls placed through an integrated
# dialler, so a counsellor ringing from a personal mobile and filling the Conversation
# Log Form leaves a 310 with no 22. These close that gap; the file is optional so an
# older data/ directory still builds.
_op = DATA / "act_other.json"
other = json.load(open(_op)) if _op.exists() else {}
OTHER = {}
for _chan, _d in other.items():
    for _pid, _v in _d.items():
        cur = OTHER.get(_pid)
        if not cur or (_v.get("first") and _v["first"] < cur.get("first", "9")):
            OTHER[_pid] = _v


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
    o = OTHER.get(pid)
    afirst = min([pd(z["first"]) for z in (h, b, f, o) if z and z.get("first")], default=None)
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
        (h or {}).get("n", 0), (b or {}).get("n", 0), (f or {}).get("n", 0),
        1 if (h or b or f or o) else 0])

# column index map, mirrored in the template's JS
IDX = dict(wkC=0, dyC=1, wkI=2, dyI=3, wkA=4, dyA=5, pool=6, worked=7, called=8, conn=9,
           couns=10, paid=11, connW=12, counsW=13, paidW=14, spC=15, spI=16, att=17,
           cen=18, prog=19, hcalls=20, bcalls=21, fcalls=22,
           # slot 7 ('worked') is LEAD FIELDS ONLY and neither bot writes them, so it
           # measures human effort. slot 23 is the activity log across every channel.
           touch=23)

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
# the window moves. Days are zero-padded (%d) to match the dashboard's DD Mon YYYY.
WINLBL = f"{WIN:%d %b %Y}"             # 01 Sep 2026 (dashboard-wide DD Mon YYYY)
WINSHORT = f"{WIN:%d %b}"               # 01 Sep
ASOF = f"{TODAY:%d %b}"                 # 19 Sep
SAME = (WIN.year, WIN.month) == (TODAY.year, TODAY.month)
RANGE = f"{WIN:%d}–{ASOF}" if SAME else f"{WINSHORT}–{ASOF}"   # 01–19 Sep
PERIOD = WIN.strftime('%B') if SAME else "the window"           # September
OLDPCT = round(100 * sum(1 for r in rows if r[4] >= 0 and r[0] < 0) / max(nA, 1))

D = dict(
    rows=rows, IDX=IDX, cenLabels=cenL, progLabels=prgL, speedLabels=SPEED, attLabels=ATT,
    weeksC=mk(0, 7), weeksI=mk(2, 7), weeksA=mk(4, 7), daysC=mk(1, 1), daysI=mk(3, 1), daysA=mk(5, 1),
    totals=dict(C=nC, I=nI, A=nA), calls=dict(human=hc, bot=bc),
    lede=f"Every Internshala lead since {WINLBL}: how many arrived, how many went to a bulk pool, who called them "
         f"(counsellors or the bot), and what came of it.",
    bases=[dict(id='C', label='Created in window', q="how did the leads we acquired perform?",
                note=f"<code>CreatedOn</code> on or after {WINSHORT} — the acquisition cohort ({nC:,} leads)."),
           dict(id='I', label='Interested in window', q="new arrivals plus older leads that came back",
                note=f"<code>mx_Interested_In_Date</code> on or after {WINSHORT} — still an arrival basis, but it also counts older leads that re-signalled interest ({nI:,} leads)."),
           dict(id='A', label='Touched in window', q="who did we actually work, whenever they arrived?",
                note=f"At least one outreach on record since {WINSHORT}, on any channel — the only basis that measures effort rather than arrival; "
                     f"{OLDPCT}% of these leads arrived before {WINSHORT} ({nA:,} leads).")],
    popNotes=dict(all="every lead on the selected basis.",
                  unpool="owned by a named counsellor or centre mailbox.",
                  pool="parked in the Avtar LCoffline (bot queue) or Futworks Lc (vendor) accounts. LSQ can't show how often these were attempted: Aavtaar only writes back completed calls, so dials that rang out never reach LSQ.",
                  worked="the lead's own call fields say a human worked it — a first-call date, an attempt or an interaction. Neither bot writes these fields, so this is human effort only.",
                  touch="at least one activity of any kind on the lead — human call, bot, Futwork, Conversation Log, WhatsApp, SMS or inbound.",
                  untouch="no activity of any kind on any channel. For pooled leads it's an upper bound (see Pooled). Empty by definition on the Touched basis."),
    defs=[
        ["Called (lead field)", "a first-call date on the lead — counsellor calls only, since neither bot writes it."],
        ["Connected · Counselled · Paid", f"shown as <b>ever</b> (the lead has reached that state at any time), with <b>since {WINSHORT}</b> beside it (the first connect, counselling or payment happened in the window). Ever keeps bases comparable; since-{WINSHORT} is what the window produced."],
        ["Human vs bot calls", "<b>Human</b> = Outbound Phone Call Activity (event 22) by a named counsellor. <b>Bot</b> = Aavataar ai LC Calling Activity (event 328), created by System."],
        ["Pooled", "owner is Avtar LCoffline (bot queue) or Futworks Lc (vendor) — not a counsellor."],
        ["Human-worked", "a first-call date, at least one logged attempt, or at least one interaction on the lead record."],
        ["Touched", "any outreach on record: human call (22), bot (328), Futwork (330), Conversation Log (310), WhatsApp (210), SMS (200), manual call log (203), inbound (21). No record doesn't prove no attempt."],
        ["Speed to lead", "hours from the basis date to the first counsellor call. Hidden on the Touched basis, where most leads arrived long before."],
        ["Centre", "the lead's own centre field (mx_Offline_Centre_Name), normalised for capitalisation — not the owner's centre used on the other reports."],
    ],
    speedNote=f"Speed to lead is hidden on the Touched basis: {OLDPCT}% of these leads arrived before {WINSHORT}, so the gap from arrival to first call would be months and say nothing about responsiveness.",
    caveats=[
        ["Three bases, three questions", "created = acquisition; interested = acquisition + reactivation; touched = effort. Their totals differ by design."],
        ["Bot calls are invisible in lead fields", "neither the Aavataar bot nor Futwork writes the first-call date or the attempt counter, so a bot-called lead still reads as never called in Called, Human-worked and Attempts."],
        ["Attempts under-logged", "the attempts field is human dialling only, and even that is incomplete."],
        ["Cohort maturity", "weeks under two weeks old are flagged; their outcomes will still rise."],
    ],
    provenance=[
        ["Leads", f"every Internshala lead from LeadSquared (Leads.Get, read-only), refreshed nightly; this build is as of {ASOF}."],
        ["Activity", f"the activity log {RANGE} (events 22, 328, 330, 310, 210, 200, 203, 21), refreshed nightly with the leads."],
        ["Totals", f"{nC:,} created · {nI:,} interested · {nA:,} touched · {hc:,} human and {bc:,} bot calls on touched leads."],
    ],
    asOfLbl=f"as of {ASOF}", winShort=WINSHORT, winLbl=WINLBL, period=PERIOD)

tpl = open(BASE / "template.html", encoding='utf-8').read()
dest = OUT / "internshala-funnel.html"
html = (tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False, separators=(',', ':')))
           .replace("__TITLE__", "Internshala"))
if "__" in re.sub(r"__[A-Z]+__", "", html):  # nothing else should look like a placeholder
    pass
for left in re.findall(r"__[A-Z]+__", html):
    raise SystemExit(f"unfilled placeholder {left} left in the output")
dest.write_text(html, encoding='utf-8')

print(f"rows {len(rows):,} | created {nC:,} | interested {nI:,} | worked {nA:,} | human calls {hc:,} | bot calls {bc:,}")
w = [r for r in rows if r[4] >= 0]
print(f"  worked basis: pooled {sum(r[6] for r in w):,} | created-in-window {sum(1 for r in w if r[0] >= 0):,} | older {sum(1 for r in w if r[0] < 0):,}")
print(f"  in-window outcomes (worked basis): conn {sum(r[12] for r in w):,} couns {sum(r[13] for r in w):,} paid {sum(r[14] for r in w):,}")
print(f"  wrote {dest} ({dest.stat().st_size // 1024} KB)")
