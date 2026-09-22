#!/usr/bin/env python3
"""
Build an Aavtaar calling cohort from LeadSquared.

ONE script, several profiles. A profile is pure data (see PROFILES below), so a
third cohort is a dict literal, not a second copy of this file.

    python3 build_cohort.py tech-reactivation
    python3 build_cohort.py finance-mumbai
    python3 build_cohort.py --list

WHAT IT WRITES
    out/<profile>_<YYYY-MM-DD>.csv   one row per lead, in Aavtaar's upload shape
                                     (name, phone, email) followed by the columns
                                     the return path needs (lead id, centre,
                                     program, stage, current owner).
    The extra columns matter: Aavtaar pushes qualified leads back through a
    LeadSquared webhook, and for tech-reactivation a qualified lead has to go
    back to its PREVIOUS owner — which only exists in this file, because parking
    the lead in the bot pool overwrites OwnerIdName on the lead itself.

WHY Leads.Get AND NOT AN ADVANCED SEARCH
    Leads.Get takes exactly ONE lookup parameter. So each profile declares a list
    of `anchors` — cheap, indexed single-field pulls — and every other condition
    (stage, program, date window, owner, city) is applied client-side. Same shape
    as reports/internshala/fetch_data.py. CLAUDE.md pins the allowed endpoint
    list; do not swap this for ByAdvancedSearch without changing that first.

CREDENTIALS — environment variables only, never hardcoded, never committed:
    LSQ_HOST    default https://api-in21.leadsquared.com/v2
    LSQ_ACCESS  access key
    LSQ_SECRET  secret key
With no LSQ_ACCESS / LSQ_SECRET the script prints the plan it *would* run —
anchors, filters, output path — and exits 0. Use that to review a profile before
spending a few hundred API calls on it.

Optional:
    AAVTAAR_OUT_DIR   output folder (default ./out)
    AAVTAAR_SINCE     YYYY-MM-DD, overrides the profile's window start (IST)
    AAVTAAR_UNTIL     YYYY-MM-DD, exclusive upper bound (IST); default: none

READ-ONLY: only calls LeadManagement.svc/Leads.Get. Never creates or updates.
Stdlib only.
"""

import os, sys, csv, json, time, collections, datetime as dt
import urllib.request, urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AAVTAAR_OUT_DIR") or BASE / "out")

HOST = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
ACCESS = os.environ.get("LSQ_ACCESS")
SECRET = os.environ.get("LSQ_SECRET")

IST = 330  # the API filters and returns UTC; the business reads IST (+5:30)

# ---------------------------------------------------------------------------
# Programme include-lists.
#
# A naive regex over mx_Program over-matches badly: "Accelerated Pathway Program
# for BSc Data Science (Hons) NU UK" is a Study Abroad product, not an offline LC
# course. These lists are therefore explicit, signed off by name, and any value
# not listed here is simply not in the cohort. Values are exact mx_Program
# strings, matched case-insensitively after whitespace collapse.
# ---------------------------------------------------------------------------

DA_DS_PROGRAMS = [
    # high-volume LC/offline data programmes
    "Data Science",
    "FutureStack: Data Science & Gen AI",
    "Futurestack Data Science",
    "FutureStack: Data Science & GenAI course for Next Gen Engineers",
    "Gen AI-powered Data Science with Machine Learning",
    "Gen AI-powered Data Science with Machine Learning - PUP",
    "Gen AI-powered Data Science with Machine Learning - PAP",
    "Certificate in Gen AI-Powered Data Science with Machine Learning ( with IIT Roorkee )",
    "Data Science IIT Roorkee",
    "Data Science & ML",
    "Data Analytics",
    "Gen AI-powered Data Analytics - PUP",
    "Gen AI-powered Data Analytics - PAP",
    # centre-named certificates — the name itself proves they are offline LC
    "Advanced Certificate in Data Science - Pune",
    "Advanced Certificate in Data Science - Bangalore",
    "Advanced Certificate in Data Science LC Bangalore",
    "Advanced Certificate in Data Science - Dehradun",
    "Advanced Certificate in Data Science (Gurugram)",
    "Advance certificate in Data Science - Hyderabad (Ameerpet)",
    "Advance certificate in Data Science - Hyderabad (Madhapur)",
    "Advance certificate in Data Science - Mangalore",
    "Advance certificate in Data Science - Coimbatore",
    "Advanced Certificate in Data Science with ML/AI - Mangalore",
    "Advanced Certificate in Data Analytics - Mangalore",
]

FINANCE_PROGRAMS = [
    "Finance",
    "Certificate in Global & Investment Banking Operations",
    "Financial Analysis",
    "Professional Certificate Program in Financial Modelling and Analysis",
    "Professional Certificate Program in Financial Modelling and Analysis with Career Accelerator Pack",
    "Financial Modelling and Analysis",
]

# Awaiting a decision from Kiran. Listed, not silently included — flip a value up
# into the list above once it is signed off. The script prints how many rows each
# one WOULD have added, so the cost of leaving it out is visible.
DA_DS_PENDING = [
    "Executive Diploma in Machine Learning and AI",
    "Executive Diploma in Data Science & AI",
    "Introduction to Data Analysis using Excel",
    "datacourses",
    "New Program DS AI",
    "ml-part-pgp",
    "Professional Certification Programme in Data Science and Generative AI",
    "Data Science with AI & ML",
    "Data Science | Yes",
    "Data Science | No",
    "Data Science |Yes",
    "Data Science |No",
]
FINANCE_PENDING = [
    "Masters in International Accounting and Finance - ACCA Accredited",
    "Professional Certificate Programme in FinTech and AI",
    "Introduction to FinTech",
    "Introduction to Digital Banking",
    "Accounting Fundamentals",
    "Job-ready Program in Financial Modelling & Analysis in association with PwC India",
]

# ---------------------------------------------------------------------------
# Centre derivation.
#
# mx_Offline_Centre_Name is blank on roughly half the instance, so the fallback
# is the same one the routing automation uses: match tokens against Source
# Campaign text. Taken verbatim from the 30 distribution rules on the
# "Offline BU Level Routing" sheet of LSQ Lead Routing Logic.xlsx — canonical
# label on the left, the tokens that rule matches on the right. Order matters
# only for Kothrud, which claims the generic "Pune" token.
# ---------------------------------------------------------------------------
CENTRE_TOKENS = [
    ("Ahmedabad", ["ahmedabad"]),
    ("Ameerpet", ["ameerpet"]),
    ("Belgaum / Belagavi", ["belgaum", "belagavi"]),
    ("Bhopal", ["bhopal"]),
    ("Bhubaneswar", ["bhubaneswar"]),
    ("Bilaspur", ["bilaspur"]),
    ("Chandigarh", ["chandigarh"]),
    ("Chennai", ["chennai"]),
    ("Coimbatore", ["coimbatore"]),
    ("Dehradun", ["dehradun"]),
    ("Gurugram", ["gurugram"]),
    ("HSR-Layout Bangalore", ["hsr", "bangalore"]),
    ("Indore", ["indore"]),
    ("Jabalpur", ["jabalpur"]),
    ("Jaipur", ["jaipur"]),
    ("Jayanagar", ["jayanagar"]),
    ("Lucknow", ["lucknow"]),
    ("Madhapur", ["madhapur"]),
    ("Mangalore", ["mangalore"]),
    ("Marathahalli", ["marathahalli"]),
    ("Noida", ["noida"]),
    ("Panchkula", ["panchkula"]),
    ("Panipat", ["panipat"]),
    ("Raipur", ["raipur"]),
    ("Rajkot", ["rajkot"]),
    ("Sambhajinagar", ["sambhaji"]),
    ("salt-lake-kolkata2", ["salt", "bengal"]),
    ("wakad", ["wakad"]),
    ("Kothrud", ["kothrud", "pune"]),
]

# ---------------------------------------------------------------------------
# Profiles. Everything a cohort needs, declared as data.
#
#   anchors      [(LookupName, LookupValue, SqlOperator)] — one Leads.Get pull
#                each, results unioned on ProspectID.
#   since/until  IST calendar dates. `date_field` says which lead date they test.
#   stages       exact ProspectStage values (the "B2C : Not Interetsed" typo is
#                real and load-bearing — do not fix it).
#   programs     exact mx_Program values; None means "no programme filter".
#   owners       exact OwnerIdName values the lead must currently sit under;
#                None means any.
#   city_any     substrings; the lead matches if ANY appears in its city, centre
#                or Source Campaign. None means no geography filter.
#   return_owner free text, written into the handover notes, not into LSQ.
# ---------------------------------------------------------------------------
PROFILES = {
    "tech-reactivation": {
        "label": "Tech reactivation (DA/DS)",
        "anchors": [("Source", v, "=") for v in
                    ["Google", "Meta", "Website/Organic", "Internshala", "Referral"]],
        "date_field": "CreatedOn",
        "since": "2026-05-01",
        "until": None,
        "stages": [
            "B2C : Did Not Pick",
            "B2C : Cold (Multiple DNPs)",
            "Disqualified - DNP",
            "Prospect",
            "B2C : Prospect (Hot)",
            "B2C : Interested in Future Cohort",
            "B2C : Not Interetsed",
        ],
        "programs": DA_DS_PROGRAMS,
        "pending_programs": DA_DS_PENDING,
        "owners": None,
        "city_any": None,
        "return_owner": "previous owner (the current_owner column in this CSV)",
        "note": "Every row carries its centre so an Excel handover is possible.",
    },
    "finance-mumbai": {
        "label": "Finance Mumbai",
        # Anchored on the programme rather than the source: the finance LC
        # programme list is short, so one small pull per value beats paging the
        # whole of Google/Meta and throwing 99% of it away.
        "anchors": [("mx_Program", v, "=") for v in FINANCE_PROGRAMS],
        "date_field": "CreatedOn",
        "since": "2026-09-01",
        "until": "2026-10-01",
        "stages": ["B2C : New Lead", "B2C : Did Not Pick"],
        "programs": FINANCE_PROGRAMS,
        "pending_programs": FINANCE_PENDING,
        "owners": ["LC Team"],
        "city_any": ["mumbai", "marol", "thane", "linking road", "vashi", "borivali",
                     "andheri", "powai", "ghatkopar", "malad", "chembur", "goregaon"],
        "return_owner": "Naziya Sultana (spelling confirmed against the LSQ owner list)",
        "note": "'Mumbai' in mx_SA_Allocation_City behaves as a REGION and pulls in "
                "Pune centres — that is why centre and Source Campaign are checked too.",
    },
}

# Columns pulled from Leads.Get. Keep in sync with row() below.
COLS = ("ProspectID,ProspectAutoId,FirstName,LastName,EmailAddress,Phone,Mobile,"
        "Source,SourceCampaign,SourceMedium,ProspectStage,OwnerIdName,CreatedOn,"
        "mx_Last_Interested_in_Date,mx_Program,mx_Offline_Centre_Name,"
        "mx_SA_Allocation_City")

api_calls = 0
api_bytes = 0


def qs():
    return urllib.parse.urlencode({"accessKey": ACCESS, "secretKey": SECRET})


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


def parse_ts(s):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def to_ist(s):
    """LSQ hands back UTC. Everything downstream is read in IST."""
    d = parse_ts(s)
    return None if d is None else d + dt.timedelta(minutes=IST)


def norm(s):
    return " ".join((s or "").split()).lower()


def fetch_anchor(lookup, value, op, since_utc):
    """Paginate one anchor, newest first, and stop once a whole page predates the
    window. Leads.Get only accepts one Parameter, so the date bound cannot be sent
    to the API alongside the anchor — sorting descending and short-circuiting is
    what keeps a Google/Meta pull from being the entire instance."""
    url = f"{HOST}/LeadManagement.svc/Leads.Get?{qs()}"
    out, page = [], 1
    while True:
        r = post(url, {
            "Parameter": {"LookupName": lookup, "LookupValue": value, "SqlOperator": op},
            "Columns": {"Include_CSV": COLS},
            "Sorting": {"ColumnName": "CreatedOn", "Direction": "0"},
            "Paging": {"PageIndex": page, "PageSize": 1000}})
        if not isinstance(r, list):
            msg = (r.get("ExceptionMessage") or r.get("Message") or str(r)[:200]
                   if isinstance(r, dict) else str(r)[:200])
            raise RuntimeError(f"{lookup}={value}: page {page} returned a fault — {msg}")
        out.extend(r)
        newest_kept = any((parse_ts(x.get("CreatedOn")) or dt.datetime.min) >= since_utc
                          for x in r)
        print(f"  {lookup}={value!r} page {page}: {len(r)} rows, {len(out):,} total", flush=True)
        if len(r) < 1000 or not newest_kept:
            break
        page += 1
    return out


def centre_of(lead):
    """mx_Offline_Centre_Name if set, else the routing rules' Source Campaign
    tokens, else blank. Returns (centre, how) so the fill rate can be split."""
    c = (lead.get("mx_Offline_Centre_Name") or "").strip()
    if c:
        return c, "field"
    camp = norm(lead.get("SourceCampaign"))
    if camp:
        for label, toks in CENTRE_TOKENS:
            if any(t in camp for t in toks):
                return label, "campaign"
    return "", "none"


def row(lead, centre, how):
    name = " ".join(x for x in [(lead.get("FirstName") or "").strip(),
                                (lead.get("LastName") or "").strip()] if x)
    phone = (lead.get("Phone") or lead.get("Mobile") or "").strip()
    created = to_ist(lead.get("CreatedOn"))
    return {
        # Aavtaar upload shape first — their template reads the first three columns.
        "name": name,
        "phone": phone,
        "email": (lead.get("EmailAddress") or "").strip(),
        # return path / Excel handover
        "lead_id": lead.get("ProspectID") or "",
        "lead_number": lead.get("ProspectAutoId") or "",
        "centre": centre,
        "centre_from": how,
        "program": lead.get("mx_Program") or "",
        "stage": lead.get("ProspectStage") or "",
        "current_owner": lead.get("OwnerIdName") or "",
        "source": lead.get("Source") or "",
        "source_campaign": lead.get("SourceCampaign") or "",
        "city": lead.get("mx_SA_Allocation_City") or "",
        "created_on_ist": created.strftime("%Y-%m-%d %H:%M:%S") if created else "",
    }


def plan(name, p, since, until):
    """What this profile would do. Printed on every run, and it is the whole
    output when there are no credentials."""
    print(f"PROFILE  {name} — {p['label']}")
    print(f"  window   {p['date_field']} >= {since}" + (f" and < {until}" if until else " (open-ended)"))
    print(f"  anchors  {len(p['anchors'])} Leads.Get pulls, one per value:")
    for lk, v, op in p["anchors"]:
        print(f"             {lk} {op} {v!r}")
    print(f"  stages   {len(p['stages'])} kept: " + ", ".join(repr(s) for s in p["stages"]))
    if p["programs"] is None:
        print("  programs no filter")
    else:
        print(f"  programs {len(p['programs'])} signed off, {len(p['pending_programs'])} awaiting a decision (excluded)")
        for v in p["programs"]:
            print(f"             + {v}")
        for v in p["pending_programs"]:
            print(f"             ? {v}   (NOT included)")
    print(f"  owners   {p['owners'] if p['owners'] else 'any'}")
    print(f"  geo      {p['city_any'] if p['city_any'] else 'none'}")
    print(f"  centre   mx_Offline_Centre_Name, else {len(CENTRE_TOKENS)} Source Campaign rules, else blank")
    print(f"  returns  qualified leads go to {p['return_owner']}")
    print(f"  note     {p['note']}")
    print(f"  writes   {OUT / (name + '_' + dt.date.today().isoformat() + '.csv')}")


def main(argv):
    if not argv or argv[0] in ("--list", "-l", "-h", "--help"):
        print("usage: build_cohort.py <profile>")
        for k, v in PROFILES.items():
            print(f"  {k:20s} {v['label']}")
        return 0
    name = argv[0]
    if name not in PROFILES:
        print(f"unknown profile {name!r}; known: {', '.join(PROFILES)}", file=sys.stderr)
        return 2
    p = PROFILES[name]

    since = os.environ.get("AAVTAAR_SINCE") or p["since"]
    until = os.environ.get("AAVTAAR_UNTIL") or p["until"]
    plan(name, p, since, until)

    if not (ACCESS and SECRET):
        print()
        print("NO CREDENTIALS — LSQ_ACCESS and LSQ_SECRET are not set, so nothing was "
              "queried and nothing was written. The plan above is what would run.")
        print("  export LSQ_ACCESS=... LSQ_SECRET=...   then re-run.")
        return 0

    # IST calendar dates, compared against UTC timestamps from the API.
    since_utc = dt.datetime.fromisoformat(since) - dt.timedelta(minutes=IST)
    until_utc = (dt.datetime.fromisoformat(until) - dt.timedelta(minutes=IST)) if until else None

    print()
    leads = {}
    for lk, v, op in p["anchors"]:
        for lead in fetch_anchor(lk, v, op, since_utc):
            leads[lead.get("ProspectID")] = lead
    print(f"  {len(leads):,} distinct leads across {len(p['anchors'])} anchors")

    progs = {norm(x) for x in p["programs"]} if p["programs"] else None
    pend = {norm(x) for x in (p["pending_programs"] or [])}
    stages = {norm(x) for x in p["stages"]}
    owners = {norm(x) for x in p["owners"]} if p["owners"] else None

    drop = collections.Counter()
    pend_hits = collections.Counter()
    rows = []
    for lead in leads.values():
        ts = parse_ts(lead.get(p["date_field"]))
        if ts is None or ts < since_utc or (until_utc and ts >= until_utc):
            drop["window"] += 1
            continue
        if norm(lead.get("ProspectStage")) not in stages:
            drop["stage"] += 1
            continue
        if owners is not None and norm(lead.get("OwnerIdName")) not in owners:
            drop["owner"] += 1
            continue
        centre, how = centre_of(lead)
        if p["city_any"]:
            hay = " ".join(norm(lead.get(k)) for k in
                           ("mx_SA_Allocation_City", "mx_Offline_Centre_Name", "SourceCampaign"))
            hay += " " + norm(centre)
            if not any(t in hay for t in p["city_any"]):
                drop["geo"] += 1
                continue
        if progs is not None:
            pn = norm(lead.get("mx_Program"))
            if pn not in progs:
                if pn in pend:
                    pend_hits[lead.get("mx_Program")] += 1
                drop["program"] += 1
                continue
        rows.append(row(lead, centre, how))

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}_{dt.date.today().isoformat()}.csv"
    fields = list(rows[0].keys()) if rows else [
        "name", "phone", "email", "lead_id", "lead_number", "centre", "centre_from",
        "program", "stage", "current_owner", "source", "source_campaign", "city",
        "created_on_ist"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ---- summary -----------------------------------------------------------
    def tally(key, top=None):
        c = collections.Counter((r[key] or "(blank)") for r in rows)
        return c.most_common(top) if top else sorted(c.items(), key=lambda x: -x[1])

    n = len(rows)
    no_phone = sum(1 for r in rows if not r["phone"])
    have_centre = sum(1 for r in rows if r["centre"])
    from_field = sum(1 for r in rows if r["centre_from"] == "field")
    from_camp = sum(1 for r in rows if r["centre_from"] == "campaign")
    pct = lambda a: f"{(100.0 * a / n):.1f}%" if n else "n/a"

    print()
    print(f"COHORT {name}: {n:,} rows -> {path}")
    print("  dropped: " + (", ".join(f"{k}={v:,}" for k, v in sorted(drop.items())) or "none"))
    print(f"  no phone number: {no_phone:,} ({pct(no_phone)})")
    print(f"  centre filled:   {have_centre:,} ({pct(have_centre)}) "
          f"— {from_field:,} from mx_Offline_Centre_Name, {from_camp:,} derived from Source Campaign")
    for key, title in (("source", "per source"), ("stage", "per stage"), ("centre", "per centre")):
        print(f"  {title}:")
        for k, v in tally(key):
            print(f"    {v:7,}  {k}")
    if pend_hits:
        print("  programmes awaiting a decision, excluded from the file above:")
        for k, v in pend_hits.most_common():
            print(f"    {v:7,}  {k}")
    print(f"  api calls {api_calls:,} · {api_bytes / 1048576:.1f} MB")
    print(f"  qualified leads must return to: {p['return_owner']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
