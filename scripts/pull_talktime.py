"""Daily talk-time pull for the dashboard's Talk Time report.

READ-ONLY. Calls Leads.Get, Leads/Retrieve/ByIds, User/AdvancedSearch and
CustomActivity/RetrieveByActivityEvent (21, 22, 310). Never writes to LeadSquared.

For every counsellor and every IST day in the window it writes one row:

  dials / answered / talk time      Ameyo outbound (22) + inbound (21) call activities
  on_call_s                         time on calls, ringing + talking ([start, end], overlaps once);
                                    Ameyo's auto wrap-up timer is removed (see "wrap.timeout" below)
  first_call / last_call            start of the first call / END of the last call (HH:MM IST). The
                                    counsellor's own day is the span between them (no fixed office
                                    hours), so Total = last - first and it contains every call.
  active_s                          inside that day, time on calls
                                    plus every gap between calls of <= GAP_MIN (default 3 min)
                                    counts as active - dialling the next lead, writing notes.
                                    Longer gaps are idle, so the dashboard's Idle = span - active.
                                    Measured, not assumed: an earlier flat 5-min wrap-up after every
                                    call (answered or not) padded fast diallers' active time.
  longest_break_s / _at             the longest idle gap (> GAP_MIN) between calls and the HH:MM it
                                    began - tells one long break apart from many short ones
  hourly_active_min                 active minutes in each IST hour 00-23 (24 values)
  no_log                            answered calls with no LSQ Conversation Log (activity 310) by the
                                    same counsellor on the same lead from 5 min before the call to
                                    LOG_WINDOW_MIN (60) after it. The 310 form drives stage
                                    automations, so this is the compliance metric. (Ameyo's own
                                    outcome screen isn't: most who skip it log in LSQ instead.)
  connected leads                   unique leads with >=1 answered call that day, split into
                                    new (first-ever human call that day; the rest are follow-ups),
                                    and created-that-day (always a subset of new)
  landed / not attempted            leads created or assigned to the counsellor that day (current
                                    owner). Not attempted = no human call from anyone AFTER the lead
                                    landed, by the end of that IST day. Calls made before a
                                    reassignment don't count. Leads that landed after the
                                    counsellor's last call are left out (they had no chance);
                                    for someone who made no calls, every landed lead counts.

Calling effort (the Effort report's "calling activity" view): every outbound call (22) in the
window, totalled per IST day x caller x lead source — calls, unique leads dialled, leads
connected (answered), leads created that day, and leads getting their first-ever call that day.
Unlike the lead-level fields, this dates the work, so calls on older leads count on the day made.

Output: data/talktime.json (compact rows; header lists in the file).

Env: LSQ_HOST, LSQ_ACCESS, LSQ_SECRET (required); TT_DAYS (default 14);
     TT_AS_OF (IST date, default yesterday); TT_GAP_MIN (3); TT_LOG_WINDOW_MIN (60);
     LSQ_POOLS (same pool list as pull.py).
"""
import bisect, json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

HOST = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
ACCESS, SECRET = os.environ.get("LSQ_ACCESS"), os.environ.get("LSQ_SECRET")
IST = timedelta(hours=5, minutes=30)
DAYS = int(os.environ.get("TT_DAYS") or 14)
AS_OF = date.fromisoformat(os.environ["TT_AS_OF"]) if os.environ.get("TT_AS_OF") else (datetime.now(timezone.utc).replace(tzinfo=None) + IST).date() - timedelta(days=1)
GAP = timedelta(minutes=int(os.environ.get("TT_GAP_MIN") or 3))
POOLS = {o.strip().lower() for o in (os.environ.get("LSQ_POOLS") or "Avtar LCoffline,Futworks Lc").split(",") if o.strip()}
NON_COUNSELLORS = POOLS | {"lc team", "arrowhead lcoffline", "system", "upgrad dev user"}
OUT = Path(__file__).resolve().parent.parent / "data" / "talktime.json"

DAY_LIST = [AS_OF - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]
calls_api = 0


def post(path, body, tries=5):
    global calls_api
    qs = urllib.parse.urlencode({"accessKey": ACCESS, "secretKey": SECRET})
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{HOST}/{path}?{qs}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            raw = urllib.request.urlopen(req, timeout=180).read()
            calls_api += 1
            return json.loads(raw)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


def ts(s):
    s = (s or "").strip().replace("T", " ")
    for f, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(s[:n], f)
        except ValueError:
            pass
    return None


# ---------- users -> name, centre ----------
users = {}
p = 1
while True:
    r = post("UserManagement.svc/User/AdvancedSearch", {"Columns": "", "Paging": {"PageIndex": p, "PageSize": 100}})
    for u in r.get("Users") or []:
        groups = [g.strip() for g in (u.get("Groups") or "").split(",")]
        cs = [re.sub(r"^Admissions?[ .]*", "", g).replace("_LC", "").replace("Hyd_", "Hyderabad ")
              .replace("Blr_", "Bangalore ").replace("blr_", "Bangalore ").replace("Pune2", "Pune 2")
              .replace("Kolkata2", "Kolkata 2").replace("Bhubaneshwar", "Bhubaneswar")
              for g in groups if g.lower().startswith("admission")]
        name = re.sub(r"\s+", " ", f"{u.get('FirstName') or ''} {u.get('LastName') or ''}").strip()
        users[u["UserId"]] = {"name": name, "login": (u.get("EmailAddress") or "").split("@")[0],
                              "centre": " / ".join(sorted(cs)) or "No centre group"}
    if len(r.get("Users") or []) < 100:
        break
    p += 1


# ---------- call activities (21 inbound, 22 outbound) ----------
def activities(event, d):
    """All activities of one type logged on IST day d (paged)."""
    lo = datetime(d.year, d.month, d.day) - IST
    out, page = [], 1
    while True:
        r = post("ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent", {
            "Parameter": {"FromDate": lo.strftime("%Y-%m-%d %H:%M:%S"),
                          "ToDate": (lo + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                          "ActivityEvent": event, "RemoveEmptyValue": True},
            "Paging": {"PageIndex": page, "PageSize": 1000},
            "Sorting": {"ColumnName": "CreatedOn", "Direction": "0"}})
        L = r.get("List") or []
        out += L
        if len(L) < 1000:
            return out
        page += 1


def source_data(a):
    m = re.search(r"SourceData\{=\}(\{.*?\})\{next\}", a.get("ActivityEvent_Note") or "", re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}


calls = []
# logged-on days: the window plus the day after (calls are sometimes logged late)
for d in DAY_LIST + [AS_OF + timedelta(days=1)]:
    for ev in (22, 21):
        for a in activities(ev, d):
            who = (a.get("CreatedByName") or "").strip()
            if who.lower() in NON_COUNSELLORS:
                continue
            sd = source_data(a)
            # CreatedOn (UTC) + 5:30 equals Ameyo's StartTime to the second for ~99.9% of calls;
            # the rest carry a UTC StartTime, which put them 5.5 h early. So the clock comes from
            # CreatedOn and Ameyo only supplies the call's length (EndTime - StartTime).
            s0, e0 = ts(sd.get("StartTime")), ts(sd.get("EndTime"))
            base = ts(a.get("CreatedOn")) or ts(a.get("mx_Custom_2"))
            s = base + IST if base else s0
            if not s:
                continue
            dur = int(float(a.get("mx_Custom_3") or sd.get("CallDuration") or 0))
            e = s + (e0 - s0) if s0 and e0 and e0 >= s0 else s + timedelta(seconds=dur)
            calls.append({"ev": ev, "user": a.get("CreatedBy"), "name": who, "lead": a.get("RelatedProspectId"),
                          "start": s, "end": max(e, s + timedelta(seconds=dur)), "dur": dur,
                          "ans": (a.get("Status") or "").lower() == "answered",
                          "wrap_timeout": sd.get("CallNotes") == "wrap.timeout"})
    print(d, "calls so far", len(calls), flush=True)

# When the counsellor doesn't submit the call outcome, Ameyo holds the call open on its own
# wrap-up timer (~75 s) and EndTime includes it (CallNotes = "wrap.timeout"): ~111 s of non-talk
# time vs ~30-40 s when the outcome is submitted. That timer is not time on the phone, so those
# calls get the median ringing of normally closed calls (answered / not answered separately).
ring = {}
for ans in (True, False):
    v = sorted((c["end"] - c["start"]).total_seconds() - c["dur"] for c in calls if c["ans"] == ans and not c["wrap_timeout"])
    ring[ans] = timedelta(seconds=max(0, v[len(v) // 2])) if v else timedelta()
for c in calls:
    if c["wrap_timeout"]:
        c["end"] = min(c["end"], c["start"] + timedelta(seconds=c["dur"]) + ring[c["ans"]])
# ---------- Conversation Log (310): the LSQ form that records each call's outcome ----------
LOG_WIN = timedelta(minutes=int(os.environ.get("TT_LOG_WINDOW_MIN") or 60))
conv = {}
for d in DAY_LIST + [AS_OF + timedelta(days=1)]:
    for a in activities(310, d):
        t = ts(a.get("CreatedOn"))
        if t and a.get("RelatedProspectId"):
            conv.setdefault((a.get("CreatedBy"), a["RelatedProspectId"]), []).append(t + IST)
for v in conv.values():
    v.sort()


def has_log(c):
    v = conv.get((c["user"], c["lead"]), [])
    i = bisect.bisect_left(v, c["start"] - timedelta(minutes=5))
    return i < len(v) and v[i] <= c["start"] + LOG_WIN


print("median ringing (s): answered", ring[True].total_seconds(), "not answered", ring[False].total_seconds())


# ---------- leads: landed with a counsellor in the window, plus every lead called ----------
COLS = "ProspectID,CreatedOn,OwnerId,mx_Assignment_Date_Current_Owner,mx_First_Call_Date_and_Time,Source"
start_utc = (datetime(DAY_LIST[0].year, DAY_LIST[0].month, DAY_LIST[0].day) - IST).strftime("%Y-%m-%d %H:%M:%S")
leads = {}
for anchor in ("CreatedOn", "mx_Assignment_Date_Current_Owner"):
    p = 1
    while True:
        L = post("LeadManagement.svc/Leads.Get", {"Parameter": {"LookupName": anchor, "LookupValue": start_utc, "SqlOperator": ">="},
                                                  "Columns": {"Include_CSV": COLS}, "Paging": {"PageIndex": p, "PageSize": 1000}})
        for x in L:
            leads[x["ProspectID"]] = x
        if len(L) < 1000:
            break
        p += 1
need = list({c["lead"] for c in calls if c["lead"] and c["lead"] not in leads})
for i in range(0, len(need), 200):
    r = post("LeadManagement.svc/Leads/Retrieve/ByIds", {"SearchParameters": {"LeadIds": need[i:i + 200]},
             "Columns": {"Include_CSV": COLS}, "Paging": {"PageIndex": 1, "PageSize": 200}})
    for x in r.get("Leads") or []:
        leads[x["ProspectID"]] = x

ist_of = lambda v: (ts(v) + IST) if ts(v) else None
first_call = {}                                   # earliest known human call per lead
for pid, x in leads.items():
    f = ist_of(x.get("mx_First_Call_Date_and_Time"))
    if f:
        first_call[pid] = f
for c in calls:
    if c["lead"] and (c["lead"] not in first_call or c["start"] < first_call[c["lead"]]):
        first_call[c["lead"]] = c["start"]


# ---------- per counsellor-day rows ----------
def union_seconds(spans, lo, hi):
    spans = sorted((max(a, lo), min(b, hi)) for a, b in spans if b > lo and a < hi)
    total, cur_a, cur_b = 0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return int(total)


HDR = ["user", "day", "dials", "inbound", "answered", "talk_s", "on_call_s", "active_s", "hourly_active_min",
       "connected", "conn_fresh_first", "conn_fresh_created", "conn_old",
       "landed", "not_attempted", "first_call", "last_call", "no_log",
       "longest_break_s", "longest_break_at"]
rows = []
by = {}
for c in calls:
    d = c["start"].date()
    if d in DAY_LIST and c["user"]:
        by.setdefault((c["user"], d), []).append(c)
landed = {}
for pid, x in leads.items():
    own = x.get("OwnerId")
    if not own or own not in users or users[own]["name"].lower() in NON_COUNSELLORS:
        continue
    cr, asg = ist_of(x.get("CreatedOn")), ist_of(x.get("mx_Assignment_Date_Current_Owner"))
    for t in {cr, asg} - {None}:
        d = t.date()
        if d in DAY_LIST:      # landed at: the assignment if it happened that day, else creation
            landed.setdefault((own, d), {})[pid] = asg if asg and asg.date() == d else cr
call_times = {}
for c in calls:
    if c["lead"]:
        call_times.setdefault(c["lead"], []).append(c["start"])

for key in sorted(set(by) | set(landed), key=lambda k: (k[1], users.get(k[0], {}).get("name", ""))):
    uid, d = key
    cs = sorted(by.get(key, []), key=lambda c: c["start"])
    day0, eod = datetime(d.year, d.month, d.day), datetime(d.year, d.month, d.day) + timedelta(days=1)
    on_call = [(c["start"], c["end"]) for c in cs]
    # a short gap before the next call is work; a long one is idle
    work = [(c["start"], n["start"] if n["start"] - c["end"] <= GAP else c["end"]) for c, n in zip(cs, cs[1:])]
    # the day ends when the last call ends (capped at midnight), so Active always covers On calls
    first, last = (cs[0]["start"], min(max(c["end"] for c in cs), eod - timedelta(minutes=1))) if cs else (None, None)
    active = union_seconds(on_call + work, first, last) if cs else 0
    brk, brk_at, reach = 0, "", None          # longest idle gap; reach = latest call end so far (calls can overlap)
    for c in cs:
        if reach is not None and (c["start"] - reach) > GAP and (c["start"] - reach).total_seconds() > brk:
            brk, brk_at = int((c["start"] - reach).total_seconds()), reach.strftime("%H:%M")
        reach = c["end"] if reach is None else max(reach, c["end"])
    hourly = [round(union_seconds(on_call + work, max(first, day0 + timedelta(hours=h)), min(last, day0 + timedelta(hours=h + 1))) / 60)
              if cs and day0 + timedelta(hours=h + 1) > first and day0 + timedelta(hours=h) < last else 0 for h in range(24)]
    conn = {c["lead"] for c in cs if c["ans"] and c["lead"]}
    fresh_first = {l for l in conn if first_call.get(l) and first_call[l].date() == d}
    fresh_created = {l for l in conn if ist_of(leads.get(l, {}).get("CreatedOn")) and ist_of(leads[l]["CreatedOn"]).date() == d}
    old = conn - fresh_first
    land = landed.get(key, {})
    not_att = {l for l, t in land.items() if (not cs or t <= last)
               and not any(t <= ct < eod for ct in call_times.get(l, []))}
    dials = [c for c in cs if c["ev"] == 22]
    rows.append([uid, str(d), len(dials), sum(1 for c in cs if c["ev"] == 21), sum(1 for c in cs if c["ans"]),
                 sum(c["dur"] for c in cs if c["ans"]), union_seconds(on_call, day0, eod + timedelta(hours=6)), active, hourly,
                 len(conn), len(fresh_first), len(fresh_created), len(old),
                 len(land), len(not_att),
                 first.strftime("%H:%M") if cs else "",
                 last.strftime("%H:%M") if cs else "",
                 sum(1 for c in cs if c["ans"] and not has_log(c)), brk, brk_at])

# ---------- calling effort by source: what was dialled each day, on new leads and older ones ----------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pull import grp                               # the one source-bucket table in the repo
eff_src, eff_raw, eff = [], [], {}
def _ix(arr, v):
    if v not in arr:
        arr.append(v)
    return arr.index(v)
for c in calls:
    d = c["start"].date()
    if c["ev"] != 22 or d not in DAY_LIST or not c["user"]:
        continue
    x = leads.get(c["lead"]) or {}
    raw = (x.get("Source") or "").strip()
    e = eff.setdefault((str(d), c["user"], _ix(eff_src, grp(raw)), _ix(eff_raw, raw or "—")),
                       {"dials": 0, "leads": set(), "conn": set(), "new": set(), "first": set()})
    e["dials"] += 1
    l = c["lead"]
    if l:
        e["leads"].add(l)
        if c["ans"]:
            e["conn"].add(l)
        cr = ist_of(x.get("CreatedOn"))
        if cr and cr.date() == d:
            e["new"].add(l)
        if first_call.get(l) and first_call[l].date() == d:
            e["first"].add(l)
EFF_HDR = ["day", "user", "src", "raw", "dials", "leads", "connected", "new_leads", "first_calls"]
eff_rows = [[*k, e["dials"], len(e["leads"]), len(e["conn"]), len(e["new"]), len(e["first"])]
            for k, e in sorted(eff.items())]

agents = {uid: users[uid] for uid in {r[0] for r in rows} if uid in users}
for uid in {r[0] for r in rows} - set(agents):
    nm = next((c["name"] for c in calls if c["user"] == uid), "Unknown user")
    agents[uid] = {"name": nm, "login": "", "centre": "Not in user list"}

OUT.write_text(json.dumps({
    "generated_at": (datetime.now(timezone.utc).replace(tzinfo=None) + IST).strftime("%Y-%m-%d %H:%M IST"),
    "days": [str(d) for d in DAY_LIST], "gap_min": int(GAP.total_seconds() // 60),
    "header": HDR, "agents": agents, "rows": rows, "api_calls": calls_api,
    "effort_header": EFF_HDR, "effort_sources": eff_src, "effort_raw": eff_raw, "effort": eff_rows},
    separators=(",", ":")))
print(f"wrote {OUT.name}: {len(rows)} counsellor-days, {len(agents)} counsellors, {len(calls)} calls, "
      f"{len(eff_rows)} effort rows, {calls_api} API calls")
