"""
market_fetcher.py
─────────────────
Fetches real-time crowd-sourced win probabilities from Polymarket
for football matches, and synthesises a multi-source consensus.

Polymarket (gamma-api.polymarket.com)
  • Free, no API key required
  • Returns implied probability 0.0–1.0 per outcome
  • Covers EPL, UCL, and major European leagues
  • Matches are 2-way markets (Home vs Away — draw resolves via tie-breaker)
    so draw probability is inferred as 1 − home − away

Data returned per fixture
  {
    home_win_pct:   float  # our model (0–100)
    draw_pct:       float  # our model
    away_win_pct:   float  # our model
    pm_home_pct:    float | None  # Polymarket crowd
    pm_away_pct:    float | None
    pm_draw_pct:    float | None
    pm_volume:      str | None    # e.g. "$282K"
    pm_url:         str | None    # direct market link
    consensus_home: float | None  # average of model + Polymarket
    consensus_away: float | None
    divergence:     float | None  # abs difference (highlights value spots)
  }
"""

import json
import asyncio
from typing import Optional

import httpx

GAMMA_API   = "https://gamma-api.polymarket.com"
SPORTS_TAGS = ["soccer"]
TIMEOUT     = 8.0


# ─────────────────────────────────────────────────────────────────────────────
# Polymarket API helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _pm_search(query: str) -> list[dict]:
    """Search Polymarket events by keyword. Returns list of event dicts."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                f"{GAMMA_API}/events",
                params={"tag_slug": "soccer", "active": "true",
                        "title": query, "limit": 5},
                headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                return r.json() or []
    except Exception:
        pass
    return []


def _parse_pm_market(event: dict, home_team: str, away_team: str) -> Optional[dict]:
    """
    Extract home/away win probabilities from a Polymarket event.
    Polymarket soccer markets are binary: outcomes are [TeamA, TeamB].
    Prices are strings like ["0.68", "0.32"] stored in outcomePrices.
    """
    try:
        markets = event.get("markets") or []
        best = None

        for market in markets:
            question = (market.get("question") or "").lower()
            outcomes_raw  = market.get("outcomes") or "[]"
            prices_raw    = market.get("outcomePrices") or "[]"

            # Parse JSON strings (Polymarket stores as JSON strings)
            if isinstance(outcomes_raw, str):
                try:
                    outcomes = json.loads(outcomes_raw)
                except Exception:
                    continue
            else:
                outcomes = outcomes_raw

            if isinstance(prices_raw, str):
                try:
                    prices = [float(p) for p in json.loads(prices_raw)]
                except Exception:
                    continue
            else:
                prices = [float(p) for p in prices_raw]

            if len(outcomes) < 2 or len(prices) < 2:
                continue

            # Match home/away outcomes to our team names
            home_lower = home_team.lower().split()[0]  # first word match
            away_lower = away_team.lower().split()[0]

            home_idx = away_idx = None
            for i, o in enumerate(outcomes):
                ol = str(o).lower()
                if home_lower in ol:
                    home_idx = i
                elif away_lower in ol:
                    away_idx = i

            if home_idx is None or away_idx is None:
                # Try question text matching
                if home_lower in question and away_lower in question:
                    home_idx, away_idx = 0, 1
                else:
                    continue

            home_prob = prices[home_idx]
            away_prob = prices[away_idx]
            # Normalise
            total = home_prob + away_prob
            if total < 0.01:
                continue
            home_prob = home_prob / total
            away_prob = away_prob / total
            draw_prob = max(0.0, 1.0 - home_prob - away_prob)

            # Volume
            volume_raw = event.get("volume") or 0
            try:
                vol = float(volume_raw)
                if vol >= 1_000_000:
                    volume_str = f"${vol/1_000_000:.1f}M"
                elif vol >= 1_000:
                    volume_str = f"${vol/1_000:.0f}K"
                else:
                    volume_str = f"${vol:.0f}"
            except Exception:
                volume_str = None

            slug = event.get("slug") or ""
            url  = f"https://polymarket.com/event/{slug}" if slug else None

            best = {
                "pm_home_pct":  round(home_prob * 100, 1),
                "pm_away_pct":  round(away_prob * 100, 1),
                "pm_draw_pct":  round(draw_prob * 100, 1),
                "pm_volume":    volume_str,
                "pm_url":       url,
            }
            break  # Take first good market

        return best
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Main fetch function
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_market_data(fixtures: list[dict]) -> dict[str, dict]:
    """
    For each fixture dict (must have 'id', 'home', 'away', 'home_prob',
    'draw_prob', 'away_prob'), fetch Polymarket crowd probabilities.

    Returns a mapping of fixture_id → enriched market data dict.
    Results for fixtures with no Polymarket data still include our model %.
    """
    async def _enrich(f: dict) -> tuple[str, dict]:
        fid       = str(f.get("id", ""))
        home      = f.get("home", "")
        away      = f.get("away", "")
        home_pct  = f.get("home_prob", 0)
        draw_pct  = f.get("draw_prob", 0)
        away_pct  = f.get("away_prob", 0)

        pm = None
        if home and away:
            # Try "{home} vs {away}" and "{away} vs {home}"
            query = f"{home.split()[0]} {away.split()[0]}"
            events = await _pm_search(query)
            for ev in events:
                pm = _parse_pm_market(ev, home, away)
                if pm:
                    break

        result = {
            "home_win_pct": home_pct,
            "draw_pct":     draw_pct,
            "away_win_pct": away_pct,
        }

        if pm:
            result.update(pm)
            # Consensus = simple average of model + Polymarket
            result["consensus_home"] = round((home_pct + pm["pm_home_pct"]) / 2, 1)
            result["consensus_away"] = round((away_pct + pm["pm_away_pct"]) / 2, 1)
            # Divergence = how much model disagrees with crowd on home win
            result["divergence"] = round(abs(home_pct - pm["pm_home_pct"]), 1)
        else:
            result["pm_home_pct"]    = None
            result["pm_away_pct"]    = None
            result["pm_draw_pct"]    = None
            result["pm_volume"]      = None
            result["pm_url"]         = None
            result["consensus_home"] = None
            result["consensus_away"] = None
            result["divergence"]     = None

        return fid, result

    # Run all fixture enrichments concurrently (with a semaphore to stay polite)
    sem = asyncio.Semaphore(4)

    async def _guarded(f):
        async with sem:
            return await _enrich(f)

    tasks   = [_guarded(f) for f in fixtures]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: dict[str, dict] = {}
    for r in results:
        if isinstance(r, tuple):
            fid, data = r
            out[fid] = data
    return out
