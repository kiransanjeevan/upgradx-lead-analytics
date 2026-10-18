// Shared by both routing-Sankey prototypes, so they draw the same data, in the same node
// order, with the same colours and the same filter bar. Only the rendering differs.
//
// Flow: source group → where the lead was routed (owner's centre) → furthest funnel step.
// Lead rules mirror index.html (isAttempted / isConnected / isEnrolled / centreOf).

export const SRC_GROUPS = ["Meta", "Google", "Internshala", "Others"];
export const OUTCOMES = [            // top → bottom, furthest step reached
  ["enrolled", "Enrolled"],
  ["counselled", "Counselled, not enrolled"],
  ["connected", "Connected, not counselled"],
  ["attempted", "Attempted, not connected"],
  ["none", "Not attempted"],
];
const TOP_CENTRES = 10;
const TAIL = ["Other centres", "No centre", "Pool accounts"];   // always last, in this order

export async function loadUniverse(url = "../data/universe.json") {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`could not load ${url} (${r.status})`);
  return r.json();
}

// ---- lead rules (same as index.html) ----
const isConnected = L => (L[8] || 0) > 0 || L[15] != null;
const isAttempted = L => (L[7] || 0) > 0 || L[2] != null || isConnected(L);
const outcomeOf = (L, D) =>
  D.stage_class[L[9]] === "won" ? "enrolled"
  : L[16] === 1 ? "counselled"
  : isConnected(L) ? "connected"
  : isAttempted(L) ? "attempted" : "none";
const srcGroupOf = (L, D) => SRC_GROUPS.includes(D.sources[L[0]]) ? D.sources[L[0]] : "Others";
const centreOf = (L, D) => L[11] === 1 ? "Pool accounts" : (D.owner_centre[L[4]] || "No centre");

// ---- days: minutes since IST midnight of D.base → day index ----
const dayOf = m => m == null ? null : Math.floor(m / 1440);
export const isoOf = (D, i) => new Date(Date.parse(D.base + "T00:00:00Z") + i * 864e5).toISOString().slice(0, 10);
export const idxOf = (D, iso) => Math.round((Date.parse(iso + "T00:00:00Z") - Date.parse(D.base + "T00:00:00Z")) / 864e5);
// last COMPLETE day = the day before the refresh (generated_at is stamped in IST)
export const lastFullDay = D => idxOf(D, D.generated_at.slice(0, 10)) - 1;

/** Filtered Sankey graph: {nodes:[{id,label,col,kind,value}], links:[{source,target,value}], total}.
 *  Node and link arrays are in the order both renderers must keep. */
export function buildSankey(D, { from, to, sources, pool }) {
  const lo = idxOf(D, from), hi = idxOf(D, to);
  const hop1 = new Map(), hop2 = new Map(), centreN = new Map();
  let total = 0;
  for (const L of D.leads) {
    const d = dayOf(L[1]);
    if (d == null || d < lo || d > hi) continue;
    if (!pool && L[11] === 1) continue;
    const s = srcGroupOf(L, D);
    if (!sources.has(s)) continue;
    const c = centreOf(L, D), o = outcomeOf(L, D);
    hop1.set(s + "|" + c, (hop1.get(s + "|" + c) || 0) + 1);
    hop2.set(c + "|" + o, (hop2.get(c + "|" + o) || 0) + 1);
    centreN.set(c, (centreN.get(c) || 0) + 1);
    total++;
  }
  // top centres by volume; the rest fold into "Other centres"
  const ranked = [...centreN.keys()].filter(c => !TAIL.includes(c)).sort((a, b) => centreN.get(b) - centreN.get(a) || a.localeCompare(b));
  const keep = new Set(ranked.slice(0, TOP_CENTRES));
  const fold = c => keep.has(c) || TAIL.includes(c) ? c : "Other centres";
  const add = (m, k, v) => m.set(k, (m.get(k) || 0) + v);
  const h1 = new Map(), h2 = new Map();
  for (const [k, v] of hop1) { const [s, c] = k.split("|"); add(h1, `s:${s}|c:${fold(c)}`, v); }
  for (const [k, v] of hop2) { const [c, o] = k.split("|"); add(h2, `c:${fold(c)}|o:${o}`, v); }

  const value = new Map();
  for (const m of [h1, h2]) for (const [k, v] of m) { const [a, b] = k.split("|"); add(value, a, v); if (m === h2) add(value, b, v); }
  // a node's value = its outflow (sources, centres) or inflow (outcomes); centres in == out
  const centres = [...ranked.filter(c => keep.has(c)), ...TAIL].filter(c => value.has("c:" + c));
  const nodes = [
    ...SRC_GROUPS.filter(s => value.has("s:" + s)).map(s => ({ id: "s:" + s, label: s, col: 0, kind: "source" })),
    ...centres.map(c => ({ id: "c:" + c, label: c, col: 1, kind: c === "Pool accounts" ? "pool" : "centre" })),
    ...OUTCOMES.filter(([k]) => value.has("o:" + k)).map(([k, l]) => ({ id: "o:" + k, label: l, col: 2, kind: "outcome", outcome: k })),
  ].map(n => ({ ...n, value: value.get(n.id) }));
  const order = new Map(nodes.map((n, i) => [n.id, i]));
  const links = [...h1, ...h2].map(([k, v]) => { const [source, target] = k.split("|"); return { source, target, value: v }; })
    .sort((a, b) => order.get(a.source) - order.get(b.source) || order.get(a.target) - order.get(b.target));
  return { nodes, links, total };
}

// ---- colours: read from the shared CSS tokens, so both pages paint identically ----
export function palette() {
  const css = getComputedStyle(document.documentElement), v = n => css.getPropertyValue(n).trim();
  const byOutcome = { enrolled: v("--out-1"), counselled: v("--out-2"), connected: v("--out-3"), attempted: v("--out-4"), none: v("--out-5") };
  const bySource = { Meta: v("--series-1"), Google: v("--series-2"), Internshala: v("--series-3"), Others: v("--series-4") };
  return {
    node: n => n.kind === "source" ? bySource[n.label] : n.kind === "outcome" ? byOutcome[n.outcome]
             : n.kind === "pool" ? v("--pool") : v("--centre"),
    text: v("--text-primary"), muted: v("--text-secondary"), surface: v("--surface-1"),
  };
}

// ---- the one filter bar both pages use ----
export function mountFilters(el, D, onChange) {
  const last = lastFullDay(D), first = 0;
  el.innerHTML = `
    <label>Range <select id="f-range">
      <option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option>
      <option value="90">Last 90 days</option><option value="all">All data</option><option value="custom">Custom</option></select></label>
    <label>From <input type="date" id="f-from"></label>
    <label>To <input type="date" id="f-to"></label>
    <span class="f-group" role="group" aria-label="Sources">${SRC_GROUPS.map(s =>
      `<label class="chk"><input type="checkbox" value="${s}" checked> ${s}</label>`).join("")}</span>
    <label class="chk"><input type="checkbox" id="f-pool" checked> Include pool accounts</label>`;
  const $ = s => el.querySelector(s), range = $("#f-range"), from = $("#f-from"), to = $("#f-to");
  from.min = to.min = isoOf(D, first); from.max = to.max = isoOf(D, last);
  const setRange = () => { if (range.value === "custom") return;
    const n = range.value === "all" ? last - first + 1 : +range.value;
    from.value = isoOf(D, Math.max(first, last - n + 1)); to.value = isoOf(D, last); };
  const state = () => ({ from: from.value <= to.value ? from.value : to.value, to: from.value <= to.value ? to.value : from.value,
    sources: new Set([...el.querySelectorAll(".f-group input:checked")].map(i => i.value)), pool: $("#f-pool").checked });
  range.onchange = () => { setRange(); onChange(state()); };
  from.onchange = to.onchange = () => { range.value = "custom"; onChange(state()); };
  el.querySelectorAll(".f-group input, #f-pool").forEach(i => i.onchange = () => onChange(state()));
  setRange();
  return state();
}

export const fmt = n => n.toLocaleString("en-IN");
export const pct = (a, b) => b ? (100 * a / b).toFixed(1) + "%" : "—";

// ---- one layout and one set of words, so the two renderers can only differ in the library ----
export const LAYOUT = {
  margin: { top: 12, right: 210, bottom: 12, left: 150 },
  nodeWidth: 14, nodePadding: 12, nodeRadius: 2, labelPadding: 9,
  linkOpacity: 0.4, font: "12px Inter, system-ui, sans-serif", duration: 600,
};
export const labelOf = n => `${n.label} · ${fmt(n.value)}`;
export const nodeTip = (n, total) => `${n.label}: ${fmt(n.value)} leads (${pct(n.value, total)} of all)`;
export const linkTip = (l, byId) => {
  const s = byId.get(l.source.id ?? l.source), t = byId.get(l.target.id ?? l.target);
  return `${s.label} → ${t.label}: ${fmt(l.value)} (${pct(l.value, s.value)} of ${s.label})`;
};
