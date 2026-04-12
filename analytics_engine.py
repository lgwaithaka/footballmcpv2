"""
analytics_engine.py — v2.2
───────────────────────────
5-outcome statistical prediction engine with full self-learning.

Self-Learning System
────────────────────
• Predictions logged to SQLite on every run
• Record actual outcomes (H/D/A) to grade predictions
• Brier Score tracks probabilistic calibration (lower = better)
• EMA weight update: weights shift smoothly toward what works
• Per-signal accuracy tracked; underperforming signals lose weight
• Learning rate controls how fast weights shift (default 0.05)
• Minimum 10 graded samples before any weight changes
• Full learning report available via learning_report()

Outcomes predicted
──────────────────
  1. Home Win
  2. Draw
  3. Away Win
  4. Over 2.5 Goals
  5. Both Teams to Score (BTTS)
"""

import math
import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

DB_PATH       = os.getenv("DB_PATH", "analytics.db")
LEARNING_RATE = float(os.getenv("LEARNING_RATE", "0.05"))


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketData:
    home: float
    draw: float
    away: float


@dataclass
class GoalMarketData:
    over_2_5:      float
    under_2_5:     float
    btts_yes:      float
    btts_no:       float


@dataclass
class AnalyticsResult:
    fixture_id:          str
    home_team:           str
    away_team:           str
    league:              str
    country:             str
    kickoff:             str
    date:                str
    home_prob:           float
    draw_prob:           float
    away_prob:           float
    over_25_prob:        float
    btts_prob:           float
    recommended_pick:    str
    pick_code:           str
    confidence:          float
    confidence_pct:      int
    confidence_label:    str
    provider_margin_pct: float
    consensus_gap_pct:   float
    home_form_pts:       float
    away_form_pts:       float
    home_xg:             float
    away_xg:             float
    h2h_home:            int
    h2h_draw:            int
    h2h_away:            int
    model_version:       str = "v2.2"


# ─────────────────────────────────────────────────────────────────────────────
# Database — schema + migrations
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Main predictions table
    c.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id       TEXT,
            home_team        TEXT,
            away_team        TEXT,
            league           TEXT,
            kickoff          TEXT,
            pick             TEXT,
            pick_code        TEXT,
            confidence       REAL,
            home_prob        REAL,
            draw_prob        REAL,
            away_prob        REAL,
            over_25_prob     REAL,
            btts_prob        REAL,
            home_xg          REAL,
            away_xg          REAL,
            created_at       TEXT,
            actual_result    TEXT    DEFAULT NULL,
            was_correct      INTEGER DEFAULT NULL,
            brier_score      REAL    DEFAULT NULL
        )
    """)

    # Model weights
    c.execute("""
        CREATE TABLE IF NOT EXISTS model_weights (
            key   TEXT PRIMARY KEY,
            value REAL
        )
    """)

    # Learning history — one row per recalibration event
    c.execute("""
        CREATE TABLE IF NOT EXISTS learning_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_at     TEXT,
            graded_count INTEGER,
            accuracy_pct REAL,
            brier_score  REAL,
            w_market     REAL,
            w_form       REAL,
            w_h2h        REAL,
            w_home_field REAL,
            w_position   REAL,
            trigger      TEXT
        )
    """)

    # Default weights
    defaults = [
        ("w_market",     0.55),
        ("w_form",       0.20),
        ("w_h2h",        0.07),
        ("w_home_field", 0.08),
        ("w_position",   0.10),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO model_weights (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────

def persist_result(res: AnalyticsResult) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO predictions
          (fixture_id, home_team, away_team, league, kickoff,
           pick, pick_code, confidence, home_prob, draw_prob, away_prob,
           over_25_prob, btts_prob, home_xg, away_xg, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        res.fixture_id, res.home_team, res.away_team, res.league, res.kickoff,
        res.recommended_pick, res.pick_code, res.confidence,
        res.home_prob, res.draw_prob, res.away_prob,
        res.over_25_prob, res.btts_prob, res.home_xg, res.away_xg,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    conn.close()


def record_actual_outcome(fixture_id: str, actual: str) -> bool:
    """
    Record actual result and compute Brier Score for this prediction.
    actual: 'H' | 'D' | 'A'
    Triggers self-learning recalibration automatically.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, pick_code, home_prob, draw_prob, away_prob FROM predictions "
        "WHERE fixture_id=? ORDER BY id DESC LIMIT 1",
        (fixture_id,),
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    pred_id, pick_code, home_prob, draw_prob, away_prob = row
    correct = int(actual in pick_code)

    # Brier Score: mean squared error between predicted vector and outcome vector
    # Outcome vector: H=[1,0,0], D=[0,1,0], A=[0,0,1]
    outcome_vec = {"H": (1.0, 0.0, 0.0), "D": (0.0, 1.0, 0.0), "A": (0.0, 0.0, 1.0)}
    ov = outcome_vec.get(actual, (0.0, 0.0, 0.0))
    probs = (home_prob or 0.33, draw_prob or 0.33, away_prob or 0.33)
    brier = round(sum((probs[i] - ov[i]) ** 2 for i in range(3)), 4)

    c.execute(
        "UPDATE predictions SET actual_result=?, was_correct=?, brier_score=? WHERE id=?",
        (actual, correct, brier, pred_id),
    )
    conn.commit()
    conn.close()
    _run_learning_cycle()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Accuracy & variance reporting
# ─────────────────────────────────────────────────────────────────────────────

def accuracy_report() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN was_correct IS NOT NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN was_correct = 1 THEN 1 ELSE 0 END),
               AVG(CASE WHEN brier_score IS NOT NULL THEN brier_score END)
        FROM predictions
    """)
    total, graded, correct, avg_brier = c.fetchone()
    graded  = graded  or 0
    correct = correct or 0
    accuracy = round(correct / graded * 100, 1) if graded > 0 else 0.0

    c.execute("""
        SELECT pick_code, COUNT(*), SUM(was_correct), AVG(brier_score)
        FROM predictions WHERE was_correct IS NOT NULL
        GROUP BY pick_code
    """)
    by_pick = {}
    for row in c.fetchall():
        code, tot, wins, bs = row
        wins = wins or 0
        by_pick[code] = {
            "total": tot,
            "correct": wins,
            "accuracy_pct": round(wins / tot * 100, 1),
            "avg_brier": round(bs or 0, 4),
        }

    conn.close()
    return {
        "total_logged":  total or 0,
        "graded":        graded,
        "correct":       correct,
        "accuracy_pct":  accuracy,
        "avg_brier":     round(avg_brier or 0, 4),
        "by_pick_type":  by_pick,
    }


def variance_history(limit: int = 50) -> list[dict]:
    """
    Return graded predictions with predicted vs actual for variance analysis.
    Each row: fixture_id, match, kickoff, pick, confidence, actual, correct,
              home_prob, draw_prob, away_prob, brier_score, variance_pct.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT fixture_id, home_team, away_team, kickoff,
               pick, pick_code, confidence,
               home_prob, draw_prob, away_prob,
               over_25_prob, btts_prob,
               actual_result, was_correct, brier_score,
               created_at
        FROM predictions
        WHERE was_correct IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = []
    for r in c.fetchall():
        (fid, home, away, kickoff, pick, code, conf,
         hp, dp, ap, ov25, btts,
         actual, correct, brier, created_at) = r

        # Predicted probability for the actual outcome
        actual_map = {"H": hp or 0.33, "D": dp or 0.33, "A": ap or 0.33}
        predicted_pct = round((actual_map.get(actual, 0.33)) * 100, 1)
        actual_pct    = 100.0 if correct else 0.0
        variance      = round(abs(actual_pct - predicted_pct), 1)

        rows.append({
            "fixture_id":    fid,
            "match":         f"{home} vs {away}",
            "kickoff":       kickoff,
            "pick":          pick,
            "pick_code":     code,
            "confidence_pct": int((conf or 0) * 100),
            "actual":        actual,
            "correct":       bool(correct),
            "predicted_pct": predicted_pct,
            "variance_pct":  variance,
            "brier_score":   round(brier or 0, 4),
            "home_prob":     round((hp or 0) * 100, 1),
            "draw_prob":     round((dp or 0) * 100, 1),
            "away_prob":     round((ap or 0) * 100, 1),
            "over_25_prob":  round((ov25 or 0) * 100, 1),
            "btts_prob":     round((btts or 0) * 100, 1),
            "created_at":    created_at,
        })
    conn.close()
    return rows


def learning_report() -> dict:
    """Return learning history — how weights evolved over time."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT event_at, graded_count, accuracy_pct, brier_score,
               w_market, w_form, w_h2h, w_home_field, w_position, trigger
        FROM learning_events
        ORDER BY id DESC
        LIMIT 20
    """)
    events = []
    for row in c.fetchall():
        events.append({
            "event_at":    row[0],
            "graded":      row[1],
            "accuracy":    row[2],
            "brier":       row[3],
            "w_market":    row[4],
            "w_form":      row[5],
            "w_h2h":       row[6],
            "w_home_field":row[7],
            "w_position":  row[8],
            "trigger":     row[9],
        })
    conn.close()

    current = load_weights()
    stats   = accuracy_report()
    return {
        "current_weights": current,
        "current_accuracy": stats["accuracy_pct"],
        "current_brier":   stats["avg_brier"],
        "total_graded":    stats["graded"],
        "learning_rate":   LEARNING_RATE,
        "history":         events,
    }


def load_weights() -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT key, value FROM model_weights")
    w = {row[0]: row[1] for row in c.fetchall()}
    conn.close()
    return w


def _save_weights(weights: dict) -> None:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for k, v in weights.items():
        c.execute("UPDATE model_weights SET value=? WHERE key=?", (v, k))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Self-Learning — EMA weight update with Brier Score feedback
# ─────────────────────────────────────────────────────────────────────────────

def _run_learning_cycle() -> None:
    """
    Full self-learning cycle triggered after every recorded outcome.

    Algorithm:
    1. Need >= 10 graded samples before changing anything.
    2. Compute rolling accuracy and Brier Score.
    3. If accuracy is improving: reward market + form weight mix.
    4. If Brier Score is high (poor calibration): push market weight up.
    5. Apply change via EMA: new_w = old_w * (1 - lr) + target_w * lr
    6. Re-normalise all weights to sum to 1.0.
    7. Log the event to learning_events table.
    """
    stats = accuracy_report()
    graded = stats["graded"]
    if graded < 10:
        return  # Not enough data yet

    acc    = stats["accuracy_pct"] / 100.0
    brier  = stats["avg_brier"]
    lr     = LEARNING_RATE
    w      = load_weights()

    # Target weight vectors based on performance
    if brier > 0.22:
        # Poor calibration — trust the market more
        target = {"w_market": 0.65, "w_form": 0.15, "w_h2h": 0.07,
                  "w_home_field": 0.07, "w_position": 0.06}
        trigger = "high_brier"
    elif acc >= 0.70 and brier < 0.15:
        # Excellent — give more room to form and H2H signals
        target = {"w_market": 0.48, "w_form": 0.28, "w_h2h": 0.10,
                  "w_home_field": 0.08, "w_position": 0.06}
        trigger = "excellent_accuracy"
    elif acc >= 0.60:
        # Good — balanced mix
        target = {"w_market": 0.55, "w_form": 0.22, "w_h2h": 0.08,
                  "w_home_field": 0.08, "w_position": 0.07}
        trigger = "good_accuracy"
    else:
        # Below target — lean heavily on market
        target = {"w_market": 0.65, "w_form": 0.15, "w_h2h": 0.06,
                  "w_home_field": 0.08, "w_position": 0.06}
        trigger = "low_accuracy"

    # EMA update
    new_w = {}
    for k in w:
        old_v    = w[k]
        tgt_v    = target.get(k, old_v)
        new_w[k] = round(old_v * (1 - lr) + tgt_v * lr, 4)

    # Re-normalise so weights always sum to 1.0
    total = sum(new_w.values())
    new_w = {k: round(v / total, 4) for k, v in new_w.items()}

    _save_weights(new_w)

    # Log event
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO learning_events
          (event_at, graded_count, accuracy_pct, brier_score,
           w_market, w_form, w_h2h, w_home_field, w_position, trigger)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        graded, round(acc * 100, 1), round(brier, 4),
        new_w.get("w_market", 0.55), new_w.get("w_form", 0.20),
        new_w.get("w_h2h", 0.07),   new_w.get("w_home_field", 0.08),
        new_w.get("w_position", 0.10), trigger,
    ))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Market probability math
# ─────────────────────────────────────────────────────────────────────────────

def remove_margin(lines: MarketData) -> tuple[float, float, float]:
    raw   = [1.0 / lines.home, 1.0 / lines.draw, 1.0 / lines.away]
    total = sum(raw)
    return raw[0] / total, raw[1] / total, raw[2] / total


def remove_margin_2way(price_yes: float, price_no: float) -> tuple[float, float]:
    raw_yes = 1.0 / price_yes
    raw_no  = 1.0 / price_no
    total   = raw_yes + raw_no
    return raw_yes / total, raw_no / total


def provider_margin(lines: MarketData) -> float:
    return round((1.0/lines.home + 1.0/lines.draw + 1.0/lines.away - 1.0) * 100, 2)


def consensus_lines(la: MarketData, lb: Optional[MarketData]) -> tuple[MarketData, float]:
    if lb is None:
        return la, 0.0
    p1  = remove_margin(la)
    p2  = remove_margin(lb)
    avg = [(p1[i] + p2[i]) / 2 for i in range(3)]
    gap = max(abs(p1[i] - p2[i]) for i in range(3))
    h   = 1.0 / avg[0] if avg[0] > 0 else 99.0
    d   = 1.0 / avg[1] if avg[1] > 0 else 99.0
    a   = 1.0 / avg[2] if avg[2] > 0 else 99.0
    return MarketData(round(h, 3), round(d, 3), round(a, 3)), round(gap, 4)


# ─────────────────────────────────────────────────────────────────────────────
# Goal market probabilities (Over 2.5 + BTTS)
# ─────────────────────────────────────────────────────────────────────────────

def goal_market_probs(
    gm_a: Optional[GoalMarketData],
    gm_b: Optional[GoalMarketData],
    home_xg: float,
    away_xg: float,
) -> tuple[float, float]:
    """Return (over_25_prob, btts_prob) — both in [0, 1]."""
    # Over 2.5
    over_list = []
    if gm_a:
        p, _ = remove_margin_2way(gm_a.over_2_5, gm_a.under_2_5)
        over_list.append(p)
    if gm_b:
        p, _ = remove_margin_2way(gm_b.over_2_5, gm_b.under_2_5)
        over_list.append(p)
    if over_list:
        over_25 = round(sum(over_list) / len(over_list), 4)
    else:
        lam   = home_xg + away_xg
        p_le2 = sum(math.exp(-lam) * lam**k / math.factorial(k) for k in range(3))
        over_25 = round(1.0 - p_le2, 4)

    # BTTS
    btts_list = []
    if gm_a:
        p, _ = remove_margin_2way(gm_a.btts_yes, gm_a.btts_no)
        btts_list.append(p)
    if gm_b:
        p, _ = remove_margin_2way(gm_b.btts_yes, gm_b.btts_no)
        btts_list.append(p)
    if btts_list:
        btts = round(sum(btts_list) / len(btts_list), 4)
    else:
        p_h  = 1.0 - math.exp(-home_xg)
        p_a  = 1.0 - math.exp(-away_xg)
        btts = round(p_h * p_a, 4)

    return over_25, btts


# ─────────────────────────────────────────────────────────────────────────────
# Statistical helpers (football-data.org dicts)
# ─────────────────────────────────────────────────────────────────────────────

def recent_form_pts(matches: list[dict], team_id: int, n: int = 5) -> float:
    pts = count = 0
    for m in reversed(matches):
        if count >= n: break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None: continue
        if m["homeTeam"]["id"] == team_id:
            if hg > ag: pts += 3
            elif hg == ag: pts += 1
        elif m["awayTeam"]["id"] == team_id:
            if ag > hg: pts += 3
            elif hg == ag: pts += 1
        count += 1
    return float(pts)


def scoring_averages(matches: list[dict], team_id: int, n: int = 8) -> tuple[float, float]:
    scored = conceded = count = 0
    for m in reversed(matches):
        if count >= n: break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None: continue
        if m["homeTeam"]["id"] == team_id:
            scored += hg; conceded += ag
        elif m["awayTeam"]["id"] == team_id:
            scored += ag; conceded += hg
        else: continue
        count += 1
    if count == 0: return 1.2, 1.0
    return round(scored / count, 2), round(conceded / count, 2)


def h2h_record(h2h_matches: list[dict], home_id: int, away_id: int) -> dict:
    hw = aw = draws = 0
    for m in h2h_matches:
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None: continue
        if m["homeTeam"]["id"] == home_id:
            if hg > ag: hw += 1
            elif hg == ag: draws += 1
            else: aw += 1
        else:
            if ag > hg: hw += 1
            elif hg == ag: draws += 1
            else: aw += 1
    return {"home": hw, "draw": draws, "away": aw, "total": hw + draws + aw}


def form_string(matches: list[dict], team_id: int, n: int = 5) -> str:
    icons = []
    for m in reversed(matches):
        if len(icons) >= n: break
        sc = m.get("score", {}).get("fullTime", {})
        hg, ag = sc.get("home"), sc.get("away")
        if hg is None or ag is None: continue
        if m["homeTeam"]["id"] == team_id:
            icons.append("🟢" if hg > ag else ("🟡" if hg == ag else "🔴"))
        else:
            icons.append("🟢" if ag > hg else ("🟡" if hg == ag else "🔴"))
    return " ".join(icons) or "N/A"


# ─────────────────────────────────────────────────────────────────────────────
# Core 3-way prediction
# ─────────────────────────────────────────────────────────────────────────────

def run_prediction(
    lines_a: Optional[MarketData],
    lines_b: Optional[MarketData],
    home_form:     float = 7.5,
    away_form:     float = 7.5,
    home_scored:   float = 1.2,
    home_conceded: float = 1.1,
    away_scored:   float = 1.2,
    away_conceded: float = 1.1,
    h2h: Optional[dict]  = None,
    home_position: Optional[int] = None,
    away_position: Optional[int] = None,
    weights: Optional[dict]      = None,
) -> dict:
    if weights is None:
        weights = load_weights()

    best = lines_a or lines_b
    if best is None:
        p_mkt = (0.34, 0.33, 0.33); margin = 0.0; gap = 0.0
    elif lines_a and lines_b:
        averaged, gap = consensus_lines(lines_a, lines_b)
        p_mkt = remove_margin(averaged); margin = provider_margin(averaged)
    else:
        p_mkt = remove_margin(best); margin = provider_margin(best); gap = 0.0

    hf_norm = home_form / 15.0; af_norm = away_form / 15.0
    diff = hf_norm - af_norm
    hfa  = weights.get("w_home_field", 0.08)
    fh   = min(max(0.33 + diff * 0.20 + hfa, 0.05), 0.85)
    fx   = min(max(0.34 - abs(diff) * 0.10,  0.05), 0.50)
    fa   = min(max(0.33 - diff * 0.20 - hfa, 0.05), 0.85)
    tot  = fh + fx + fa
    p_form = (fh / tot, fx / tot, fa / tot)

    home_xg = (home_scored + away_conceded) / 2
    away_xg = (away_scored + home_conceded) / 2
    xg_diff = (home_xg - away_xg) / max(home_xg + away_xg, 0.1)
    xh = p_mkt[0] * (1 + xg_diff * 0.10)
    xd = p_mkt[1]
    xa = p_mkt[2] * (1 - xg_diff * 0.10)
    tot2   = xh + xd + xa
    p_xg   = (xh / tot2, xd / tot2, xa / tot2)

    if h2h and h2h.get("total", 0) > 0:
        th = h2h["total"]
        p_h2h = (h2h["home"] / th, h2h["draw"] / th, h2h["away"] / th)
    else:
        p_h2h = p_mkt

    if home_position and away_position:
        pd = (away_position - home_position) / 20.0
        ph = min(max(0.33 + pd * 0.08, 0.05), 0.85)
        pp = 0.34
        pa = min(max(0.33 - pd * 0.08, 0.05), 0.85)
        tot3   = ph + pp + pa
        p_pos  = (ph / tot3, pp / tot3, pa / tot3)
    else:
        p_pos = p_mkt

    wm = weights.get("w_market",     0.55)
    wf = weights.get("w_form",       0.20)
    wh = weights.get("w_h2h",        0.07)
    wp = max(1.0 - wm - wf - wh, 0.0)

    blended     = [wm*p_xg[i] + wf*p_form[i] + wh*p_h2h[i] + wp*p_pos[i] for i in range(3)]
    total_blend = sum(blended)
    hp, dp, ap  = [b / total_blend for b in blended]

    hp = min(max(hp, 0.04), 0.93)
    dp = min(max(dp, 0.04), 0.50)
    ap = min(max(ap, 0.04), 0.93)
    tf = hp + dp + ap
    hp, dp, ap = hp / tf, dp / tf, ap / tf

    return {
        "hp": round(hp, 4), "dp": round(dp, 4), "ap": round(ap, 4),
        "home_xg": round(home_xg, 2), "away_xg": round(away_xg, 2),
        "margin_pct": round(margin, 2), "consensus_gap_pct": round(gap * 100, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pick selection
# ─────────────────────────────────────────────────────────────────────────────

def select_pick(hp: float, dp: float, ap: float) -> tuple[str, str, float]:
    if hp >= 0.50: return "Home Win", "H", hp
    if ap >= 0.50: return "Away Win", "A", ap
    if dp >= 0.38: return "Draw", "D", dp
    hd = hp + dp; da = dp + ap; ha = hp + ap
    if hd >= 0.65: return "Home Win or Draw", "HD", hd
    if da >= 0.65: return "Draw or Away Win", "DA", da
    if ha >= 0.65: return "Home or Away Win", "HA", ha
    best = max(hp, dp, ap)
    if best == hp: return "Home Win", "H", hp
    if best == dp: return "Draw", "D", dp
    return "Away Win", "A", ap


def confidence_tier(c: float) -> str:
    if c >= 0.72: return "HIGH"
    elif c >= 0.58: return "MEDIUM"
    return "LOW"


def confidence_bar(confidence: float, width: int = 20) -> str:
    filled = int(confidence * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {int(confidence * 100)}%"
