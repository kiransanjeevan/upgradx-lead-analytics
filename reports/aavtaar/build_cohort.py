#!/usr/bin/env python3
"""
Build an Aavtaar calling cohort from LeadSquared.

ONE script, several profiles. A profile is pure data (see PROFILES below), so a
third cohort is a dict literal, not a second copy of this file.

    python3 build_cohort.py tech-reactivation
    python3 build_cohort.py finance-central
    python3 build_cohort.py --list

WHAT IT WRITES
    out/cohort_<profile>_<YYYY-MM-DD>.csv
        one row per lead, in Aavtaar's upload shape (name, phone, email) followed
        by the columns the return path needs (lead id, centre, course, program,
        stage, current owner).
    out/cohort_<profile>_undeclared_<YYYY-MM-DD>.csv
        leads that passed every other filter but whose COURSE could not be
        determined (Program of Interest is 'general-interest' or blank and the
        campaign carries no course token). These are never silently merged into
        the cohort above — an undeclared lead is a different calling script.

    The extra columns matter: Aavtaar pushes qualified leads back through a
    LeadSquared webhook, and for tech-reactivation a qualified lead has to go
    back to its PREVIOUS owner — which only exists in this file, because parking
    the lead in the bot pool overwrites OwnerIdName on the lead itself.

WHY ONE DATE-ANCHORED PULL AND NOT ONE PULL PER SOURCE
    Leads.Get takes exactly ONE lookup parameter, so the anchor has to be the
    single most selective field. It used to be `Source = "Meta"` (etc.), which is
    an EXACT match against a field whose raw values are fragmented: the LSQ
    picklist carries `meta`, `Meta Ads`, `MetaAds`, `Meta-Ads`, `Meta Ads website`,
    `MetaAdswebsite` and the typo `Meta Ada`; Google has a dozen more. The clean
    12-value source list the dashboard renders is BUCKETED output from grp() in
    scripts/pull.py, not raw values — so anchoring on "Meta" caught one spelling
    in seven and the cohort was missing most of its own leads.

    So: anchor on the date field (one bounded pull), and bucket Source
    CLIENT-SIDE with the *imported* grp(). Importing rather than copying is
    deliberate — a second copy of that table is exactly how this class of bug
    keeps recurring here.

    CLAUDE.md pins the allowed endpoint list; do not swap this for
    ByAdvancedSearch without changing that first.

CREDENTIALS — environment variables only, never hardcoded, never committed:
    LSQ_HOST    default https://api-in21.leadsquared.com/v2
    LSQ_ACCESS  access key
    LSQ_SECRET  secret key
With no LSQ_ACCESS / LSQ_SECRET the script prints the plan it *would* run —
anchor, filters, ladder, output paths — and exits 0. Use that to review a profile
before spending a few hundred API calls on it.

Optional:
    AAVTAAR_OUT_DIR   output folder (default ./out)
    AAVTAAR_SINCE     YYYY-MM-DD, overrides the profile's window start (IST)
    AAVTAAR_UNTIL     YYYY-MM-DD, exclusive upper bound (IST); default: none

READ-ONLY: only calls LeadManagement.svc/Leads.Get. Never creates or updates.
Stdlib only.
"""

import os, sys, csv, json, time, collections, re, datetime as dt
import urllib.request, urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("AAVTAAR_OUT_DIR") or BASE / "out")

# The source-bucket table has exactly one home in this repo: grp() in
# scripts/pull.py, which is what data/universe.json and the dashboard are built
# from. Import it so this cohort buckets Source identically to the dashboard.
sys.path.insert(0, str(BASE.parent.parent / "scripts"))
from pull import grp  # noqa: E402  (path has to be set first)

HOST = os.environ.get("LSQ_HOST", "https://api-in21.leadsquared.com/v2").rstrip("/")
ACCESS = os.environ.get("LSQ_ACCESS")
SECRET = os.environ.get("LSQ_SECRET")

IST = 330  # the API filters and returns UTC; the business reads IST (+5:30)

MAX_PAGES = 400  # 400k rows; a date-anchored pull raises rather than short-pulls

# ---------------------------------------------------------------------------
# Programme include-lists.
#
# A naive regex over mx_Program over-matches badly: "Accelerated Pathway Program
# for BSc Data Science (Hons) NU UK" is a Study Abroad product, not an offline LC
# course. These lists are therefore explicit, signed off by name, and any value
# not listed here is simply not in the cohort. Values are exact mx_Program
# strings (mx_Program is the API name for the "Program of Interest" field),
# matched case-insensitively after whitespace collapse.
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

# EXCLUDED, not forgotten.
#
# The three CONFLICT programmes — the ones that are genuinely arguable because
# they are sold by more than one BU and could belong to either cohort — are
# marked below. Kiran's decision (Sept 2026): all three stay OUT pending
# confirmation with the programme owners. Everything else in these lists is the
# older "awaiting a decision" backlog and is out for the same reason.
#
# Nothing here is silently dropped: the run summary prints how many rows each
# value WOULD have added, so the cost of leaving it out is visible on real data.
# To include one, move it up into the list above — do not special-case it here.
DA_DS_PENDING = [
    "Executive Diploma in Machine Learning and AI",      # CONFLICT — excluded pending confirmation
    "Executive Diploma in Data Science & AI",             # CONFLICT — excluded pending confirmation
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
    "Masters in International Accounting and Finance - ACCA Accredited",  # CONFLICT — excluded pending confirmation
    "Professional Certificate Programme in FinTech and AI",
    "Introduction to FinTech",
    "Introduction to Digital Banking",
    "Accounting Fundamentals",
    "Job-ready Program in Financial Modelling & Analysis in association with PwC India",
]

# The three arguable ones, called out by name so the summary can report them as a
# group rather than leaving them buried in the backlog above.
CONFLICT_PROGRAMS = [
    "Executive Diploma in Machine Learning and AI",
    "Executive Diploma in Data Science & AI",
    "Masters in International Accounting and Finance - ACCA Accredited",
]

# mx_Program values that are declared but say nothing about a course. A lead
# carrying one of these is UNDECLARED, not "some other course we don't want".
GENERIC_PROGRAMS = {
    "", "—", "-", "general-interest", "general interest", "generalinterest",
    "all course", "all courses", "not sure", "others", "other", "na", "n/a", "none",
}

# ---------------------------------------------------------------------------
# Course resolution — see the ladder in resolve_course().
#
# The `OFFLINE-<course>` campaign suffix is a META-ONLY convention. Measured on
# the 1-25 Aug leads-created export: Meta carries it on roughly half its leads,
# and Internshala / Google / Seminar / Referral / Website carry it on none.
# Campaign grammar differs per source, so course identification has to be a
# ladder rather than one regex:
#   Meta         LearningCentre_Wakad_Offline_Meta-Ads_..._OFFLINE-DS
#                (also the centre-first variant <Centre>_LearningCentre_... —
#                 which is why every campaign test here is a SUBSTRING, never a
#                 prefix)
#   Internshala  Learningcentre__Internshala_Ads_140826 — date-stamped, no course
#                token at all. mx_Program is clean and fully populated instead.
#   Google       IND_ACQ_WEB_GOOGLE_PMAX_DV_IIITB_AGENTICAI_T1_INTEREST — course
#                is present but in its own grammar.
#   Referral     <Centre>_LearningCentre_Offline_Insta-page-Form_... — mostly
#                general-interest, i.e. genuinely undeclared.
# ---------------------------------------------------------------------------

# Rung 1: the Meta suffix. Verified pure on the August export (OFFLINE-DS carries
# Program of Interest "Data Science" for all but a handful of rows; same for -DA).
# Finance runs the same convention in two spellings (OFFLINE-Finance and, on older
# campaigns, OFFLINE-Product_Finance) — September LC Team finance leads carry it
# on every row, while mx_Program disagrees on a handful.
SUFFIX_RE = re.compile(r"offline-(ds|da|finance|product_finance)\b")
SUFFIX_COURSE = {"ds": "DS", "da": "DA", "finance": "FIN", "product_finance": "FIN"}

# Rung 3: Google-style campaign tokens, delimiter-bounded so "ads" never matches
# "DS". Extend this dict — it is the only place a new token grammar belongs.
TOKEN_RE = re.compile(r"(?:^|[_\-.])(agenticai|eml|ds|da)(?:[_\-.]|$)")
TOKEN_COURSE = {"agenticai": "AGENTICAI", "eml": "EML", "ds": "DS", "da": "DA"}


def course_of_program(name):
    """Map a signed-off mx_Program value to a course code. Only ever applied to
    values already in a profile's include-list, so the substrings below cannot
    over-match into Study Abroad or MBA products."""
    n = norm(name)
    if not n:
        return None
    if "analytic" in n:                       # before Data Science: "Data Analytics"
        return "DA"                           # contains "data" but not "data science"
    if "data science" in n or "futurestack" in n:
        return "DS"
    if any(k in n for k in ("financ", "banking", "accounting", "acca", "fintech")):
        return "FIN"
    return None


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
# Mumbai.
#
# mx_SA_Allocation_City = "Mumbai" behaves as a REGION in this instance and drags
# in Pune centres, which is why it cannot be the primary signal. There is no
# Mumbai LEARNING Centre in the routing rules at all — the Mumbai sites below are
# upGrad CAREER Centres (COCO), a different network.
#
# The campaign convention is LearningCentre_<Centre>_Offline_..., and live
# September campaigns of the form LearningCentre_Mumbai_Offline_Meta-Ads_... do
# exist. So the primary test is "LearningCentre" AND a Mumbai-area token, both in
# Source Campaign; the city field is a fallback only, and every row records which
# one matched in `geo_from` so the two can be counted separately.
#
# The same campaign slot carries the other locations the central finance bucket
# is run for — LearningCentre_Kolkata_... and LearningCentre_No offline centre
# near me_... / LearningCentre_India_... (no city named: city unknown). Those two
# are campaign-only; neither has a city field worth falling back on.
# ---------------------------------------------------------------------------
MUMBAI_TOKENS = ["mumbai", "marol", "thane", "andheri", "powai", "vashi",
                 "borivali", "ghatkopar", "malad", "chembur", "goregaon",
                 "linking road"]
LC_CAMPAIGN_MARK = "learningcentre"   # tested against the de-separated campaign

# (label, campaign tokens, fall back to mx_SA_Allocation_City?) — first match wins.
FINANCE_CENTRAL_GEO = [
    ("Mumbai", MUMBAI_TOKENS, True),
    ("Kolkata", ["kolkata"], False),
    # LearningCentre_India_... names no city either. Anchored on its neighbours
    # because a bare "india" would hit any campaign that mentions the country.
    ("City unsure", ["no offline centre near me", "learningcentre_india_offline"], False),
]

# ---------------------------------------------------------------------------
# Profiles. Everything a cohort needs, declared as data.
#
#   date_field   the lead date the window tests. It is ALSO the Leads.Get anchor:
#                one bounded pull, everything else applied client-side.
#   since/until  IST calendar dates.
#   sources      grp() BUCKET names (not raw Source values) the lead must fall
#                into; None means any. See the module docstring for why.
#   stages       exact ProspectStage values (the "B2C : Not Interetsed" typo is
#                real and load-bearing — do not fix it).
#   programs     exact mx_Program values; None means "no programme filter".
#   courses      course codes this cohort wants, resolved by the ladder in
#                resolve_course(). A lead whose course resolves to something else
#                is dropped; a lead whose course does not resolve at all goes to
#                the undeclared file.
#   owners       exact OwnerIdName values the lead must currently sit under;
#                None means any.
#   geo          location groups (see FINANCE_CENTRAL_GEO) the lead's campaign
#                must name; None means no location filter. The matched label
#                fills the centre column when mx_Offline_Centre_Name is blank.
#   return_owner free text, written into the handover notes, not into LSQ.
# ---------------------------------------------------------------------------
PROFILES = {
    "tech-reactivation": {
        "label": "Tech reactivation (DA/DS)",
        # Kiran, Sept 2026: the basis is CreatedOn, NOT Interested In Date — the
        # cohort is "leads that arrived since May and went cold", and Interested
        # In Date is only set on the subset that ever declared interest.
        "date_field": "CreatedOn",
        "since": "2026-05-01",
        "until": None,
        "sources": ["Google", "Meta", "Website/Organic", "Internshala", "Referral"],
        # Exactly these six. B2C : Prospect (Hot) was removed — a hot prospect is
        # a live human conversation and must not be handed to the bot.
        "stages": [
            "B2C : Did Not Pick",
            "B2C : Interested in Future Cohort",
            "B2C : Not Interetsed",
            "Prospect",
            "B2C : Cold (Multiple DNPs)",
            "Disqualified - DNP",
        ],
        "programs": DA_DS_PROGRAMS,
        "pending_programs": DA_DS_PENDING,
        "courses": ["DS", "DA"],
        "owners": None,
        "geo": None,
        "return_owner": "previous owner (the current_owner column in this CSV)",
        "note": "Every row carries its centre so an Excel handover is possible.",
    },
    "finance-central": {
        "label": "Finance — LC Team central bucket (Mumbai, Kolkata, city unsure)",
        "date_field": "CreatedOn",
        "since": "2026-09-01",
        "until": "2026-10-01",
        "sources": None,
        "stages": ["B2C : New Lead", "B2C : Did Not Pick"],
        "programs": FINANCE_PROGRAMS,
        "pending_programs": FINANCE_PENDING,
        "courses": ["FIN"],
        "owners": ["LC Team"],
        "geo": FINANCE_CENTRAL_GEO,
        # Kiran, 23 Sep 2026: all three locations return to Naziya.
        "return_owner": "Naziya Sultana, all locations (spelling confirmed against the LSQ owner list)",
        "note": "Location comes from the campaign (LearningCentre + a location token). "
                "Only Mumbai falls back to mx_SA_Allocation_City: 'Mumbai' in that field "
                "is a REGION and pulls in Pune centres.",
    },
}

# Columns pulled from Leads.Get. Keep in sync with row() below.
COLS = ("ProspectID,ProspectAutoId,FirstName,LastName,EmailAddress,Phone,Mobile,"
        "Source,SourceCampaign,SourceMedium,ProspectStage,OwnerIdName,CreatedOn,"
        "mx_Last_Interested_in_Date,mx_Program,mx_Offline_Centre_Name,"
        "mx_SA_Allocation_City")

FIELDS = ["name", "phone", "email", "lead_id", "lead_number", "centre", "centre_from",
          "course", "course_from", "program", "stage", "current_owner", "source",
          "source_bucket", "source_campaign", "city", "geo_from", "created_on_ist"]

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


def flat(s):
    """Campaign text with separators removed, for tests that must survive both
    LearningCentre_Wakad_... and Learningcentre__Wakad-..."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def fetch_window(date_field, since_utc):
    """One date-bounded Leads.Get pull, oldest first, paginated to completion.

    Leads.Get accepts exactly ONE Parameter, and the date is the only field
    selective enough to be it — see the module docstring on why Source is not.
    Raises rather than returning a short list: a truncated pull that reports
    success hands Aavtaar a silently incomplete calling list."""
    url = f"{HOST}/LeadManagement.svc/Leads.Get?{qs()}"
    since_s = since_utc.strftime("%Y-%m-%d %H:%M:%S")
    out, page = [], 1
    while page <= MAX_PAGES:
        r = post(url, {
            "Parameter": {"LookupName": date_field, "LookupValue": since_s, "SqlOperator": ">="},
            "Columns": {"Include_CSV": COLS},
            "Sorting": {"ColumnName": date_field, "Direction": "0"},
            "Paging": {"PageIndex": page, "PageSize": 1000}})
        if not isinstance(r, list):
            msg = (r.get("ExceptionMessage") or r.get("Message") or str(r)[:200]
                   if isinstance(r, dict) else str(r)[:200])
            raise RuntimeError(f"{date_field} >= {since_s}: page {page} returned a fault — {msg}")
        if not r:
            break
        out.extend(r)
        print(f"  {date_field} >= {since_s} page {page}: {len(r)} rows, {len(out):,} total", flush=True)
        if len(r) < 1000:
            break
        page += 1
    else:
        raise RuntimeError(
            f"{date_field}: hit the {MAX_PAGES}-page cap with a full final page — "
            f"{len(out):,} rows pulled and more remain. Raise MAX_PAGES; leads are being dropped.")
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


def geo_of(lead, groups):
    """Which location group this lead belongs to, and which signal said so.
    Returns (label, 'campaign' | 'city') or ('', '').

    Campaign first and on purpose: mx_SA_Allocation_City = 'Mumbai' is a REGION
    and brings Pune centres with it, so it is only ever a fallback."""
    camp = flat(lead.get("SourceCampaign"))
    if LC_CAMPAIGN_MARK in camp:
        for label, toks, _ in groups:
            if any(flat(t) in camp for t in toks):
                return label, "campaign"
    city = norm(lead.get("mx_SA_Allocation_City"))
    for label, toks, fallback in groups:
        if fallback and city and any(t in city for t in toks):
            # The fallback yields to any named centre outside the group, whether it
            # came from mx_Offline_Centre_Name or from the campaign. CENTRE_TOKENS
            # holds no Mumbai entry, so a campaign-derived centre is always
            # non-Mumbai. This is the Pune drag-in, blocked on both paths.
            centre = norm(centre_of(lead)[0])
            if centre and not any(t in centre for t in toks):
                return "", ""
            return label, "city"
    return "", ""


def resolve_course(lead, approved, pending):
    """The course-identification ladder. Returns (course_code, rung).

    Rung order is fixed and each rung is verified separately on real data:
      1 campaign_suffix  OFFLINE-(DS|DA) — a Meta-only convention, but the purest
                         signal there is, so it outranks the programme field.
      2 program          mx_Program in the profile's signed-off include-list.
      2'                 mx_Program in the PENDING list -> excluded, but counted.
      2''                mx_Program declared and specific but not ours -> a real
                         other course, dropped. NOT undeclared — putting 'Digital
                         Marketing' in the undeclared file would make that file
                         a junk drawer instead of a callable list.
      3 campaign_token   Google grammar (AGENTICAI, EML, DS, DA) in the campaign.
      4 undeclared       nothing said what this lead wants.

    Returned rung is what goes in the course_from column, so the split is
    measured on every run instead of assumed from one August export."""
    camp = norm(lead.get("SourceCampaign"))
    m = SUFFIX_RE.search(camp)
    if m:
        return SUFFIX_COURSE[m.group(1)], "campaign_suffix"

    pn = norm(lead.get("mx_Program"))
    if pn in approved:
        return course_of_program(lead.get("mx_Program")), "program"
    if pn in pending:
        return None, "program_pending"
    if pn and pn not in GENERIC_PROGRAMS:
        return None, "program_other"

    t = TOKEN_RE.search(camp)
    if t:
        return TOKEN_COURSE[t.group(1)], "campaign_token"
    return None, "undeclared"


def row(lead, centre, how, course, course_from, bucket, gfrom):
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
        "course": course or "",
        "course_from": course_from,
        "program": lead.get("mx_Program") or "",
        "stage": lead.get("ProspectStage") or "",
        "current_owner": lead.get("OwnerIdName") or "",
        "source": lead.get("Source") or "",
        "source_bucket": bucket,
        "source_campaign": lead.get("SourceCampaign") or "",
        "city": lead.get("mx_SA_Allocation_City") or "",
        "geo_from": gfrom,
        "created_on_ist": created.strftime("%Y-%m-%d %H:%M:%S") if created else "",
    }


def paths_for(name):
    stamp = dt.date.today().isoformat()
    return (OUT / f"cohort_{name}_{stamp}.csv",
            OUT / f"cohort_{name}_undeclared_{stamp}.csv")


def check_profile(name, p):
    """Fail fast on a profile that cannot work: every signed-off programme must
    map to a course code this cohort actually wants, or the include-list and the
    ladder have drifted apart and leads would vanish with no message."""
    if not p["programs"]:
        return
    want = set(p["courses"])
    bad = [v for v in p["programs"] if course_of_program(v) not in want]
    if bad:
        raise SystemExit(
            f"profile {name}: {len(bad)} signed-off programme(s) do not resolve to "
            f"{sorted(want)} via course_of_program() — fix the mapping or the list:\n  "
            + "\n  ".join(bad))


def plan(name, p, since, until):
    """What this profile would do. Printed on every run, and it is the whole
    output when there are no credentials."""
    main_csv, und_csv = paths_for(name)
    print(f"PROFILE  {name} — {p['label']}")
    print(f"  window   {p['date_field']} >= {since}" + (f" and < {until}" if until else " (open-ended)"))
    print(f"  anchor   ONE Leads.Get pull on {p['date_field']} >= {since} (UTC-shifted), "
          f"paged 1000 at a time, max {MAX_PAGES} pages")
    print(f"  sources  " + (", ".join(p["sources"]) + "  (grp() buckets, applied CLIENT-SIDE — "
                            "raw Source values are fragmented and must not be matched exactly)"
                            if p["sources"] else "no source filter"))
    print(f"  stages   {len(p['stages'])} kept: " + ", ".join(repr(s) for s in p["stages"]))
    if p["programs"] is None:
        print("  programs no filter")
    else:
        nconf = sum(1 for v in p["pending_programs"] if v in CONFLICT_PROGRAMS)
        print(f"  programs {len(p['programs'])} signed off, {len(p['pending_programs'])} excluded "
              f"(of which {nconf} {'is a conflict programme' if nconf == 1 else 'are conflict programmes'}, "
              f"deliberately out pending confirmation)")
        for v in p["programs"]:
            print(f"             + {v}")
        for v in p["pending_programs"]:
            tag = "   (CONFLICT — excluded pending confirmation)" if v in CONFLICT_PROGRAMS else "   (NOT included)"
            print(f"             ? {v}{tag}")
    print(f"  courses  {', '.join(p['courses'])} — resolved by ladder: "
          f"campaign_suffix -> program -> campaign_token -> undeclared")
    print(f"  owners   {p['owners'] if p['owners'] else 'any'}")
    print(f"  geo      " + ("campaign must contain 'LearningCentre' + a token for one of: "
                            + "; ".join(f"{g[0]} ({'/'.join(g[1])}"
                                        + (", else mx_SA_Allocation_City" if g[2] else "") + ")"
                                        for g in p["geo"])
                            if p["geo"] else "none"))
    print(f"  centre   mx_Offline_Centre_Name, else {len(CENTRE_TOKENS)} Source Campaign rules, else blank")
    print(f"  returns  qualified leads go to {p['return_owner']}")
    print(f"  note     {p['note']}")
    print(f"  writes   {main_csv}")
    print(f"           {und_csv}   (course could not be determined)")


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


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
    check_profile(name, p)

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
    for lead in fetch_window(p["date_field"], since_utc):
        leads[lead.get("ProspectID")] = lead
    print(f"  {len(leads):,} distinct leads in the window")

    approved = {norm(x) for x in p["programs"]} if p["programs"] else set()
    pending = {norm(x) for x in (p["pending_programs"] or [])}
    conflict = {norm(x) for x in CONFLICT_PROGRAMS}
    stages = {norm(x) for x in p["stages"]}
    owners = {norm(x) for x in p["owners"]} if p["owners"] else None
    want = set(p["courses"])
    srcs = set(p["sources"]) if p["sources"] else None

    drop = collections.Counter()
    pend_hits = collections.Counter()
    conflict_hits = collections.Counter()
    rungs = collections.Counter()
    rows, undeclared = [], []
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
        bucket = grp(lead.get("Source"))
        if srcs is not None and bucket not in srcs:
            drop["source"] += 1
            continue
        geo, gfrom = geo_of(lead, p["geo"]) if p["geo"] else ("", "")
        if p["geo"] and not geo:
            drop["geo"] += 1
            continue
        centre, how = centre_of(lead)
        if not centre and geo:
            centre, how = geo, "geo"

        course, rung = resolve_course(lead, approved, pending)
        rungs[rung] += 1
        if rung == "program_pending":
            pn = norm(lead.get("mx_Program"))
            pend_hits[lead.get("mx_Program")] += 1
            if pn in conflict:
                conflict_hits[lead.get("mx_Program")] += 1
            drop["program_pending"] += 1
            continue
        if rung == "program_other":
            drop["program_other"] += 1
            continue
        r = row(lead, centre, how, course, rung, bucket, gfrom)
        if course is None:
            undeclared.append(r)          # never merged into the cohort
        elif course in want:
            rows.append(r)
        else:
            drop[f"course:{course}"] += 1

    main_csv, und_csv = paths_for(name)
    write_csv(main_csv, rows)
    write_csv(und_csv, undeclared)

    # ---- summary -----------------------------------------------------------
    def tally(key, src=None):
        c = collections.Counter((r[key] or "(blank)") for r in (rows if src is None else src))
        return sorted(c.items(), key=lambda x: -x[1])

    n = len(rows)
    no_phone = sum(1 for r in rows if not r["phone"])
    have_centre = sum(1 for r in rows if r["centre"])
    from_field = sum(1 for r in rows if r["centre_from"] == "field")
    from_camp = sum(1 for r in rows if r["centre_from"] == "campaign")
    from_geo = sum(1 for r in rows if r["centre_from"] == "geo")
    pct = lambda a: f"{(100.0 * a / n):.1f}%" if n else "n/a"

    print()
    print(f"COHORT {name}: {n:,} rows -> {main_csv}")
    print(f"  undeclared:      {len(undeclared):,} rows -> {und_csv}  "
          f"(course could not be determined — NOT in the cohort above)")
    print("  dropped: " + (", ".join(f"{k}={v:,}" for k, v in sorted(drop.items())) or "none"))
    print(f"  no phone number: {no_phone:,} ({pct(no_phone)})")
    print(f"  centre filled:   {have_centre:,} ({pct(have_centre)}) "
          f"— {from_field:,} from mx_Offline_Centre_Name, {from_camp:,} derived from Source Campaign, "
          f"{from_geo:,} from the location group")
    print("  course resolution ladder (every lead that got past the stage/source filters):")
    for rung in ("campaign_suffix", "program", "campaign_token", "undeclared",
                 "program_pending", "program_other"):
        if rungs[rung]:
            print(f"    {rungs[rung]:7,}  {rung}")
    print("  ladder rung x course, in the cohort file:")
    for (cf, cs), v in sorted(collections.Counter((r["course_from"], r["course"])
                                                  for r in rows).items(), key=lambda x: -x[1]):
        print(f"    {v:7,}  {cf} -> {cs}")
    if p["geo"]:
        print("  location matched by:")
        for k, v in tally("geo_from"):
            print(f"    {v:7,}  {k}")
    for key, title in (("source_bucket", "per source bucket"), ("stage", "per stage"),
                       ("centre", "per centre")):
        print(f"  {title}:")
        for k, v in tally(key):
            print(f"    {v:7,}  {k}")
    if undeclared:
        print("  undeclared file, per source bucket:")
        for k, v in tally("source_bucket", undeclared):
            print(f"    {v:7,}  {k}")
    if conflict_hits:
        print("  CONFLICT programmes — deliberately excluded pending confirmation. "
              "Rows they WOULD have added:")
        for k, v in conflict_hits.most_common():
            print(f"    {v:7,}  {k}")
        print(f"    {sum(conflict_hits.values()):7,}  total")
    other_pend = {k: v for k, v in pend_hits.items() if k not in CONFLICT_PROGRAMS}
    if other_pend:
        print("  other programmes awaiting a decision, also excluded:")
        for k, v in sorted(other_pend.items(), key=lambda x: -x[1]):
            print(f"    {v:7,}  {k}")
    print(f"  api calls {api_calls:,} · {api_bytes / 1048576:.1f} MB")
    print(f"  qualified leads must return to: {p['return_owner']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
