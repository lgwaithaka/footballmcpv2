"""
live_schedule.py
────────────────
Fetches upcoming EPL, UEFA Champions League, and UEFA Europa League fixtures
from football-data.org at server startup and merges them into the in-memory
fixture registry via schedule_data.register_live_fixtures().

Called once from server.py before uvicorn starts.
Uses synchronous httpx (called inside asyncio.run()) so it works before
the event loop is fully running.

Leagues fetched
───────────────
  PL  = English Premier League  (id 2021)
  CL  = UEFA Champions League   (id 2001)
  EL  = UEFA Europa League      (id 2146)
"""

import os
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional

from schedule_data import ScheduledMatch, MarketLines, register_live_fixtures

API_BASE    = "https://api.football-data.org/v4"
API_KEY     = os.getenv("FOOTBALL_DATA_API_KEY", "")
DAYS_AHEAD  = int(os.getenv("LIVE_SCHEDULE_DAYS", "21"))   # look 3 weeks ahead

LIVE_LEAGUES = {
    2021: ("Premier League",        "England", "England"),
    2001: ("UEFA Champions League", "Europe",  "Europe"),
    # UEFA Europa League (id 2146) requires a paid API tier — excluded.
    # Static UEL fixtures are served from schedule_data.py instead.
}


def _get(path: str, params: dict = None) -> Optional[dict]:
    """Synchronous GET — used at startup before the async event loop is available."""
    if not API_KEY:
        return None
    try:
        with httpx.Client(timeout=12) as client:
            r = client.get(
                f"{API_BASE}{path}",
                headers={"X-Auth-Token": API_KEY},
                params=params or {},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[live_schedule] API error for {path}: {e}")
        return None


def _to_scheduled_match(m: dict, league_name: str, country: str) -> Optional[ScheduledMatch]:
    """Convert a football-data.org match dict to ScheduledMatch."""
    try:
        utc_dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        date_str   = utc_dt.strftime("%d/%m/%Y")
        kickoff    = utc_dt.strftime("%H:%M")
        home_team  = m["homeTeam"].get("name") or m["homeTeam"].get("shortName", "TBD")
        away_team  = m["awayTeam"].get("name") or m["awayTeam"].get("shortName", "TBD")

        if home_team == "TBD" or away_team == "TBD":
            return None  # Skip fixtures without confirmed teams

        return ScheduledMatch(
            provider_a_id=m.get("id"),   # football-data match ID as provider A
            provider_b_id=None,
            date=date_str,
            kickoff=kickoff,
            home_team=home_team,
            away_team=away_team,
            league=league_name,
            country=country,
            lines_a=None,   # football-data.org free tier does not provide odds
            lines_b=None,   # predictions use xG/form model only for these fixtures
        )
    except Exception:
        return None


def fetch_and_register() -> dict:
    """
    Main entry point — call at server startup.
    Fetches upcoming fixtures for PL, CL, EL and registers them.
    Returns a summary dict.
    """
    if not API_KEY:
        print("[live_schedule] FOOTBALL_DATA_API_KEY not set — skipping live fetch")
        return {"fetched": 0, "registered": 0, "leagues": []}

    date_from = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_to   = (datetime.now(timezone.utc) + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")

    all_new: list[ScheduledMatch] = []
    leagues_loaded: list[str] = []

    for league_id, (league_name, country, _) in LIVE_LEAGUES.items():
        data = _get(f"/competitions/{league_id}/matches", {
            "dateFrom": date_from,
            "dateTo":   date_to,
            "status":   "SCHEDULED",
        })
        if not data:
            print(f"[live_schedule] No data for {league_name}")
            continue

        matches = data.get("matches", [])
        converted = [_to_scheduled_match(m, league_name, country) for m in matches]
        valid     = [m for m in converted if m is not None]
        all_new.extend(valid)
        if valid:
            leagues_loaded.append(f"{league_name} ({len(valid)} fixtures)")
        print(f"[live_schedule] {league_name}: {len(valid)}/{len(matches)} fixtures fetched")

    added = register_live_fixtures(all_new)
    print(f"[live_schedule] Registered {added} new live fixtures")

    return {
        "fetched":    len(all_new),
        "registered": added,
        "leagues":    leagues_loaded,
    }
