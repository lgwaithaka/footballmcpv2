"""
live_schedule.py — v2.4
────────────────────────
Two responsibilities:

1. fetch_and_register()   [synchronous, called at startup]
   Fetches upcoming EPL + UCL scheduled fixtures and merges them into
   the in-memory registry via schedule_data.register_live_fixtures().

2. fetch_live_scores()    [async, called by dashboard API]
   Fetches today's IN_PLAY, PAUSED, and FINISHED matches for EPL + UCL,
   cross-references with our stored predictions, and returns structured
   score + prediction-accuracy data for the Live Scores tab.

Leagues
───────
  PL  = English Premier League  (id 2021)  — free tier ✅
  CL  = UEFA Champions League   (id 2001)  — free tier ✅
  EL  = UEFA Europa League      (id 2146)  — paid tier only ❌ (excluded from live fetch)
"""

import os
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional

from schedule_data import ScheduledMatch, MarketLines, register_live_fixtures

API_BASE   = "https://api.football-data.org/v4"
API_KEY    = os.getenv("FOOTBALL_DATA_API_KEY", "")
DAYS_AHEAD = int(os.getenv("LIVE_SCHEDULE_DAYS", "21"))

LIVE_LEAGUES = {
    2021: ("Premier League",        "England", "England"),
    2001: ("UEFA Champions League", "Europe",  "Europe"),
    # Europa League (2146) excluded — requires paid tier.
}

# Which statuses count as "live"
LIVE_STATUSES    = {"IN_PLAY", "PAUSED", "HALFTIME"}
# Which statuses count as finished
FINISHED_STATUSES = {"FINISHED", "FT", "AET", "PEN"}
# Both combined for the scores poll
SCORE_STATUSES   = LIVE_STATUSES | FINISHED_STATUSES


# ─────────────────────────────────────────────────────────────────────────────
# Synchronous GET  (for startup use)
# ─────────────────────────────────────────────────────────────────────────────

def _get_sync(path: str, params: dict = None) -> Optional[dict]:
    if not API_KEY:
        return None
    try:
        with httpx.Client(timeout=12) as client:
            r = client.get(f"{API_BASE}{path}",
                           headers={"X-Auth-Token": API_KEY},
                           params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[live_schedule] API error for {path}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Async GET  (for dashboard API use)
# ─────────────────────────────────────────────────────────────────────────────

async def _get_async(path: str, params: dict = None) -> Optional[dict]:
    if not API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(f"{API_BASE}{path}",
                                 headers={"X-Auth-Token": API_KEY},
                                 params=params or {})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        print(f"[live_schedule] Async API error for {path}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_scheduled_match(m: dict, league_name: str, country: str) -> Optional[ScheduledMatch]:
    """Convert API match dict → ScheduledMatch. Returns None for TBD/null teams."""
    try:
        utc_dt    = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        date_str  = utc_dt.strftime("%d/%m/%Y")
        kickoff   = utc_dt.strftime("%H:%M")
        home_raw  = m.get("homeTeam") or {}
        away_raw  = m.get("awayTeam") or {}
        home_team = (home_raw.get("name") or home_raw.get("shortName") or "").strip()
        away_team = (away_raw.get("name") or away_raw.get("shortName") or "").strip()
        if not home_team or not away_team or home_team == "TBD" or away_team == "TBD":
            return None
        return ScheduledMatch(
            provider_a_id=m.get("id"),
            provider_b_id=None,
            date=date_str,
            kickoff=kickoff,
            home_team=home_team,
            away_team=away_team,
            league=league_name,
            country=country,
            lines_a=None,
            lines_b=None,
        )
    except Exception as e:
        print(f"[live_schedule] Skipping match: {e}")
        return None


def _determine_result(home_score: int, away_score: int) -> str:
    """Return H / D / A from a final score."""
    if home_score > away_score:
        return "H"
    if away_score > home_score:
        return "A"
    return "D"


def _result_label(code: str) -> str:
    return {"H": "Home Win", "D": "Draw", "A": "Away Win"}.get(code, code)


def _minute_str(m: dict) -> str:
    """Return a display string for current match minute."""
    time_info = m.get("minute")
    if time_info:
        return f"{time_info}'"
    elapsed = (m.get("score") or {}).get("duration", "")
    return str(elapsed) if elapsed else ""


# ─────────────────────────────────────────────────────────────────────────────
# 1. Startup: fetch scheduled fixtures
# ─────────────────────────────────────────────────────────────────────────────

def fetch_and_register() -> dict:
    """Fetch upcoming scheduled fixtures and merge into registry."""
    if not API_KEY:
        print("[live_schedule] FOOTBALL_DATA_API_KEY not set — skipping live fetch")
        return {"fetched": 0, "registered": 0, "leagues": []}

    date_from = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_to   = (datetime.now(timezone.utc) + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")

    all_new: list[ScheduledMatch] = []
    leagues_loaded: list[str]     = []

    for league_id, (league_name, country, _) in LIVE_LEAGUES.items():
        data = _get_sync(f"/competitions/{league_id}/matches", {
            "dateFrom": date_from,
            "dateTo":   date_to,
            "status":   "SCHEDULED",
        })
        if not data:
            print(f"[live_schedule] No data for {league_name}")
            continue
        matches   = data.get("matches", [])
        converted = [_to_scheduled_match(m, league_name, country) for m in matches]
        valid     = [m for m in converted if m is not None]
        all_new.extend(valid)
        if valid:
            leagues_loaded.append(f"{league_name} ({len(valid)} fixtures)")
        print(f"[live_schedule] {league_name}: {len(valid)}/{len(matches)} fixtures fetched")

    added = register_live_fixtures(all_new)
    print(f"[live_schedule] Registered {added} new live fixtures")
    return {"fetched": len(all_new), "registered": added, "leagues": leagues_loaded}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Live scores: fetch today's in-play + finished matches
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_live_scores(stored_predictions: list[dict]) -> list[dict]:
    """
    Fetch live and finished match scores for EPL + UCL for today
    (and yesterday for recently finished matches).

    Cross-references with stored_predictions (list of prediction dicts from
    /api/fixtures) to show whether each pick was correct.

    Returns a list of score dicts, newest first:
      {
        match_id, home, away, league, country, kickoff, date,
        status,       # IN_PLAY | PAUSED | HALFTIME | FINISHED
        home_score,   # int or None if not started
        away_score,   # int or None
        minute,       # e.g. "67'" or "" if not live
        result_code,  # "H"/"D"/"A" or None
        result_label, # "Home Win" / "Draw" / "Away Win" / None
        # prediction cross-reference (None if no prediction stored)
        predicted_pick,       # e.g. "Home Win or Draw"
        predicted_pick_code,  # e.g. "HD"
        predicted_confidence, # e.g. 72
        prediction_correct,   # True / False / None
        prediction_status,    # "CORRECT" / "WRONG" / "IN_PLAY" / "NO_PREDICTION"
      }
    """
    if not API_KEY:
        return []

    # Build prediction lookup: normalised team key → prediction dict
    pred_lookup: dict[str, dict] = {}
    for p in stored_predictions:
        key = f"{(p.get('home') or '').lower()[:8]}|{(p.get('away') or '').lower()[:8]}"
        pred_lookup[key] = p

    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    results: list[dict] = []

    for league_id, (league_name, country, _) in LIVE_LEAGUES.items():
        # Fetch today's live + finished matches
        data = await _get_async(f"/competitions/{league_id}/matches", {
            "dateFrom": yesterday,
            "dateTo":   today,
        })
        if not data:
            continue

        for m in data.get("matches", []):
            status = (m.get("status") or "").upper()
            if status not in SCORE_STATUSES:
                continue

            home_raw  = m.get("homeTeam") or {}
            away_raw  = m.get("awayTeam") or {}
            home_name = (home_raw.get("name") or home_raw.get("shortName") or "").strip()
            away_name = (away_raw.get("name") or away_raw.get("shortName") or "").strip()

            if not home_name or not away_name:
                continue

            # Scores
            score_info = m.get("score") or {}
            full_time  = score_info.get("fullTime") or {}
            in_play    = score_info.get("regularTime") or full_time
            home_score = full_time.get("home") if status in FINISHED_STATUSES else in_play.get("home")
            away_score = full_time.get("away") if status in FINISHED_STATUSES else in_play.get("away")

            # Determine result
            result_code = None
            result_label_str = None
            if home_score is not None and away_score is not None:
                result_code      = _determine_result(home_score, away_score)
                result_label_str = _result_label(result_code)

            # Cross-reference prediction
            key = f"{home_name.lower()[:8]}|{away_name.lower()[:8]}"
            pred = pred_lookup.get(key)
            predicted_pick      = None
            predicted_pick_code = None
            predicted_confidence = None
            prediction_correct  = None
            pred_status         = "NO_PREDICTION"

            if pred:
                predicted_pick       = pred.get("pick")
                predicted_pick_code  = pred.get("pick_code")
                predicted_confidence = pred.get("confidence")
                if result_code and predicted_pick_code:
                    prediction_correct = result_code in predicted_pick_code
                    pred_status        = "CORRECT" if prediction_correct else "WRONG"
                elif status in LIVE_STATUSES:
                    pred_status = "IN_PLAY"
                else:
                    pred_status = "PENDING"

            # Kickoff
            try:
                utc_dt  = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                kickoff = utc_dt.strftime("%H:%M")
                date_str = utc_dt.strftime("%d/%m/%Y")
            except Exception:
                kickoff  = ""
                date_str = today

            # Live minute
            minute = ""
            if status in LIVE_STATUSES:
                raw_min = m.get("minute")
                if raw_min is not None:
                    minute = f"{raw_min}'"
                elif status == "HALFTIME":
                    minute = "HT"

            results.append({
                "match_id":            m.get("id"),
                "home":                home_name,
                "away":                away_name,
                "league":              league_name,
                "country":             country,
                "kickoff":             kickoff,
                "date":                date_str,
                "status":              status,
                "home_score":          home_score,
                "away_score":          away_score,
                "minute":              minute,
                "result_code":         result_code,
                "result_label":        result_label_str,
                "predicted_pick":      predicted_pick,
                "predicted_pick_code": predicted_pick_code,
                "predicted_confidence": predicted_confidence,
                "prediction_correct":  prediction_correct,
                "prediction_status":   pred_status,
            })

    # Sort: live first, then finished, newest kickoff first
    order = {"IN_PLAY": 0, "PAUSED": 1, "HALFTIME": 2, "FINISHED": 3}
    results.sort(key=lambda x: (order.get(x["status"], 9), x["kickoff"]), reverse=False)
    return results
