#!/usr/bin/env python3
"""
Fetch the three JSON inputs that build.py needs for the Internshala funnel report.

WRITES (into ./data, or $INTERNSHALA_DATA_DIR)
    internshala_raw.json   every Internshala lead, all-time, with the funnel fields.
                           A list of raw Leads.Get dicts.
    act_sep.json           human calls (activity event 22) inside the window, rolled
                           up per lead:  {"internshala": {ProspectID: {n, first, last, who}}}
                           plus pull stats (calls, mb, total, daily, top_callers).
    act_sep_botfut.json    the two automated channels, same per-lead shape:
                           {"bot": {...}, "futwork": {...}}

WHY THREE FILES AND NOT ONE JOIN
    The lead record only knows about human calling. Neither automated channel writes
    mx_First_Call_Date_and_Time or the attempt counter, so the only way to see bot
    effort is to pull the activity log separately and join on ProspectID. build.py
    does that join; this script just gets the raw material.

ACTIVITY EVENT CODES
    22   Outbound Phone Call Activity      human counsellor call
    328  Aavataar ai LC Calling Activity   AI voice bot, owner pool "Avtar LCoffline"
    330  Futwork LC Lead Qualification     Futwork vendor,  owner pool "Futworks Lc"

WINDOW
    The activity pulls cover [window start, as-of date + 1 day). The lead pull is
    all-time — build.py needs older leads so the "worked" basis can include a lead
    that arrived in July and was called in September.

CREDENTIALS — environment variables only, never hardcoded, never committed:
    LSQ_HOST    default https://api-in21.leadsquared.com/v2
    LSQ_ACCESS  access key
    LSQ_SECRET  secret key
Optional:
    INTERNSHALA_WINDOW_START  YYYY-MM-DD  (default 2026-09-01)
    INTERNSHALA_AS_OF         YYYY-MM-DD  (default: system date)
    INTERNSHALA_DATA_DIR      output folder (default ./data)

READ-ONLY: calls Leads.Get and RetrieveByActivityEvent. Never creates or updates.
Stdlib only. Costs a few hundred API calls and a few hundred MB of response —
run it when you actually need fresh numbers, not on every build.

USAGE
    export LSQ_ACCESS=... LSQ_SECRET=...
    python3 fetch_data.py
    python3 build.py
"""

import os, json, time, collections, datetime as dt
import urllib.request, urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("INTERNSHALA_DATA_DIR") or BASE / "data")
DATA.mkdir(parents=True, exist_ok=True)

WIN = dt.date.fromisoformat(os.environ.get("INTERNSHALA_WINDOW_START") or "2026-09-01")
END = dt.date.fromisoformat(os.environ["INTERNSHALA_AS_OF"]) if os.environ.get("INTERNSHALA_AS_OF") else dt.date.today()
END = END + dt.timedelta(days=1)   # exclusive upper bound, so "today" is included

HOST = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
QS = urllib.parse.urlencode({"accessKey": os.environ["LSQ_ACCESS"], "secretKey": os.environ["LSQ_SECRET"]})
LEADS = f"{HOST}/LeadManagement.svc/Leads.Get?{QS}"
ACTS = f"{HOST}/ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent?{QS}"

# Every field the funnel needs. Keep in sync with build.py's row builder.
COLS = ("ProspectID,CreatedOn,Source,OwnerIdName,ProspectStage,"
        "mx_Interested_In_Date,mx_Last_Interested_in_Date,mx_First_Call_Date_and_Time,"
        "mx_First_Interaction_Date,mx_Reached_Out_Attempts,mx_Interacted_Count,"
        "mx_Is_Counselled_Lead,mx_First_Counselling_Date,mx_First_Transaction_Date,"
        "mx_Offline_Centre_Name,mx_Program")

api_calls = 0
api_bytes = 0


def post(url, body, timeout=240, tries=3):
    """POST JSON with a simple backoff. LSQ times out under load often enough to need it."""
    global api_calls, api_bytes
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            api_calls += 1
            api_bytes += len(raw)
            return json.loads(raw)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


# ---------- 1. leads ----------------------------------------------------------
# All-time, oldest first. No date filter: the worked basis needs older leads too.
def fetch_leads():
    out, page = [], 1
    while True:
        r = post(LEADS, {
            "Parameter": {"LookupName": "Source", "LookupValue": "Internshala", "SqlOperator": "="},
            "Columns": {"Include_CSV": COLS},
            "Sorting": {"ColumnName": "CreatedOn", "Direction": "1"},
            "Paging": {"PageIndex": page, "PageSize": 1000}})
        rows = r if isinstance(r, list) else (r.get("Leads") or [])
        out.extend(rows)
        print(f"  leads page {page}: {len(rows)} rows, {len(out):,} total", flush=True)
        if len(rows) < 1000:
            break
        page += 1
    return out


# ---------- 2/3. activities ---------------------------------------------------
def fetch_event(event, pids, page_size, label):
    """Roll up one activity event into {ProspectID: {n, first, last, who}}.

    Pulled one calendar day at a time: a single wide date range blows past the
    API's paging depth and starts returning duplicates, and day slices also give
    a usable progress line and a free daily histogram.
    `pids` restricts the rollup to Internshala leads; the pull itself is unfiltered
    because the activity endpoint cannot filter by lead source.
    """
    per = collections.defaultdict(lambda: {"n": 0, "first": None, "last": None, "who": collections.Counter()})
    daily, actors, total = collections.Counter(), collections.Counter(), 0
    d0 = WIN
    while d0 < END:
        d1 = d0 + dt.timedelta(days=1)
        page = 1
        while True:
            r = post(ACTS, {
                "Parameter": {"FromDate": f"{d0} 00:00:00", "ToDate": f"{d1} 00:00:00",
                              "ActivityEvent": event, "RemoveEmptyValue": True},
                "Paging": {"PageIndex": page, "PageSize": page_size},
                "Sorting": {"ColumnName": "CreatedOn", "Direction": "0"}})
            recs = r.get("List") or []
            total += len(recs)
            for a in recs:
                pid, ts = a.get("RelatedProspectId"), (a.get("CreatedOn") or "")
                daily[ts[:10]] += 1
                actors[a.get("CreatedByName")] += 1
                if pid in pids:
                    p = per[pid]
                    p["n"] += 1
                    p["first"] = min(p["first"] or ts, ts)
                    p["last"] = max(p["last"] or ts, ts)
                    p["who"][a.get("CreatedByName")] += 1
            if len(recs) < page_size:
                break
            page += 1
        print(f"  {label} {d0}: {total:,} activities, {len(per):,} Internshala leads touched", flush=True)
        d0 = d1
    flat = {k: {"n": v["n"], "first": v["first"], "last": v["last"], "who": dict(v["who"])}
            for k, v in per.items()}
    return flat, dict(daily), actors, total


if __name__ == "__main__":
    print(f"window {WIN} .. {END - dt.timedelta(days=1)} (inclusive)")

    print("1/3 Internshala leads (Leads.Get, all-time)")
    raw = fetch_leads()
    (DATA / "internshala_raw.json").write_text(json.dumps(raw))
    pids = {x["ProspectID"] for x in raw}

    print("2/3 human calls (event 22)")
    hum, daily, callers, total = fetch_event(22, pids, 1000, "calls")
    (DATA / "act_sep.json").write_text(json.dumps({
        "calls": api_calls, "mb": round(api_bytes / 1048576, 1), "total": total,
        "daily": daily, "internshala": hum, "top_callers": callers.most_common(25)}))

    print("3/3 bot (328) and Futwork (330)")
    # PageSize 100: these custom activities carry ~30 mx_Custom_* fields each, so a
    # 1000-row page is large enough to time out.
    bot, _, _, botN = fetch_event(328, pids, 100, "bot")
    futw, _, _, futN = fetch_event(330, pids, 100, "futwork")
    (DATA / "act_sep_botfut.json").write_text(json.dumps({"bot": bot, "futwork": futw}))

    print(f"DONE · api calls {api_calls:,} · {api_bytes / 1048576:.1f} MB")
    print(f"  leads {len(raw):,} | human-called {len(hum):,} | bot-called {len(bot):,} | futwork {len(futw):,}")
    print(f"  activities scanned: 22={total:,} 328={botN:,} 330={futN:,}")
    print(f"  wrote {DATA}/internshala_raw.json, act_sep.json, act_sep_botfut.json")
