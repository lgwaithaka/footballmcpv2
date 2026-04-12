"""
dashboard.py
────────────
Starlette web application — serves the analytics dashboard UI and REST API.

Routes
  GET  /                      → HTML dashboard
  GET  /health                → JSON health check
  GET  /api/fixtures          → JSON list of all fixtures + predictions
  GET  /api/fixture/{id}      → JSON single fixture prediction (5 outcomes)
  GET  /api/stats             → JSON model accuracy stats
"""

import json
import os
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from analytics_engine import (
    MarketData, GoalMarketData, AnalyticsResult,
    init_db, persist_result, accuracy_report, load_weights,
    remove_margin, provider_margin, goal_market_probs,
    run_prediction, select_pick, confidence_tier,
)
from schedule_data import all_fixtures, fixture_by_id, ScheduledMatch


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _market_data(f: ScheduledMatch):
    la = MarketData(f.lines_a.home, f.lines_a.draw, f.lines_a.away) if f.lines_a else None
    lb = MarketData(f.lines_b.home, f.lines_b.draw, f.lines_b.away) if f.lines_b else None
    gm_a = GoalMarketData(f.lines_a.over_2_5, f.lines_a.under_2_5,
                          f.lines_a.both_score_yes, f.lines_a.both_score_no) if f.lines_a else None
    gm_b = GoalMarketData(f.lines_b.over_2_5, f.lines_b.under_2_5,
                          f.lines_b.both_score_yes, f.lines_b.both_score_no) if f.lines_b else None
    return la, lb, gm_a, gm_b


def _predict_fixture(f: ScheduledMatch) -> dict:
    la, lb, gm_a, gm_b = _market_data(f)
    if not la and not lb:
        return None
    weights = load_weights()
    r = run_prediction(la, lb, weights=weights)
    hp, dp, ap = r["hp"], r["dp"], r["ap"]
    over_25, btts = goal_market_probs(gm_a, gm_b, r["home_xg"], r["away_xg"])
    pick_label, pick_code, conf = select_pick(hp, dp, ap)
    tier = confidence_tier(conf)
    fid = str(f.provider_b_id or f.provider_a_id)

    res = AnalyticsResult(
        fixture_id=fid,
        home_team=f.home_team, away_team=f.away_team,
        league=f.league, country=f.country,
        kickoff=f.kickoff, date=f.date,
        home_prob=hp, draw_prob=dp, away_prob=ap,
        over_25_prob=over_25, btts_prob=btts,
        recommended_pick=pick_label, pick_code=pick_code,
        confidence=conf, confidence_pct=int(conf * 100),
        confidence_label=tier,
        provider_margin_pct=r["margin_pct"],
        consensus_gap_pct=r["consensus_gap_pct"],
        home_form_pts=7.5, away_form_pts=7.5,
        home_xg=r["home_xg"], away_xg=r["away_xg"],
        h2h_home=0, h2h_draw=0, h2h_away=0,
    )
    persist_result(res)

    return {
        "id": fid,
        "home": f.home_team, "away": f.away_team,
        "league": f.league, "country": f.country,
        "kickoff": f.kickoff, "date": f.date,
        "home_prob":   round(hp * 100, 1),
        "draw_prob":   round(dp * 100, 1),
        "away_prob":   round(ap * 100, 1),
        "over_25_prob": round(over_25 * 100, 1),
        "btts_prob":   round(btts * 100, 1),
        "pick":        pick_label,
        "pick_code":   pick_code,
        "confidence":  int(conf * 100),
        "tier":        tier,
        "home_xg":     r["home_xg"],
        "away_xg":     r["away_xg"],
        "margin_pct":  r["margin_pct"],
        "has_two_sources": la is not None and lb is not None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Route handlers
# ─────────────────────────────────────────────────────────────────────────────

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "Football Analytics MCP",
                         "ts": datetime.now(timezone.utc).isoformat()})


async def api_fixtures(request: Request) -> JSONResponse:
    fixtures = all_fixtures()
    results = []
    for f in fixtures:
        d = _predict_fixture(f)
        if d:
            results.append(d)
    results.sort(key=lambda x: x["confidence"], reverse=True)
    return JSONResponse({"count": len(results), "fixtures": results})


async def api_fixture(request: Request) -> JSONResponse:
    fid = request.path_params.get("fixture_id")
    try:
        f = fixture_by_id(int(fid))
    except (ValueError, TypeError):
        f = None
    if not f:
        return JSONResponse({"error": f"Fixture {fid} not found"}, status_code=404)
    d = _predict_fixture(f)
    if not d:
        return JSONResponse({"error": "No market data for this fixture"}, status_code=404)
    return JSONResponse(d)


async def api_stats(request: Request) -> JSONResponse:
    return JSONResponse({"accuracy": accuracy_report(), "weights": load_weights()})


async def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard HTML — dark tactical board aesthetic
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Football Analytics — Match Predictions</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=JetBrains+Mono:wght@300;400;600&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #06080C;
    --surface:  #0D1117;
    --card:     #111823;
    --card2:    #151F2B;
    --border:   rgba(0,255,135,0.12);
    --border2:  rgba(0,255,135,0.25);
    --green:    #00FF87;
    --green2:   #00CC6A;
    --blue:     #0094FF;
    --amber:    #FFB800;
    --coral:    #FF5252;
    --purple:   #C084FC;
    --text:     #E2EAF4;
    --muted:    #5A6880;
    --dim:      #2A3548;
  }

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── Grid pattern background ── */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(0,255,135,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,255,135,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  /* ── Header ── */
  header {
    position: relative;
    z-index: 10;
    padding: 28px 40px 24px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(0,255,135,0.05) 0%, transparent 100%);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
  }

  .logo {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .logo-icon {
    width: 44px; height: 44px;
    background: var(--green);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px;
    box-shadow: 0 0 20px rgba(0,255,135,0.4);
  }

  .logo-text {
    font-family: 'Orbitron', sans-serif;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text);
  }

  .logo-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: var(--green);
    letter-spacing: 0.15em;
    text-transform: uppercase;
    margin-top: 2px;
  }

  .header-stats {
    display: flex;
    gap: 24px;
    align-items: center;
  }

  .stat-pill {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 16px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .stat-pill span {
    color: var(--green);
    font-weight: 600;
  }

  /* ── Filters ── */
  .filters {
    position: relative;
    z-index: 10;
    padding: 20px 40px;
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }

  .filter-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-right: 4px;
  }

  .filter-btn {
    background: var(--card);
    border: 1px solid var(--dim);
    color: var(--muted);
    padding: 7px 16px;
    border-radius: 6px;
    cursor: pointer;
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    transition: all 0.18s ease;
    white-space: nowrap;
  }

  .filter-btn:hover {
    border-color: var(--border2);
    color: var(--text);
  }

  .filter-btn.active {
    background: rgba(0,255,135,0.1);
    border-color: var(--green);
    color: var(--green);
  }

  .search-box {
    background: var(--card);
    border: 1px solid var(--dim);
    color: var(--text);
    padding: 7px 14px;
    border-radius: 6px;
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    outline: none;
    width: 220px;
    margin-left: auto;
    transition: border-color 0.18s;
  }

  .search-box:focus { border-color: var(--border2); }
  .search-box::placeholder { color: var(--muted); }

  /* ── Sort controls ── */
  .sort-bar {
    position: relative;
    z-index: 10;
    padding: 12px 40px;
    display: flex;
    gap: 8px;
    align-items: center;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }

  .sort-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .sort-btn {
    background: transparent;
    border: none;
    color: var(--muted);
    padding: 4px 10px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    font-family: 'DM Sans', sans-serif;
    transition: all 0.15s;
  }

  .sort-btn:hover, .sort-btn.active { color: var(--green); background: rgba(0,255,135,0.07); }

  .count-badge {
    margin-left: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: var(--muted);
  }

  .count-badge span { color: var(--text); font-weight: 600; }

  /* ── Main grid ── */
  main {
    position: relative;
    z-index: 10;
    padding: 28px 40px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 20px;
  }

  /* ── Fixture card ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
    transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
    cursor: default;
  }

  .card:hover {
    transform: translateY(-3px);
    border-color: var(--border2);
    box-shadow: 0 12px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(0,255,135,0.08);
  }

  .card-header {
    padding: 16px 20px 14px;
    background: var(--card2);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }

  .card-meta {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }

  .league-tag {
    font-family: 'JetBrains Mono', monospace;
    font-size: 9px;
    color: var(--green);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .country-tag {
    font-size: 10px;
    color: var(--muted);
  }

  .kickoff-tag {
    font-family: 'Orbitron', sans-serif;
    font-size: 12px;
    color: var(--amber);
    font-weight: 700;
    letter-spacing: 0.05em;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .teams {
    padding: 18px 20px 14px;
    display: flex;
    align-items: center;
    gap: 0;
    position: relative;
  }

  .team {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .team.away { align-items: flex-end; text-align: right; }

  .team-name {
    font-family: 'Orbitron', sans-serif;
    font-size: 13px;
    font-weight: 700;
    color: var(--text);
    letter-spacing: 0.02em;
    line-height: 1.3;
    word-break: break-word;
  }

  .team-xg {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    color: var(--muted);
  }

  .vs-block {
    width: 44px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  .vs-text {
    font-family: 'Orbitron', sans-serif;
    font-size: 9px;
    color: var(--dim);
    letter-spacing: 0.1em;
  }

  .vs-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    opacity: 0.6;
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 0.3; transform: scale(1); }
    50%       { opacity: 0.9; transform: scale(1.3); }
  }

  /* ── Confidence badge ── */
  .confidence-row {
    padding: 0 20px 14px;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .pick-badge {
    background: rgba(0,255,135,0.1);
    border: 1px solid rgba(0,255,135,0.3);
    color: var(--green);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    padding: 5px 12px;
    border-radius: 5px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 180px;
  }

  .tier-badge {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    font-weight: 600;
    padding: 4px 9px;
    border-radius: 4px;
    white-space: nowrap;
  }

  .tier-HIGH   { background: rgba(0,255,135,0.15); color: var(--green);  border: 1px solid rgba(0,255,135,0.3); }
  .tier-MEDIUM { background: rgba(255,184,0,0.12); color: var(--amber);  border: 1px solid rgba(255,184,0,0.3); }
  .tier-LOW    { background: rgba(255,82,82,0.1);  color: var(--coral);  border: 1px solid rgba(255,82,82,0.25); }

  .conf-pct {
    margin-left: auto;
    font-family: 'Orbitron', monospace;
    font-size: 20px;
    font-weight: 900;
    color: var(--text);
    line-height: 1;
  }

  .conf-pct small {
    font-size: 11px;
    color: var(--muted);
    font-weight: 400;
    font-family: 'DM Sans', sans-serif;
  }

  /* ── 5 outcome bars ── */
  .outcomes {
    padding: 0 20px 20px;
    display: flex;
    flex-direction: column;
    gap: 9px;
  }

  .outcome-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .outcome-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 9px;
    color: var(--muted);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    width: 72px;
    flex-shrink: 0;
  }

  .bar-track {
    flex: 1;
    height: 7px;
    background: rgba(255,255,255,0.05);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
  }

  .bar-fill {
    height: 100%;
    border-radius: 4px;
    width: 0%;
    transition: width 0.9s cubic-bezier(0.16,1,0.3,1);
  }

  .bar-fill.home  { background: linear-gradient(90deg, #00CC6A, #00FF87); }
  .bar-fill.draw  { background: linear-gradient(90deg, #CC9200, #FFB800); }
  .bar-fill.away  { background: linear-gradient(90deg, #CC3030, #FF5252); }
  .bar-fill.over  { background: linear-gradient(90deg, #0070CC, #0094FF); }
  .bar-fill.btts  { background: linear-gradient(90deg, #9B60E0, #C084FC); }

  .bar-pct {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    color: var(--text);
    width: 36px;
    text-align: right;
    flex-shrink: 0;
  }

  /* ── Card footer ── */
  .card-footer {
    padding: 10px 20px;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 16px;
    align-items: center;
  }

  .footer-stat {
    font-family: 'JetBrains Mono', monospace;
    font-size: 9px;
    color: var(--muted);
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .footer-stat .val { color: var(--text); font-size: 10px; }

  .sources-dot {
    width: 7px; height: 7px; border-radius: 50%;
    flex-shrink: 0;
  }

  /* ── Loading / empty states ── */
  .loading {
    position: relative; z-index: 10;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px 40px;
    gap: 20px;
  }

  .spinner {
    width: 48px; height: 48px;
    border: 3px solid var(--dim);
    border-top-color: var(--green);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .loading-text {
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .empty {
    position: relative; z-index: 10;
    text-align: center;
    padding: 80px 40px;
    color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
  }

  /* ── Error toast ── */
  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: #1A0808; border: 1px solid rgba(255,82,82,0.4);
    color: var(--coral);
    padding: 14px 20px;
    border-radius: 10px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    z-index: 1000;
    transform: translateY(80px);
    opacity: 0;
    transition: all 0.3s ease;
  }
  .toast.show { transform: translateY(0); opacity: 1; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--dim); border-radius: 3px; }

  @media (max-width: 640px) {
    header { padding: 20px; }
    .filters, .sort-bar { padding: 12px 20px; }
    main { padding: 16px 20px; grid-template-columns: 1fr; }
    .search-box { width: 100%; margin-left: 0; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">⚽</div>
    <div>
      <div class="logo-text">FOOTBALL ANALYTICS</div>
      <div class="logo-sub">Statistical Prediction Engine · v2.1</div>
    </div>
  </div>
  <div class="header-stats" id="headerStats">
    <div class="stat-pill">FIXTURES <span id="totalCount">—</span></div>
    <div class="stat-pill">MODEL <span id="modelAccuracy">—</span></div>
    <div class="stat-pill">UPDATED <span id="lastUpdate">—</span></div>
  </div>
</header>

<div class="filters">
  <span class="filter-label">League</span>
  <button class="filter-btn active" data-filter="all">All</button>
  <button class="filter-btn" data-filter="Italy">Serie A</button>
  <button class="filter-btn" data-filter="Spain">La Liga</button>
  <button class="filter-btn" data-filter="Germany">Bundesliga</button>
  <button class="filter-btn" data-filter="France">France</button>
  <button class="filter-btn" data-filter="Brazil">Brazil</button>
  <button class="filter-btn" data-filter="Egypt">Egypt</button>
  <button class="filter-btn" data-filter="USA">MLS</button>
  <input class="search-box" type="text" placeholder="Search teams or league…" id="searchBox">
</div>

<div class="sort-bar">
  <span class="sort-label">Sort by</span>
  <button class="sort-btn active" data-sort="confidence">Confidence ↓</button>
  <button class="sort-btn" data-sort="kickoff">Kickoff</button>
  <button class="sort-btn" data-sort="home">Home %</button>
  <button class="sort-btn" data-sort="over">Over 2.5 %</button>
  <div class="count-badge">Showing <span id="shownCount">0</span> fixtures</div>
</div>

<main id="grid">
  <div class="loading">
    <div class="spinner"></div>
    <div class="loading-text">Loading fixtures…</div>
  </div>
</main>

<div class="toast" id="toast"></div>

<script>
let allFixtures = [];
let currentFilter = 'all';
let currentSort = 'confidence';
let searchQuery = '';

// ── Fetch all predictions ─────────────────────────────────────────────────
async function loadData() {
  try {
    const [fixturesRes, statsRes] = await Promise.all([
      fetch('/api/fixtures'),
      fetch('/api/stats'),
    ]);
    const fixturesData = await fixturesRes.json();
    const statsData    = await statsRes.json();

    allFixtures = fixturesData.fixtures || [];

    document.getElementById('totalCount').textContent = allFixtures.length;
    const acc = statsData.accuracy?.accuracy_pct;
    document.getElementById('modelAccuracy').textContent =
      acc ? acc + '%' : 'Calibrating';
    document.getElementById('lastUpdate').textContent =
      new Date().toLocaleTimeString('en-GB', {hour:'2-digit', minute:'2-digit'});

    renderGrid();
  } catch (err) {
    showToast('Failed to load fixture data. Retrying…');
    setTimeout(loadData, 5000);
  }
}

// ── Filtering & sorting ───────────────────────────────────────────────────
function getFiltered() {
  let data = [...allFixtures];
  if (currentFilter !== 'all') {
    data = data.filter(f => f.country === currentFilter || f.league.includes(currentFilter));
  }
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    data = data.filter(f =>
      f.home.toLowerCase().includes(q) ||
      f.away.toLowerCase().includes(q) ||
      f.league.toLowerCase().includes(q)
    );
  }
  data.sort((a, b) => {
    if (currentSort === 'confidence') return b.confidence - a.confidence;
    if (currentSort === 'kickoff')    return a.kickoff.localeCompare(b.kickoff);
    if (currentSort === 'home')       return b.home_prob - a.home_prob;
    if (currentSort === 'over')       return b.over_25_prob - a.over_25_prob;
    return 0;
  });
  return data;
}

// ── Render ────────────────────────────────────────────────────────────────
function renderGrid() {
  const grid    = document.getElementById('grid');
  const data    = getFiltered();
  document.getElementById('shownCount').textContent = data.length;

  if (!data.length) {
    grid.innerHTML = '<div class="empty">No fixtures match your filter.</div>';
    return;
  }

  grid.innerHTML = data.map(f => cardHTML(f)).join('');

  // Animate bars after paint
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      document.querySelectorAll('.bar-fill').forEach(el => {
        el.style.width = el.dataset.pct + '%';
      });
    });
  });
}

function tierClass(tier) {
  return 'tier-' + (tier || 'LOW');
}

function cardHTML(f) {
  const sourceDot = f.has_two_sources
    ? `<div class="sources-dot" style="background:var(--green)" title="Two sources"></div>`
    : `<div class="sources-dot" style="background:var(--muted)" title="One source"></div>`;

  return `
  <div class="card" data-id="${f.id}">
    <div class="card-header">
      <div class="card-meta">
        <div class="league-tag">${f.league}</div>
        <div class="country-tag">${f.country}</div>
      </div>
      <div class="kickoff-tag">${f.kickoff}</div>
    </div>

    <div class="teams">
      <div class="team home">
        <div class="team-name">${f.home}</div>
        <div class="team-xg">xG ${f.home_xg}</div>
      </div>
      <div class="vs-block">
        <div class="vs-text">VS</div>
        <div class="vs-dot"></div>
      </div>
      <div class="team away">
        <div class="team-name">${f.away}</div>
        <div class="team-xg">xG ${f.away_xg}</div>
      </div>
    </div>

    <div class="confidence-row">
      <div class="pick-badge" title="${f.pick}">${f.pick}</div>
      <div class="tier-badge ${tierClass(f.tier)}">${f.tier}</div>
      <div class="conf-pct">${f.confidence}<small>%</small></div>
    </div>

    <div class="outcomes">
      ${outcomeBar('HOME WIN',  f.home_prob,   'home')}
      ${outcomeBar('DRAW',      f.draw_prob,   'draw')}
      ${outcomeBar('AWAY WIN',  f.away_prob,   'away')}
      ${outcomeBar('OVER 2.5',  f.over_25_prob,'over')}
      ${outcomeBar('BTTS',      f.btts_prob,   'btts')}
    </div>

    <div class="card-footer">
      ${sourceDot}
      <div class="footer-stat">MARGIN <span class="val">${f.margin_pct}%</span></div>
      <div class="footer-stat">DATE <span class="val">${f.date}</span></div>
      <div class="footer-stat" style="margin-left:auto">ID <span class="val">#${f.id}</span></div>
    </div>
  </div>`;
}

function outcomeBar(label, pct, cls) {
  const capped = Math.min(Math.max(pct, 0), 100);
  return `
  <div class="outcome-row">
    <div class="outcome-label">${label}</div>
    <div class="bar-track">
      <div class="bar-fill ${cls}" data-pct="${capped}" style="width:0%"></div>
    </div>
    <div class="bar-pct">${pct.toFixed(0)}%</div>
  </div>`;
}

// ── Event listeners ───────────────────────────────────────────────────────
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.filter;
    renderGrid();
  });
});

document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSort = btn.dataset.sort;
    renderGrid();
  });
});

document.getElementById('searchBox').addEventListener('input', e => {
  searchQuery = e.target.value.trim();
  renderGrid();
});

// ── Toast ────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 4000);
}

// ── Init ──────────────────────────────────────────────────────────────────
loadData();
// Auto-refresh every 5 minutes
setInterval(loadData, 300000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Starlette app factory
# ─────────────────────────────────────────────────────────────────────────────

def create_dashboard_app() -> Starlette:
    init_db()
    return Starlette(routes=[
        Route("/",                        dashboard),
        Route("/health",                  health),
        Route("/api/fixtures",            api_fixtures),
        Route("/api/fixture/{fixture_id}", api_fixture),
        Route("/api/stats",               api_stats),
    ])
