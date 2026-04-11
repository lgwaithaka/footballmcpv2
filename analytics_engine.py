"""
analytics_engine.py
────────────────────
Multi-signal statistical prediction engine for football match outcomes.

Signals used:
  1. Market-implied probability  — remove provider margin; convert lines to true probability
  2. Provider consensus          — average two independent sources to reduce noise
  3. Recent form                 — last-N results weighted by recency
  4. Expected goals proxy (xG)  — scoring / conceding rate differential
  5. Head-to-head record        — historical outcome ratios
  6. Home field factor          — structural home advantage
  7. League table position gap  — current standings differential

Self-learning: predictions are logged to SQLite; recording actual outcomes
triggers automatic weight recalibration after 20+ graded samples.
"""

import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "analytics.db")


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketData:
    """Decimal market lines for home / draw / away outcomes."""
    home: float
    draw: float
    away: float


@dataclass
class AnalyticsResult:
    """Full output of the prediction engine for one fixture."""
    fixture_id: str
    home_team: str
    away_team: str
    league: str
    kickoff: str

    # Outcome probabilities (sum to 1.0)
    home_prob: float
    draw_prob: float
    away_prob: float

    # Recommended selection
    recommended_pick: str    # human-readable label
    pick_code: str           # "H", "D", "A", "HD", "DA", "HA"
    confidence: float
    confidence_pct: int
    confidence_label: str    # HIGH / MEDIUM / LOW

    # Diagnostics
    implied_home: float
    implied_draw: float
    implied_away: float
    provider_margin_pct: float
    consensus_gap_pct: float
    home_form_pts: float
    away_form_pts: float
    home_xg: float
    away_xg: float
    h2h_home: int
    h2h_draw: int
    h2h_away: int
    model_version: str = "v2.1"


# ─────────────────────────────────────────────────────────────────────────────
# SQLite — self-learning persistence
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Initialise database tables and default model weights."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id    TEXT,
            home_team     TEXT,
            away_team     TEXT,
            league        TEXT,
            kickoff       TEXT,
            pick          TEXT,
            pick_code     TEXT,
            confidence    REAL,
            home_prob     REAL,
            draw_prob     REAL,
            away_prob     REAL,
            created_at    TEXT,
            actual_result TEXT    DEFAULT NULL,
            was_correct   INTEGER DEFAULT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS model_weights (
            key   TEXT PRIMARY KEY,
            value REAL
        )
    """)
    defaults = [
        ("w_market",       0.55),
        ("w_form",         0.20),
        ("w_h2h",          0.07),
        ("w_home_field",   0.08),
        ("w_position",     0.10),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO model_weights (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def persist_result(res: AnalyticsResult) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO predictions
          (fixture_id, home_team, away_team, league, kickoff,
           pick, pick_code, confidence, home_prob, draw_prob, away_prob, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        res.fixture_id, res.home_team, res.away_team, res.league, res.kickoff,
        res.recommended_pick, res.pick_code, res.confidence,
        res.home_prob, res.draw_prob, res.away_prob,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    conn.close()


def record_actual_outcome(fixture_id: str, actual: str) -> bool:
    """
    Record actual match result and flag whether our pick was correct.
    actual: 'H' (home win) | 'D' (draw) | 'A' (away win)
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, pick_code FROM predictions WHERE fixture_id=? ORDER BY id DESC LIMIT 1",
        (fixture_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    pred_id, pick_code = row
    correct = int(actual in pick_code)
    c.execute(
        "UPDATE predictions SET actual_result=?, was_correct=? WHERE id=?",
        (actual, correct, pred_id),
    )
    conn.commit()
    conn.close()
    _recalibrate()
    return True


def accuracy_report() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*), 
               SUM(CASE WHEN was_correct IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END)
        FROM predictions
    """)
    total, graded, correct = c.fetchone()
    graded = graded or 0
    correct = correct or 0
    accuracy = round(correct / graded * 100, 1) if graded > 0 else 0.0

    c.execute("""
        SELECT pick_code, COUNT(*), SUM(was_correct)
        FROM predictions
        WHERE was_correct IS NOT NULL
        GROUP BY pick_code
    """)
    by_pick = {}
    for row in c.fetchall():
        code, tot, wins = row
        wins = wins or 0
        by_pick[code] = {
            "total": tot,
            "correct": wins,
            "accuracy_pct": round(wins / tot * 100, 1),
        }
    conn.close()
    return {
        "total_logged": total or 0,
        "graded": graded,
        "correct": correct,
        "accuracy_pct": accuracy,
        "by_pick_type": by_pick,
    }


def load_weights() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, value FROM model_weights")
    weights = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    return weights


def _recalibrate() -> None:
    """Auto-adjust weights once 20+ graded samples are available."""
    report = accuracy_report()
    if report["graded"] < 20:
        return
    acc = report["accuracy_pct"] / 100.0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if acc < 0.60:
        # Trust market lines more when our model is underperforming
        c.execute("UPDATE model_weights SET value=0.65 WHERE key='w_market'")
        c.execute("UPDATE model_weights SET value=0.15 WHERE key='w_form'")
    elif acc > 0.75:
        # Model is performing well — give form more weight
        c.execute("UPDATE model_weights SET value=0.50 WHERE key='w_market'")
        c.execute("UPDATE model_weights SET value=0.25 WHERE key='w_form'")
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Market probability math
# ─────────────────────────────────────────────────────────────────────────────

def remove_margin(lines: MarketData) -> tuple[float, float, float]:
    """
    Convert decimal market lines to true probabilities by normalising away
    the provider's overround (margin).
    Returns (p_home, p_draw, p_away) — sums to exactly 1.0.
    """
    raw = [1.0 / lines.home, 1.0 / lines.draw, 1.0 / lines.away]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def provider_margin(lines: MarketData) -> float:
    """Return the provider's overround as a percentage."""
    return round((1.0 / lines.home + 1.0 / lines.draw + 1.0 / lines.away - 1.0) * 100, 2)


def consensus_lines(lines_a: MarketData, lines_b: Optional[MarketData]) -> tuple[MarketData, float]:
    """
    Average implied probabilities from two providers.
    Returns (averaged MarketData, max divergence between providers as fraction).
    """
    if lines_b is None:
        return lines_a, 0.0
    p1 = remove_margin(lines_a)
    p2 = remove_margin(lines_b)
    avg = [(p1[i] + p2[i]) / 2 for i in range(3)]
    divergence = max(abs(p1[i] - p2[i]) for i in range(3))
    # Convert back to decimal
    h = 1.0 / avg[0] if avg[0] > 0 else 99.0
    d = 1.0 / avg[1] if avg[1] > 0 else 99.0
    a = 1.0 / avg[2] if avg[2] > 0 else 99.0
    return MarketData(round(h, 3), round(d, 3), round(a, 3)), round(divergence, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Statistical signal helpers  (operate on football-data.org match dicts)
# ─────────────────────────────────────────────────────────────────────────────

def recent_form_pts(matches: list[dict], team_id: int, n: int = 5) -> float:
    """
    Compute form points (0–15) from last N completed fixtures.
    Win = 3 pts, Draw = 1 pt, Loss = 0 pts.
    """
    pts = count = 0
    for m in reversed(matches):
        if count >= n:
            break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None:
            continue
        is_home = m["homeTeam"]["id"] == team_id
        if is_home:
            if hg > ag:   pts += 3
            elif hg == ag: pts += 1
        elif m["awayTeam"]["id"] == team_id:
            if ag > hg:   pts += 3
            elif hg == ag: pts += 1
        count += 1
    return float(pts)


def scoring_averages(matches: list[dict], team_id: int, n: int = 8) -> tuple[float, float]:
    """Return (avg_scored, avg_conceded) over last N fixtures."""
    scored = conceded = count = 0
    for m in reversed(matches):
        if count >= n:
            break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None:
            continue
        if m["homeTeam"]["id"] == team_id:
            scored += hg; conceded += ag
        elif m["awayTeam"]["id"] == team_id:
            scored += ag; conceded += hg
        else:
            continue
        count += 1
    if count == 0:
        return 1.2, 1.0
    return round(scored / count, 2), round(conceded / count, 2)


def h2h_record(h2h_matches: list[dict], home_id: int, away_id: int) -> dict:
    """Return win/draw/loss counts from head-to-head history."""
    hw = aw = draws = 0
    for m in h2h_matches:
        mid_home = m["homeTeam"]["id"]
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None:
            continue
        if mid_home == home_id:
            if hg > ag: hw += 1
            elif hg == ag: draws += 1
            else: aw += 1
        else:
            if ag > hg: hw += 1
            elif hg == ag: draws += 1
            else: aw += 1
    return {"home": hw, "draw": draws, "away": aw, "total": hw + draws + aw}


def form_string(matches: list[dict], team_id: int, n: int = 5) -> str:
    """Return emoji form string e.g. '🟢 🟢 🟡 🔴 🟢'."""
    icons = []
    for m in reversed(matches):
        if len(icons) >= n:
            break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None:
            continue
        is_home = m["homeTeam"]["id"] == team_id
        if is_home:
            icons.append("🟢" if hg > ag else ("🟡" if hg == ag else "🔴"))
        else:
            icons.append("🟢" if ag > hg else ("🟡" if hg == ag else "🔴"))
    return " ".join(icons) or "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# Core prediction function
# ─────────────────────────────────────────────────────────────────────────────

def run_prediction(
    lines_a: Optional[MarketData],
    lines_b: Optional[MarketData],
    home_form: float = 7.5,
    away_form: float = 7.5,
    home_scored: float = 1.2,
    home_conceded: float = 1.1,
    away_scored: float = 1.2,
    away_conceded: float = 1.1,
    h2h: Optional[dict] = None,
    home_position: Optional[int] = None,
    away_position: Optional[int] = None,
    weights: Optional[dict] = None,
) -> dict:
    """
    Blend five statistical signals into outcome probabilities.
    Returns a dict with keys: hp, dp, ap, home_xg, away_xg,
    margin_pct, consensus_gap_pct.
    """
    if weights is None:
        weights = load_weights()

    # ── 1. Market signal ─────────────────────────────────────
    best = lines_a or lines_b
    if best is None:
        p_mkt = (0.34, 0.33, 0.33)
        margin = 0.0
        gap = 0.0
    else:
        if lines_a and lines_b:
            averaged, gap = consensus_lines(lines_a, lines_b)
            p_mkt = remove_margin(averaged)
            margin = provider_margin(averaged)
        else:
            p_mkt = remove_margin(best)
            margin = provider_margin(best)
            gap = 0.0

    # ── 2. Form signal ───────────────────────────────────────
    hf_norm = home_form / 15.0
    af_norm = away_form / 15.0
    diff = hf_norm - af_norm   # -1 to +1
    hfa = weights.get("w_home_field", 0.08)
    fh = min(max(0.33 + diff * 0.20 + hfa, 0.05), 0.85)
    fx = min(max(0.34 - abs(diff) * 0.10,   0.05), 0.50)
    fa = min(max(0.33 - diff * 0.20 - hfa,  0.05), 0.85)
    tot = fh + fx + fa
    p_form = (fh / tot, fx / tot, fa / tot)

    # ── 3. xG proxy ──────────────────────────────────────────
    home_xg = (home_scored + away_conceded) / 2
    away_xg = (away_scored + home_conceded) / 2
    xg_diff = (home_xg - away_xg) / max(home_xg + away_xg, 0.1)

    # Nudge market probabilities by xG differential
    xh = p_mkt[0] * (1 + xg_diff * 0.10)
    xd = p_mkt[1]
    xa = p_mkt[2] * (1 - xg_diff * 0.10)
    tot2 = xh + xd + xa
    p_xg = (xh / tot2, xd / tot2, xa / tot2)

    # ── 4. H2H signal ────────────────────────────────────────
    if h2h and h2h.get("total", 0) > 0:
        tot_h2h = h2h["total"]
        p_h2h = (
            h2h["home"] / tot_h2h,
            h2h["draw"] / tot_h2h,
            h2h["away"] / tot_h2h,
        )
    else:
        p_h2h = p_mkt

    # ── 5. League position signal ─────────────────────────────
    if home_position and away_position:
        pos_diff = (away_position - home_position) / 20.0
        ph = min(max(0.33 + pos_diff * 0.08, 0.05), 0.85)
        pd = 0.34
        pa = min(max(0.33 - pos_diff * 0.08, 0.05), 0.85)
        tot3 = ph + pd + pa
        p_pos = (ph / tot3, pd / tot3, pa / tot3)
    else:
        p_pos = p_mkt

    # ── 6. Weighted blend ────────────────────────────────────
    wm = weights.get("w_market", 0.55)
    wf = weights.get("w_form", 0.20)
    wh = weights.get("w_h2h", 0.07)
    wp = max(1.0 - wm - wf - wh, 0.0)

    blended = [
        wm * p_xg[i] + wf * p_form[i] + wh * p_h2h[i] + wp * p_pos[i]
        for i in range(3)
    ]
    total_blend = sum(blended)
    hp, dp, ap = [b / total_blend for b in blended]

    # Clamp extremes
    hp = min(max(hp, 0.04), 0.93)
    dp = min(max(dp, 0.04), 0.50)
    ap = min(max(ap, 0.04), 0.93)
    tf = hp + dp + ap
    hp, dp, ap = hp / tf, dp / tf, ap / tf

    return {
        "hp": round(hp, 4),
        "dp": round(dp, 4),
        "ap": round(ap, 4),
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "margin_pct": round(margin, 2),
        "consensus_gap_pct": round(gap * 100, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pick selection
# ─────────────────────────────────────────────────────────────────────────────

SINGLE_THRESHOLD   = 0.50
COMPOUND_THRESHOLD = 0.65


def select_pick(hp: float, dp: float, ap: float) -> tuple[str, str, float]:
    """
    Choose the highest-confidence pick.
    Returns (label, code, confidence).
    Codes: H=Home, D=Draw, A=Away, HD=Home or Draw, DA=Draw or Away, HA=Home or Away.
    """
    if hp >= SINGLE_THRESHOLD:
        return "Home Win", "H", hp
    if ap >= SINGLE_THRESHOLD:
        return "Away Win", "A", ap
    if dp >= 0.38:
        return "Draw", "D", dp

    hd = hp + dp
    da = dp + ap
    ha = hp + ap

    if hd >= COMPOUND_THRESHOLD:
        return "Home Win or Draw", "HD", hd
    if da >= COMPOUND_THRESHOLD:
        return "Draw or Away Win", "DA", da
    if ha >= COMPOUND_THRESHOLD:
        return "Home or Away Win", "HA", ha

    # Fallback: pick best single
    best = max(hp, dp, ap)
    if best == hp:
        return "Home Win", "H", hp
    if best == dp:
        return "Draw", "D", dp
    return "Away Win", "A", ap


def confidence_tier(confidence: float) -> str:
    if confidence >= 0.72:
        return "HIGH ⭐⭐⭐"
    elif confidence >= 0.58:
        return "MEDIUM ⭐⭐"
    return "LOW ⭐"


def confidence_bar(confidence: float, width: int = 20) -> str:
    filled = int(confidence * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {int(confidence * 100)}%"
