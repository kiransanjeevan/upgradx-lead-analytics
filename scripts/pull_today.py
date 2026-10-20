"""Pull TODAY's activity only and write data/today.json for the Centre hygiene report (Daily shares).

The hygiene table is shared at 12pm, 3pm and 6pm IST, so it needs data from minutes before each share.
The full pull (pull.py) re-reads every call since START and takes ~25 min — too slow and too costly to
run three times a day. This script reuses pull.py's own rules (call_log: a call and its Conversation
Log count once, connected = logged outcome or answered >= 10 s; walk-ins from the Conversation Log;
owner centre from the LSQ admission groups) but only for today's IST day, so its numbers are the same
as the full pull's for that day. Typical run: ~10 s and a few dozen read-only API calls (more on a busy day).

Writes data/today.json:
  {generated_at, day, activity_asof, centres: {centre: {called, conn, talk_s, cns, walk, book, amt}}}
Centre = the lead owner's centre for every column except talk_s, which is by the caller's centre
(as in pull.py talk_by_day). "No centre" and "Pool accounts" are kept; the report decides what to show.

Read-only: Leads.Get, Leads/Retrieve/ByIds, User/AdvancedSearch, CustomActivity/RetrieveByActivityEvent.
Credentials from LSQ_HOST / LSQ_ACCESS / LSQ_SECRET (GitHub Secrets in CI). Stdlib only.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import pull as P   # noqa: E402  reuse the full pull's rules, pointed at today

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "today.json")

# today's IST calendar day; pull.py's minute/day maths is relative to BASE, so moving BASE to today's
# IST midnight makes day 0 = today and makes call_log() read today's activities only
now_ist = datetime.now(timezone.utc) + timedelta(minutes=P.IST)
TODAY = datetime(now_ist.year, now_ist.month, now_ist.day)
P.BASE = TODAY
P.START = (TODAY - timedelta(minutes=P.IST)).strftime("%Y-%m-%d %H:%M:%S")
P.COLS = ("ProspectID,OwnerId,OwnerIdName,mx_First_Counselling_Date,mx_Latest_Counselling_Date,"
          "mx_First_Transaction_Date,mx_Total_Amount_Paid")


def post(path, body, tries=5):
    qs = urllib.parse.urlencode({"accessKey": P.ACCESS, "secretKey": P.SECRET})
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{P.HOST}/{path}?{qs}", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=180).read())
        except (OSError, ValueError):
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


def by_ids(ids):
    """Owner and outcome fields for the leads called today. Batches of 25: larger ByIds batches
    have silently dropped leads before (LSQ_KNOWLEDGE.md §2.2)."""
    out = {}
    for i in range(0, len(ids), 25):
        r = post("LeadManagement.svc/Leads/Retrieve/ByIds", {"SearchParameters": {"LeadIds": ids[i:i + 25]},
                 "Columns": {"Include_CSV": P.COLS}, "Paging": {"PageIndex": 1, "PageSize": 25}})
        if not (isinstance(r, dict) and isinstance(r.get("Leads"), list)):
            raise RuntimeError(f"Leads/Retrieve/ByIds batch {i // 25}: unexpected response — {str(r)[:200]}")
        for x in r["Leads"]:
            out[x["ProspectID"]] = x
    return out


def main():
    if not (P.ACCESS and P.SECRET):
        raise SystemExit("LSQ_ACCESS and LSQ_SECRET must be set (GitHub Secrets in CI).")
    print(f"today (IST) {TODAY:%Y-%m-%d}")
    calls = P.call_log()                                   # also fills P.WALKINS and P.TALK for today
    print(f"  call log: {sum(len(v) for v in calls.values()):,} contacts on {len(calls):,} leads")
    leads = {}
    # counselled or paid today, whether or not they were called today
    for lookup in ("mx_First_Counselling_Date", "mx_Latest_Counselling_Date", "mx_First_Transaction_Date"):
        for L in P.pull(lookup):
            leads[L["ProspectID"]] = L
    need = [pid for pid in set(calls) | set(P.WALKINS) if pid not in leads]
    leads.update(by_ids(need))
    print(f"  leads: {len(leads):,} ({len(need):,} fetched by id)")
    ucent = P.user_centres()

    day = lambda s: (lambda m: None if m is None else m // 1440)(P.mins(s))
    cen, last = {}, None
    get = lambda c: cen.setdefault(c, {"called": 0, "conn": 0, "talk_s": 0, "cns": 0, "walk": 0, "book": 0, "amt": 0})
    for pid, L in leads.items():
        c = P.lead_owner_centre(L.get("OwnerId"), L.get("OwnerIdName") or "", ucent)
        rows = [r for r in calls.get(pid, []) if r[0] // 1440 == 0]
        att = any(k in ("out", "log") for _, k, _ in rows)
        con = any(s for _, _, s in rows)
        walk = any(m // 1440 == 0 for m in P.WALKINS.get(pid, []))
        cns = day(L.get("mx_First_Counselling_Date")) == 0 or day(L.get("mx_Latest_Counselling_Date")) == 0
        book = day(L.get("mx_First_Transaction_Date")) == 0
        if not (att or con or walk or cns or book):
            continue
        o = get(c)
        o["called"] += att or con
        o["conn"] += con
        o["walk"] += walk
        o["cns"] += cns
        if book:
            o["book"] += 1
            o["amt"] += P.money(L.get("mx_Total_Amount_Paid")) if hasattr(P, "money") else 0
        if rows:
            last = max(last or 0, max(r[0] for r in rows))
    for (d, user), sec in P.TALK.items():
        if d == 0:
            get(ucent.get(user) or "No centre")["talk_s"] += round(sec)
    asof = (TODAY + timedelta(minutes=last)).strftime("%H:%M") if last is not None else None
    out = {"generated_at": datetime.now(timezone.utc).astimezone(P.ISTTZ).isoformat(),
           "day": f"{TODAY:%Y-%m-%d}", "activity_asof": asof, "centres": cen}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    tot = {k: sum(o[k] for o in cen.values()) for k in ("called", "conn", "cns", "walk", "book")}
    print(f"wrote {OUT}: {len(cen)} centres | {tot} | latest call {asof} IST")


if __name__ == "__main__":
    sys.exit(main())
