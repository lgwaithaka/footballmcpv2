"""
server.py — Football Match Analytics MCP Server v2.0
══════════════════════════════════════════════════════
Statistical analysis engine for football fixture outcomes.

Transport : HTTP (Render cloud) or stdio (Claude Desktop)
Data      : football-data.org API + pre-loaded fixture schedule
Learning  : SQLite — records predictions and actual outcomes,
            auto-recalibrates model weights over time.

Tools
─────
  analytics_list_fixtures      — Show today's loaded fixture schedule
  analytics_predict_fixture    — Full prediction for one fixture by ID
  analytics_bulk_predictions   — Ranked predictions for all fixtures
  analytics_live_prediction    — Deep prediction using live API data
  analytics_live_fixtures      — Fetch upcoming fixtures from live API
  analytics_record_outcome     — Record actual result (triggers learning)
  analytics_model_report       — Accuracy stats and current model weights
"""

import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from pydantic import BaseModel, Field, ConfigDict
from mcp.server.fastmcp import FastMCP

from analytics_engine import (
    MarketData, AnalyticsResult,
    init_db, persist_result, record_actual_outcome,
    accuracy_report, load_weights,
    remove_margin, provider_margin, consensus_lines,
    recent_form_pts, scoring_averages, h2h_record,
    form_string, run_prediction, select_pick,
    confidence_tier, confidence_bar,
)
from schedule_data import (
    all_fixtures, fixture_by_id, fixtures_by_country,
    fixtures_by_league, FIXTURES, ScheduledMatch,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

API_BASE  = "https://api.football-data.org/v4"
API_KEY   = os.getenv("FOOTBALL_DATA_API_KEY", "")
try:
    PORT = int(os.getenv("PORT", "8000"))
except (ValueError, TypeError):
    PORT = 8000

LEAGUE_IDS = {
    "PL":  2021,  # English Premier League
    "PD":  2014,  # La Liga
    "SA":  2019,  # Serie A
    "BL1": 2002,  # Bundesliga
    "FL1": 2015,  # Ligue 1
    "CL":  2001,  # UEFA Champions League
    "EL":  2146,  # UEFA Europa League
    "PPL": 2017,  # Primeira Liga
    "EC":  2016,  # Championship
}

LEAGUE_NAMES = {
    "PL":  "English Premier League",
    "PD":  "La Liga",
    "SA":  "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL":  "UEFA Champions League",
    "EL":  "UEFA Europa League",
    "PPL": "Primeira Liga",
    "EC":  "Championship",
}

init_db()

# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _api(path: str, params: dict = None) -> dict:
    if not API_KEY:
        raise ValueError(
            "FOOTBALL_DATA_API_KEY is not configured. "
            "Register free at football-data.org/client/register"
        )
    headers = {"X-Auth-Token": API_KEY}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{API_BASE}{path}", headers=headers, params=params or {})
        r.raise_for_status()
        return r.json()


def _api_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        msgs = {
            401: "API key invalid. Check FOOTBALL_DATA_API_KEY.",
            403: "Your API plan does not cover this competition.",
            429: "Rate limit reached (10 req/min on free tier). Wait 60 s.",
            404: "Resource not found. Verify the ID.",
        }
        return f"❌ {msgs.get(code, f'HTTP {code}: {e.response.text[:120]}')}"
    if isinstance(e, ValueError):
        return f"❌ {e}"
    return f"❌ {type(e).__name__}: {e}"


def _fmt_utc(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.strftime("%a %d %b %Y  %H:%M UTC")


def _lines_from_fixture(f: ScheduledMatch) -> tuple[Optional[MarketData], Optional[MarketData]]:
    la = MarketData(f.lines_a.home, f.lines_a.draw, f.lines_a.away) if f.lines_a else None
    lb = MarketData(f.lines_b.home, f.lines_b.draw, f.lines_b.away) if f.lines_b else None
    return la, lb


# ─────────────────────────────────────────────────────────────────────────────
# MCP server
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP("football_analytics_mcp")

# ─────────────────────────────────────────────────────────────────────────────
# Input models
# ─────────────────────────────────────────────────────────────────────────────

class PredictFixtureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: int = Field(..., description="Provider A or Provider B fixture ID from the loaded schedule")
    show_analytics: bool = Field(True, description="Include detailed analytics breakdown in output")


class BulkPredictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: Optional[str] = Field(None, description="Filter by country (e.g. 'Italy', 'Brazil')")
    league: Optional[str]  = Field(None, description="Filter by league (e.g. 'Serie A', 'Bundesliga')")
    min_confidence: float  = Field(0.0, ge=0.0, le=1.0, description="Only return picks above this confidence (0.0 – 1.0)")
    top_n: int             = Field(15, ge=1, le=50, description="Maximum number of results to return")


class LivePredictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match_id: int = Field(..., description="Match ID from football-data.org (use analytics_live_fixtures to find IDs)")


class LiveFixturesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    league: str  = Field(..., description="League code: PL, PD, SA, BL1, FL1, CL, EL, PPL, EC")
    days_ahead: int = Field(3, ge=1, le=14, description="Days ahead to look (1 – 14)")


class RecordOutcomeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_id: str = Field(..., description="Fixture ID string used when the prediction was generated")
    actual_result: str = Field(..., description="Actual outcome: 'H' (home win), 'D' (draw), 'A' (away win)")


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1 — List fixture schedule
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_list_fixtures",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def analytics_list_fixtures(
    country: Optional[str] = None,
    league:  Optional[str] = None,
) -> str:
    """
    List all fixtures in the pre-loaded schedule (April 11-12, 2026).
    Optionally filter by country or league name.
    Returns fixture IDs needed by analytics_predict_fixture.

    Args:
        country: Optional country filter, e.g. 'Italy', 'Brazil', 'Egypt'
        league:  Optional league filter, e.g. 'Serie A', 'Bundesliga', 'MLS'

    Returns:
        Table of fixtures with Provider A / Provider B IDs, kickoff time,
        teams, league, and country.
    """
    fixtures = all_fixtures()
    if country:
        fixtures = [f for f in fixtures if country.lower() in f.country.lower()]
    if league:
        fixtures = [f for f in fixtures if league.lower() in f.league.lower()]

    if not fixtures:
        return f"No fixtures found for filter — country='{country}' league='{league}'"

    lines = [
        f"## 📋 Fixture Schedule — {len(fixtures)} matches",
        "*(Use Provider A ID or Provider B ID with `analytics_predict_fixture`)*\n",
        f"{'Prov-A':>7}  {'Prov-B':>7}  {'Time':>6}  {'Match':<44}  {'League':<22}  Country",
        "─" * 108,
    ]
    for f in sorted(fixtures, key=lambda x: x.kickoff):
        aid = str(f.provider_a_id or "—")
        bid = str(f.provider_b_id or "—")
        match = f"{f.home_team} vs {f.away_team}"[:43]
        lines.append(
            f"{aid:>7}  {bid:>7}  {f.kickoff:>6}  {match:<44}  {f.league:<22}  {f.country}"
        )

    lines.append("\n💡 Use `analytics_bulk_predictions` to score all fixtures at once.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2 — Predict single fixture from schedule
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_predict_fixture",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def analytics_predict_fixture(params: PredictFixtureInput) -> str:
    """
    Generate a full statistical prediction for a single fixture from the loaded schedule.
    Combines Provider A and Provider B market lines with live form and H2H data when available.

    Args:
        params.fixture_id:    Provider A or Provider B ID from analytics_list_fixtures
        params.show_analytics: Include full analytics table in output

    Returns:
        Detailed prediction report — probabilities, recommended pick,
        confidence level, market diagnostics, and team statistics.
    """
    try:
        f = fixture_by_id(params.fixture_id)
        if not f:
            return (
                f"❌ Fixture ID {params.fixture_id} not found in the loaded schedule.\n"
                "Run `analytics_list_fixtures` to see all available fixture IDs."
            )

        la, lb = _lines_from_fixture(f)

        # Defaults
        h_form = a_form = 7.5
        h_scored = h_conceded = a_scored = a_conceded = 1.2
        h2h = {"home": 0, "draw": 0, "away": 0, "total": 0}
        h_form_str = a_form_str = "N/A"
        live_enriched = False

        # Attempt live enrichment via football-data.org
        if API_KEY:
            try:
                for league_code, league_id in LEAGUE_IDS.items():
                    if league_code == "SA" and "serie a" in f.league.lower():
                        pass
                    elif league_code == "PL" and "premier" in f.league.lower() and f.country == "England":
                        pass
                    else:
                        continue
                    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    matches_data = await _api(
                        f"/competitions/{league_id}/matches",
                        {"dateFrom": date_str, "dateTo": date_str, "status": "SCHEDULED"},
                    )
                    for m in matches_data.get("matches", []):
                        if f.home_team.lower()[:6] in m["homeTeam"]["name"].lower():
                            home_id = m["homeTeam"]["id"]
                            away_id = m["awayTeam"]["id"]
                            hd, ad, h2hd = await asyncio.gather(
                                _api(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10}),
                                _api(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10}),
                                _api(f"/matches/{m['id']}/head2head", {"limit": 10}),
                            )
                            hm = hd.get("matches", [])
                            am = ad.get("matches", [])
                            h2hm = h2hd.get("matches", [])
                            h_form   = recent_form_pts(hm, home_id)
                            a_form   = recent_form_pts(am, away_id)
                            h_scored, h_conceded = scoring_averages(hm, home_id)
                            a_scored, a_conceded = scoring_averages(am, away_id)
                            h2h      = h2h_record(h2hm, home_id, away_id)
                            h_form_str = form_string(hm, home_id)
                            a_form_str = form_string(am, away_id)
                            live_enriched = True
                            break
                    if live_enriched:
                        break
            except Exception:
                pass  # Silently fall back to lines-only

        weights = load_weights()
        r = run_prediction(
            la, lb,
            h_form, a_form,
            h_scored, h_conceded,
            a_scored, a_conceded,
            h2h, weights=weights,
        )
        hp, dp, ap = r["hp"], r["dp"], r["ap"]
        pick_label, pick_code, conf = select_pick(hp, dp, ap)

        # Persist prediction
        best_lines = la or lb
        imp = remove_margin(best_lines) if best_lines else (hp, dp, ap)
        result = AnalyticsResult(
            fixture_id=str(params.fixture_id),
            home_team=f.home_team, away_team=f.away_team,
            league=f.league, kickoff=f"{f.date} {f.kickoff}",
            home_prob=hp, draw_prob=dp, away_prob=ap,
            recommended_pick=pick_label, pick_code=pick_code,
            confidence=conf, confidence_pct=int(conf * 100),
            confidence_label=confidence_tier(conf),
            implied_home=round(imp[0], 4),
            implied_draw=round(imp[1], 4),
            implied_away=round(imp[2], 4),
            provider_margin_pct=r["margin_pct"],
            consensus_gap_pct=r["consensus_gap_pct"],
            home_form_pts=h_form, away_form_pts=a_form,
            home_xg=r["home_xg"], away_xg=r["away_xg"],
            h2h_home=h2h["home"], h2h_draw=h2h["draw"], h2h_away=h2h["away"],
        )
        persist_result(result)

        # ── Format report ─────────────────────────────────────────────
        source_lines = []
        if la:
            source_lines.append(f"Provider A: {la.home} / {la.draw} / {la.away}  (H/D/A)")
        if lb:
            source_lines.append(f"Provider B: {lb.home} / {lb.draw} / {lb.away}  (H/D/A)")

        cbar = confidence_bar(conf)

        report = f"""## 🔮 Match Analysis: {f.home_team} vs {f.away_team}

🏆 {f.league} · {f.country}
📅 {f.date} at {f.kickoff} UTC
{"📡 Live form data included ✅" if live_enriched else "📊 Market-data model (set FOOTBALL_DATA_API_KEY for live form)"}

### 📊 Market Lines
{chr(10).join(source_lines)}
📉 Provider margin: **{r['margin_pct']}%**
{"🤝 Providers agree closely ✅" if r['consensus_gap_pct'] < 2.0 else f"⚠️ Providers diverge by {r['consensus_gap_pct']}% — added uncertainty"}

---

### 🎯 Recommended Pick
> **{pick_label}**
> Confidence: {cbar} {confidence_tier(conf)}

---

### 📈 Outcome Probabilities
| Outcome       | Probability | Distribution |
|---------------|-------------|--------------|
| 🏠 Home Win   | **{hp*100:.1f}%** | {'█'*int(hp*24)}{'░'*(24-int(hp*24))} |
| 🤝 Draw       | **{dp*100:.1f}%** | {'█'*int(dp*24)}{'░'*(24-int(dp*24))} |
| ✈️ Away Win   | **{ap*100:.1f}%** | {'█'*int(ap*24)}{'░'*(24-int(ap*24))} |"""

        if params.show_analytics:
            report += f"""

---

### 📋 Team Statistics
| Metric              | {f.home_team[:22]:<22} | {f.away_team[:22]:<22} |
|---------------------|----------------------|----------------------|
| Recent Form (last 5)| {h_form_str:<20} | {a_form_str:<20} |
| Form Points (/15)   | {h_form:<20} | {a_form:<20} |
| Avg Goals Scored    | {h_scored:<20} | {a_scored:<20} |
| Avg Goals Conceded  | {h_conceded:<20} | {a_conceded:<20} |
| xG Estimate         | {r['home_xg']:<20} | {r['away_xg']:<20} |

### ⚔️ Head-to-Head (last {h2h['total']} meetings)
🏠 {f.home_team.split()[0]}: **{h2h['home']}** wins  ·  🤝 Draws: **{h2h['draw']}**  ·  ✈️ {f.away_team.split()[0]}: **{h2h['away']}** wins"""

        report += f"""

---
🆔 Fixture ID: `{params.fixture_id}` — Use `analytics_record_outcome` after the match to improve model accuracy.
⚠️ Statistical analysis only. Always assess independently before acting on any prediction."""
        return report

    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3 — Bulk predictions from schedule
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_bulk_predictions",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def analytics_bulk_predictions(params: BulkPredictInput) -> str:
    """
    Generate and rank predictions for all fixtures in the loaded schedule.
    Optionally filter by country or league, and set a minimum confidence threshold.

    Args:
        params.country:        Filter by country name
        params.league:         Filter by league name
        params.min_confidence: Skip picks below this confidence (0.0 – 1.0)
        params.top_n:          Return at most this many results

    Returns:
        Ranked list of predictions sorted by confidence (highest first),
        with pick recommendation, probabilities, and xG estimates.
    """
    try:
        fixtures = all_fixtures()
        if params.country:
            fixtures = [f for f in fixtures if params.country.lower() in f.country.lower()]
        if params.league:
            fixtures = [f for f in fixtures if params.league.lower() in f.league.lower()]

        weights = load_weights()
        scored = []

        for f in fixtures:
            la, lb = _lines_from_fixture(f)
            if not la and not lb:
                continue

            r = run_prediction(la, lb, weights=weights)
            hp, dp, ap = r["hp"], r["dp"], r["ap"]
            pick_label, pick_code, conf = select_pick(hp, dp, ap)

            if conf < params.min_confidence:
                continue

            fid = str(f.provider_b_id or f.provider_a_id)

            res = AnalyticsResult(
                fixture_id=fid,
                home_team=f.home_team, away_team=f.away_team,
                league=f.league, kickoff=f"{f.date} {f.kickoff}",
                home_prob=hp, draw_prob=dp, away_prob=ap,
                recommended_pick=pick_label, pick_code=pick_code,
                confidence=conf, confidence_pct=int(conf * 100),
                confidence_label=confidence_tier(conf),
                implied_home=remove_margin(la or lb)[0],
                implied_draw=remove_margin(la or lb)[1],
                implied_away=remove_margin(la or lb)[2],
                provider_margin_pct=r["margin_pct"],
                consensus_gap_pct=r["consensus_gap_pct"],
                home_form_pts=7.5, away_form_pts=7.5,
                home_xg=r["home_xg"], away_xg=r["away_xg"],
                h2h_home=0, h2h_draw=0, h2h_away=0,
            )
            persist_result(res)
            scored.append({"f": f, "r": r, "res": res,
                           "pick_label": pick_label, "pick_code": pick_code,
                           "conf": conf, "fid": fid})

        scored.sort(key=lambda x: x["conf"], reverse=True)
        scored = scored[: params.top_n]

        if not scored:
            return "No fixtures meet the specified criteria."

        lines = [
            f"## 📊 Bulk Analytics — Top {len(scored)} Fixtures",
            f"Sorted by confidence  |  Min confidence: {int(params.min_confidence*100)}%\n",
        ]
        for i, row in enumerate(scored, 1):
            f   = row["f"]
            res = row["res"]
            r   = row["r"]
            cbar = confidence_bar(row["conf"], width=15)
            lines.append(
                f"### {i}. {f.home_team} vs {f.away_team}\n"
                f"🏆 {f.league} · {f.country} · {f.kickoff}\n"
                f"🎯 **{row['pick_label']}** — {cbar} {confidence_tier(row['conf'])}\n"
                f"📊 H: {res.home_prob*100:.0f}% | D: {res.draw_prob*100:.0f}% "
                f"| A: {res.away_prob*100:.0f}%  |  xG: {r['home_xg']} – {r['away_xg']}\n"
                f"🆔 ID: `{row['fid']}`\n"
            )

        lines += [
            "---",
            "💡 Use `analytics_predict_fixture` with any ID for the full detailed report.",
            "📝 Use `analytics_record_outcome` after matches to train the self-learning model.",
            "⚠️ Statistical analysis only. Results are probabilistic, not guaranteed.",
        ]
        return "\n".join(lines)

    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4 — Live prediction (football-data.org match ID)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_live_prediction",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def analytics_live_prediction(params: LivePredictInput) -> str:
    """
    Generate a deep prediction using live form, head-to-head, and standings data
    from football-data.org. Requires FOOTBALL_DATA_API_KEY.

    Args:
        params.match_id: football-data.org match ID (from analytics_live_fixtures)

    Returns:
        Full prediction report with live team statistics, H2H history,
        league positions, xG estimates, and confidence-rated pick.
    """
    try:
        md = await _api(f"/matches/{params.match_id}")
        m  = md.get("match", md)
        home_id  = m["homeTeam"]["id"]
        away_id  = m["awayTeam"]["id"]
        home_name = m["homeTeam"]["name"]
        away_name = m["awayTeam"]["name"]
        league   = m.get("competition", {}).get("name", "Unknown League")
        comp_id  = m.get("competition", {}).get("id")

        # Fetch stats concurrently
        tasks = [
            _api(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10}),
            _api(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10}),
            _api(f"/matches/{params.match_id}/head2head", {"limit": 10}),
        ]
        if comp_id:
            tasks.append(_api(f"/competitions/{comp_id}/standings"))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        hm  = results[0].get("matches", []) if not isinstance(results[0], Exception) else []
        am  = results[1].get("matches", []) if not isinstance(results[1], Exception) else []
        h2hm = results[2].get("matches", []) if not isinstance(results[2], Exception) else []
        std_data = results[3] if len(results) > 3 and not isinstance(results[3], Exception) else {}

        h_form    = recent_form_pts(hm, home_id)
        a_form    = recent_form_pts(am, away_id)
        h_sc, h_cn = scoring_averages(hm, home_id)
        a_sc, a_cn = scoring_averages(am, away_id)
        h2h       = h2h_record(h2hm, home_id, away_id)
        h_fe      = form_string(hm, home_id)
        a_fe      = form_string(am, away_id)

        hp2 = ap2 = None
        for s in std_data.get("standings", [{}])[:1]:
            for row in s.get("table", []):
                if row["team"]["id"] == home_id: hp2 = row["position"]
                if row["team"]["id"] == away_id: ap2 = row["position"]

        # Try to match fixture in schedule for market lines
        la = lb = None
        for fix in FIXTURES:
            if fix.home_team.lower()[:6] in home_name.lower() or home_name.lower()[:6] in fix.home_team.lower():
                la, lb = _lines_from_fixture(fix)
                break

        weights = load_weights()
        r = run_prediction(la, lb, h_form, a_form, h_sc, h_cn, a_sc, a_cn, h2h, hp2, ap2, weights)
        hp, dp, ap = r["hp"], r["dp"], r["ap"]
        pick_label, pick_code, conf = select_pick(hp, dp, ap)

        res = AnalyticsResult(
            fixture_id=str(params.match_id),
            home_team=home_name, away_team=away_name,
            league=league, kickoff=_fmt_utc(m["utcDate"]),
            home_prob=hp, draw_prob=dp, away_prob=ap,
            recommended_pick=pick_label, pick_code=pick_code,
            confidence=conf, confidence_pct=int(conf * 100),
            confidence_label=confidence_tier(conf),
            implied_home=remove_margin(la or lb)[0] if (la or lb) else hp,
            implied_draw=remove_margin(la or lb)[1] if (la or lb) else dp,
            implied_away=remove_margin(la or lb)[2] if (la or lb) else ap,
            provider_margin_pct=r["margin_pct"],
            consensus_gap_pct=r["consensus_gap_pct"],
            home_form_pts=h_form, away_form_pts=a_form,
            home_xg=r["home_xg"], away_xg=r["away_xg"],
            h2h_home=h2h["home"], h2h_draw=h2h["draw"], h2h_away=h2h["away"],
        )
        persist_result(res)

        report = f"""## 🔮 Live Analysis: {home_name} vs {away_name}
🏆 {league}
📅 {_fmt_utc(m['utcDate'])}
{"📊 Schedule market lines found ✅" if (la or lb) else "📊 Live stats only (no market lines matched)"}

### 🎯 Recommended Pick
> **{pick_label}**
> Confidence: {confidence_bar(conf)} {confidence_tier(conf)}

### 📈 Probabilities
| Outcome | Probability |
|---------|-------------|
| 🏠 Home Win | **{hp*100:.1f}%** |
| 🤝 Draw     | **{dp*100:.1f}%** |
| ✈️ Away Win  | **{ap*100:.1f}%** |

---

### 📋 Live Team Statistics
| Metric            | {home_name[:20]:<20} | {away_name[:20]:<20} |
|-------------------|----------------------|----------------------|
| Form (last 5)     | {h_fe:<20} | {a_fe:<20} |
| Form Pts (/15)    | {h_form:<20} | {a_form:<20} |
| Avg Goals Scored  | {h_sc:<20} | {a_sc:<20} |
| Avg Goals Concd   | {h_cn:<20} | {a_cn:<20} |
| xG Estimate       | {r['home_xg']:<20} | {r['away_xg']:<20} |
| League Position   | {"#"+str(hp2) if hp2 else "N/A":<20} | {"#"+str(ap2) if ap2 else "N/A":<20} |

### ⚔️ Head-to-Head (last {h2h['total']} meetings)
🏠 {home_name.split()[0]}: **{h2h['home']}** · Draws: **{h2h['draw']}** · ✈️ {away_name.split()[0]}: **{h2h['away']}**

---
🆔 Match ID: `{params.match_id}`
⚠️ Statistical analysis only. Use `analytics_record_outcome` after the match to improve model accuracy."""
        return report

    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 5 — Live fixture list (football-data.org)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_live_fixtures",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def analytics_live_fixtures(params: LiveFixturesInput) -> str:
    """
    Fetch upcoming fixtures from football-data.org for a major league.
    Returns match IDs for use with analytics_live_prediction.
    Requires FOOTBALL_DATA_API_KEY.

    Args:
        params.league:     League code — PL, PD, SA, BL1, FL1, CL, EL, PPL, EC
        params.days_ahead: How many days ahead to look (1 – 14, default 3)

    Returns:
        Table of upcoming fixtures with match IDs and kickoff times.
    """
    try:
        code = params.league.upper()
        lid  = LEAGUE_IDS.get(code)
        if not lid:
            return f"❌ Unknown league code '{code}'. Valid codes: {', '.join(LEAGUE_IDS)}"

        date_from = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        date_to   = (datetime.now(timezone.utc) + timedelta(days=params.days_ahead)).strftime("%Y-%m-%d")

        data    = await _api(f"/competitions/{lid}/matches",
                             {"dateFrom": date_from, "dateTo": date_to, "status": "SCHEDULED"})
        matches = data.get("matches", [])

        if not matches:
            return f"No scheduled fixtures for {LEAGUE_NAMES.get(code, code)} in the next {params.days_ahead} days."

        lines = [
            f"## ⚽ {LEAGUE_NAMES.get(code, code)} — Next {params.days_ahead} Days ({len(matches)} fixtures)\n",
            f"{'Match ID':<10}  {'Date & Time (UTC)':<22}  {'Home':<26}  Away",
            "─" * 82,
        ]
        for m in matches:
            dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).strftime("%d %b %H:%M")
            lines.append(
                f"{m['id']:<10}  {dt:<22}  {m['homeTeam']['name'][:25]:<26}  {m['awayTeam']['name'][:25]}"
            )
        lines.append("\n💡 Use `analytics_live_prediction` with any Match ID for a detailed analysis.")
        return "\n".join(lines)

    except Exception as e:
        return _api_error(e)


# ─────────────────────────────────────────────────────────────────────────────
# Tool 6 — Record actual outcome (self-learning)
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_record_outcome",
    annotations={"readOnlyHint": False, "destructiveHint": False},
)
async def analytics_record_outcome(params: RecordOutcomeInput) -> str:
    """
    Record the actual result of a fixture after it has been played.
    This feeds the self-learning model and triggers weight recalibration
    once 20 or more graded fixtures have been recorded.

    Args:
        params.fixture_id:    The ID used when the prediction was generated
        params.actual_result: 'H' (Home win), 'D' (Draw), or 'A' (Away win)

    Returns:
        Confirmation of recording and updated accuracy statistics.
    """
    valid = {"H", "D", "A"}
    if params.actual_result.upper() not in valid:
        return f"❌ actual_result must be 'H', 'D', or 'A'. Received: '{params.actual_result}'"

    ok = record_actual_outcome(params.fixture_id, params.actual_result.upper())
    if not ok:
        return (
            f"❌ No prediction found for fixture ID '{params.fixture_id}'.\n"
            "Ensure you ran a prediction for this fixture before recording its outcome."
        )

    stats = accuracy_report()
    bar = confidence_bar(stats["accuracy_pct"] / 100)
    return f"""✅ Outcome recorded — Fixture `{params.fixture_id}`: **{params.actual_result.upper()}**

### 📊 Model Accuracy (Updated)
| Metric | Value |
|--------|-------|
| Total predictions logged | {stats['total_logged']} |
| Graded (outcomes recorded) | {stats['graded']} |
| Correct | {stats['correct']} |
| **Overall Accuracy** | **{stats['accuracy_pct']}%** |

Accuracy: {bar}

{"🔥 Strong accuracy — model weights stable." if stats['accuracy_pct'] >= 65 else "⚙️ Below target — model weights auto-adjusted for future predictions."}

*The self-learning engine updates weights automatically after each graded outcome.*"""


# ─────────────────────────────────────────────────────────────────────────────
# Tool 7 — Model performance report
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="analytics_model_report",
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def analytics_model_report() -> str:
    """
    Display the prediction model's accuracy statistics and current signal weights.
    Shows overall accuracy, breakdown by pick type, and the active model configuration.

    Returns:
        Performance report with accuracy metrics and model weight table.
    """
    stats   = accuracy_report()
    weights = load_weights()

    lines = [
        "## 📊 Analytics Model — Performance Report\n",
        "### Accuracy Summary",
        f"- Total predictions logged : **{stats['total_logged']}**",
        f"- Graded (outcomes known)  : **{stats['graded']}**",
        f"- Correct predictions      : **{stats['correct']}**",
        f"- **Overall Accuracy       : {stats['accuracy_pct']}%**",
    ]

    if stats["by_pick_type"]:
        lines += [
            "\n### Accuracy by Pick Type",
            "| Pick Code | Total | Correct | Accuracy |",
            "|-----------|-------|---------|----------|",
        ]
        for code, s in stats["by_pick_type"].items():
            lines.append(f"| {code} | {s['total']} | {s['correct']} | {s['accuracy_pct']}% |")

    lines += [
        "\n### Active Model Weights",
        "| Signal | Weight |",
        "|--------|--------|",
    ]
    labels = {
        "w_market":     "Market Implied Probability",
        "w_form":       "Recent Form",
        "w_h2h":        "Head-to-Head Record",
        "w_home_field": "Home Field Advantage",
        "w_position":   "League Position Gap",
    }
    for k, v in weights.items():
        lines.append(f"| {labels.get(k, k)} | {v:.2f} ({v*100:.0f}%) |")

    lines += [
        "",
        "💡 Use `analytics_record_outcome` after each match to continuously improve accuracy.",
        "♻️ Weights auto-recalibrate once 20+ outcomes have been recorded.",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "http":
        import uvicorn
        print(f"🚀 Football Analytics MCP — HTTP transport on port {PORT}")
        # FastMCP.run() does not accept a port arg in this SDK version.
        # Serve the ASGI app directly via uvicorn so Render binds the correct port.
        app = mcp.streamable_http_app()
        uvicorn.run(app, host="0.0.0.0", port=PORT)
    else:
        print("🚀 Football Analytics MCP — stdio transport")
        mcp.run()
