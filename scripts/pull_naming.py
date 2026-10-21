"""Campaign naming: the data behind the Campaign naming page (Option 1, Kiran 20 Oct 2026).

LSQ routes a new lead by searching its Source Campaign for centre words (30 "contains" rules, first
match wins, LC Team when none match). Source Campaign is typed by hand into each Zap / ad URL, so a
misspelt or missing centre word sends leads to the wrong place. The page builds names from fixed
lists (so the centre word always matches a rule) and checks the names already arriving.

Writes data/naming.json:
  generated_at   IST timestamp
  rules          LSQ routing rules in order [{centre, words}] (export of 15 Oct 2026)
  planned        the agreed rule fixes, so the page can show routing "after the fixes"
  centres        builder centre list [{centre, label, token}]: token = the word put in the name
  campaigns      Source Campaigns of leads CREATED in the last DAYS days:
                 [{c: campaign, src: {group: n}, n, own: {owner centre: n}}]
  meta           live Meta ad sets and lead-form centre questions (only if META_* is set; else the
                 previous file's meta block is kept with its own timestamp)

Read-only everywhere: LSQ Leads.Get + User/AdvancedSearch; Meta Marketing API GETs.
No learner data leaves this script: campaign names, counts and owner centres only.
Stdlib only. LSQ credentials as for pull.py; Meta from META_ACCESS_TOKEN / META_AD_ACCOUNT_ID
(GitHub Secrets in CI; the repo's .env locally).
"""
import json, os, re, sys, time, urllib.request, urllib.parse, urllib.error
from collections import Counter
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import pull as P   # noqa: E402  reuse the full pull's paging, source buckets and owner centres

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "naming.json")
DAYS = 7   # Kiran, 20 Oct 2026: a week of names is enough to catch drift
META_API = "https://graph.facebook.com/v25.0"

# LSQ routing rules, in LSQ's order (LSQ_KNOWLEDGE.md §5.3, export of 15 Oct 2026). Each rule fires when
# Source Campaign CONTAINS one of its words (case-insensitive); the first match wins; none -> LC Team.
# The same words sit on all 4 cards (Meta, Internshala, Google/web, other); only the assignees differ.
# centre = the dashboard's centre name (the owner's admission group), so routing can be compared with
# where leads really went. Re-export from LSQ when Sales Ops changes a rule.
RULES_ASOF = "2026-10-15"
RULES = [
    ("Ahmedabad", ["Ahmedabad"]), ("Hyderabad Ameerpet", ["Ameerpet"]), ("Belgaum", ["Belgaum", "Belagavi"]),
    ("Bhopal", ["Bhopal"]), ("Bhubaneswar", ["Bhubaneswar"]), ("Bilaspur", ["Bilaspur"]),
    ("Coimbatore", ["Coimbatore"]), ("Dehradun", ["Dehradun"]), ("Gurugram", ["Gurugram"]),
    ("Bangalore", ["HSR", "Bangalore"]), ("Indore", ["Indore"]), ("Jabalpur", ["Jabalpur"]),
    ("Jaipur", ["Jaipur"]), ("Bangalore Jayanagar", ["Jayanagar"]), ("Kolkata 2", ["bengal"]),
    ("Lucknow", ["Lucknow"]), ("Hyderabad Madhapur", ["Madhapur"]), ("Mangalore", ["Mangalore"]),
    ("Bangalore Marathahalli", ["Marathahalli"]), ("Panchkula", ["Panchkula"]), ("Raipur", ["Raipur"]),
    ("Rajkot", ["Rajkot"]), ("Sambhajinagar", ["Sambhaji"]), ("Kolkata 2", ["salt"]),
    ("Chandigarh", ["Chandigarh"]), ("Noida", ["Noida"]), ("Panipat", ["Panipat"]), ("Pune 2", ["wakad"]),
    ("Chennai", ["chennai"]), ("Pune", ["Kothrud", "Pune"]),
]
# The fixes agreed on 20 Oct 2026 (Kiran; Sales Ops applies them in LSQ): new Mumbai and Kolkata rules
# at the end, "Gurgaon" added to Gurugram, and "bengal" removed (it also matches "Bengaluru").
PLANNED = {"add": [("Mumbai Andheri", ["Mumbai", "Marol", "Andheri"]), ("Kolkata 2", ["Kolkata"])],
           "extend": {"Gurugram": ["Gurgaon"]}, "drop": ["bengal"]}
# Builder centres: the live centres (routing sheet of 18 Oct 2026, LSQ_KNOWLEDGE.md §5.13). token = the
# word the builder writes into the name; each one routes to its own centre under the live rules, except
# Mumbai, which waits for its new rule. Kolkata uses "SaltLake" so it routes today through the "salt" rule.
CENTRES = [
    ("Bangalore", "Bangalore HSR", "HSR"), ("Bangalore Jayanagar", "Bangalore Jayanagar", "Jayanagar"),
    ("Bangalore Marathahalli", "Bangalore Marathahalli", "Marathahalli"),
    ("Hyderabad Ameerpet", "Hyderabad Ameerpet", "Ameerpet"), ("Hyderabad Madhapur", "Hyderabad Madhapur", "Madhapur"),
    ("Mumbai Andheri", "Mumbai", "Mumbai"), ("Pune", "Pune Kothrud", "Kothrud"), ("Pune 2", "Pune Wakad", "Wakad"),
    ("Bhopal", "Bhopal", "Bhopal"), ("Bhubaneswar", "Bhubaneswar", "Bhubaneswar"),
    ("Chandigarh", "Chandigarh", "Chandigarh"), ("Chennai", "Chennai", "Chennai"),
    ("Coimbatore", "Coimbatore", "Coimbatore"), ("Gurugram", "Gurugram", "Gurugram"), ("Indore", "Indore", "Indore"),
    ("Jabalpur", "Jabalpur", "Jabalpur"), ("Jaipur", "Jaipur", "Jaipur"), ("Kolkata 2", "Kolkata Salt Lake", "SaltLake"),
    ("Mangalore", "Mangalore", "Mangalore"), ("Panipat", "Panipat", "Panipat"), ("Raipur", "Raipur", "Raipur"),
    ("Sambhajinagar", "Sambhajinagar", "Sambhajinagar"),
]
# a lead-form question that asks the learner WHICH centre (not yes/no "are you comfortable attending at our
# X centre?" questions, and not the franchise-enquiry forms)
CENTRE_Q = re.compile(r"^(?!are_|would_|when_|do_)(?=.*(nearest|closest|most_convenient|learning_cent|which_upgrad))", re.I)


def lsq_campaigns(now_ist):
    """Source Campaign of every lead created in the last DAYS days: count, source group, owner centre."""
    since = datetime(now_ist.year, now_ist.month, now_ist.day) - timedelta(days=DAYS)
    P.START = (since - timedelta(minutes=P.IST)).strftime("%Y-%m-%d %H:%M:%S")
    P.COLS = "ProspectID,CreatedOn,Source,SourceCampaign,OwnerId,OwnerIdName"
    leads = P.pull("CreatedOn")
    ucent = P.user_centres()
    agg = {}
    for L in leads:
        c = (L.get("SourceCampaign") or "").strip() or "(blank)"
        a = agg.setdefault(c, {"c": c, "n": 0, "src": Counter(), "own": Counter()})
        a["n"] += 1
        a["src"][P.grp(L.get("Source"))] += 1
        owner = (L.get("OwnerIdName") or "").strip()
        # LC Team is the routing default: keep it apart from the other "No centre" owners
        a["own"]["LC Team" if owner.lower() == "lc team" else
                 P.lead_owner_centre(L.get("OwnerId"), owner, ucent)] += 1
    out = sorted(agg.values(), key=lambda a: -a["n"])
    for a in out:
        a["src"], a["own"] = dict(a["src"]), dict(a["own"])
    print(f"  LSQ: {len(leads):,} leads created since {since:%d %b %Y}, {len(out):,} campaigns")
    return out, f"{since:%Y-%m-%d}"


def meta_env():
    tok, act = os.environ.get("META_ACCESS_TOKEN"), os.environ.get("META_AD_ACCOUNT_ID")
    env = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not (tok and act) and os.path.exists(env):        # local runs read the repo's .env
        kv = dict(l.strip().split("=", 1) for l in open(env) if "=" in l and not l.startswith("#"))
        tok, act = tok or kv.get("META_ACCESS_TOKEN"), act or kv.get("META_AD_ACCOUNT_ID")
    return tok, act


def meta_get(path, tok, **params):
    url = path if path.startswith("http") else f"{META_API}/{path}?" + urllib.parse.urlencode({"access_token": tok, **params})
    for i in range(4):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=120).read())
        except urllib.error.HTTPError as e:
            err = json.loads(e.read() or b"{}").get("error", {})
            if err.get("code") in (4, 17, 32, 613) and i < 3:      # rate limits: wait and retry
                time.sleep(30 * (i + 1)); continue
            raise RuntimeError(f"Meta API {path.split('?')[0][-60:]}: {err.get('message')}")
        except OSError:
            if i == 3:
                raise
            time.sleep(4 * (i + 1))


def meta_all(path, tok, **params):
    out, r = [], meta_get(path, tok, **params)
    while True:
        out += r.get("data", [])
        nxt = r.get("paging", {}).get("next")
        if not nxt:
            return out
        r = meta_get(nxt, tok)


def meta_names(tok, act):
    """Live (ACTIVE) ad sets with their campaign, and the centre options on live lead forms."""
    adsets = meta_all(f"{act}/adsets", tok, fields="id,name,created_time,campaign{name}",
                      effective_status='["ACTIVE"]', limit=200)
    sets = [{"id": a["id"], "name": a["name"], "campaign": (a.get("campaign") or {}).get("name", ""),
             "created": a.get("created_time", "")[:10]} for a in adsets]
    forms = []
    for p in meta_get(f"{act}/promote_pages", tok, fields="id,name").get("data", []):
        ptok = meta_get(p["id"], tok, fields="access_token").get("access_token", tok)
        for f in meta_all(f"{p['id']}/leadgen_forms", ptok, fields="id,name,status,questions", limit=100):
            if f.get("status") != "ACTIVE":
                continue
            for q in f.get("questions") or []:
                if q.get("options") and CENTRE_Q.search(q.get("key", "")) and "franchise" not in q.get("key", ""):
                    forms.append({"id": f["id"], "name": f["name"], "q": q.get("label", ""),
                                  "options": [o.get("value", "") for o in q["options"]]})
    print(f"  Meta: {len(sets)} live ad sets, {len(forms)} live forms that ask for a centre")
    return {"generated_at": datetime.now(timezone.utc).astimezone(P.ISTTZ).isoformat(), "adsets": sets, "forms": forms}


def main():
    if not (P.ACCESS and P.SECRET):
        raise SystemExit("LSQ_ACCESS and LSQ_SECRET must be set (GitHub Secrets in CI).")
    now_ist = datetime.now(timezone.utc) + timedelta(minutes=P.IST)
    camps, since = lsq_campaigns(now_ist)
    try:
        with open(OUT) as f:
            meta = json.load(f).get("meta")
    except (OSError, ValueError):
        meta = None
    tok, act = meta_env()
    if tok and act:
        meta = meta_names(tok, act)
    else:
        print("  Meta: META_ACCESS_TOKEN / META_AD_ACCOUNT_ID not set — keeping the previous ad set list")
    out = {"generated_at": datetime.now(timezone.utc).astimezone(P.ISTTZ).isoformat(),
           "rules_asof": RULES_ASOF, "since": since, "days": DAYS,
           "rules": [{"centre": c, "words": w} for c, w in RULES],
           "planned": {"add": [{"centre": c, "words": w} for c, w in PLANNED["add"]],
                       "extend": PLANNED["extend"], "drop": PLANNED["drop"]},
           "centres": [{"centre": c, "label": l, "token": t} for c, l, t in CENTRES],
           "campaigns": camps, "meta": meta}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
