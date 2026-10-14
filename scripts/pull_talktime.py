"""Daily talk-time pull for the dashboard's Talk Time report.

READ-ONLY. Calls Leads.Get, Leads/Retrieve/ByIds, User/AdvancedSearch and
CustomActivity/RetrieveByActivityEvent. Never writes to LeadSquared.

For every counsellor and every IST day in the window it writes one row:

  dials / answered / talk time      Ameyo outbound (22) + inbound (21) call activities
  active / idle (10:00-19:00 IST)   each call occupies [start, end] plus a wrap-up allowance
                                    (WRAP_MIN, default 5 min); overlapping spans are merged,
                                    clipped to the working day, and summed. Idle = day - active.
  active_span_s                     the same merged spans clipped to [first call start, last call
                                    start] instead, so the dashboard's Idle = (last - first) - this
                                    is never negative and covers calls outside the working day.
  connected leads                   unique leads with >=1 answered call that day, split into
                                    fresh-by-first-call (never called before that day),
                                    fresh-by-creation (created that day) and old
  landed / not attempted            leads created or assigned to the counsellor that day
                                    (current owner) with no human call from anyone by 19:00

Output: data/talktime.json (compact rows; header list in the file).

Env: LSQ_HOST, LSQ_ACCESS, LSQ_SECRET (required); TT_DAYS (default 7);
     TT_AS_OF (IST date, default yesterday); TT_WORKDAY ("10:00-19:00"); TT_WRAP_MIN (5);
     LSQ_POOLS (same pool list as pull.py).
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

HOST = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
ACCESS, SECRET = os.environ.get("LSQ_ACCESS"), os.environ.get("LSQ_SECRET")
IST = timedelta(hours=5, minutes=30)
DAYS = int(os.environ.get("TT_DAYS") or 7)
AS_OF = date.fromisoformat(os.environ["TT_AS_OF"]) if os.environ.get("TT_AS_OF") else (datetime.now(timezone.utc).replace(tzinfo=None) + IST).date() - timedelta(days=1)
WD0, WD1 = (os.environ.get("TT_WORKDAY") or "10:00-19:00").split("-")
WRAP = timedelta(minutes=int(os.environ.get("TT_WRAP_MIN") or 5))
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


def at(d, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m)


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
            s, e = ts(sd.get("StartTime")), ts(sd.get("EndTime"))            # Ameyo times, already IST
            if not s:
                base = ts(a.get("mx_Custom_2")) or ts(a.get("CreatedOn"))     # UTC
                s = base + IST if base else None
            if not s:
                continue
            dur = int(float(a.get("mx_Custom_3") or sd.get("CallDuration") or 0))
            if not e or e < s:
                e = s + timedelta(seconds=dur)
            calls.append({"ev": ev, "user": a.get("CreatedBy"), "name": who, "lead": a.get("RelatedProspectId"),
                          "start": s, "end": max(e, s + timedelta(seconds=dur)), "dur": dur,
                          "ans": (a.get("Status") or "").lower() == "answered"})
    print(d, "calls so far", len(calls), flush=True)


# ---------- leads: landed with a counsellor in the window, plus every lead called ----------
COLS = "ProspectID,CreatedOn,OwnerId,mx_Assignment_Date_Current_Owner,mx_First_Call_Date_and_Time"
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


HDR = ["user", "day", "dials", "inbound", "answered", "talk_s", "active_s", "hourly_active_min",
       "connected", "conn_fresh_first", "conn_fresh_created", "conn_old",
       "landed", "not_attempted", "first_call", "last_call", "calls_outside_hours", "active_span_s"]
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
    days = {t.date() for t in (ist_of(x.get("CreatedOn")), ist_of(x.get("mx_Assignment_Date_Current_Owner"))) if t}
    for d in days:
        if d in DAY_LIST:
            landed.setdefault((own, d), set()).add(pid)

for key in sorted(set(by) | set(landed), key=lambda k: (k[1], users.get(k[0], {}).get("name", ""))):
    uid, d = key
    cs = by.get(key, [])
    lo, hi = at(d, WD0), at(d, WD1)
    spans = [(c["start"], c["end"] + WRAP) for c in cs]
    active = union_seconds(spans, lo, hi)
    hourly = [round(union_seconds(spans, lo + timedelta(hours=h), lo + timedelta(hours=h + 1)) / 60)
              for h in range(int((hi - lo).total_seconds() // 3600))]
    conn = {c["lead"] for c in cs if c["ans"] and c["lead"]}
    fresh_first = {l for l in conn if first_call.get(l) and first_call[l].date() == d}
    fresh_created = {l for l in conn if ist_of(leads.get(l, {}).get("CreatedOn")) and ist_of(leads[l]["CreatedOn"]).date() == d}
    old = conn - fresh_first
    land = landed.get(key, set())
    not_att = {l for l in land if not (first_call.get(l) and first_call[l] <= hi)}
    dials = [c for c in cs if c["ev"] == 22]
    rows.append([uid, str(d), len(dials), sum(1 for c in cs if c["ev"] == 21), sum(1 for c in cs if c["ans"]),
                 sum(c["dur"] for c in cs if c["ans"]), active, hourly,
                 len(conn), len(fresh_first), len(fresh_created), len(old),
                 len(land), len(not_att),
                 min(c["start"] for c in cs).strftime("%H:%M") if cs else "",
                 max(c["start"] for c in cs).strftime("%H:%M") if cs else "",
                 sum(1 for c in cs if not (lo <= c["start"] < hi)),
                 union_seconds(spans, min(c["start"] for c in cs), max(c["start"] for c in cs)) if cs else 0])

agents = {uid: users[uid] for uid in {r[0] for r in rows} if uid in users}
for uid in {r[0] for r in rows} - set(agents):
    nm = next((c["name"] for c in calls if c["user"] == uid), "Unknown user")
    agents[uid] = {"name": nm, "login": "", "centre": "Not in user list"}

OUT.write_text(json.dumps({
    "generated_at": (datetime.now(timezone.utc).replace(tzinfo=None) + IST).strftime("%Y-%m-%d %H:%M IST"),
    "days": [str(d) for d in DAY_LIST], "workday": [WD0, WD1], "wrap_min": int(WRAP.total_seconds() // 60),
    "header": HDR, "agents": agents, "rows": rows, "api_calls": calls_api}, separators=(",", ":")))
print(f"wrote {OUT.name}: {len(rows)} counsellor-days, {len(agents)} counsellors, {len(calls)} calls, {calls_api} API calls")
