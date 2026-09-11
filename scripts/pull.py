#!/usr/bin/env python3
"""Pull the LeadSquared 'universe' and write data/universe.json for the dashboard.

Reads credentials from environment variables (set as GitHub Secrets in CI):
  LSQ_HOST    e.g. https://api-in21.leadsquared.com/v2
  LSQ_ACCESS  access key
  LSQ_SECRET  secret key
Optional:
  LSQ_START   window start (default 2026-09-01); widen to pull more history.

Read-only: only calls Leads.Get (a search). Never creates/updates/deletes.
Stdlib only — no pip install needed.
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

HOST   = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
ACCESS = os.environ["LSQ_ACCESS"]
SECRET = os.environ["LSQ_SECRET"]
BASE_DATE = os.environ.get("LSQ_START") or "2026-09-01"   # empty env var -> default
START  = BASE_DATE + " 00:00:00"
BASE   = datetime.strptime(START, "%Y-%m-%d %H:%M:%S")
IST    = 330  # API returns UTC; dashboard buckets days in IST (+5:30)

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "universe.json")

qs  = urllib.parse.urlencode({"accessKey": ACCESS, "secretKey": SECRET})
URL = f"{HOST}/LeadManagement.svc/Leads.Get?{qs}"


def pull(lookup):
    out, page = [], 1
    while page <= 200:
        body = {"Parameter": {"LookupName": lookup, "LookupValue": START, "SqlOperator": ">="},
                "Sorting": {"ColumnName": lookup, "Direction": "0"},
                "Paging": {"PageIndex": page, "PageSize": 1000}}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
        if not isinstance(d, list) or not d:
            break
        out += d
        if len(d) < 1000:
            break
        page += 1
    return out


def parse(s):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def mins(s):
    dt = parse(s)
    return None if dt is None else round((dt - BASE).total_seconds() / 60) + IST


def num(v):
    v = str(v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else 0


def grp(s):
    s = (s or "").lower()
    if not s:
        return "Unknown"
    if "intern" in s:
        return "Internshala"
    if "google" in s:
        return "Google"
    if "meta" in s or s in ("fb", "ig") or "facebook" in s or "instagram" in s:
        return "Meta"
    if "seminar" in s:
        return "Seminar"
    if "website" in s or "organic" in s or "walk" in s:
        return "Website/Organic"
    if "referral" in s:
        return "Referral"
    return "Other"


def main():
    print("pulling created...");   a = pull("CreatedOn")
    print("pulling first-call..."); b = pull("mx_First_Call_Date_and_Time")
    print("pulling assignment..."); c = pull("mx_Assignment_Date_Current_Owner")
    uni = {}
    for L in a + b + c:
        pid = L.get("ProspectID")
        if pid and pid not in uni:
            uni[pid] = L
    print(f"union {len(uni)}")

    srcs, sidx = [], {}
    owns, oidx = [], {}
    stgs, stidx = [], {}
    quals, qidx = [], {}

    def idxof(v, arr, d):
        v = (v or "").strip() or "—"
        if v not in d:
            d[v] = len(arr)
            arr.append(v)
        return d[v]

    leads = []
    for L in uni.values():
        leads.append([
            idxof(grp(L.get("Source")), srcs, sidx),
            mins(L.get("CreatedOn")), mins(L.get("mx_First_Call_Date_and_Time")),
            mins(L.get("mx_Assignment_Date_Current_Owner")),
            idxof(L.get("OwnerIdName") or "Unassigned", owns, oidx),
            mins(L.get("ProspectActivityDate_Max")), mins(L.get("mx_Follow_Up_Date")),
            num(L.get("mx_Reached_Out_Attempts")), num(L.get("mx_Interacted_Count")),
            idxof(L.get("ProspectStage"), stgs, stidx),
            idxof(L.get("mx_Highest_Qualification"), quals, qidx),
        ])

    stamp = datetime.now(timezone.utc).astimezone().isoformat()
    out = {"base": BASE_DATE, "tz": "IST", "generated_at": stamp,
           "sources": srcs, "owners": owns, "stages": stgs, "quals": quals,
           "cols": ["src", "created", "firstcall", "assign", "owner", "lastactivity",
                    "followup", "attempts", "interacted", "stage", "qual"],
           "leads": leads}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(leads)} leads | {len(srcs)} src | {len(owns)} owners "
          f"| {os.path.getsize(OUT)//1024} KB")


if __name__ == "__main__":
    sys.exit(main())
