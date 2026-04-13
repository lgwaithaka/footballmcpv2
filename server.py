"""
server.py — Football Analytics MCP Server v2.3
═══════════════════════════════════════════════
On HTTP startup:
  1. Fetches live EPL + UEFA CL + EL fixtures from football-data.org
  2. Merges them into the in-memory registry alongside static fixtures
  3. Serves the web dashboard at /  and MCP tools at /mcp

MCP_TRANSPORT=http   → Render / cloud  (dashboard + MCP via uvicorn)
MCP_TRANSPORT=stdio  → Claude Desktop  (MCP tools only via stdin/stdout)
"""

import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount

from analytics_engine import (
    MarketData, GoalMarketData, AnalyticsResult,
    init_db, persist_result, record_actual_outcome,
    accuracy_report, load_weights,
    remove_margin, goal_market_probs,
    run_prediction, select_pick, confidence_tier, confidence_bar,
    recent_form_pts, scoring_averages, h2h_record, form_string,
    variance_history, learning_report,
)
from schedule_data import (
    all_fixtures, fixture_by_id, FIXTURES, ScheduledMatch,
    register_live_fixtures,
)
from dashboard import create_dashboard_app
from live_schedule import fetch_and_register, fetch_team_standings, get_team_standing

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

API_BASE = "https://api.football-data.org/v4"
API_KEY  = os.getenv("FOOTBALL_DATA_API_KEY", "")

try:
    PORT = int(os.getenv("PORT", "8000"))
except (ValueError, TypeError):
    PORT = 8000

LEAGUE_IDS = {
    "PL":  2021, "PD":  2014, "SA":  2019, "BL1": 2002,
    "FL1": 2015, "CL":  2001, "EL":  2146, "PPL": 2017, "EC": 2016,
}
LEAGUE_NAMES = {
    "PL": "English Premier League", "PD": "La Liga", "SA": "Serie A",
    "BL1": "Bundesliga", "FL1": "Ligue 1", "CL": "UEFA Champions League",
    "EL": "UEFA Europa League", "PPL": "Primeira Liga", "EC": "Championship",
}

init_db()

# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _api(path: str, params: dict = None) -> dict:
    if not API_KEY:
        raise ValueError("FOOTBALL_DATA_API_KEY not configured.")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{API_BASE}{path}",
                             headers={"X-Auth-Token": API_KEY},
                             params=params or {})
        r.raise_for_status()
        return r.json()


def _api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        c = e.response.status_code
        msgs = {401: "API key invalid.", 403: "Plan doesn't cover this competition.",
                429: "Rate limit (10 req/min). Wait 60 s.", 404: "Not found."}
        return f"❌ {msgs.get(c, f'HTTP {c}')}"
    return f"❌ {type(e).__name__}: {e}"


def _fmt_utc(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%a %d %b %Y  %H:%M UTC")


def _lines(f: ScheduledMatch):
    la = MarketData(f.lines_a.home, f.lines_a.draw, f.lines_a.away) if f.lines_a else None
    lb = MarketData(f.lines_b.home, f.lines_b.draw, f.lines_b.away) if f.lines_b else None
    gm_a = GoalMarketData(f.lines_a.over_2_5, f.lines_a.under_2_5,
                          f.lines_a.both_score_yes, f.lines_a.both_score_no) if f.lines_a else None
    gm_b = GoalMarketData(f.lines_b.over_2_5, f.lines_b.under_2_5,
                          f.lines_b.both_score_yes, f.lines_b.both_score_no) if f.lines_b else None
    return la, lb, gm_a, gm_b


# ─────────────────────────────────────────────────────────────────────────────
# MCP server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP("football_analytics_mcp")

# ─────────────────────────────────────────────────────────────────────────────
# Input models
# ─────────────────────────────────────────────────────────────────────────────

class PredictFixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id:     int  = Field(..., description="Provider A or Provider B fixture ID")
    show_analytics: bool = Field(True, description="Include detailed analytics breakdown")


class BulkPredictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country:        Optional[str] = Field(None, description="Filter by country, e.g. 'England', 'Europe'")
    league:         Optional[str] = Field(None, description="Filter by league, e.g. 'Premier League', 'Champions League'")
    min_confidence: float         = Field(0.0, ge=0.0, le=1.0)
    top_n:          int           = Field(15, ge=1, le=50)


class LivePredictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: int = Field(..., description="Match ID from football-data.org")


class LiveFixturesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    league:     str = Field(..., description="PL, PD, SA, BL1, FL1, CL, EL, PPL, EC")
    days_ahead: int = Field(3, ge=1, le=21)


class RecordOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id:    str = Field(..., description="Fixture ID used when the prediction was generated")
    actual_result: str = Field(..., description="H (home win), D (draw), A (away win)")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — List fixtures
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_list_fixtures",
          annotations={"readOnlyHint": True, "destructiveHint": False})
async def analytics_list_fixtures(country: Optional[str] = None,
                                   league:  Optional[str] = None) -> str:
    """
    List all fixtures in the schedule (static + live EPL/UEFA).
    Filter by country or league name.
    Returns fixture IDs needed by analytics_predict_fixture.
    """
    fixtures = all_fixtures()
    if country: fixtures = [f for f in fixtures if country.lower() in f.country.lower()]
    if league:  fixtures = [f for f in fixtures if league.lower() in f.league.lower()]
    if not fixtures:
        return f"No fixtures found — country='{country}' league='{league}'"

    lines = [
        f"## 📋 Fixture Schedule — {len(fixtures)} matches\n",
        f"{'Prov-A':>8}  {'Prov-B':>7}  {'Date':<12}  {'Time':>6}  {'Match':<44}  League",
        "─" * 110,
    ]
    for f in sorted(fixtures, key=lambda x: (x.date, x.kickoff)):
        aid = str(f.provider_a_id or "—")
        bid = str(f.provider_b_id or "—")
        match = f"{f.home_team} vs {f.away_team}"[:43]
        lines.append(f"{aid:>8}  {bid:>7}  {f.date:<12}  {f.kickoff:>6}  {match:<44}  {f.league}")
    lines.append("\n💡 Use `analytics_bulk_predictions` to score all fixtures at once.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — Predict single fixture (5 outcomes)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_predict_fixture",
          annotations={"readOnlyHint": False, "destructiveHint": False})
async def analytics_predict_fixture(params: PredictFixtureInput) -> str:
    """
    Full 5-outcome prediction for one fixture (Home Win, Draw, Away Win,
    Over 2.5 Goals, Both Teams to Score). Works for all leagues including
    EPL, UEFA Champions League, and UEFA Europa League.
    """
    try:
        f = fixture_by_id(params.fixture_id)
        if not f:
            return (f"❌ Fixture ID {params.fixture_id} not found.\n"
                    "Run `analytics_list_fixtures` to see all available IDs.")

        la, lb, gm_a, gm_b = _lines(f)

        # Attempt live enrichment for EPL / UEFA via API
        h_form = a_form = 7.5
        h_scored = h_conceded = a_scored = a_conceded = 1.2
        h2h = {"home": 0, "draw": 0, "away": 0, "total": 0}
        h_form_str = a_form_str = "N/A"
        live_enriched = False

        if API_KEY and f.provider_a_id and f.provider_a_id >= 60000:
            # This is a live-API fixture — fetch form + H2H
            try:
                md = await _api(f"/matches/{f.provider_a_id}")
                m  = md.get("match", md)
                home_id = m["homeTeam"]["id"]
                away_id = m["awayTeam"]["id"]
                hm_data, am_data, h2h_data = await asyncio.gather(
                    _api(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10}),
                    _api(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10}),
                    _api(f"/matches/{f.provider_a_id}/head2head", {"limit": 10}),
                )
                hm   = hm_data.get("matches", [])
                am   = am_data.get("matches", [])
                h2hm = h2h_data.get("matches", [])
                h_form  = recent_form_pts(hm, home_id)
                a_form  = recent_form_pts(am, away_id)
                h_scored, h_conceded = scoring_averages(hm, home_id)
                a_scored, a_conceded = scoring_averages(am, away_id)
                h2h = h2h_record(h2hm, home_id, away_id)
                h_form_str = form_string(hm, home_id)
                a_form_str = form_string(am, away_id)
                live_enriched = True
            except Exception:
                pass

        weights = load_weights()
        r = run_prediction(la, lb, h_form, a_form, h_scored, h_conceded,
                           a_scored, a_conceded, h2h, weights=weights)
        hp, dp, ap = r["hp"], r["dp"], r["ap"]
        over_25, btts = goal_market_probs(gm_a, gm_b, r["home_xg"], r["away_xg"])
        pick_label, pick_code, conf = select_pick(hp, dp, ap)

        res = AnalyticsResult(
            fixture_id=str(params.fixture_id),
            home_team=f.home_team, away_team=f.away_team,
            league=f.league, country=f.country,
            kickoff=f.kickoff, date=f.date,
            home_prob=hp, draw_prob=dp, away_prob=ap,
            over_25_prob=over_25, btts_prob=btts,
            recommended_pick=pick_label, pick_code=pick_code,
            confidence=conf, confidence_pct=int(conf * 100),
            confidence_label=confidence_tier(conf),
            provider_margin_pct=r["margin_pct"],
            consensus_gap_pct=r["consensus_gap_pct"],
            home_form_pts=h_form, away_form_pts=a_form,
            home_xg=r["home_xg"], away_xg=r["away_xg"],
            h2h_home=h2h["home"], h2h_draw=h2h["draw"], h2h_away=h2h["away"],
        )
        persist_result(res)

        sources = []
        if f.lines_a: sources.append(f"Provider A: {f.lines_a.home}/{f.lines_a.draw}/{f.lines_a.away}")
        if f.lines_b: sources.append(f"Provider B: {f.lines_b.home}/{f.lines_b.draw}/{f.lines_b.away}")
        if not sources: sources = ["No market lines — using statistical model only"]

        report = f"""## 🔮 {f.home_team} vs {f.away_team}
🏆 {f.league} · {f.country}  |  📅 {f.date} {f.kickoff} UTC
{"📡 Live form data enriched ✅" if live_enriched else "📊 Market-data model"}

### Market Lines
{chr(10).join(sources)}

---

### 🎯 Recommended Pick
> **{pick_label}**
> Confidence: {confidence_bar(conf)} {confidence_tier(conf)}

---

### 📊 5-Outcome Probabilities
| # | Outcome             | Probability | Bar |
|---|---------------------|-------------|-----|
| 1 | 🏠 Home Win         | **{hp*100:.1f}%** | {'█'*int(hp*20)}{'░'*(20-int(hp*20))} |
| 2 | 🤝 Draw             | **{dp*100:.1f}%** | {'█'*int(dp*20)}{'░'*(20-int(dp*20))} |
| 3 | ✈️ Away Win          | **{ap*100:.1f}%** | {'█'*int(ap*20)}{'░'*(20-int(ap*20))} |
| 4 | ⚽ Over 2.5 Goals   | **{over_25*100:.1f}%** | {'█'*int(over_25*20)}{'░'*(20-int(over_25*20))} |
| 5 | 🎯 Both Teams Score | **{btts*100:.1f}%** | {'█'*int(btts*20)}{'░'*(20-int(btts*20))} |

xG: {f.home_team.split()[0]} **{r['home_xg']}** — {f.away_team.split()[0]} **{r['away_xg']}**"""

        if params.show_analytics and (h_form_str != "N/A" or h2h["total"] > 0):
            report += f"""

### 📋 Team Stats
| | {f.home_team[:22]} | {f.away_team[:22]} |
|--|--|--|
| Form (last 5) | {h_form_str} | {a_form_str} |
| Form Pts /15  | {h_form} | {a_form} |

H2H: {f.home_team.split()[0]} **{h2h['home']}** wins · Draws **{h2h['draw']}** · {f.away_team.split()[0]} **{h2h['away']}** wins"""

        report += f"\n\n🆔 ID: `{params.fixture_id}` — Use `analytics_record_outcome` after the match."
        return report

    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — Bulk predictions
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_bulk_predictions",
          annotations={"readOnlyHint": False, "destructiveHint": False})
async def analytics_bulk_predictions(params: BulkPredictInput) -> str:
    """
    Ranked 5-outcome predictions for all fixtures including EPL and UEFA.
    Filter by country='England' for EPL, or country='Europe' for UEFA.
    """
    try:
        fixtures = all_fixtures()
        if params.country: fixtures = [f for f in fixtures if params.country.lower() in f.country.lower()]
        if params.league:  fixtures = [f for f in fixtures if params.league.lower() in f.league.lower()]

        weights = load_weights()
        scored  = []

        for f in fixtures:
            la, lb, gm_a, gm_b = _lines(f)
            if not la and not lb:
                # No market lines — use flat priors (statistical model only)
                pass  # run_prediction handles None lines gracefully
            r = run_prediction(la, lb, weights=weights)
            hp, dp, ap = r["hp"], r["dp"], r["ap"]
            over_25, btts = goal_market_probs(gm_a, gm_b, r["home_xg"], r["away_xg"])
            pick_label, pick_code, conf = select_pick(hp, dp, ap)
            if conf < params.min_confidence:
                continue

            fid = str(f.provider_b_id or f.provider_a_id)
            res = AnalyticsResult(
                fixture_id=fid, home_team=f.home_team, away_team=f.away_team,
                league=f.league, country=f.country, kickoff=f.kickoff, date=f.date,
                home_prob=hp, draw_prob=dp, away_prob=ap,
                over_25_prob=over_25, btts_prob=btts,
                recommended_pick=pick_label, pick_code=pick_code,
                confidence=conf, confidence_pct=int(conf * 100),
                confidence_label=confidence_tier(conf),
                provider_margin_pct=r["margin_pct"],
                consensus_gap_pct=r["consensus_gap_pct"],
                home_form_pts=7.5, away_form_pts=7.5,
                home_xg=r["home_xg"], away_xg=r["away_xg"],
                h2h_home=0, h2h_draw=0, h2h_away=0,
            )
            persist_result(res)
            scored.append({"f": f, "res": res, "r": r,
                           "pick_label": pick_label, "conf": conf,
                           "over_25": over_25, "btts": btts, "fid": fid})

        scored.sort(key=lambda x: x["conf"], reverse=True)
        scored = scored[:params.top_n]

        if not scored:
            return "No fixtures meet the specified criteria."

        lines = [f"## 📊 Bulk Predictions — Top {len(scored)} Fixtures\n"]
        for i, row in enumerate(scored, 1):
            f, res, r = row["f"], row["res"], row["r"]
            cbar = confidence_bar(row["conf"], 15)
            lines.append(
                f"### {i}. {f.home_team} vs {f.away_team}\n"
                f"🏆 {f.league} · {f.country} · {f.date} {f.kickoff}\n"
                f"🎯 **{row['pick_label']}** — {cbar} {confidence_tier(row['conf'])}\n"
                f"H:{res.home_prob*100:.0f}% D:{res.draw_prob*100:.0f}% A:{res.away_prob*100:.0f}% "
                f"| Ov2.5:{row['over_25']*100:.0f}% BTTS:{row['btts']*100:.0f}%\n"
                f"🆔 `{row['fid']}`\n"
            )
        lines.append("⚠️ Statistical analysis only. Record outcomes to improve the model.")
        return "\n".join(lines)
    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — Live prediction
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_live_prediction",
          annotations={"readOnlyHint": False, "destructiveHint": False})
async def analytics_live_prediction(params: LivePredictInput) -> str:
    """Deep 5-outcome prediction using live form, H2H, and standings data."""
    try:
        md = await _api(f"/matches/{params.match_id}")
        m  = md.get("match", md)
        home_id = m["homeTeam"]["id"]; away_id = m["awayTeam"]["id"]
        hname   = m["homeTeam"]["name"]; aname = m["awayTeam"]["name"]
        league  = m.get("competition", {}).get("name", "Unknown")
        comp_id = m.get("competition", {}).get("id")

        tasks = [
            _api(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10}),
            _api(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10}),
            _api(f"/matches/{params.match_id}/head2head", {"limit": 10}),
        ]
        if comp_id: tasks.append(_api(f"/competitions/{comp_id}/standings"))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        hm   = results[0].get("matches", []) if not isinstance(results[0], Exception) else []
        am   = results[1].get("matches", []) if not isinstance(results[1], Exception) else []
        h2hm = results[2].get("matches", []) if not isinstance(results[2], Exception) else []
        std  = results[3] if len(results) > 3 and not isinstance(results[3], Exception) else {}

        h_form  = recent_form_pts(hm, home_id); a_form = recent_form_pts(am, away_id)
        h_sc, h_cn = scoring_averages(hm, home_id); a_sc, a_cn = scoring_averages(am, away_id)
        h2h = h2h_record(h2hm, home_id, away_id)
        h_fe = form_string(hm, home_id); a_fe = form_string(am, away_id)
        hp2 = ap2 = None
        for s in std.get("standings", [{}])[:1]:
            for row in s.get("table", []):
                if row["team"]["id"] == home_id: hp2 = row["position"]
                if row["team"]["id"] == away_id: ap2 = row["position"]

        la = lb = gm_a = gm_b = None
        for fix in all_fixtures():
            if fix.home_team.lower()[:6] in hname.lower():
                la, lb, gm_a, gm_b = _lines(fix); break

        weights = load_weights()
        r = run_prediction(la, lb, h_form, a_form, h_sc, h_cn, a_sc, a_cn, h2h, hp2, ap2, weights)
        hp, dp, ap = r["hp"], r["dp"], r["ap"]
        over_25, btts = goal_market_probs(gm_a, gm_b, r["home_xg"], r["away_xg"])
        pick_label, _, conf = select_pick(hp, dp, ap)

        return f"""## 🔮 Live: {hname} vs {aname}
🏆 {league}  |  📅 {_fmt_utc(m['utcDate'])}

### 🎯 Pick: **{pick_label}**  |  Confidence: {confidence_bar(conf)} {confidence_tier(conf)}

### 📊 5-Outcome Probabilities
| Outcome             | Probability |
|---------------------|-------------|
| 🏠 Home Win         | **{hp*100:.1f}%** |
| 🤝 Draw             | **{dp*100:.1f}%** |
| ✈️ Away Win          | **{ap*100:.1f}%** |
| ⚽ Over 2.5 Goals   | **{over_25*100:.1f}%** |
| 🎯 Both Teams Score | **{btts*100:.1f}%** |

| | {hname[:20]} | {aname[:20]} |
|--|--|--|
| Form (5) | {h_fe} | {a_fe} |
| Pts /15  | {h_form} | {a_form} |
| xG       | {r['home_xg']} | {r['away_xg']} |
| Position | {"#"+str(hp2) if hp2 else "N/A"} | {"#"+str(ap2) if ap2 else "N/A"} |

H2H: {hname.split()[0]} {h2h['home']} · Draw {h2h['draw']} · {aname.split()[0]} {h2h['away']}"""
    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — Live fixtures
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_live_fixtures",
          annotations={"readOnlyHint": True, "destructiveHint": False})
async def analytics_live_fixtures(params: LiveFixturesInput) -> str:
    """Fetch upcoming fixtures from football-data.org (PL, CL, EL, SA, etc.)."""
    try:
        lid = LEAGUE_IDS.get(params.league.upper())
        if not lid: return f"❌ Unknown league code. Valid: {', '.join(LEAGUE_IDS)}"
        date_from = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_to   = (datetime.now(timezone.utc) + timedelta(days=params.days_ahead)).strftime("%Y-%m-%d")
        data    = await _api(f"/competitions/{lid}/matches",
                             {"dateFrom": date_from, "dateTo": date_to, "status": "SCHEDULED"})
        matches = data.get("matches", [])
        if not matches:
            return f"No upcoming fixtures for {LEAGUE_NAMES.get(params.league.upper(), params.league)}."
        lines = [f"## ⚽ {LEAGUE_NAMES.get(params.league.upper())} — {len(matches)} fixtures\n",
                 f"{'ID':<10}  {'Date/Time (UTC)':<22}  {'Home':<26}  Away", "─" * 80]
        for m in matches:
            dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).strftime("%d %b %H:%M")
            lines.append(f"{m['id']:<10}  {dt:<22}  {m['homeTeam']['name'][:25]:<26}  {m['awayTeam']['name'][:25]}")
        return "\n".join(lines)
    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 — Record outcome
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_record_outcome",
          annotations={"readOnlyHint": False, "destructiveHint": False})
async def analytics_record_outcome(params: RecordOutcomeInput) -> str:
    """Record actual match result to train the self-learning model."""
    if params.actual_result.upper() not in {"H", "D", "A"}:
        return f"❌ actual_result must be 'H', 'D', or 'A'. Received: '{params.actual_result}'"
    ok = record_actual_outcome(params.fixture_id, params.actual_result.upper())
    if not ok:
        return f"❌ No prediction found for fixture '{params.fixture_id}'."
    stats = accuracy_report()
    return f"""✅ Outcome recorded — `{params.fixture_id}`: **{params.actual_result.upper()}**

**Overall Accuracy: {stats['accuracy_pct']}%** ({stats['correct']}/{stats['graded']} graded)
{"🔥 Strong — weights stable." if stats['accuracy_pct'] >= 65 else "⚙️ Auto-recalibrating weights…"}"""


# ─────────────────────────────────────────────────────────────────────────────
# Tool 7 — Model report
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(name="analytics_model_report",
          annotations={"readOnlyHint": True, "destructiveHint": False})
async def analytics_model_report() -> str:
    """View accuracy statistics, model weights, and self-learning history."""
    stats = accuracy_report(); weights = load_weights(); lr = learning_report()
    lines = [
        "## 📊 Model Performance Report\n",
        f"- Total logged : **{stats['total_logged']}**",
        f"- Graded       : **{stats['graded']}**",
        f"- Correct      : **{stats['correct']}**",
        f"- **Accuracy   : {stats['accuracy_pct']}%**",
        f"- **Brier Score: {stats['avg_brier']}**\n",
        "### Signal Weights", "| Signal | Weight |", "|--------|--------|",
    ]
    labels = {"w_market": "Market Implied Prob", "w_form": "Recent Form",
              "w_h2h": "Head-to-Head", "w_home_field": "Home Field", "w_position": "League Position"}
    for k, v in weights.items():
        lines.append(f"| {labels.get(k, k)} | {v:.2f} ({v*100:.0f}%) |")
    lines.append(f"\n*{len(lr['history'])} learning events logged. Learning rate: {lr['learning_rate']}*")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "http":
        import uvicorn

        # ── Fetch live EPL + UEFA fixtures before serving ──────
        print("[startup] Fetching live EPL + UEFA fixtures…")
        summary = fetch_and_register()
        print(f"[startup] Live fixtures: {summary['registered']} added "
              f"({', '.join(summary['leagues']) or 'none'})")
        print(f"[startup] Total fixtures in schedule: {len(all_fixtures())}")

        print("[startup] Fetching team standings for prediction enrichment…")
        n_teams = fetch_team_standings()
        print(f"[startup] Team standings: {n_teams} entries loaded")

        # ── Build combined Starlette app ───────────────────────
        dashboard_app = create_dashboard_app()
        mcp_app       = mcp.streamable_http_app()

        combined = Starlette(routes=[
            Mount("/mcp", app=mcp_app),
            Mount("/",    app=dashboard_app),
        ])

        print(f"🚀 Football Analytics — HTTP on port {PORT}")
        print(f"   Dashboard : http://0.0.0.0:{PORT}/")
        print(f"   MCP       : http://0.0.0.0:{PORT}/mcp")
        uvicorn.run(combined, host="0.0.0.0", port=PORT)
    else:
        print("🚀 Football Analytics MCP — stdio transport")
        # Also register live fixtures for stdio mode if API key is available
        if API_KEY:
            summary = fetch_and_register()
            if summary["registered"]:
                print(f"[startup] {summary['registered']} live fixtures added")
            fetch_team_standings()
        mcp.run()
