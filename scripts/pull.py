#!/usr/bin/env python3
"""Pull the LeadSquared 'universe' and write data/universe.json for the dashboard.

Reads credentials from environment variables (set as GitHub Secrets in CI):
  LSQ_HOST    e.g. https://api-in21.leadsquared.com/v2
  LSQ_ACCESS  access key
  LSQ_SECRET  secret key
Optional:
  LSQ_START   window start, an IST calendar date (default 2026-09-01); widen for more history.
  LSQ_POOLS   comma-separated owner names that are unworked holding/bot pools
              (default: Avtar LCoffline,Futworks Lc). Their leads are flagged pool=1 so the
              dashboard can keep them out of rate denominators.
  LSQ_REFRESH_IST  the daily schedule as HH:MM IST (default 01:11), written into the
              data file so the dashboard can show "next refresh in ...". Keep it in
              sync with the cron in .github/workflows/refresh.yml.

Read-only: only calls Leads.Get and User/AdvancedSearch (both searches). Never
creates/updates/deletes.
Stdlib only — no pip install needed.
"""
import json, os, re, sys, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

HOST   = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
# .get(), not [], because this module is imported for grp() alone by
# reports/aavtaar/build_cohort.py — the source-bucket table must have exactly ONE
# definition in this repo, and a KeyError at import time would have forced a second
# copy of it. main() still refuses to run without credentials, below.
ACCESS = os.environ.get("LSQ_ACCESS")
SECRET = os.environ.get("LSQ_SECRET")
BASE_DATE = os.environ.get("LSQ_START") or "2026-09-01"   # empty env var -> default
BASE   = datetime.strptime(BASE_DATE + " 00:00:00", "%Y-%m-%d %H:%M:%S")
IST    = 330  # API returns UTC; dashboard buckets days in IST (+5:30)
ISTTZ  = timezone(timedelta(minutes=IST))

# LeadSquared filters in the SAME timezone it returns — UTC. BASE_DATE is an IST
# calendar date, so filtering at "BASE_DATE 00:00" UTC silently drops everything
# created in the first 5h30m of that IST day. Start the window 330 minutes earlier
# so IST day 0 is complete; mins() already maps those rows to day 0 correctly, and
# the reports' own `day >= 0` guard discards the sliver that falls on the prior day.
START  = (BASE - timedelta(minutes=IST)).strftime("%Y-%m-%d %H:%M:%S")

# Bulk/bot accounts that hold leads without working them. Their leads are real and
# still counted as Created, but including them in rate denominators (coverage, avg
# attempts, connect %, enrolment %) understates what counsellors actually do.
# `Avtar LCoffline` is the Aavataar AI voice-bot queue; `Futworks Lc` is the Futwork
# tele-qualification vendor. NEITHER writes the lead call fields — their work lives in
# activities (event 328 and 330) — so leads sitting there look untouched by design.
POOL_OWNERS = {o.strip().lower() for o in
               (os.environ.get("LSQ_POOLS") or "Avtar LCoffline,Futworks Lc").split(",") if o.strip()}

# Daily refresh time (IST), surfaced to the dashboard so it can show the next run and
# flag a missed one. The workflow passes this from the same place the cron is defined.
REFRESH_IST = (os.environ.get("LSQ_REFRESH_IST") or "01:11").strip()   # = the primary cron in refresh.yml

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "universe.json")

def api_url():
    """Built on demand rather than at import, so importing this module for grp()
    never needs credentials and never bakes a `secretKey=None` URL into a global."""
    qs = urllib.parse.urlencode({"accessKey": ACCESS, "secretKey": SECRET})
    return f"{HOST}/LeadManagement.svc/Leads.Get?{qs}"

# Only the fields the dashboard needs — keeps each page ~14 fields instead of 640,
# which makes the pull ~4-5x faster and the payload far smaller (critical for wide date windows).
COLS = ("ProspectID,CreatedOn,mx_First_Call_Date_and_Time,mx_Date_of_Lead_Assignment,"
        "OwnerId,OwnerIdName,ProspectActivityDate_Max,mx_Follow_Up_Date,mx_Reached_Out_Attempts,"
        "mx_Interacted_Count,ProspectStage,mx_Highest_Qualification,Source,"
        # report-architecture fields required by LSQ_Sales_Report_Master_Dashboard.xlsx
        "mx_Program,mx_SA_Allocation_City,mx_Offline_Centre_Name,mx_First_Interaction_Date,"
        "mx_Is_Counselled_Lead,mx_Total_Amount_Paid,mx_Assignment_Date_Current_Owner,"
        # event dates — needed for the Operational view (what happened IN a window,
        # as opposed to how a created cohort eventually converted)
        "mx_First_Counselling_Date,mx_First_Transaction_Date,mx_Interested_In_Date,"
        # the campaign name carries the product (…_OFFLINE-data_analytics, LearningCentre_FSD_…)
        "SourceCampaign")


MAX_PAGES = 200          # 200k rows per anchor; the assignment anchor is already ~125


def pull(lookup):
    """Paginate one anchor. Raises rather than returning a short list: a truncated
    pull that reports success is worse than a failed run, because the dashboard
    deploys wrong numbers with no way to tell them from right ones."""
    url = api_url()
    out, page = [], 1
    while page <= MAX_PAGES:
        body = {"Parameter": {"LookupName": lookup, "LookupValue": START, "SqlOperator": ">="},
                "Columns": {"Include_CSV": COLS},
                "Sorting": {"ColumnName": lookup, "Direction": "0"},
                "Paging": {"PageIndex": page, "PageSize": 1000}}
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        d = json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
        # A non-list is LeadSquared reporting a fault (rate limit, transient error).
        # The old code treated that the same as "no more data" and exited quietly.
        if not isinstance(d, list):
            msg = d.get("ExceptionMessage") or d.get("Message") or str(d)[:200] if isinstance(d, dict) else str(d)[:200]
            raise RuntimeError(f"{lookup}: page {page} returned {type(d).__name__}, not leads — {msg}")
        if not d:
            break                       # genuine end of data
        out += d
        if len(d) < 1000:
            break                       # short page = last page
        page += 1
    else:
        raise RuntimeError(
            f"{lookup}: hit the {MAX_PAGES}-page cap with a full final page — "
            f"{len(out):,} rows pulled and more remain. Raise MAX_PAGES; data is being dropped.")
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


# Stage lifecycle class, so every report shares one definition of "still open".
#   won  = closed-won            lost = closed-lost / disqualified
#   test = test data (excluded)  open = genuinely live pipeline
LOST_KEYS = ("junk", "not intere", "interetsed", "did not pick", "dnp",
             "cold", "disqualified", "lost")


def stage_class(label):
    s = (label or "").lower()
    if "enrol" in s:
        return "won"
    if "dummy" in s or s.strip() == "test":
        return "test"
    if any(k in s for k in LOST_KEYS):
        return "lost"
    return "open"


def money(v):
    v = str(v or "").replace(",", "").strip()
    try:
        return round(float(v))
    except ValueError:
        return 0


# Source buckets. This is an explicit table, not a substring waterfall, because the
# old waterfall put 70.6% of revenue into "Other": affiliate networks had no bucket,
# walk-ins were swallowed by "organic", and inbound calls fell through. Order matters
# below — walk-in is matched BEFORE organic, and Internshala is matched on the full
# name so a future "Internal Referral" is not captured by a bare "intern".
AFFILIATES = {
    "collectcent", "vertozad", "flymedia", "nextgen", "prudentads", "cuelinks",
    "digitalmediafeed", "famapp", "affiliates", "midfunnel", "netambit", "vipl1",
    "adcanopus", "icubeswire", "adzberg",
}


def grp(s):
    t = (s or "").strip().lower()
    if not t:
        return "Unknown"
    if t in AFFILIATES:
        return "Affiliate/Partner"
    if "walk" in t:                                    # before organic — walk-ins are
        return "Walk-in"                               # the highest-intent offline source
    if "incoming call" in t or t in ("inbound", "ivr"):
        return "Inbound Call"
    if "internshala" in t:
        return "Internshala"
    if "google" in t:
        return "Google"
    if "meta" in t or t in ("fb", "ig") or "facebook" in t or "instagram" in t:
        return "Meta"
    if t in ("li", "linkedin") or "linkedin" in t:
        return "LinkedIn"
    if "seminar" in t:
        return "Seminar"
    if "outreach" in t:                                # College-Outreach / College-outreach:
        return "College Outreach"                      # ~1,100 leads/month, was hiding in Other
    if "referral" in t:
        return "Referral"
    if "website" in t or "organic" in t or "web" in t:
        return "Website/Organic"
    return "Other"


# Program groups. The campaign name is the primary signal: Meta/Google LC campaigns end in the
# product (…_Meta-Ads_OFFLINE-data_science, LearningCentre_FSD_Coimbatore_PMax), and it is set
# by the system, whereas Program of Interest says "general-interest" for ~46% of leads. Program
# of Interest is the fallback. Order matters: the domain wins over the AI wrapper, so
# "Gen AI-powered Data Science" is DS and "AI-Powered Digital Marketing" is DM; AI only catches
# AI-first programs. Anything unmatched (college outreach, seminars, MBA, generic "Product"
# ads) is Other. Validated on Oct 2026 leads: campaign decides 71%, program 7%, Other 21%.
PROG_GROUPS = ["DS", "DA", "DM", "Finance", "AI", "FSD", "Other"]
PROG_RULES = [
    ("DA", r"data ?anal|analytics|\bda\b|excel|power ?bi|tableau"),
    ("DS", r"data ?scien|datascience|\bds\b"),
    ("DM", r"digital ?market|\bdm\b|marketing"),
    ("Finance", r"financ|account|acca|\bcfa\b|banking|invest|\bcma\b"),
    ("FSD", r"full ?stack|\bfsd\b|software|ssoftw|cyber|develop|devops|cloud"),
    ("AI", r"\bai\b|gen ?ai|agentic|artificial|machine learning|\bml\b|llm"),
]
GENERIC_PROGRAM = re.compile(r"^(general[- ]?interest|all course|—|)$", re.I)
# campaigns that carry no product by design (multi-product ads, outreach, events, database
# calling) — expected in Other, so they do not trigger the unmapped-campaign warning
GENERIC_CAMPAIGN = re.compile(r"product|outreach|seminar|event|database|placement|masterclass|walk", re.I)


def prog_group(campaign, program):
    """Index into PROG_GROUPS: campaign first, Program of Interest second, else Other."""
    for text in (campaign, "" if GENERIC_PROGRAM.match((program or "").strip()) else program):
        t = re.sub(r"[_\-]+", " ", (text or "").lower())
        for g, rx in PROG_RULES:
            if re.search(rx, t):
                return PROG_GROUPS.index(g)
    return PROG_GROUPS.index("Other")


def centre_of_user(u):
    """One centre for an LSQ user, from their "Admission(s) <centre>_LC" groups — the only centre
    signal LSQ holds (a lead's own centre field is ~22% filled). Shared by pull_talktime.py so the
    dashboard and Talk Time agree. A shared login in two groups ("Admission Dehradun" also in
    Haldwani) takes the centre in its own name; anything else in 2+ groups keeps both, joined."""
    cs = sorted({re.sub(r"^Admissions?[ .]*", "", g.strip()).replace("_LC", "").replace("Hyd_", "Hyderabad ")
                 .replace("Blr_", "Bangalore ").replace("blr_", "Bangalore ").replace("Pune2", "Pune 2")
                 .replace("Kolkata2", "Kolkata 2").replace("Bhubaneshwar", "Bhubaneswar")
                 .replace("Mumbaiandheri", "Mumbai Andheri")
                 for g in (u.get("Groups") or "").split(",") if g.strip().lower().startswith("admission")})
    if len(cs) > 1:
        name = f"{u.get('FirstName') or ''} {u.get('LastName') or ''}".lower()
        own = [c for c in cs if c.lower().split()[0] in name]
        if len(own) == 1:
            return own[0]
    return " / ".join(cs)


def user_centres():
    """{UserId: centre} for every ACTIVE LSQ user (AdvancedSearch returns active users only)."""
    qs = urllib.parse.urlencode({"accessKey": ACCESS, "secretKey": SECRET})
    out, p = {}, 1
    while True:
        req = urllib.request.Request(f"{HOST}/UserManagement.svc/User/AdvancedSearch?{qs}",
                                     data=json.dumps({"Columns": "", "Paging": {"PageIndex": p, "PageSize": 100}}).encode(),
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
        # a short user list would silently mark counsellors "No centre"
        if not (isinstance(r, dict) and isinstance(r.get("Users"), list)):
            raise RuntimeError(f"User/AdvancedSearch page {p}: unexpected response — {str(r)[:200]}")
        for u in r["Users"]:
            out[u["UserId"]] = centre_of_user(u)
        if len(r["Users"]) < 100:
            break
        p += 1
    return out


def lead_owner_centre(owner_id, owner_name, ucent):
    """Centre of the ACCOUNT that owns the lead. Keyed by account, not name: a counsellor who
    moved centre keeps an old account (old centre) and a new one, and each lead belongs to
    exactly one of them — so each lead lands in exactly one centre, with no overlap."""
    k = re.sub(r"\s+", " ", owner_name).strip().lower()
    if k in POOL_OWNERS:
        return "Pool accounts"
    if ucent.get(owner_id):
        return ucent[owner_id]
    m = re.match(r"([a-z]+?)(?:lc)?\s+ac\d+$", k)        # deactivated: "Chandigarhlc Ac3" -> Chandigarh
    return m.group(1).title() if m else "No centre"


def main():
    if not (ACCESS and SECRET):
        raise SystemExit("LSQ_ACCESS and LSQ_SECRET must be set (GitHub Secrets in CI).")
    print("pulling created...");   a = pull("CreatedOn")
    print("pulling first-call..."); b = pull("mx_First_Call_Date_and_Time")
    print("pulling assignment..."); c = pull("mx_Assignment_Date_Current_Owner")
    # A lead created before the window that pays or is counselled in it, without a new first
    # call or reassignment, matched none of the anchors above — so the Overview's activity
    # view (first payments, counselling sessions) silently missed it. Pull those too.
    print("pulling first-payment..."); d = pull("mx_First_Transaction_Date")
    print("pulling first-counselling..."); e = pull("mx_First_Counselling_Date")
    uni = {}
    for L in a + b + c + d + e:
        pid = L.get("ProspectID")
        if pid and pid not in uni:
            uni[pid] = L
    print(f"union {len(uni)}")

    srcs, sidx = [], {}
    # one owner entry per (name, centre): the same counsellor in two centres is two rows, each
    # holding only that centre's leads; two accounts of one person in the SAME centre merge
    owns, octr, oidx = [], [], {}
    ucent = user_centres()
    progs, pidx = [], {}
    citys, cidx = [], {}
    ctrs, ctidx = [], {}
    stgs, stidx = [], {}
    quals, qidx = [], {}
    rsrcs, ridx = [], {}

    def idxof(v, arr, d):
        v = (v or "").strip() or "—"
        if v not in d:
            d[v] = len(arr)
            arr.append(v)
        return d[v]

    leads, unmapped, unprog = [], {}, {}
    for L in uni.values():
        owner = L.get("OwnerIdName") or "Unassigned"
        ctr = lead_owner_centre(L.get("OwnerId"), owner, ucent)
        if (owner, ctr) not in oidx:
            oidx[(owner, ctr)] = len(owns); owns.append(owner); octr.append(ctr)
        raw = (L.get("Source") or "").strip()
        bucket = grp(raw)
        if bucket == "Other" and raw:
            a = unmapped.setdefault(raw, [0, 0])
            a[0] += 1
            a[1] += money(L.get("mx_Total_Amount_Paid"))
        leads.append([
            idxof(bucket, srcs, sidx),
            mins(L.get("CreatedOn")), mins(L.get("mx_First_Call_Date_and_Time")),
            # slot 3 = the ORIGINAL assignment. This read mx_Assignment_Date_Current_Owner
            # by mistake, which reassignment overwrites — so the Owner Scorecard clock and
            # the Speed "assign" anchor were both measuring the wrong field, and slots 3
            # and 18 were byte-identical for all 131,030 leads.
            mins(L.get("mx_Date_of_Lead_Assignment")),
            oidx[(owner, ctr)],
            mins(L.get("ProspectActivityDate_Max")), mins(L.get("mx_Follow_Up_Date")),
            num(L.get("mx_Reached_Out_Attempts")), num(L.get("mx_Interacted_Count")),
            idxof(L.get("ProspectStage"), stgs, stidx),
            idxof(L.get("mx_Highest_Qualification"), quals, qidx),
            1 if owner.strip().lower() in POOL_OWNERS else 0,
            # --- 12..18: report-architecture fields (see xlsx) ---
            idxof(L.get("mx_Program"), progs, pidx),                      # 12 program
            idxof(L.get("mx_SA_Allocation_City"), citys, cidx),           # 13 city (100% filled)
            idxof(L.get("mx_Offline_Centre_Name"), ctrs, ctidx),          # 14 centre (~22% filled)
            mins(L.get("mx_First_Interaction_Date")),                     # 15 first CONNECT
            1 if str(L.get("mx_Is_Counselled_Lead") or "").strip().lower() in ("yes", "true", "1") else 0,
            money(L.get("mx_Total_Amount_Paid")),                         # 17 revenue
            mins(L.get("mx_Assignment_Date_Current_Owner")),              # 18 current-owner assign
            mins(L.get("mx_First_Counselling_Date")),                     # 19 counselled ON
            mins(L.get("mx_First_Transaction_Date")),                     # 20 first payment ON
            mins(L.get("mx_Interested_In_Date")),                         # 21 interested-in ON
            idxof(raw, rsrcs, ridx),                                      # 22 raw Source value
            prog_group(L.get("SourceCampaign"), L.get("mx_Program")),     # 23 program group
        ])
        camp = (L.get("SourceCampaign") or "").strip()
        if leads[-1][23] == PROG_GROUPS.index("Other") and camp and not GENERIC_CAMPAIGN.search(camp):
            unprog[camp] = unprog.get(camp, 0) + 1

    # Any raw Source landing in "Other" with real volume or real money should get its
    # own bucket — surface it here rather than letting it hide in the catch-all.
    for raw, (cnt, rev) in sorted(unmapped.items(), key=lambda kv: -kv[1][1])[:10]:
        if cnt >= 50 or rev >= 100000:
            print(f"  WARNING: source '{raw}' is unmapped -> 'Other' "
                  f"({cnt} leads, Rs {rev:,} collected) — add it to grp()")

    # A new campaign that names its product in a way PROG_RULES misses lands in Other —
    # surface it so the rule table keeps up with marketing's naming.
    for camp, cnt in sorted(unprog.items(), key=lambda kv: -kv[1])[:10]:
        if cnt >= 50:
            print(f"  WARNING: campaign '{camp}' maps to program group 'Other' ({cnt} leads) "
                  f"— add its product to PROG_RULES if it has one")

    # Flag any *other* account that looks like an unworked pool, so a new bot/bulk
    # owner surfaces in the CI log instead of silently re-entering the denominators.
    seen = {}
    for x in leads:
        if x[1] is None or x[1] < 0:
            continue                      # created-cohort only (what the reports render)
        a = seen.setdefault(x[4], [0, 0, 0])
        a[0] += 1
        a[1] += x[7]
        a[2] += 1 if x[2] is not None else 0
    for oi, (n, att, con) in sorted(seen.items(), key=lambda kv: -kv[1][0]):
        if n >= 300 and att / n < 0.05 and con / n < 0.05 and owns[oi].strip().lower() not in POOL_OWNERS:
            print(f"  WARNING: '{owns[oi]}' looks like an unworked pool "
                  f"({n} leads, {att/n:.2f} avg attempts, {100*con/n:.1f}% contacted) "
                  f"— consider adding it to LSQ_POOLS")

    # stamp in IST explicitly — CI runners are UTC, and a raw +00:00 timestamp in a
    # file labelled tz:"IST" is exactly the kind of thing that gets misread later.
    # Compare against the snapshot we are replacing. A large swing is usually a real
    # CRM event (a bulk reassignment moved 6,236 leads on 14-09-2026), so this warns
    # and never fails — structural truncation is what raises, above.
    try:
        with open(OUT) as f:
            prev = len(json.load(f).get("leads", []))
        if prev:
            delta = (len(leads) - prev) / prev * 100
            if abs(delta) >= 3:
                # Truncation would have raised above, so this is a real CRM change —
                # but a 5% shift and a 70% shift deserve very different volume.
                loud = "!! LARGE CHANGE !!" if abs(delta) >= 25 else "WARNING:"
                print(f"  {loud} universe {prev:,} -> {len(leads):,} ({delta:+.1f}%). "
                      f"Every anchor paginated to completion, so this is a real change in "
                      f"LeadSquared, not a short pull.")
                if abs(delta) >= 25:
                    print(f"  {loud} A swing this size is unusual — verify in LSQ before "
                          f"anyone reads the dashboard. Likely causes: a bulk reassignment, "
                          f"an owner deactivation, or a changed lead-status filter.")
    except (OSError, ValueError):
        pass                            # first run, or unreadable previous file

    stamp = datetime.now(timezone.utc).astimezone(ISTTZ).isoformat()
    out = {"base": BASE_DATE, "tz": "IST", "generated_at": stamp,
           "sources": srcs, "owners": owns, "stages": stgs, "quals": quals,
           "programs": progs, "cities": citys, "centres": ctrs,
           # raw LSQ Source values behind each bucket, and each owner's centre (parallel to owners)
           "raw_sources": rsrcs, "owner_centre": octr,
           "prog_groups": PROG_GROUPS,
           # lifecycle class per stage index — one shared definition of "still open"
           "stage_class": [stage_class(s) for s in stgs],
           "pools": sorted({o for o in owns if o.strip().lower() in POOL_OWNERS}),
           "refresh_ist": REFRESH_IST,   # daily schedule, HH:MM IST
           # `assign` is now mx_Date_of_Lead_Assignment (the original, near-immutable
           # assignment). mx_Assignment_Date_Current_Owner is kept as `assign_cur`, but it
           # post-dates the first call for ~41% of leads because reassignment overwrites it.
           "cols": ["src", "created", "firstcall", "assign", "owner", "lastactivity",
                    "followup", "attempts", "interacted", "stage", "qual", "pool",
                    "program", "city", "centre", "firstconnect", "counselled", "revenue",
                    "assign_cur", "counselled_on", "paid_on", "interested_on", "rawsrc", "proggroup"],
           "leads": leads}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    npool = sum(x[11] for x in leads)
    nconn = sum(1 for x in leads if x[15] is not None)
    ncoun = sum(x[16] for x in leads)
    nrev  = sum(1 for x in leads if x[17] > 0)
    print(f"  event dates: counselled-on {sum(1 for x in leads if x[19] is not None)} "
          f"| paid-on {sum(1 for x in leads if x[20] is not None)}")
    print(f"  new fields: first-connect {nconn} | counselled {ncoun} | with revenue {nrev} "
          f"| programs {len(progs)} | cities {len(citys)} | centres {len(ctrs)}")
    print(f"wrote {OUT}: {len(leads)} leads | {len(srcs)} src | {len(owns)} owners "
          f"| {npool} pool-flagged ({', '.join(out['pools']) or 'none'}) "
          f"| {os.path.getsize(OUT)//1024} KB")


if __name__ == "__main__":
    sys.exit(main())
