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
import difflib
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
    """Pull (rule name, assigned user) pairs out of a distribution card's criteria blob."""
    return [[m.group(1).strip(), m.group(2).strip()] for m in
            re.finditer(r'Rule Name\s*:?\s*([^\n/]+)[\s\S]*?Assigned Users?\s*:\s*([^\n]+)', t or "")]


# ---- graph: col/row hand-placed for a clean left-to-right read ----
def n(id, label, role, col, row, sub="", crit=None, comment=None, owner=""):
    return dict(id=id, label=label, role=role, col=col, row=row, sub=sub,
                crit=crit if crit is not None else C(id),
                comment=comment if comment is not None else C(id, "comment"),
                exitc=C(id, "exit"), owner=owner)


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


def walk(cur, conds):
    kids = [e for e in EDGES if e["f"] == cur]
    if not kids:
        paths.append(dict(dest=byid[cur]["label"], destId=cur, role=byid[cur]["role"],
                          owner=byid[cur]["owner"], conds=list(conds)))
        return
    for e in kids:
        node = byid[e["f"]]
        walk(e["t"], conds + ([f"{node['label']} → {e['lab']}"] if e["lab"] else []))


walk("Trigger32", [])

# ---- centre x source-branch ownership matrix ----
LBL = {"DistributeLead208": "Meta Ads", "DistributeLead117": "Internshala",
       "DistributeLead221": "Google / Meta web", "DistributeLead219": "Other sources"}
CARDS = {k: rules(C(k)) for k in LBL}
norm = lambda s: s.split('<')[0].strip()
centres = sorted({c for v in CARDS.values() for c, _ in v}, key=str.lower)
matrix = []
for c in centres:
    row = {"centre": c}
    vals = []
    for k in CARDS:
        u = next((norm(u) for nm, u in CARDS[k] if nm == c), "")
        row[k] = u
        vals.append(u)
    row["differs"] = len({v for v in vals if v}) > 1
    row["missing"] = sum(1 for v in vals if not v)
    matrix.append(row)

# ---- derive the spelling defects instead of hardcoding them ----
# A centre named in exactly one branch, that closely resembles a centre named in
# the others, is a typo: leads matching one spelling are invisible to the other
# branch's rule and fall through to the default owner with no error raised.
present = {k: {c for c, _ in v} for k, v in CARDS.items()}
fold = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
defects = []
for c in centres:
    where = [k for k, s in present.items() if c in s]
    if len(where) != 1:
        continue
    near = max((o for o in centres if o != c),
               key=lambda o: (fold(c) in fold(o) or fold(o) in fold(c),
                              difflib.SequenceMatcher(None, fold(c), fold(o)).ratio()),
               default=None)
    if near and (fold(c) in fold(near) or fold(near) in fold(c)
                 or difflib.SequenceMatcher(None, fold(c), fold(near)).ratio() >= 0.8):
        defects.append({"centre": c, "only": where[0], "near": near})

# ---- upstream steps, channel fan-out, queries, form ----
trows = [[("" if v is None else str(v).strip()) for v in r]
         for r in wb['Top Level Routing'].iter_rows(values_only=True)][1:]
steps = [dict(step=r[0], applies=r[1], name=r[2], summary=r[3], trigger=r[4],
              time=str(r[5])[:5], wait=r[6], dur=r[7], writes=r[8])
         for r in trows if any(r) and r[0].startswith('Step')]
chans = [[r[3], r[8]] for r in trows if any(r) and not r[0] and r[3]]
queries = [str(r[1]) for r in wb['Queries'].iter_rows(min_row=2, values_only=True) if r[1]]
form = [[("" if v is None else str(v)) for v in r]
        for r in wb['Conversation Log Form'].iter_rows(min_row=2, values_only=True) if any(r)]

D = dict(nodes=NODES, edges=EDGES, paths=paths, matrix=matrix, cardLabels=LBL, centres=centres,
         diffs=sum(1 for m in matrix if m["differs"]),
         missing=sum(1 for m in matrix if m["missing"]),
         defects=defects, steps=steps, chans=chans, queries=queries, form=form,
         ext=EXTERNAL, source=WORKBOOK.name)

OUT_DIR.mkdir(parents=True, exist_ok=True)
out = OUT_DIR / "lsq-routing.html"
tpl = (HERE / "template.html").read_text(encoding="utf-8")
out.write_text(tpl.replace("__DATA__", json.dumps(D, ensure_ascii=False)), encoding="utf-8")

distinct = len({s["step"] for s in steps})
print(f"nodes {len(NODES)} | edges {len(EDGES)} | paths {len(paths)} | centres {len(centres)} "
      f"| differ {D['diffs']} | missing {D['missing']} | defects {len(defects)}")
print(f"steps {len(steps)} automations across {distinct} distinct steps | channels {len(chans)} "
      f"| queries {len(queries)} | form rows {len(form)}")
for d in defects:
    print(f"   defect: {d['centre']!r} only in {LBL[d['only']]}, vs {d['near']!r}")
for p in paths:
    print("   →", p["destId"], "|", " · ".join(p["conds"]) or "(direct)")
print(f"   wrote {out} ({out.stat().st_size // 1024} KB)")
