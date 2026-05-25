"""
Generate the self-contained results UI at ui/index.html.

Reads every data/results/{slug}_results.json, embeds them as a JS
constant inside a single static HTML page. Two-state UI:
  - LANDING: brand title + tagline + centered textarea + sample-query chips
  - RESULTS: pipeline funnel strip + tiered tables + criteria right rail

Run:
    python -m scripts.build_ui
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "data" / "results"
UI_DIR = REPO_ROOT / "ui"
UI_DIR.mkdir(parents=True, exist_ok=True)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CoreSimilar</title>
<style>
:root {
  --bg: #f6f7f5;
  --bg-soft: #eef0eb;
  --surface: #ffffff;
  --border: #e3e6e0;
  --border-strong: #c8ccc2;
  --text: #1c1f1a;
  --text-muted: #5a6058;
  --text-dim: #97a094;
  --accent: #3d6b50;
  --accent-soft: #e6f0e9;
  --accent-grad: linear-gradient(135deg, #c9d6c0, #94a991);
  --match-bg: #e2f0db;
  --match-text: #2f5d3a;
  --nomatch-bg: #ececec;
  --nomatch-text: #97a094;
  --c1: #8aa985;
  --c2: #d99b6b;
  --c3: #7aa9b9;
  --c4: #b59cc8;
  --c5: #d97777;
  --c6: #9ec18b;
  --c7: #d4af74;
  --tier1-bg: #e2f0db; --tier1-fg: #2f5d3a;
  --tier2-bg: #fbe8c8; --tier2-fg: #8a5a18;
  --tier3-bg: #ebebeb; --tier3-fg: #5a6058;
}
* { box-sizing: border-box; }
html, body { margin:0; padding:0; background:var(--bg); color:var(--text);
  font: 14px -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ============================================================
   LANDING VIEW
   ============================================================ */
.landing {
  min-height: 100vh;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 40px 20px 80px;
  background: linear-gradient(180deg, var(--bg) 0%, var(--bg-soft) 100%);
}
.landing-brand {
  text-align: center; margin-bottom: 32px;
}
.landing-pill {
  display: inline-flex; align-items: center; gap: 10px;
  padding: 12px 26px; border-radius: 999px;
  background: var(--accent-grad);
  font-weight: 700; font-size: 17px; color: var(--text);
  box-shadow: 0 4px 14px rgba(96, 120, 90, 0.18);
}
.landing-pill .arrow { font-size: 15px; }
.landing-sub {
  font-size: 13px; color: var(--text-muted);
  margin-top: 14px; max-width: 520px;
  text-align: center; line-height: 1.5;
}
.landing-sub b { color: var(--text); font-weight: 600; }

.search-card {
  width: 100%; max-width: 760px;
  background: var(--surface);
  border-radius: 22px;
  box-shadow: 0 4px 28px rgba(28, 35, 25, 0.06);
  border: 1px solid var(--border);
  padding: 18px 18px 14px;
  position: relative;
}
.search-textarea {
  width: 100%;
  border: 0; outline: 0; resize: none;
  font: 16px inherit; color: var(--text);
  background: transparent;
  padding: 6px 56px 12px 6px;
  min-height: 110px;
  line-height: 1.5;
}
.search-textarea::placeholder {
  color: var(--text-dim); font-style: italic;
}
.search-submit {
  position: absolute; right: 18px; bottom: 16px;
  width: 36px; height: 36px; border-radius: 50%;
  border: 1px solid var(--border-strong);
  background: var(--bg-soft);
  display: inline-flex; align-items: center; justify-content: center;
  cursor: pointer; color: var(--text-muted);
  transition: all 0.12s;
}
.search-submit:hover { background: var(--accent); color: white; border-color: var(--accent); }
.search-submit svg { width: 16px; height: 16px; }

.chips-row {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-top: 26px; max-width: 780px; justify-content: center;
}
.chip {
  padding: 7px 14px; border-radius: 999px;
  background: var(--surface); border: 1px solid var(--border);
  font-size: 12.5px; color: var(--text-muted);
  cursor: pointer; transition: all 0.12s;
}
.chip:hover {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.landing-footer {
  margin-top: 56px; text-align: center;
  font-size: 11.5px; color: var(--text-dim);
  max-width: 640px; line-height: 1.7;
}
.landing-footer code {
  background: var(--bg-soft); padding: 2px 6px;
  border-radius: 4px; font-size: 11px;
}

/* ============================================================
   RESULTS VIEW
   ============================================================ */
.results-view { display: none; min-height: 100vh; }
.results-view.active { display: block; }

header.results-header {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 16px;
  padding: 12px 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 50;
}
.results-brand { font-weight: 700; font-size: 15px; display: flex; align-items: center; gap: 8px; cursor: pointer; }
.results-brand .logo { width: 22px; height: 22px; border-radius: 6px;
  background: var(--accent-grad); display: inline-block; }
.back-btn {
  font-size: 12px; color: var(--text-muted); padding: 6px 10px;
  border-radius: 6px; cursor: pointer; user-select: none;
}
.back-btn:hover { background: var(--bg-soft); color: var(--text); }

.search-wrap { position: relative; max-width: 600px; width: 100%; justify-self: center; }
.search-input {
  width: 100%; padding: 9px 14px 9px 38px; border-radius: 10px;
  border: 1px solid var(--border-strong); background: var(--bg-soft);
  font: 13px inherit; color: var(--text);
}
.search-input:focus { outline: none; background: #fff; border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(61,107,80,0.12); }
.search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
  width: 16px; height: 16px; opacity: 0.5; pointer-events: none; }
.search-suggestions {
  position: absolute; top: calc(100% + 6px); left: 0; right: 0;
  background: #fff; border: 1px solid var(--border); border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0,0,0,0.08); padding: 6px; z-index: 100;
  max-height: 360px; overflow-y: auto; display: none;
}
.search-suggestions.open { display: block; }
.search-suggestion {
  padding: 8px 12px; border-radius: 6px; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center; gap: 12px;
}
.search-suggestion:hover, .search-suggestion.focused { background: var(--accent-soft); }
.search-suggestion .name { font-weight: 600; font-size: 13px; }
.search-suggestion .domain { font-size: 11px; color: var(--text-dim); }
.search-suggestion .hint { font-size: 11px; color: var(--text-dim); margin-top: 2px; }
.search-suggestions .empty {
  padding: 12px; color: var(--text-dim); font-size: 12px; text-align: center;
}

/* Pipeline funnel — the "behind the scenes" strip */
.funnel {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 16px 24px;
}
.funnel-row {
  display: flex; align-items: center; gap: 0;
  max-width: 1280px; margin: 0 auto; flex-wrap: wrap;
}
.funnel-stage {
  display: flex; flex-direction: column; align-items: center;
  padding: 8px 14px;
  min-width: 110px;
}
.funnel-stage .num {
  font-size: 17px; font-weight: 700; color: var(--text);
  font-variant-numeric: tabular-nums; line-height: 1.1;
}
.funnel-stage .label {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-muted); margin-top: 4px; font-weight: 600;
}
.funnel-stage.active .num { color: var(--accent); }
.funnel-arrow {
  flex: 0 0 auto;
  color: var(--text-dim); font-size: 16px;
  padding: 0 4px;
}
.funnel-meta {
  margin-left: auto;
  display: flex; gap: 16px; align-items: center;
  font-size: 11.5px; color: var(--text-muted);
}
.funnel-meta .badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 4px 10px; border-radius: 999px;
  background: var(--accent-soft); color: var(--accent);
  font-weight: 600;
}
.funnel-meta .badge.muted { background: var(--bg-soft); color: var(--text-muted); }
.funnel-meta .badge svg { width: 11px; height: 11px; }

.query-banner {
  padding: 11px 24px; background: var(--accent-soft); color: var(--accent);
  font-size: 13px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
  max-width: 1280px; margin: 0 auto;
}
.query-banner b { color: var(--text); font-weight: 700; }
.query-banner .arrow { color: var(--text-dim); }

main.results-main {
  display: grid; grid-template-columns: 1fr 380px; gap: 0;
}

.toolbar { padding: 12px 24px; border-bottom: 1px solid var(--border);
  background: var(--surface); display:flex; gap: 14px; align-items: center;
  color: var(--text-muted); font-size: 13px; }
.toolbar .dot { width: 4px; height: 4px; border-radius: 50%;
  background: var(--text-dim); }
.toolbar .counts { margin-left: auto; color: var(--text-dim); font-variant-numeric: tabular-nums; }

.results { padding: 8px 24px 80px; background: var(--bg); overflow-x: auto; }

/* TIERS */
.tier { margin-top: 28px; }
.tier-header { display:flex; align-items:baseline; gap: 10px;
  padding: 10px 4px 12px; flex-wrap: wrap; cursor: pointer; user-select: none; }
.tier-pill { display:inline-flex; align-items:center;
  padding: 4px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 700; letter-spacing: 0.04em; }
.tier-pill.t1 { background: var(--tier1-bg); color: var(--tier1-fg); }
.tier-pill.t2 { background: var(--tier2-bg); color: var(--tier2-fg); }
.tier-pill.t3 { background: var(--tier3-bg); color: var(--tier3-fg); }
.tier-title { font-weight: 700; font-size: 15px; }
.tier-sub { font-size: 12px; color: var(--text-muted); }
.tier-count { margin-left: auto; font-size: 12px; color: var(--text-dim); }
.tier-header .chev { color: var(--text-dim); font-size: 11px; margin-right: 2px; }
.tier.collapsed .tier-body { display: none; }
.tier.collapsed .chev::before { content: "▶"; }
.tier:not(.collapsed) .chev::before { content: "▼"; }

table.results-table {
  width: 100%; border-collapse: separate; border-spacing: 0;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; overflow: hidden;
  margin-top: 6px;
}
table.results-table th {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; color: var(--text-muted); text-align: left;
  padding: 10px 14px; background: var(--bg-soft);
  border-bottom: 1px solid var(--border);
}
table.results-table td { padding: 12px 14px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
table.results-table tr:last-child td { border-bottom: none; }
table.results-table tbody tr:hover td { background: rgba(230, 240, 233, 0.3); }

.col-name   { width: 180px; }
.col-desc   { min-width: 280px; }
.col-url    { width: 160px; }
.col-crit   { width: 92px; text-align: center; }
.col-cosine { width: 70px; text-align: right; font-variant-numeric: tabular-nums; }

.name-cell { display:flex; align-items:center; gap: 8px; font-weight: 600;
  line-height: 1.3; }
.name-cell .icon { width: 24px; height: 24px; border-radius: 6px; flex: none;
  display:flex; align-items:center; justify-content: center;
  color: white; font-weight: 700; font-size: 10px;
  background: linear-gradient(135deg, #b8c2a8, #94a991); }
.desc-cell { color: var(--text-muted); line-height: 1.45;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  overflow: hidden; max-width: 460px; position: relative; cursor: help; }
.desc-cell[data-full]:hover::after {
  content: attr(data-full);
  position: absolute; top: calc(100% + 6px); left: 0; z-index: 50;
  background: var(--text); color: var(--bg);
  padding: 10px 14px; border-radius: 8px;
  font-size: 13px; font-weight: 400; line-height: 1.55;
  width: max-content; max-width: 520px; min-width: 280px;
  white-space: normal;
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.22);
  pointer-events: none;
}
.desc-cell[data-full=""]:hover::after,
.desc-cell[data-full="—"]:hover::after { display: none; }
.url-cell { color: var(--accent); font-size: 13px;
  text-overflow: ellipsis; white-space: nowrap; overflow: hidden;
  display: inline-block; max-width: 150px; vertical-align: middle; }

.pill { display:inline-flex; align-items:center; gap: 6px;
  padding: 5px 12px; border-radius: 999px;
  font-size: 12px; font-weight: 600;
  cursor: pointer; user-select: none; transition: transform 0.05s; }
.pill:hover { transform: scale(1.04); }
.pill.match { background: var(--match-bg); color: var(--match-text); }
.pill.nomatch { background: var(--nomatch-bg); color: var(--nomatch-text); }

.evidence-row td { background: rgba(246, 247, 245, 0.7); padding: 14px 18px 18px;
  border-top: 1px dashed var(--border); border-bottom: 1px solid var(--border); }
.evidence-block { display:grid; grid-template-columns: 220px 1fr; gap: 14px 16px;
  align-items: start; }
.evidence-block .crit-label {
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
  border-left: 3px solid var(--accent); padding-left: 10px; color: var(--text); padding-top: 1px;
}
.evidence-block .crit-text { font-size: 13px; color: var(--text-muted); margin-top: 2px; padding-left: 10px; }
.evidence-block .evi { font-size: 13px; color: var(--text); line-height: 1.5; }
.evidence-block .evi.nomatch { color: var(--text-dim); font-style: italic; }

/* RIGHT RAIL */
.rail { padding: 22px 24px; background: var(--surface);
  border-left: 1px solid var(--border); overflow-y: auto; }
.rail h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.07em;
  color: var(--text-muted); margin: 0 0 12px; font-weight: 600; }
.rail h2.sep { margin-top: 28px; }

.seed-card { padding: 16px; border: 1px solid var(--border); border-radius: 10px; }
.seed-card .name { font-weight: 700; font-size: 17px; }
.seed-card .meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.seed-card .desc { font-size: 13px; color: var(--text); margin-top: 10px; line-height: 1.5; }
.seed-card .tags { margin-top: 12px; display:flex; flex-wrap: wrap; gap: 4px; }
.seed-card .tag { font-size: 11px; padding: 3px 8px; border-radius: 5px;
  background: var(--bg-soft); color: var(--text-muted); }

.criteria-list { display:flex; flex-direction: column; gap: 10px; }
.criterion {
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 14px 12px 16px; position: relative; background: var(--surface);
}
.criterion::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 4px;
  background: var(--c1); border-radius: 8px 0 0 8px;
}
.criterion.c2::before { background: var(--c2); }
.criterion.c3::before { background: var(--c3); }
.criterion.c4::before { background: var(--c4); }
.criterion.c5::before { background: var(--c5); }
.criterion.c6::before { background: var(--c6); }
.criterion.c7::before { background: var(--c7); }
.criterion .id { font-size: 10px; font-weight: 700; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: 0.06em; }
.criterion .text { font-size: 13px; font-weight: 600; margin-top: 4px; line-height: 1.4; }
.criterion .rat  { font-size: 12px; color: var(--text-muted); margin-top: 6px; line-height: 1.5; }

/* THINKING / EMPTY */
.thinking {
  padding: 60px 24px; text-align: center; color: var(--text-muted);
  font-size: 14px;
}
.thinking .pulse { display: inline-block; padding: 9px 20px;
  background: var(--accent-soft); color: var(--accent); border-radius: 999px;
  font-weight: 600; font-size: 13px; animation: pulse 1.4s ease-in-out infinite; }
.thinking .step { color: var(--text-dim); margin-top: 14px; font-size: 12px; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.55; } }
</style>
</head>
<body>

<!-- =============== LANDING =============== -->
<div class="landing" id="landing">
  <div class="landing-brand">
    <div class="landing-pill">CoreSimilar <span class="arrow">→</span></div>
    <div class="landing-sub">
      Semantic company similarity over a <b>2.8M-company corpus</b>.
      Ask in plain English — the engine extracts criteria, retrieves
      candidates by description embedding, and verifies each on its
      own merits.
    </div>
  </div>

  <div class="search-card">
    <textarea id="landing-input" class="search-textarea"
              placeholder="&quot;vertical AI applications like Harvey for legal&quot;"
              rows="3"></textarea>
    <button class="search-submit" id="landing-submit" title="Run query">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
        <path d="M12 19V5M5 12l7-7 7 7" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
  </div>

  <div class="chips-row" id="chips-row"></div>

  <div class="landing-footer">
    The demo has <span id="footer-count">…</span> pre-computed queries listed above.
    Click a chip to load it instantly, or type any of the queries and hit Enter.
    Behind the scenes each query runs <code>seed lookup → enrichment → criteria → pre-filter → embedding → verification → ranking</code>.
  </div>
</div>

<!-- =============== RESULTS =============== -->
<div class="results-view" id="results-view">

<header class="results-header">
  <div class="results-brand" id="back-home">
    <span class="logo"></span> CoreSimilar
  </div>
  <div class="search-wrap">
    <svg class="search-icon" viewBox="0 0 20 20" fill="none">
      <path d="M9 2a7 7 0 105.29 11.71l3.5 3.5a1 1 0 001.42-1.42l-3.5-3.5A7 7 0 009 2zm0 2a5 5 0 110 10 5 5 0 010-10z" fill="currentColor"/>
    </svg>
    <input class="search-input" id="search" autocomplete="off"
           placeholder="Search a different query…">
    <div class="search-suggestions" id="suggestions"></div>
  </div>
  <div class="back-btn" id="back-btn">← new search</div>
</header>

<div class="query-banner" id="query-banner" style="display:none;">
  <span>You searched:</span>
  <b id="query-text">…</b>
  <span class="arrow">→</span>
  <span>resolved to seed <b id="query-seed">…</b></span>
</div>

<div class="funnel" id="funnel" style="display:none;">
  <div class="funnel-row" id="funnel-row"></div>
</div>

<main class="results-main">
  <div>
    <div class="toolbar" id="toolbar" style="display:none;">
      <span><b id="tier-summary">…</b></span>
      <span class="dot"></span>
      <span id="criteria-summary">…</span>
      <span class="counts" id="counts">…</span>
    </div>
    <div class="results" id="results">
      <div class="thinking"><div class="pulse">Loading…</div></div>
    </div>
  </div>
  <aside class="rail" id="rail"></aside>
</main>

</div>

<script>
const DATA = __DATA__;
const SEED_ORDER = __SEED_ORDER__;
const TOTAL_CORPUS = 2810986;
const DEMO_CORPUS  = 349934;

const TIER_BUCKETS = [
  { id: "t1", label: "Tier 1 — strong peers",  pillClass: "t1", sub: "match 4 or 5 of the criteria" },
  { id: "t2", label: "Tier 2 — partial peers", pillClass: "t2", sub: "match 3 of the criteria" },
  { id: "t3", label: "Tier 3 — adjacent",      pillClass: "t3", sub: "match 1 or 2 of the criteria" },
];
const PER_TIER_CAP = 25;

function $(s, root) { return (root || document).querySelector(s); }
function $all(s, root) { return [...(root || document).querySelectorAll(s)]; }

function critClass(id) { return "c" + (((id - 1) % 7) + 1); }
function initials(name) {
  const parts = (name || "?").split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase();
}
function fmtCosine(c) { return c == null ? "—" : c.toFixed(3); }
function fmtNum(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 10_000)    return Math.round(n / 1000) + "K";
  if (n >= 1000)      return (n / 1000).toFixed(1).replace(/\.0$/, "") + "K";
  return String(n);
}
function urlFor(d) { return d ? "https://" + d.replace(/^https?:\/\//, "") : null; }
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, ch => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[ch]));
}

/* -------------------- Regroup candidates into 3 match-count tiers -------------------- */
function consolidatedTiers(data) {
  const criteria = data.criteria || [];
  const flat = [];
  for (const t of data.tiers || []) {
    for (const c of t.candidates || []) {
      const evi = c.evidence || {};
      const matchCount = criteria.reduce((n, cr) => {
        const e = evi[cr.id] || evi[String(cr.id)];
        return n + (e && e.match ? 1 : 0);
      }, 0);
      flat.push({ ...c, matchCount });
    }
  }
  const buckets = TIER_BUCKETS.map(spec => ({ ...spec, candidates: [] }));
  for (const c of flat) {
    if (c.matchCount === 0) continue;
    let idx = -1;
    if (c.matchCount >= 4) idx = 0;
    else if (c.matchCount === 3) idx = 1;
    else if (c.matchCount >= 1) idx = 2;
    if (idx >= 0) buckets[idx].candidates.push(c);
  }
  for (const b of buckets) {
    const seen = new Set();
    b.candidates = b.candidates
      .filter(c => {
        if (seen.has(c.record_id)) return false;
        seen.add(c.record_id);
        return true;
      })
      .sort((a, b) => (b.cosine || 0) - (a.cosine || 0))
      .slice(0, PER_TIER_CAP);
  }
  return buckets;
}

/* -------------------- Pipeline funnel -------------------- */
function renderFunnel(data, tiers) {
  const m = data.meta || {};
  const prefilter = m.n_candidates_prefilter || 0;
  const ranked = (m.embed_top_k != null) ? Math.min(m.embed_top_k, prefilter) : (data.candidate_count_total || 0);
  const verified = tiers.reduce((n, t) => n + t.candidates.length, 0);

  const stages = [
    { num: fmtNum(TOTAL_CORPUS),  label: "full corpus",  full: TOTAL_CORPUS.toLocaleString() },
    { num: fmtNum(DEMO_CORPUS),   label: "demo trim",    full: DEMO_CORPUS.toLocaleString() },
    { num: fmtNum(prefilter),     label: "pre-filtered", full: prefilter.toLocaleString() },
    { num: fmtNum(ranked),        label: "ranked",       full: ranked.toLocaleString() },
    { num: String(verified),      label: "verified peers", active: true,
      full: verified + " across " + tiers.filter(t => t.candidates.length).length + " tiers" },
  ];
  const stagesHtml = stages.map((s, i) =>
    `<div class="funnel-stage${s.active ? " active" : ""}" title="${s.full}">
       <div class="num">${s.num}</div>
       <div class="label">${s.label}</div>
     </div>` + (i < stages.length - 1 ? `<div class="funnel-arrow">→</div>` : "")
  ).join("");

  const compCount = (data.meta?.crustdata_competitors_used ?? null);
  const enrichBadge = m.crustdata_matched
    ? `<span class="badge">enrichment ON</span>`
    : `<span class="badge muted">enrichment off</span>`;
  const embedBadge = m.n_new_embeddings != null
    ? `<span class="badge muted">${m.n_new_embeddings.toLocaleString()} new embeddings</span>`
    : "";
  const secsBadge = data.pipeline_seconds != null
    ? `<span class="badge muted">${data.pipeline_seconds.toFixed(0)}s pipeline</span>`
    : "";

  $("#funnel").style.display = "";
  $("#funnel-row").innerHTML = stagesHtml +
    `<div class="funnel-meta">${enrichBadge} ${embedBadge} ${secsBadge}</div>`;
}

/* -------------------- Render results -------------------- */
function render(slug) {
  const data = DATA[slug];
  if (!data) {
    $("#results").innerHTML = `<div class="thinking">No results.</div>`;
    return;
  }
  const seed = data.seed;
  const criteria = data.criteria || [];

  if (data.user_query) {
    $("#query-banner").style.display = "";
    $("#query-text").textContent = `"${data.user_query}"`;
    $("#query-seed").textContent = seed.name;
  } else {
    $("#query-banner").style.display = "none";
  }

  const tiers = consolidatedTiers(data);
  renderFunnel(data, tiers);

  const tier1 = tiers[0];
  $("#tier-summary").textContent = `${tier1.candidates.length} strong peer${tier1.candidates.length === 1 ? "" : "s"}`;
  $("#criteria-summary").textContent = `${criteria.length} criteria`;
  const totalShown = tiers.reduce((n, t) => n + t.candidates.length, 0);
  $("#counts").textContent = `${totalShown} shown · ${data.candidate_count_total} ranked`;
  $("#toolbar").style.display = "";

  /* Right rail */
  const tagsHtml = (seed.industries || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("");
  $("#rail").innerHTML = `
    <h2>Seed</h2>
    <div class="seed-card">
      <div class="name">${escapeHtml(seed.name || "—")}</div>
      <div class="meta">${escapeHtml(seed.domain || "—")}${seed.country ? "  ·  " + escapeHtml(seed.country) : ""}</div>
      <div class="tags">${tagsHtml}</div>
    </div>
    <h2 class="sep">Criteria</h2>
    <div class="criteria-list">
      ${criteria.map(c => `
        <div class="criterion ${critClass(c.id)}">
          <div class="id">Criterion ${c.id}</div>
          <div class="text">${escapeHtml(c.text)}</div>
          <div class="rat">${escapeHtml(c.rationale || "")}</div>
        </div>`).join("")}
    </div>
  `;

  /* Tiers */
  if (tiers.every(t => t.candidates.length === 0)) {
    $("#results").innerHTML = `<div class="thinking">No candidates matched any criterion.</div>`;
    return;
  }
  $("#results").innerHTML = tiers.map((t, idx) => {
    if (!t.candidates.length) return "";
    return `
      <div class="tier" data-tier="${t.id}">
        <div class="tier-header" data-toggle="tier-${idx}">
          <span class="chev"></span>
          <span class="tier-pill ${t.pillClass}">${t.label.split(" — ")[0].toUpperCase()}</span>
          <span class="tier-title">${escapeHtml(t.label)}</span>
          <span class="tier-sub">· ${escapeHtml(t.sub)}</span>
          <span class="tier-count">${t.candidates.length} of ${PER_TIER_CAP}</span>
        </div>
        <div class="tier-body">
          <table class="results-table">
            <thead>
              <tr>
                <th class="col-name">Name</th>
                <th class="col-desc">Description</th>
                <th class="col-url">URL</th>
                ${criteria.map(c => `<th class="col-crit" title="${escapeHtml(c.text)}">C${c.id}</th>`).join("")}
                <th class="col-cosine">cosine</th>
              </tr>
            </thead>
            <tbody>
              ${t.candidates.map((c, ci) => renderRow(c, criteria, idx, ci)).join("")}
            </tbody>
          </table>
        </div>
      </div>`;
  }).join("");

  $all(".tier-header", $("#results")).forEach(el => {
    el.addEventListener("click", e => {
      if (e.target.closest(".pill")) return;
      el.closest(".tier").classList.toggle("collapsed");
    });
  });
  $all("[data-evi-toggle]", $("#results")).forEach(el => {
    el.addEventListener("click", e => {
      e.stopPropagation();
      const row = document.getElementById(el.dataset.eviToggle);
      if (row) row.style.display = row.style.display === "none" ? "" : "none";
    });
  });
}

function renderRow(cand, criteria, tierIdx, candIdx) {
  const rowId = `evi-${tierIdx}-${candIdx}`;
  const url = urlFor(cand.domain);
  const cells = criteria.map(c => {
    const ev = cand.evidence?.[c.id] || cand.evidence?.[String(c.id)];
    const match = ev && ev.match;
    return `<td class="col-crit">
      <span class="pill ${match ? "match" : "nomatch"}" data-evi-toggle="${rowId}">
        ${match ? "Match" : "—"}
      </span></td>`;
  }).join("");
  return `
    <tr>
      <td class="col-name">
        <div class="name-cell">
          <span class="icon">${initials(cand.name)}</span>
          <span>${escapeHtml(cand.name || "—")}</span>
        </div>
      </td>
      <td class="col-desc"><div class="desc-cell" data-full="${escapeHtml(cand.description_full || cand.one_liner || "")}">${escapeHtml(cand.one_liner || "—")}</div></td>
      <td class="col-url">${url
        ? `<a class="url-cell" href="${url}" target="_blank" rel="noreferrer">${escapeHtml(cand.domain)}</a>`
        : "<span class='url-cell'>—</span>"}</td>
      ${cells}
      <td class="col-cosine">${fmtCosine(cand.cosine)}</td>
    </tr>
    <tr class="evidence-row" id="${rowId}" style="display:none;">
      <td colspan="${4 + criteria.length}">
        <div class="evidence-block">
          ${criteria.map(c => {
            const ev = cand.evidence?.[c.id] || cand.evidence?.[String(c.id)] || {};
            const isMatch = !!ev.match;
            const eviText = isMatch
              ? (ev.evidence || "(matched, no evidence text returned)")
              : "(no match — criterion not supported by the description)";
            return `
              <div class="crit-label ${critClass(c.id)}">C${c.id}</div>
              <div>
                <div class="crit-text">${escapeHtml(c.text)}</div>
                <div class="evi ${isMatch ? "" : "nomatch"}" style="margin-top:6px;">${escapeHtml(eviText)}</div>
              </div>`;
          }).join("")}
        </div>
      </td>
    </tr>`;
}

/* -------------------- Search (results-view) -------------------- */
const availableSeeds = Object.entries(DATA).map(([slug, d]) => ({
  slug,
  name:        d.seed?.name || slug,
  domain:      d.seed?.domain || "",
  industries:  d.seed?.industries || [],
  user_query:  d.user_query || "",
}));

function suggest(query) {
  const q = (query || "").toLowerCase().trim();
  const matches = availableSeeds.filter(s =>
    s.user_query.toLowerCase().includes(q) ||
    s.name.toLowerCase().includes(q) ||
    s.domain.toLowerCase().includes(q)
  ).slice(0, 8);
  const box = $("#suggestions");
  if (!q || !matches.length) {
    if (q && !matches.length) {
      box.innerHTML = `<div class="empty">No pre-computed match for “${escapeHtml(query)}”.<br>
        <span style="font-size:11px;">A hosted version would trigger a fresh run (~10 min).</span></div>`;
      box.classList.add("open");
    } else { box.classList.remove("open"); }
    return;
  }
  box.innerHTML = matches.map((s, i) => `
    <div class="search-suggestion${i === 0 ? " focused" : ""}" data-slug="${s.slug}">
      <div style="min-width:0;">
        <div class="name">${escapeHtml(s.user_query || s.name)}</div>
        <div class="hint">${escapeHtml(s.name)} · ${escapeHtml((s.industries || []).slice(0, 3).join(" · "))}</div>
      </div>
      <div class="domain">${escapeHtml(s.domain)}</div>
    </div>`).join("");
  box.classList.add("open");
  $all(".search-suggestion", box).forEach(el => {
    el.addEventListener("click", () => runQuery(el.dataset.slug));
  });
}

function findSlugByQuery(raw) {
  const q = (raw || "").toLowerCase().trim();
  if (!q) return null;
  if (DATA[q]) return q;
  return availableSeeds.find(s =>
    s.user_query.toLowerCase().includes(q) ||
    s.name.toLowerCase().includes(q) ||
    s.domain.toLowerCase().includes(q))?.slug || null;
}

function showResultsView() {
  $("#landing").style.display = "none";
  $("#results-view").classList.add("active");
}
function showLandingView() {
  $("#landing").style.display = "";
  $("#results-view").classList.remove("active");
  $("#search").value = "";
  $("#landing-input").value = "";
  $("#suggestions").classList.remove("open");
}

async function mapQueryViaServer(raw) {
  /* Calls the local /api/map_query proxy (scripts/serve_ui.py).
     Returns the matched slug, or null. Silently falls back to null if
     the static file is opened directly (no server). */
  try {
    const res = await fetch("/api/map_query?q=" + encodeURIComponent(raw), {
      method: "GET",
      headers: { "Accept": "application/json" },
    });
    if (!res.ok) return null;
    const body = await res.json();
    return body.slug || null;
  } catch (e) {
    return null;
  }
}

function showNoMatch(rawQuery, hint) {
  showResultsView();
  $("#search").value = rawQuery || "";
  $("#query-banner").style.display = "none";
  $("#funnel").style.display = "none";
  $("#toolbar").style.display = "none";
  $("#rail").innerHTML = "";
  const tip = hint
    ? `<div class="step">${escapeHtml(hint)}</div>`
    : `<div class="step">Try one of the chips on the home page, or run <code>python -m scripts.run_all_seeds</code> for more seeds.</div>`;
  $("#results").innerHTML = `
    <div class="thinking">
      <div class="pulse">No pre-computed match for &ldquo;${escapeHtml(rawQuery)}&rdquo;</div>
      ${tip}
    </div>`;
}

async function runQuery(slugOrRawQuery) {
  const raw = slugOrRawQuery || "";
  let slug = DATA[raw] ? raw : findSlugByQuery(raw);

  /* Show a thinking screen while we may be calling the LLM mapper. */
  showResultsView();
  $("#suggestions").classList.remove("open");
  $("#search").value = (slug && DATA[slug]) ? (DATA[slug].user_query || DATA[slug].seed?.name || slug) : raw;
  $("#rail").innerHTML = "";
  $("#query-banner").style.display = "none";
  $("#funnel").style.display = "none";
  $("#toolbar").style.display = "none";
  $("#results").innerHTML = `
    <div class="thinking">
      <div class="pulse">${slug ? "Extracting criteria · ranking peers" : "Mapping your query to a seed"}</div>
      <div class="step">seed lookup → enrichment → criteria → pre-filter → embedding → verification</div>
    </div>`;

  if (!slug) {
    const mapped = await mapQueryViaServer(raw);
    if (mapped && DATA[mapped]) {
      slug = mapped;
      $("#search").value = raw;
    } else {
      const tip = mapped
        ? `Mapped to slug “${mapped}” but no pre-computed result exists for it yet.`
        : "Try one of the example chips below, or run locally with python -m scripts.serve_ui for LLM-powered query mapping.";
      showNoMatch(raw, tip);
      return;
    }
  }

  setTimeout(() => render(slug), 520);
}

/* -------------------- LANDING wiring -------------------- */
function renderChips() {
  const ordered = SEED_ORDER.filter(s => DATA[s])
    .concat(Object.keys(DATA).filter(s => !SEED_ORDER.includes(s)));
  $("#footer-count").textContent = ordered.length;
  if (!ordered.length) {
    $("#chips-row").innerHTML = `<div style="font-size:12px; color:var(--text-dim);">No pre-computed queries yet. Run <code>python -m scripts.run_all_seeds</code>.</div>`;
    return;
  }
  $("#chips-row").innerHTML = ordered.map(slug => {
    const q = DATA[slug].user_query || `companies similar to ${DATA[slug].seed?.name || slug}`;
    return `<span class="chip" data-slug="${slug}">${escapeHtml(q)}</span>`;
  }).join("");
  $all(".chip").forEach(el => el.addEventListener("click", () => {
    $("#landing-input").value = DATA[el.dataset.slug].user_query || "";
    runQuery(el.dataset.slug);
  }));
}

$("#landing-submit").addEventListener("click", () => {
  runQuery($("#landing-input").value);
});
$("#landing-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    runQuery($("#landing-input").value);
  }
});
$("#back-btn").addEventListener("click", showLandingView);
$("#back-home").addEventListener("click", showLandingView);

/* Results-view search bar wiring */
const inp = $("#search");
inp.addEventListener("input", e => suggest(e.target.value));
inp.addEventListener("focus", e => { if (e.target.value) suggest(e.target.value); });
inp.addEventListener("blur", () => setTimeout(() => $("#suggestions").classList.remove("open"), 120));
inp.addEventListener("keydown", e => {
  if (e.key === "Enter") {
    const first = $(".search-suggestion.focused, .search-suggestion", $("#suggestions"));
    if (first) runQuery(first.dataset.slug);
    else runQuery(inp.value);
    e.preventDefault();
  } else if (e.key === "Escape") {
    $("#suggestions").classList.remove("open");
  }
});

/* Boot */
renderChips();
</script>
</body>
</html>
"""


SEED_ORDER = [
    "harvey", "hugging_face", "klarna", "wiz", "vercel",
    "figma", "notion", "mercury", "replicate", "function_health",
]


def main():
    if not RESULTS_DIR.exists():
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {}
    for path in sorted(RESULTS_DIR.glob("*_results.json")):
        slug = path.stem
        if slug.endswith("_results"):
            slug = slug[: -len("_results")]
        try:
            results[slug] = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"  skipping {path.name}: {e}")

    data_js = json.dumps(results)
    html = (HTML_TEMPLATE
            .replace("__DATA__", data_js)
            .replace("__SEED_ORDER__", json.dumps(SEED_ORDER)))

    out = UI_DIR / "index.html"
    out.write_text(html)
    print(f"Wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {len(results)} seeds embedded)")
    if results:
        print(f"  Open in browser:  file://{out}")


if __name__ == "__main__":
    main()
