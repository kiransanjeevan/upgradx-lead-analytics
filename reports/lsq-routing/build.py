#!/usr/bin/env python3
"""
LSQ lead-routing map — generator.

Regenerates the routing-map report from the LeadSquared routing workbook. The
workbook is the source of truth: whenever the LSQ automations change, update
"LSQ Lead Routing Logic.xlsx" and re-run this script. Never hand-edit the
generated HTML — the next run will overwrite it, and hand-edits are how the
published copy silently drifts away from what LeadSquared actually does.

Published artifact (republish to this same URL, don't create a new one):
  https://claude.ai/artifact/3ghUWW1wFjdfm6FMVb9gco

Reads these sheets:
  Offline BU Level Routing   the automation cards -> flow graph + centre rules
  Top Level Routing          the upstream steps and the channel fan-out
  Queries                    open questions
  Conversation Log Form      the form that drives the stage automations

The workbook lives outside this repo (it is a working document, not code) and is
not committed. Override its location with ROUTING_WORKBOOK.

  python3 build.py
  ROUTING_WORKBOOK=/path/to/book.xlsx python3 build.py

Everything that appears as a number in the report's prose is computed here and
interpolated into the template. Do not type a count into template.html.
"""
import json
import os
import re
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
OUT_DIR = Path(os.environ.get("ROUTING_OUT_DIR") or HERE / "out")
WORKBOOK = Path(os.environ.get("ROUTING_WORKBOOK") or
                Path.home() / "Downloads/UpGrad_LC/LSQ/LSQ Lead Routing Logic.xlsx")

# Figures that are NOT derivable from the workbook belong here, with their
# provenance, so they are visible instead of buried in template prose.
# How a centre is identified. Every one of the 30 rules matches this way, so it
# belongs next to the centre list rather than repeated in prose.
CENTRE_MATCH = ("The centre is read from the same free text: Source Campaign, Offline Centre Name "
                "or the page URL must contain the centre\u2019s name. So one campaign name carries "
                "both the \u201cthis is a Learning Centre lead\u201d marker and the city.")

EXTERNAL = {
    # share of September Internshala arrivals parked in the Avtar LCoffline bot
    # queue, from the Internshala funnel pull (reports/internshala)
    "botPoolShare": 85,
    "botPoolShareAsOf": "2026-09-19",
}

if not WORKBOOK.exists():
    raise SystemExit(f"workbook not found: {WORKBOOK}\nset ROUTING_WORKBOOK to its path")

wb = load_workbook(WORKBOOK, data_only=True)

# ---- parse the automation cards -------------------------------------------
ws = wb['Offline BU Level Routing']
cell = {}
for r in ws.iter_rows(values_only=True):
    v = [("" if x is None else str(x).strip()) for x in r]
    if len(v) > 4 and v[2]:
        cell[v[2]] = {"type": v[3], "criteria": v[4],
                      "exit": v[5] if len(v) > 5 else "", "comment": v[6] if len(v) > 6 else ""}
    elif len(v) > 4 and v[3] == 'Updation' and not v[2]:
        cell["UpdateCard"] = {"type": "Updation", "criteria": v[4], "exit": "",
                              "comment": v[6] if len(v) > 6 else ""}

C = lambda k, f="criteria": cell.get(k, {}).get(f, "")


def rules(t):
    """Parse a distribution card's criteria blob into one dict per rule block.

    Keyed on `sig` — the sorted set of quoted match tokens from the Conditions
    block — NOT on the rule name. The rule name is free text someone typed and
    drifts between branches (Bengal/Kolkata and salt/salt-lake-kolkata2 are the
    same rule with identical conditions); the conditions are what LeadSquared
    actually evaluates. Keying on the name invents mismatches that do not exist
    in the instance.

    `users` may legitimately be empty — an "Assigned Users:" line with nothing
    after it is a rule that matches and then distributes to nobody. The pattern
    must stay on its own line rather than running on to the next one and
    mistaking "Check for User Availability : No" for an owner.
    """
    out = []
    for b in re.split(r'(?=Rule Name\s*:)', t or "")[1:]:
        nm = re.search(r'Rule Name\s*:?\s*([^\n/]+)', b)
        if not nm:
            continue
        us = re.search(r'Assigned Users?\s*:[ \t]*([^\n]*)', b)
        cd = re.search(r'Conditions?\s*:?[ \t]*\n([\s\S]*?)(?:\n\s*Rule\s+\d+|\Z)', b)
        conds = cd.group(1).strip() if cd else ""
        toks = sorted({q.lower() for q in re.findall(r'"([^"]+)"', conds)})
        name = nm.group(1).strip()
        out.append(dict(name=name, users=(us.group(1).strip() if us else ""), conds=conds,
                        sig="|".join(toks) if toks else "name:" + name.lower()))
    return out


def owners(u):
    """Assigned Users line -> display names. Empty list means no owner at all.

    Splitting on '>' leaves each name after the first carrying a LEADING comma
    (", Tammana Nagar"). rstrip(',') only trims the trailing side, so those
    commas survived and rendered as "Devika KP, , Tammana Nagar". strip(',')
    takes both ends.
    """
    return [n for n in (x.split('<')[0].strip().strip(',').strip()
                        for x in (u or "").split('>')) if n]


# Plain-language wording for the "Where leads land" view, which is read by people
# who do not know the LeadSquared card names. Keyed by node id so the two views
# can never describe different logic — they render the same graph.
# Plain-language wording, keyed by node id, so the diagram and the "Where leads
# land" view render the same graph and can never describe different logic.
# `q` is the question a first-time reader would ask; `why` says why the step
# exists at all -- the LeadSquared label alone ("Route to POC", "Owner = POC")
# means nothing to someone seeing this for the first time.
PLAIN = {
    "Trigger32":  ("A new lead arrives", "Any lead that lands in the Offline & Learning Centre vertical, still owned by the system account.", "", ""),
    "If6":        ("Is this a Learning Centre lead?",
                   "Decided entirely by the Source Campaign name and the page URL: the campaign must contain "
                   "\u201cLearningCentre\u201d or \u201cCOCO\u201d, or the URL must be an offline-centre page "
                   "(/offline-centres/, upgrad-learning-centre, upgrad-career-centre, or -city). "
                   "Nothing else marks a lead as ours.",
                   "Yes — it belongs to us", "No — it leaves this flow"),
    "IfElse29":   ("Is the lead free to reassign?", "A lead already owned by a person is left alone.", "Yes — nobody owns it yet", "No"),
    "DistributeLead8": ("Hand it to the Learning Centre desk", "A holding step: the lead gets a temporary owner so the rest of the flow has something to act on.", "", ""),
    "IfElse226":  ("Did that hand-off work?", "Confirms the temporary owner was actually set before anything else runs.", "Yes", "No"),
    "UpdateCard": ("Tag it as Offline – Learning Centre", "Stamps the programme, owner and assignment date so later automations and reports can find it.", "", ""),
    "Wait11":     ("Short pause", "Gives the earlier updates time to save before the lead is sorted by source.", "", ""),
    "IfElse74":   ("Did it come from Meta Ads?", "", "Yes", ""),
    "IfElse115":  ("Did it come from Internshala?", "", "Yes", ""),
    "IfElse234":  ("Did they only browse a page?", "A page view means they showed interest without filling anything in — so there is nothing to call them about yet.",
                   "Yes — only browsed", "No — they filled something in"),
    "IfElse237":  ("Has the calling bot spoken to them in the last 2 days?", "Stops the same person being called again straight away.",
                   "Yes — the bot qualified them", "Not yet"),
    "IfElse198":  ("Did it come from Google or a Meta web form?", "", "Yes", ""),
    "IfElse218":  ("Any other source?", "Everything that is not Meta Ads, Internshala, Google or a Meta web form.", "Yes", ""),
    "IfElse225":  ("Is it still sitting unassigned?", "A safety net: anything the rules above missed is picked up here.", "Yes", ""),
    "EXIT":       ("It leaves this flow", "Not a Learning Centre lead, so none of these rules apply to it.", "", ""),
    "DistributeLead208": ("The counsellor for that city", "", "", ""),
    "DistributeLead117": ("The counsellor for that city", "", "", ""),
    "DistributeLead221": ("The counsellor for that city", "", "", ""),
    "DistributeLead219": ("The counsellor for that city", "", "", ""),
    "DistributeLead238": ("Three counsellors who take bot-qualified leads", "", "", ""),
    "UpdateLead235":     ("Waits in the bot calling queue", "", "", ""),
    "DistributeLead216": ("The central LC Team", "", "", ""),
}
DEST = {k: v[0] for k, v in PLAIN.items()}

# What each upstream automation is actually FOR, in the reader's terms. The
# workbook's "Applies to" column already says who each step covers; this adds
# the purpose, which the automation names ("## 03 { New } Interested in Date
# Changed | Reactivation | Junk pullout | SA") do not convey.
STEP_PURPOSE = {
    "Step 1": ("Stamp a brand-new lead", "Fills in the first fields the moment a lead is created — campaign, UTM, and the date the rest of the chain keys off."),
    "Step 2": ("Catch reactivated leads", "Fires whenever Interested In Date changes. A fresh lead passes through; an existing lead that shows interest again is pulled back in here."),
    "Step 3": ("Tag international leads", "Adds region details, but only where the lead is outside India."),
    "Step 4": ("Choose the business unit", "Reads what steps 1-3 wrote and decides which BU the lead belongs to. This is where the Offline & Learning Centre path begins."),
    "Step 5": ("Assign an owner inside that BU", "One automation per business unit, running in parallel. The Offline & LC one is what the routing map above describes."),
}


# ---- graph: col/row hand-placed for a clean left-to-right read ----
def n(id, label, role, col, row, sub="", crit=None, comment=None, owner=""):
    p = PLAIN.get(id, ("", "", "", ""))
    return dict(id=id, label=label, role=role, col=col, row=row, sub=sub,
                crit=crit if crit is not None else C(id),
                comment=comment if comment is not None else C(id, "comment"),
                exitc=C(id, "exit"), owner=owner,
                plain=p[0], why=p[1], yes=p[2], no=p[3], dest=DEST.get(id, ""))


NODES = [
 n("Trigger32", "Lead enters Offline + LC", "trigger", 0, 3, "Trigger"),
 n("If6", "Campaign or URL is an LC page", "decision", 1, 3, "If"),
 n("IfElse29", "Lead is assignable", "decision", 2, 3, "If / Else"),
 n("DistributeLead8", "Route to POC", "action", 3, 3, "Distribution"),
 n("IfElse226", "Owner = POC", "decision", 4, 3, "If / Else"),
 n("UpdateCard", "Stamp Program = Offline-LC", "action", 5, 3, "Update"),
 n("Wait11", "Wait", "action", 6, 3, "Wait"),
 n("IfElse74", "Source = Meta Ads", "decision", 7, 0, "If / Else"),
 n("DistributeLead208", "30 centre rules — Meta", "terminal", 10, 0, "Distribution", owner="Centre counsellor"),
 n("IfElse115", "Source = Internshala", "decision", 7, 1, "If / Else"),
 n("IfElse234", "Medium = Page View", "decision", 8, 1, "If / Else"),
 n("IfElse237", "Bot called in last 2 days", "decision", 9, 1, "If / Else"),
 n("DistributeLead238", "3 named counsellors", "terminal", 10, 1, "Distribution · Avatar Qualified",
   owner="Chhaya Gupta · Prasad Pachore · Shubham Narkar"),
 n("UpdateLead235", "Park in Avtar LCoffline (bot queue)", "terminal-bot", 10, 2, "Update", owner="Avtar LCoffline"),
 n("DistributeLead117", "30 centre rules — Internshala", "terminal", 10, 3, "Distribution", owner="Centre counsellor"),
 n("IfElse198", "Source = Google / Meta web", "decision", 7, 4, "If / Else"),
 n("DistributeLead221", "30 centre rules — Google/web", "terminal", 10, 4, "Distribution", owner="Centre counsellor"),
 n("IfElse218", "Any other source", "decision", 7, 5, "If / Else"),
 n("DistributeLead219", "30 centre rules — other", "terminal", 10, 5, "Distribution", owner="Centre counsellor"),
 n("IfElse225", "Owner still UG_Offline", "decision", 7, 6, "If / Else"),
 n("DistributeLead216", "Offline URL → LC Team", "terminal", 10, 6, "Distribution", owner="LC Team"),
 n("EXIT", "Leaves the flow", "exit", 2, 6, "No match"),
]
E = lambda a, b, lab="": dict(f=a, t=b, lab=lab)
EDGES = [
 E("Trigger32", "If6"), E("If6", "IfElse29", "Yes"), E("If6", "EXIT", "No"),
 E("IfElse29", "DistributeLead8", "Yes"), E("DistributeLead8", "IfElse226"),
 E("IfElse226", "UpdateCard", "Yes"), E("UpdateCard", "Wait11"),
 E("Wait11", "IfElse74"), E("Wait11", "IfElse115"), E("Wait11", "IfElse198"),
 E("Wait11", "IfElse218"), E("Wait11", "IfElse225"),
 E("IfElse74", "DistributeLead208", "Yes"),
 E("IfElse115", "IfElse234", "Yes"),
 E("IfElse234", "IfElse237", "Yes"), E("IfElse234", "DistributeLead117", "No"),
 E("IfElse237", "DistributeLead238", "Yes"), E("IfElse237", "UpdateLead235", "No"),
 E("IfElse198", "DistributeLead221", "Yes"), E("IfElse218", "DistributeLead219", "Yes"),
 E("IfElse225", "DistributeLead216", "Yes")]

# ---- enumerate every path to a terminal ----
byid = {x["id"]: x for x in NODES}
paths = []


def walk(cur, conds, steps):
    kids = [e for e in EDGES if e["f"] == cur]
    if not kids:
        paths.append(dict(dest=byid[cur]["label"], destId=cur, role=byid[cur]["role"],
                          owner=byid[cur]["owner"], conds=list(conds), steps=list(steps),
                          plainDest=byid[cur]["dest"]))
        return
    for e in kids:
        node = byid[e["f"]]
        walk(e["t"], conds + ([f"{node['label']} → {e['lab']}"] if e["lab"] else []),
             steps + ([dict(id=e["f"], answer=e["lab"])] if e["lab"] else []))


walk("Trigger32", [], [])

# Which source branch each path belongs to, so the plain view can group the 8
# paths under the 4 sources a colleague would actually name.
SRC_NODE = {"IfElse74": "Meta Ads", "IfElse115": "Internshala",
            "IfElse198": "Google / Meta web", "IfElse218": "Other sources"}
for p in paths:
    p["source"] = next((SRC_NODE[s["id"]] for s in p["steps"] if s["id"] in SRC_NODE), "")
    # does this path end at the per-city centre rules, or somewhere fixed?
    p["byCentre"] = p["destId"] in ("DistributeLead208", "DistributeLead117",
                                    "DistributeLead221", "DistributeLead219")

# ---- centre x source-branch ownership matrix ----
LBL = {"DistributeLead208": "Meta Ads", "DistributeLead117": "Internshala",
       "DistributeLead221": "Google / Meta web", "DistributeLead219": "Other sources"}
CARDS = {k: rules(C(k)) for k in LBL}

# One matrix row per DISTINCT RULE (condition signature), not per rule name.
# The display label is the rule name the branches agree on — the one used by the
# most branches — with any other spellings recorded as aliases.
bysig = {}
for k, rs in CARDS.items():
    for r in rs:
        bysig.setdefault(r["sig"], {})[k] = r
label = {}
for sig, per in bysig.items():
    names = [r["name"] for r in per.values()]
    label[sig] = max(sorted(set(names)), key=names.count)

matrix = []
for sig in sorted(bysig, key=lambda s: label[s].lower()):
    per = bysig[sig]
    row = {"centre": label[sig], "sig": sig,
           "aliases": sorted({r["name"] for r in per.values()} - {label[sig]}, key=str.lower),
           "conds": next(iter(per.values()))["conds"]}
    seen = []
    for k in CARDS:
        r = per.get(k)
        row[k] = ", ".join(owners(r["users"])) if r else ""
        row[k + "_state"] = "absent" if r is None else ("unassigned" if not owners(r["users"]) else "ok")
        seen.append(row[k + "_state"])
    row["differs"] = len({row[k] for k in CARDS if row[k]}) > 1
    row["missing"] = sum(1 for s in seen if s == "absent")
    row["unassigned"] = sum(1 for s in seen if s == "unassigned")
    matrix.append(row)

# ---- the two things worth flagging, both DERIVED ----
# 1. Naming drift: one rule, spelled differently across branches. The conditions
#    are identical, so routing is unaffected — a documentation-hygiene issue, NOT
#    a defect. (An earlier version of this script keyed the matrix on rule names
#    and reported these as misrouted leads. They never were.)
aliases = [{"centre": m["centre"], "aliases": m["aliases"], "conds": m["conds"],
            "where": {LBL[k]: bysig[m["sig"]][k]["name"]
                      for k in CARDS if k in bysig[m["sig"]]}}
           for m in matrix if m["aliases"]]

# 2. Rules whose "Assigned Users:" line is blank. This is DELIBERATE, not a bug:
#    the centre has closed, so nobody is assigned, and LeadSquared hands the lead
#    to the card's Default User instead. Worth surfacing so a reader knows why the
#    cell is empty and where those leads actually go — but it is working as built.
DEFAULT_USER = {k: (re.search(r'Default User\s*:\s*([^\n<]+)', C(k)) or [None, ""])[1].strip()
                for k in LBL}
no_owner = [{"centre": m["centre"],
             "branches": [LBL[k] for k in CARDS if m[k + "_state"] == "unassigned"],
             "owned": {LBL[k]: m[k] for k in CARDS if m[k + "_state"] == "ok"},
             "fallback": sorted({DEFAULT_USER[k] for k in CARDS
                                 if m[k + "_state"] == "unassigned" and DEFAULT_USER[k]})}
            for m in matrix if m["unassigned"]]

# ---- upstream steps, channel fan-out, queries, form ----
trows = [[("" if v is None else str(v).strip()) for v in r]
         for r in wb['Top Level Routing'].iter_rows(values_only=True)][1:]
steps = [dict(step=r[0], applies=r[1], name=r[2], summary=r[3], trigger=r[4],
              time=str(r[5])[:5], wait=r[6], dur=r[7], writes=r[8],
              purpose=STEP_PURPOSE.get(r[0], ("", ""))[0], why=STEP_PURPOSE.get(r[0], ("", ""))[1])
         for r in trows if any(r) and r[0].startswith('Step')]
chans = [[r[3], r[8]] for r in trows if any(r) and not r[0] and r[3]]
queries = [str(r[1]) for r in wb['Queries'].iter_rows(min_row=2, values_only=True) if r[1]]
form = [[("" if v is None else str(v)) for v in r]
        for r in wb['Conversation Log Form'].iter_rows(min_row=2, values_only=True) if any(r)]

D = dict(nodes=NODES, edges=EDGES, paths=paths, matrix=matrix, cardLabels=LBL,
         centres=[m["centre"] for m in matrix],
         diffs=sum(1 for m in matrix if m["differs"]),
         missing=sum(1 for m in matrix if m["missing"]),
         aliases=aliases, noOwner=no_owner, defaultUser=DEFAULT_USER,
         steps=steps, chans=chans, queries=queries, form=form,
         cardRules={k: [dict(name=r["name"], owners=owners(r["users"]), conds=r["conds"])
                        for r in rules(C(k))]
                    for k in list(LBL) + ["DistributeLead8", "DistributeLead238"]},
         defaultUserAll={k: (re.search(r'Default User\s*:\s*([^\n<]+)', C(k)) or [None, ""])[1].strip()
                         for k in list(LBL) + ["DistributeLead8", "DistributeLead238"]},
         ext=EXTERNAL, centreMatch=CENTRE_MATCH, source=WORKBOOK.name)

OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / "lsq-routing.html"
tpl = (HERE / "template.html").read_text(encoding="utf-8")
out.write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False)), encoding="utf-8")

distinct = len({s["step"] for s in steps})
print(f"nodes {len(NODES)} | edges {len(EDGES)} | paths {len(paths)} | rules {len(matrix)} "
      f"| differ {D['diffs']} | missing {D['missing']} "
      f"| naming drift {len(aliases)} | closed centres {len(no_owner)}")
print(f"steps {len(steps)} automations across {distinct} distinct steps | channels {len(chans)} "
      f"| queries {len(queries)} | form rows {len(form)}")
for a in aliases:
    print(f"   naming drift: {a['centre']!r} also spelled {a['aliases']} "
          f"— identical conditions, no routing impact")
for u in no_owner:
    print(f"   no owner: {u['centre']!r} blank in {u['branches']} "
          f"-> default user {u['fallback']}")
for p in paths:
    print("   →", p["destId"], "|", " · ".join(p["conds"]) or "(direct)")
print(f"   wrote {out} ({out.stat().st_size // 1024} KB)")
