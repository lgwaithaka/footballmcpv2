"""
dashboard.py — v2.4
────────────────────
Three-tab Starlette dashboard:
  Tab 1 — Predictions       : fixture cards with 5-outcome probability bars
  Tab 2 — Live Scores       : real-time match scores + prediction accuracy
  Tab 3 — Results Tracker   : historical variance, Brier Score, self-learning weights

Routes
  GET /                          HTML dashboard
  GET /health                    JSON health check
  GET /api/fixtures              JSON all fixture predictions
  GET /api/fixture/{id}          JSON single fixture prediction
  GET /api/scores                JSON live + finished scores with prediction accuracy
  GET /api/history               JSON graded predictions with variance data
  GET /api/learning              JSON learning events + current weights
  GET /api/stats                 JSON model accuracy stats
"""

import os
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from analytics_engine import (
    MarketData, GoalMarketData, AnalyticsResult,
    init_db, persist_result, accuracy_report,
    variance_history, learning_report, load_weights,
    goal_market_probs, run_prediction, select_pick, confidence_tier,
)
from schedule_data import all_fixtures, fixture_by_id, ScheduledMatch
from live_schedule import fetch_live_scores


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _market_data(f: ScheduledMatch):
    la   = MarketData(f.lines_a.home, f.lines_a.draw, f.lines_a.away) if f.lines_a else None
    lb   = MarketData(f.lines_b.home, f.lines_b.draw, f.lines_b.away) if f.lines_b else None
    gm_a = GoalMarketData(f.lines_a.over_2_5, f.lines_a.under_2_5,
                          f.lines_a.both_score_yes, f.lines_a.both_score_no) if f.lines_a else None
    gm_b = GoalMarketData(f.lines_b.over_2_5, f.lines_b.under_2_5,
                          f.lines_b.both_score_yes, f.lines_b.both_score_no) if f.lines_b else None
    return la, lb, gm_a, gm_b


def _predict_fixture(f: ScheduledMatch) -> dict | None:
    la, lb, gm_a, gm_b = _market_data(f)
    weights             = load_weights()
    r                   = run_prediction(la, lb, weights=weights)
    hp, dp, ap          = r["hp"], r["dp"], r["ap"]
    over_25, btts       = goal_market_probs(gm_a, gm_b, r["home_xg"], r["away_xg"])
    pick_label, pick_code, conf = select_pick(hp, dp, ap)
    tier                = confidence_tier(conf)
    fid                 = str(f.provider_b_id or f.provider_a_id)

    res = AnalyticsResult(
        fixture_id=fid, home_team=f.home_team, away_team=f.away_team,
        league=f.league, country=f.country, kickoff=f.kickoff, date=f.date,
        home_prob=hp, draw_prob=dp, away_prob=ap,
        over_25_prob=over_25, btts_prob=btts,
        recommended_pick=pick_label, pick_code=pick_code,
        confidence=conf, confidence_pct=int(conf * 100), confidence_label=tier,
        provider_margin_pct=r["margin_pct"], consensus_gap_pct=r["consensus_gap_pct"],
        home_form_pts=7.5, away_form_pts=7.5,
        home_xg=r["home_xg"], away_xg=r["away_xg"],
        h2h_home=0, h2h_draw=0, h2h_away=0,
    )
    persist_result(res)

    return {
        "id": fid, "home": f.home_team, "away": f.away_team,
        "league": f.league, "country": f.country,
        "kickoff": f.kickoff, "date": f.date,
        "home_prob": round(hp * 100, 1), "draw_prob": round(dp * 100, 1),
        "away_prob": round(ap * 100, 1), "over_25_prob": round(over_25 * 100, 1),
        "btts_prob": round(btts * 100, 1),
        "pick": pick_label, "pick_code": pick_code,
        "confidence": int(conf * 100), "tier": tier,
        "home_xg": r["home_xg"], "away_xg": r["away_xg"],
        "margin_pct": r["margin_pct"],
        "has_two_sources": la is not None and lb is not None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Route handlers
# ─────────────────────────────────────────────────────────────────────────────

async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "Football Analytics MCP",
                         "ts": datetime.now(timezone.utc).isoformat()})


async def api_fixtures(request: Request) -> JSONResponse:
    results = []
    for f in all_fixtures():
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
        return JSONResponse({"error": "No market data"}, status_code=404)
    return JSONResponse(d)


async def api_scores(request: Request) -> JSONResponse:
    """Live + finished scores with prediction accuracy cross-reference."""
    # Get current stored predictions to cross-reference
    predictions = []
    for f in all_fixtures():
        d = _predict_fixture(f)
        if d:
            predictions.append(d)
    scores = await fetch_live_scores(predictions)
    live_count     = sum(1 for s in scores if s["status"] in ("IN_PLAY", "PAUSED", "HALFTIME"))
    finished_count = sum(1 for s in scores if s["status"] == "FINISHED")
    correct_count  = sum(1 for s in scores if s.get("prediction_correct") is True)
    wrong_count    = sum(1 for s in scores if s.get("prediction_correct") is False)
    return JSONResponse({
        "live":     live_count,
        "finished": finished_count,
        "correct":  correct_count,
        "wrong":    wrong_count,
        "scores":   scores,
        "ts":       datetime.now(timezone.utc).isoformat(),
    })


async def api_history(request: Request) -> JSONResponse:
    limit   = int(request.query_params.get("limit", 100))
    history = variance_history(limit)
    stats   = accuracy_report()
    avg_var = round(sum(r["variance_pct"] for r in history) / len(history), 1) if history else 0
    return JSONResponse({
        "count": len(history), "accuracy_pct": stats["accuracy_pct"],
        "avg_brier": stats["avg_brier"], "avg_variance_pct": avg_var,
        "records": history,
    })


async def api_learning(request: Request) -> JSONResponse:
    return JSONResponse(learning_report())


async def api_stats(request: Request) -> JSONResponse:
    return JSONResponse({"accuracy": accuracy_report(), "weights": load_weights()})


async def dashboard(request: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard HTML — three tabs
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Football Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=JetBrains+Mono:wght@300;400;600&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#06080C;--surface:#0D1117;--card:#111823;--card2:#151F2B;
  --border:rgba(0,255,135,.12);--border2:rgba(0,255,135,.28);
  --green:#00FF87;--green2:#00CC6A;--blue:#0094FF;--amber:#FFB800;
  --coral:#FF5252;--purple:#C084FC;--text:#E2EAF4;--muted:#5A6880;--dim:#2A3548;
  --live:#FF4F4F;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'DM Sans',sans-serif;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,255,135,.03)1px,transparent 1px),linear-gradient(90deg,rgba(0,255,135,.03)1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}

/* Header */
header{position:relative;z-index:10;padding:22px 40px;border-bottom:1px solid var(--border);background:linear-gradient(180deg,rgba(0,255,135,.05)0%,transparent 100%);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.logo{display:flex;align-items:center;gap:12px}
.logo-icon{width:42px;height:42px;background:var(--green);border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px;box-shadow:0 0 20px rgba(0,255,135,.4)}
.logo-text{font-family:'Orbitron',sans-serif;font-size:17px;font-weight:700;letter-spacing:.05em}
.logo-sub{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--green);letter-spacing:.15em;text-transform:uppercase;margin-top:2px}
.hstats{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.stat-pill{background:var(--card);border:1px solid var(--border);border-radius:7px;padding:7px 13px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);display:flex;align-items:center;gap:7px}
.stat-pill span{color:var(--green);font-weight:600}
.live-indicator{display:flex;align-items:center;gap:6px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--live);font-weight:600}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--live);animation:blink 1.2s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

/* Tabs */
.tabs{position:relative;z-index:10;padding:0 40px;display:flex;gap:0;border-bottom:1px solid var(--border)}
.tab-btn{padding:14px 26px;background:transparent;border:none;border-bottom:2px solid transparent;color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:11px;letter-spacing:.08em;text-transform:uppercase;cursor:pointer;transition:all .2s;margin-bottom:-1px;display:flex;align-items:center;gap:7px}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--green);border-bottom-color:var(--green)}
.tab-badge{background:var(--live);color:#fff;border-radius:10px;padding:1px 7px;font-size:9px;font-weight:700}
.tab-badge.green{background:var(--green2);color:#000}
.tab-pane{display:none}.tab-pane.active{display:block}

/* ── SHARED ── */
.loading{position:relative;z-index:10;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 40px;gap:16px}
.spinner{width:44px;height:44px;border:3px solid var(--dim);border-top-color:var(--green);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-text{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
.empty{position:relative;z-index:10;text-align:center;padding:60px 40px;color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:12px;line-height:2}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--dim);border-radius:3px}

/* ── TAB 1: PREDICTIONS ── */
.filters{position:relative;z-index:10;padding:14px 40px;display:flex;gap:9px;align-items:center;flex-wrap:wrap;border-bottom:1px solid rgba(255,255,255,.04)}
.filter-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-right:3px}
.filter-btn{background:var(--card);border:1px solid var(--dim);color:var(--muted);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;transition:all .18s;white-space:nowrap}
.filter-btn:hover{border-color:var(--border2);color:var(--text)}
.filter-btn.active{background:rgba(0,255,135,.1);border-color:var(--green);color:var(--green)}
.search-box{background:var(--card);border:1px solid var(--dim);color:var(--text);padding:6px 12px;border-radius:6px;font-size:13px;outline:none;width:190px;margin-left:auto;transition:border-color .18s}
.search-box:focus{border-color:var(--border2)}
.search-box::placeholder{color:var(--muted)}
.sort-bar{position:relative;z-index:10;padding:9px 40px;display:flex;gap:5px;align-items:center;border-bottom:1px solid rgba(255,255,255,.04)}
.sort-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase}
.sort-btn{background:transparent;border:none;color:var(--muted);padding:3px 8px;border-radius:4px;cursor:pointer;font-size:11px;transition:all .15s}
.sort-btn:hover,.sort-btn.active{color:var(--green);background:rgba(0,255,135,.07)}
.count-badge{margin-left:auto;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)}
.count-badge span{color:var(--text);font-weight:600}
main{position:relative;z-index:10;padding:22px 40px;display:grid;grid-template-columns:repeat(auto-fill,minmax(365px,1fr));gap:17px}
.card{background:var(--card);border:1px solid var(--border);border-radius:13px;overflow:hidden;transition:transform .2s,border-color .2s,box-shadow .2s}
.card:hover{transform:translateY(-3px);border-color:var(--border2);box-shadow:0 12px 40px rgba(0,0,0,.5)}
.card-header{padding:13px 17px 11px;background:var(--card2);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.card-meta{display:flex;flex-direction:column;gap:2px;min-width:0}
.league-tag{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--green);letter-spacing:.12em;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.country-tag{font-size:10px;color:var(--muted)}
.kickoff-tag{font-family:'Orbitron',sans-serif;font-size:11px;color:var(--amber);font-weight:700;white-space:nowrap;flex-shrink:0}
.teams{padding:15px 17px 11px;display:flex;align-items:center}
.team{flex:1;display:flex;flex-direction:column;gap:2px}
.team.away{align-items:flex-end;text-align:right}
.team-name{font-family:'Orbitron',sans-serif;font-size:12px;font-weight:700;color:var(--text);line-height:1.3;word-break:break-word}
.team-xg{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted)}
.vs-block{width:38px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;gap:2px}
.vs-text{font-family:'Orbitron',sans-serif;font-size:8px;color:var(--dim);letter-spacing:.1em}
.vs-dot{width:5px;height:5px;border-radius:50%;background:var(--green);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.3;transform:scale(1)}50%{opacity:.9;transform:scale(1.4)}}
.confidence-row{padding:0 17px 11px;display:flex;align-items:center;gap:7px}
.pick-badge{background:rgba(0,255,135,.1);border:1px solid rgba(0,255,135,.3);color:var(--green);font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:4px 9px;border-radius:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:155px}
.tier-badge{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;padding:3px 7px;border-radius:4px;white-space:nowrap}
.tier-HIGH{background:rgba(0,255,135,.15);color:var(--green);border:1px solid rgba(0,255,135,.3)}
.tier-MEDIUM{background:rgba(255,184,0,.12);color:var(--amber);border:1px solid rgba(255,184,0,.3)}
.tier-LOW{background:rgba(255,82,82,.1);color:var(--coral);border:1px solid rgba(255,82,82,.25)}
.conf-pct{margin-left:auto;font-family:'Orbitron',monospace;font-size:18px;font-weight:900;color:var(--text);line-height:1}
.conf-pct small{font-size:10px;color:var(--muted);font-weight:400;font-family:'DM Sans',sans-serif}
.outcomes{padding:0 17px 17px;display:flex;flex-direction:column;gap:7px}
.outcome-row{display:flex;align-items:center;gap:8px}
.outcome-label{font-family:'JetBrains Mono',monospace;font-size:8px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;width:66px;flex-shrink:0}
.bar-track{flex:1;height:6px;background:rgba(255,255,255,.05);border-radius:3px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px;width:0%;transition:width .9s cubic-bezier(.16,1,.3,1)}
.bar-fill.home{background:linear-gradient(90deg,#00CC6A,#00FF87)}
.bar-fill.draw{background:linear-gradient(90deg,#CC9200,#FFB800)}
.bar-fill.away{background:linear-gradient(90deg,#CC3030,#FF5252)}
.bar-fill.over{background:linear-gradient(90deg,#0070CC,#0094FF)}
.bar-fill.btts{background:linear-gradient(90deg,#9B60E0,#C084FC)}
.bar-pct{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;color:var(--text);width:32px;text-align:right;flex-shrink:0}
.card-footer{padding:8px 17px;border-top:1px solid var(--border);display:flex;gap:12px;align-items:center}
.footer-stat{font-family:'JetBrains Mono',monospace;font-size:8px;color:var(--muted);display:flex;align-items:center;gap:4px}
.footer-stat .val{color:var(--text);font-size:9px}
.sources-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
/* Team outcome highlight colours */
.team-name.tn-win{color:var(--green)}
.team-name.tn-loss{color:var(--coral)}
.team-name.tn-draw{color:var(--blue)}
.team-xg.tn-win{color:rgba(0,255,135,.6)}
.team-xg.tn-loss{color:rgba(255,82,82,.5)}
.team-xg.tn-draw{color:rgba(0,148,255,.5)}
/* Venue badge on pick row */
.venue-badge{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:600;padding:3px 8px;border-radius:4px;white-space:nowrap;flex-shrink:0;letter-spacing:.05em}
.venue-home{background:rgba(0,255,135,.1);color:var(--green);border:1px solid rgba(0,255,135,.2)}
.venue-away{background:rgba(255,82,82,.1);color:var(--coral);border:1px solid rgba(255,82,82,.2)}
.venue-draw{background:rgba(0,148,255,.1);color:var(--blue);border:1px solid rgba(0,148,255,.2)}
.venue-combo{background:rgba(255,184,0,.1);color:var(--amber);border:1px solid rgba(255,184,0,.2)}

/* ── TAB 2: LIVE SCORES ── */
.scores-wrap{position:relative;z-index:10;padding:26px 40px}
.scores-header{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;margin-bottom:22px}
.scores-title{font-family:'Orbitron',sans-serif;font-size:20px;font-weight:700}
.refresh-info{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)}
.refresh-info span{color:var(--green)}
.score-summary{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.sc-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 20px;flex:1;min-width:120px}
.sc-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px}
.sc-val{font-family:'Orbitron',sans-serif;font-size:26px;font-weight:900;line-height:1}
.sc-val.live-col{color:var(--live)}
.sc-val.green-col{color:var(--green)}
.sc-val.coral-col{color:var(--coral)}
.sc-val.amber-col{color:var(--amber)}
/* Score cards grid */
.score-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
.score-card{background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;transition:border-color .2s}
.score-card.live-card{border-color:rgba(255,79,79,.35);box-shadow:0 0 18px rgba(255,79,79,.08)}
.score-card.correct-card{border-color:rgba(0,255,135,.25)}
.score-card.wrong-card{border-color:rgba(255,82,82,.2)}
.sc-header{padding:10px 16px 9px;background:var(--card2);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:10px}
.sc-league{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--green);letter-spacing:.1em;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc-kick{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--amber);white-space:nowrap}
/* Status pill */
.status-pill{font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;padding:3px 9px;border-radius:10px;white-space:nowrap;flex-shrink:0}
.pill-live{background:rgba(255,79,79,.2);color:var(--live);border:1px solid rgba(255,79,79,.4);animation:blink-border 1.5s ease-in-out infinite}
@keyframes blink-border{0%,100%{border-color:rgba(255,79,79,.4)}50%{border-color:rgba(255,79,79,.9)}}
.pill-ht{background:rgba(255,184,0,.15);color:var(--amber);border:1px solid rgba(255,184,0,.3)}
.pill-fin{background:rgba(90,104,128,.15);color:var(--muted);border:1px solid rgba(90,104,128,.25)}
/* Scoreline */
.scoreline{padding:16px;display:flex;align-items:center;gap:0}
.sc-team{flex:1;display:flex;flex-direction:column;gap:3px}
.sc-team.away-t{align-items:flex-end;text-align:right}
.sc-team-name{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;color:var(--text);line-height:1.2;word-break:break-word}
.sc-team-name.winner{color:var(--green)}
.score-center{width:80px;flex-shrink:0;display:flex;flex-direction:column;align-items:center;gap:3px}
.score-box{font-family:'Orbitron',sans-serif;font-size:28px;font-weight:900;color:var(--text);letter-spacing:.05em;line-height:1}
.score-box.live-score{color:var(--live)}
.score-sep{font-family:'Orbitron',sans-serif;font-size:18px;color:var(--dim);font-weight:700}
.minute-badge{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--live);font-weight:600}
.score-tbd{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--dim);letter-spacing:.05em}
/* Prediction strip */
.pred-strip{padding:0 16px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.pred-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;flex-shrink:0}
.pred-pick{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:3px 9px;border-radius:4px;white-space:nowrap}
.pred-pick.pred-correct{background:rgba(0,255,135,.15);color:var(--green);border:1px solid rgba(0,255,135,.3)}
.pred-pick.pred-wrong{background:rgba(255,82,82,.1);color:var(--coral);border:1px solid rgba(255,82,82,.25)}
.pred-pick.pred-pending{background:rgba(0,148,255,.1);color:var(--blue);border:1px solid rgba(0,148,255,.2)}
.pred-pick.pred-none{background:rgba(90,104,128,.1);color:var(--muted);border:1px solid rgba(90,104,128,.2)}
.pred-conf{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)}
.accuracy-chip{margin-left:auto;font-family:'Orbitron',sans-serif;font-size:11px;font-weight:900;padding:3px 10px;border-radius:5px}
.chip-correct{background:rgba(0,255,135,.15);color:var(--green)}
.chip-wrong{background:rgba(255,82,82,.12);color:var(--coral)}
.chip-play{background:rgba(0,148,255,.1);color:var(--blue)}
/* Empty state for scores */
.no-scores{text-align:center;padding:60px 40px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);line-height:2}
.no-scores a{color:var(--green);text-decoration:none}

/* ── TAB 3: RESULTS TRACKER ── */
.tracker-wrap{position:relative;z-index:10;padding:26px 40px}
.tracker-title{font-family:'Orbitron',sans-serif;font-size:20px;font-weight:700;margin-bottom:20px}
.summary-cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:26px}
.sum-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 20px;min-width:130px;flex:1}
.sum-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:5px}
.sum-value{font-family:'Orbitron',sans-serif;font-size:24px;font-weight:900;line-height:1}
.sum-value.green{color:var(--green)}.sum-value.amber{color:var(--amber)}.sum-value.coral{color:var(--coral)}.sum-value.blue{color:var(--blue)}
.sum-sub{font-size:10px;color:var(--muted);margin-top:3px}
.variance-section,.learning-section,.results-table-wrap{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:20px}
.section-title{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;margin-bottom:12px}
#varianceChart{width:100%;height:140px;display:flex;align-items:flex-end;gap:3px;overflow:hidden}
.v-bar-wrap{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;min-width:0}
.v-bar{width:100%;border-radius:3px 3px 0 0;transition:height .6s cubic-bezier(.16,1,.3,1)}
.v-bar.correct{background:var(--green2);opacity:.85}.v-bar.wrong{background:var(--coral);opacity:.8}
.v-bar-label{font-family:'JetBrains Mono',monospace;font-size:7px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;text-align:center}
.weight-bars{display:flex;flex-direction:column;gap:8px;margin-top:10px}
.w-row{display:flex;align-items:center;gap:10px}
.w-label{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;width:128px;flex-shrink:0}
.w-track{flex:1;height:7px;background:rgba(255,255,255,.05);border-radius:3px;overflow:hidden}
.w-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--blue),var(--green));width:0%;transition:width .8s cubic-bezier(.16,1,.3,1)}
.w-pct{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--text);width:36px;text-align:right;flex-shrink:0}
.results-table{width:100%;border-collapse:collapse}
.results-table th{background:var(--card2);padding:9px 12px;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;text-align:left;border-bottom:1px solid var(--border)}
.results-table td{padding:9px 12px;font-size:12px;border-bottom:1px solid rgba(255,255,255,.04);vertical-align:middle}
.results-table tr:last-child td{border-bottom:none}
.results-table tr:hover td{background:rgba(255,255,255,.02)}
.match-cell{font-weight:500;color:var(--text)}.match-league{font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);margin-top:2px}
.pick-cell{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:600;padding:2px 7px;border-radius:4px;background:rgba(0,255,135,.08);color:var(--green);display:inline-block}
.actual-H{color:var(--green)}.actual-D{color:var(--amber)}.actual-A{color:var(--coral)}
.correct-chip{font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;display:inline-block}
.chip-yes{background:rgba(0,255,135,.15);color:var(--green)}.chip-no{background:rgba(255,82,82,.12);color:var(--coral)}
.var-low{color:var(--green)}.var-mid{color:var(--amber)}.var-high{color:var(--coral)}
.brier-cell{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted)}

/* Toast */
.toast{position:fixed;bottom:20px;right:20px;background:#1A0808;border:1px solid rgba(255,82,82,.4);color:var(--coral);padding:11px 16px;border-radius:9px;font-family:'JetBrains Mono',monospace;font-size:11px;z-index:1000;transform:translateY(80px);opacity:0;transition:all .3s}
.toast.show{transform:translateY(0);opacity:1}
@media(max-width:640px){header,div.tabs,.filters,.sort-bar,.scores-wrap,.tracker-wrap{padding-left:20px;padding-right:20px}main{padding:14px 20px;grid-template-columns:1fr}.search-box{width:100%;margin-left:0}.score-grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">⚽</div>
    <div>
      <div class="logo-text">FOOTBALL ANALYTICS</div>
      <div class="logo-sub">Statistical Prediction Engine · v2.4</div>
    </div>
  </div>
  <div class="hstats">
    <div class="live-indicator" id="liveIndicator" style="display:none">
      <div class="live-dot"></div>LIVE
    </div>
    <div class="stat-pill">FIXTURES <span id="totalCount">—</span></div>
    <div class="stat-pill">ACCURACY <span id="modelAccuracy">—</span></div>
    <div class="stat-pill">BRIER <span id="brierScore">—</span></div>
    <div class="stat-pill">UPDATED <span id="lastUpdate">—</span></div>
  </div>
</header>

<div class="tabs">
  <button class="tab-btn active" data-tab="predictions">⚡ Predictions</button>
  <button class="tab-btn" data-tab="scores">🔴 Live Scores <span class="tab-badge" id="liveCountBadge" style="display:none">0</span></button>
  <button class="tab-btn" data-tab="tracker">📊 Results Tracker</button>
</div>

<!-- ── TAB 1: PREDICTIONS ── -->
<div class="tab-pane active" id="tab-predictions">
  <div class="filters">
    <span class="filter-label">League</span>
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="England">🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL</button>
    <button class="filter-btn" data-filter="Champions League">🌍 UCL</button>
    <button class="filter-btn" data-filter="Europa League">🌍 UEL</button>
    <button class="filter-btn" data-filter="Italy">🇮🇹 Serie A</button>
    <button class="filter-btn" data-filter="Spain">🇪🇸 La Liga</button>
    <button class="filter-btn" data-filter="Germany">🇩🇪 Bundesliga</button>
    <button class="filter-btn" data-filter="France">🇫🇷 France</button>
    <button class="filter-btn" data-filter="Brazil">🇧🇷 Brazil</button>
    <button class="filter-btn" data-filter="Egypt">🇪🇬 Egypt</button>
    <button class="filter-btn" data-filter="USA">🇺🇸 MLS</button>
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
    <div class="loading"><div class="spinner"></div><div class="loading-text">Loading fixtures…</div></div>
  </main>
</div>

<!-- ── TAB 2: LIVE SCORES ── -->
<div class="tab-pane" id="tab-scores">
  <div class="scores-wrap">
    <div class="scores-header">
      <div class="scores-title">Live Scores & Results</div>
      <div class="refresh-info">Auto-refresh every <span>60s</span> · EPL + UCL</div>
    </div>

    <div class="score-summary">
      <div class="sc-card"><div class="sc-label">Live Now</div><div class="sc-val live-col" id="scLive">—</div></div>
      <div class="sc-card"><div class="sc-label">Finished</div><div class="sc-val amber-col" id="scFin">—</div></div>
      <div class="sc-card"><div class="sc-label">Correct Picks</div><div class="sc-val green-col" id="scCorr">—</div></div>
      <div class="sc-card"><div class="sc-label">Wrong Picks</div><div class="sc-val coral-col" id="scWrong">—</div></div>
    </div>

    <div class="score-grid" id="scoreGrid">
      <div class="no-scores">
        Loading scores…<br>
        <div class="spinner" style="margin:16px auto 0"></div>
      </div>
    </div>
  </div>
</div>

<!-- ── TAB 3: RESULTS TRACKER ── -->
<div class="tab-pane" id="tab-tracker">
  <div class="tracker-wrap">
    <div class="tracker-title">Results vs Predictions</div>

    <div class="summary-cards">
      <div class="sum-card"><div class="sum-label">Accuracy</div><div class="sum-value green" id="sumAccuracy">—</div><div class="sum-sub">predictions correct</div></div>
      <div class="sum-card"><div class="sum-label">Avg Variance</div><div class="sum-value amber" id="sumVariance">—</div><div class="sum-sub">predicted vs outcome</div></div>
      <div class="sum-card"><div class="sum-label">Brier Score</div><div class="sum-value blue" id="sumBrier">—</div><div class="sum-sub">lower = better</div></div>
      <div class="sum-card"><div class="sum-label">Total Graded</div><div class="sum-value" id="sumTotal">—</div><div class="sum-sub">outcomes recorded</div></div>
    </div>

    <div class="variance-section">
      <div class="section-title">Confidence vs Outcome (last 30)</div>
      <div id="varianceChart"><div class="loading-text" style="margin:auto">No graded results yet</div></div>
    </div>

    <div class="learning-section">
      <div class="section-title">Self-Learning — Current Model Weights</div>
      <div class="weight-bars" id="weightBars"><div class="loading-text">Loading…</div></div>
      <div style="margin-top:12px;font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);line-height:1.8" id="learningMeta"></div>
    </div>

    <div class="results-table-wrap">
      <table class="results-table">
        <thead>
          <tr>
            <th>Match</th><th>Pick</th><th>Conf%</th>
            <th>Actual</th><th>Correct</th><th>Pred%</th>
            <th>Variance</th><th>Brier</th>
          </tr>
        </thead>
        <tbody id="resultsBody">
          <tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px;font-family:'JetBrains Mono',monospace;font-size:11px">Record outcomes via Claude to populate this table</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ─────────────────────────────────────────────────────────────────
let allFixtures=[], currentFilter='all', currentSort='confidence', searchQuery='';
let scoresInterval=null, scoresLoaded=false;

// ── Tabs ──────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
    btn.classList.add('active');
    const pane=document.getElementById('tab-'+btn.dataset.tab);
    if(pane) pane.classList.add('active');
    if(btn.dataset.tab==='scores'){
      loadScores();
      if(!scoresInterval) scoresInterval=setInterval(loadScores,60000);
    } else {
      if(scoresInterval){clearInterval(scoresInterval);scoresInterval=null;}
    }
    if(btn.dataset.tab==='tracker') loadTracker();
  });
});

// ── Predictions tab ───────────────────────────────────────────────────────
async function loadData(){
  try{
    const [fr,sr]=await Promise.all([fetch('/api/fixtures'),fetch('/api/stats')]);
    const fd=await fr.json(), sd=await sr.json();
    allFixtures=fd.fixtures||[];
    document.getElementById('totalCount').textContent=allFixtures.length;
    const acc=sd.accuracy?.accuracy_pct;
    document.getElementById('modelAccuracy').textContent=acc?acc+'%':'New';
    const br=sd.accuracy?.avg_brier;
    document.getElementById('brierScore').textContent=br||0;
    document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
    renderGrid();
  }catch(e){showToast('Failed to load fixtures. Retrying…');setTimeout(loadData,6000)}
}

function getFiltered(){
  let d=[...allFixtures];
  if(currentFilter!=='all') d=d.filter(f=>
    f.country===currentFilter||
    f.league.includes(currentFilter)||
    f.country.includes(currentFilter)||
    f.league.toLowerCase().includes(currentFilter.toLowerCase())
  );
  if(searchQuery){const q=searchQuery.toLowerCase();d=d.filter(f=>f.home.toLowerCase().includes(q)||f.away.toLowerCase().includes(q)||f.league.toLowerCase().includes(q))}
  d.sort((a,b)=>{
    if(currentSort==='confidence') return b.confidence-a.confidence;
    if(currentSort==='kickoff')    return a.kickoff.localeCompare(b.kickoff);
    if(currentSort==='home')       return b.home_prob-a.home_prob;
    if(currentSort==='over')       return b.over_25_prob-a.over_25_prob;
    return 0;
  });
  return d;
}

function renderGrid(){
  const grid=document.getElementById('grid'), data=getFiltered();
  document.getElementById('shownCount').textContent=data.length;
  if(!data.length){grid.innerHTML='<div class="empty">No fixtures match your filter.</div>';return}
  grid.innerHTML=data.map(cardHTML).join('');
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    document.querySelectorAll('.bar-fill').forEach(el=>el.style.width=el.dataset.pct+'%');
  }));
}

function teamColour(pickCode, side){
  // Returns CSS class for team name based on expected outcome
  // side: 'home' or 'away'
  const code = pickCode || '';
  if(code==='H')  return side==='home'?'tn-win':'tn-loss';
  if(code==='A')  return side==='home'?'tn-loss':'tn-win';
  if(code==='D')  return 'tn-draw';
  if(code==='HD') return side==='home'?'tn-win':'tn-loss';   // home won't lose
  if(code==='DA') return side==='away'?'tn-win':'tn-loss';   // away won't lose
  if(code==='HA') return 'tn-win';  // both can win (draw unlikely)
  return '';
}

function venueBadge(pickCode){
  // Shows which team context the pick favours
  const code = pickCode || '';
  if(code==='H')  return `<div class="venue-badge venue-home">🏠 HOME WIN</div>`;
  if(code==='A')  return `<div class="venue-badge venue-away">✈️ AWAY WIN</div>`;
  if(code==='D')  return `<div class="venue-badge venue-draw">🤝 DRAW</div>`;
  if(code==='HD') return `<div class="venue-badge venue-home">🏠 HOME / DRAW</div>`;
  if(code==='DA') return `<div class="venue-badge venue-away">✈️ AWAY / DRAW</div>`;
  if(code==='HA') return `<div class="venue-badge venue-combo">⚡ HOME / AWAY</div>`;
  return '';
}

function cardHTML(f){
  const dot=f.has_two_sources
    ?`<div class="sources-dot" style="background:var(--green)" title="Two sources"></div>`
    :`<div class="sources-dot" style="background:var(--muted)" title="One source"></div>`;

  const homeCls = teamColour(f.pick_code, 'home');
  const awayCls = teamColour(f.pick_code, 'away');

  return`<div class="card">
    <div class="card-header">
      <div class="card-meta"><div class="league-tag">${f.league}</div><div class="country-tag">${f.country}</div></div>
      <div class="kickoff-tag">${f.kickoff}</div>
    </div>
    <div class="teams">
      <div class="team home">
        <div class="team-name ${homeCls}">${f.home}</div>
        <div class="team-xg ${homeCls}">xG ${f.home_xg}</div>
      </div>
      <div class="vs-block"><div class="vs-text">VS</div><div class="vs-dot"></div></div>
      <div class="team away">
        <div class="team-name ${awayCls}">${f.away}</div>
        <div class="team-xg ${awayCls}">xG ${f.away_xg}</div>
      </div>
    </div>
    <div class="confidence-row">
      <div class="pick-badge" title="${f.pick}">${f.pick}</div>
      ${venueBadge(f.pick_code)}
      <div class="conf-pct" style="margin-left:auto">${f.confidence}<small>%</small></div>
    </div>
    <div style="padding:0 17px 4px;display:flex;gap:6px;align-items:center">
      <div class="tier-badge tier-${f.tier}">${f.tier}</div>
    </div>
    <div class="outcomes">
      ${oBar('HOME WIN',f.home_prob,'home')}${oBar('DRAW',f.draw_prob,'draw')}
      ${oBar('AWAY WIN',f.away_prob,'away')}${oBar('OVER 2.5',f.over_25_prob,'over')}
      ${oBar('BTTS',f.btts_prob,'btts')}
    </div>
    <div class="card-footer">
      ${dot}
      <div class="footer-stat">MARGIN <span class="val">${f.margin_pct}%</span></div>
      <div class="footer-stat">DATE <span class="val">${f.date}</span></div>
      <div class="footer-stat" style="margin-left:auto">ID <span class="val">#${f.id}</span></div>
    </div>
  </div>`;
}

function oBar(label,pct,cls){
  const c=Math.min(Math.max(pct,0),100);
  return`<div class="outcome-row">
    <div class="outcome-label">${label}</div>
    <div class="bar-track"><div class="bar-fill ${cls}" data-pct="${c}" style="width:0%"></div></div>
    <div class="bar-pct">${pct.toFixed(0)}%</div>
  </div>`;
}

document.querySelectorAll('.filter-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter=btn.dataset.filter;
    renderGrid();
  });
});
document.querySelectorAll('.sort-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.sort-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    currentSort=btn.dataset.sort;
    renderGrid();
  });
});
document.getElementById('searchBox').addEventListener('input',e=>{searchQuery=e.target.value.trim();renderGrid();});

// ── Live Scores tab ───────────────────────────────────────────────────────
async function loadScores(){
  try{
    const r=await fetch('/api/scores');
    const d=await r.json();

    // Update summary
    document.getElementById('scLive').textContent=d.live;
    document.getElementById('scFin').textContent=d.finished;
    document.getElementById('scCorr').textContent=d.correct;
    document.getElementById('scWrong').textContent=d.wrong;

    // Live indicator in header
    const liveBadge=document.getElementById('liveCountBadge');
    const liveInd=document.getElementById('liveIndicator');
    if(d.live>0){
      liveBadge.textContent=d.live;
      liveBadge.style.display='inline-block';
      liveInd.style.display='flex';
    } else {
      liveBadge.style.display='none';
      liveInd.style.display='none';
    }

    const grid=document.getElementById('scoreGrid');
    if(!d.scores||d.scores.length===0){
      const hasKey=!!('""')||true;
      grid.innerHTML=`<div class="no-scores">
        No live or recent matches found for EPL / UCL.<br>
        Scores appear here when matches are in progress or completed today.<br>
        <small>API key required · Auto-refreshes every 60 seconds</small>
      </div>`;
      return;
    }

    grid.innerHTML=d.scores.map(scoreCardHTML).join('');

  }catch(e){
    document.getElementById('scoreGrid').innerHTML=`<div class="no-scores">
      Failed to load scores.<br>
      <small>Check that FOOTBALL_DATA_API_KEY is set in Render.</small>
    </div>`;
  }
}

function scoreCardHTML(s){
  const isLive    = ['IN_PLAY','PAUSED','HALFTIME'].includes(s.status);
  const isFinished= s.status==='FINISHED';
  const hasScore  = s.home_score!==null && s.home_score!==undefined;

  // Card class
  let cardCls='score-card';
  if(isLive) cardCls+=' live-card';
  else if(s.prediction_status==='CORRECT') cardCls+=' correct-card';
  else if(s.prediction_status==='WRONG')   cardCls+=' wrong-card';

  // Status pill
  let pillCls='status-pill', pillTxt=s.status;
  if(isLive)             { pillCls+=' pill-live'; pillTxt=s.minute?`LIVE ${s.minute}`:'LIVE'; }
  else if(s.status==='HALFTIME')  { pillCls+=' pill-ht';   pillTxt='HALF TIME'; }
  else if(isFinished)    { pillCls+=' pill-fin'; pillTxt='FULL TIME'; }

  // Winner styling
  let homeWin=false, awayWin=false;
  if(hasScore && isFinished){
    homeWin=s.home_score>s.away_score;
    awayWin=s.away_score>s.home_score;
  }

  // Score display
  let scoreHTML='';
  if(hasScore){
    const scoreCol=isLive?'live-score':'';
    scoreHTML=`
      <div class="score-box ${scoreCol}">${s.home_score}</div>
      <div class="score-sep">-</div>
      <div class="score-box ${scoreCol}">${s.away_score}</div>`;
  } else {
    scoreHTML=`<div class="score-tbd">vs</div>`;
  }

  // Prediction strip
  let predHTML='';
  if(s.predicted_pick){
    let pickCls='pred-pick';
    if(s.prediction_status==='CORRECT')     pickCls+=' pred-correct';
    else if(s.prediction_status==='WRONG')  pickCls+=' pred-wrong';
    else if(s.prediction_status==='IN_PLAY')pickCls+=' pred-pending';
    else                                     pickCls+=' pred-pending';

    const chipMap={
      'CORRECT':  '<div class="accuracy-chip chip-correct">✓ CORRECT</div>',
      'WRONG':    '<div class="accuracy-chip chip-wrong">✗ WRONG</div>',
      'IN_PLAY':  '<div class="accuracy-chip chip-play">⏱ IN PLAY</div>',
      'PENDING':  '<div class="accuracy-chip chip-play">⏳ PENDING</div>',
    };
    const chip=chipMap[s.prediction_status]||'';

    predHTML=`<div class="pred-strip">
      <div class="pred-label">PICK</div>
      <div class="${pickCls}">${s.predicted_pick}</div>
      <div class="pred-conf">${s.predicted_confidence||'—'}%</div>
      ${chip}
    </div>`;
  } else {
    predHTML=`<div class="pred-strip">
      <div class="pred-label">PICK</div>
      <div class="pred-pick pred-none">No prediction stored</div>
    </div>`;
  }

  // Result label (finished only)
  const resultLine = isFinished && s.result_label
    ? `<div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--muted);text-align:center;margin-top:2px">${s.result_label}</div>`
    : '';

  return`<div class="${cardCls}">
    <div class="sc-header">
      <div class="sc-league">${s.league}</div>
      <div class="sc-kick">${s.kickoff} UTC</div>
      <div class="${pillCls}">${pillTxt}</div>
    </div>
    <div class="scoreline">
      <div class="sc-team">
        <div class="sc-team-name ${homeWin?'winner':''}">${s.home}</div>
      </div>
      <div class="score-center">
        ${scoreHTML}
        ${resultLine}
      </div>
      <div class="sc-team away-t">
        <div class="sc-team-name ${awayWin?'winner':''}">${s.away}</div>
      </div>
    </div>
    ${predHTML}
  </div>`;
}

// ── Results Tracker tab ───────────────────────────────────────────────────
async function loadTracker(){
  try{
    const [hr,lr]=await Promise.all([fetch('/api/history?limit=100'),fetch('/api/learning')]);
    const hd=await hr.json(), ld=await lr.json();

    document.getElementById('sumAccuracy').textContent=(hd.accuracy_pct||0)+'%';
    document.getElementById('sumVariance').textContent=(hd.avg_variance_pct||0)+'%';
    document.getElementById('sumBrier').textContent=(hd.avg_brier||0).toFixed(3);
    document.getElementById('sumTotal').textContent=hd.count;

    renderVarianceChart(hd.records.slice(0,30).reverse());
    renderWeightBars(ld.current_weights);

    document.getElementById('learningMeta').innerHTML=
      `LEARNING RATE: ${ld.learning_rate} &nbsp;|&nbsp; `+
      `GRADED: ${ld.total_graded} &nbsp;|&nbsp; `+
      `BRIER: ${(ld.current_brier||0).toFixed(4)} &nbsp;|&nbsp; `+
      `EVENTS LOGGED: ${ld.history.length}`;

    renderResultsTable(hd.records);
  }catch(e){console.error('Tracker error',e)}
}

function renderVarianceChart(records){
  const el=document.getElementById('varianceChart');
  if(!records.length){el.innerHTML='<div class="loading-text" style="margin:auto;color:var(--muted)">No graded results yet — record outcomes via Claude</div>';return}
  el.innerHTML=records.map(r=>{
    const h=Math.max(8,r.confidence_pct);
    const cls=r.correct?'correct':'wrong';
    return`<div class="v-bar-wrap" title="${r.match}\nPred:${r.confidence_pct}% | Actual:${r.actual} | Var:${r.variance_pct}%">
      <div class="v-bar ${cls}" style="height:${h}px"></div>
      <div class="v-bar-label">${r.actual||'?'}</div>
    </div>`;
  }).join('');
}

function renderWeightBars(weights){
  const labels={w_market:'Market Lines',w_form:'Recent Form',w_h2h:'Head-to-Head',w_home_field:'Home Field',w_position:'Lge Position'};
  document.getElementById('weightBars').innerHTML=Object.entries(weights).map(([k,v])=>{
    const pct=Math.round(v*100);
    return`<div class="w-row">
      <div class="w-label">${labels[k]||k}</div>
      <div class="w-track"><div class="w-fill" style="width:${pct}%"></div></div>
      <div class="w-pct">${pct}%</div>
    </div>`;
  }).join('');
}

function renderResultsTable(records){
  const tbody=document.getElementById('resultsBody');
  if(!records.length){tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:20px;font-family:JetBrains Mono,monospace;font-size:11px">No results recorded yet.</td></tr>';return}
  tbody.innerHTML=records.map(r=>{
    const varClass=r.variance_pct<20?'var-low':r.variance_pct<40?'var-mid':'var-high';
    return`<tr>
      <td><div class="match-cell">${r.match}</div><div class="match-league">${r.kickoff}</div></td>
      <td><span class="pick-cell">${r.pick_code}</span></td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${r.confidence_pct}%</td>
      <td class="actual-${r.actual}" style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700">${r.actual}</td>
      <td><span class="correct-chip ${r.correct?'chip-yes':'chip-no'}">${r.correct?'✓ YES':'✗ NO'}</span></td>
      <td style="font-family:'JetBrains Mono',monospace;font-size:11px">${r.predicted_pct}%</td>
      <td class="${varClass}" style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600">${r.variance_pct}%</td>
      <td class="brier-cell">${r.brier_score}</td>
    </tr>`;
  }).join('');
}

// ── Utilities ────────────────────────────────────────────────────────────
function showToast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),4000)}

// ── Init ─────────────────────────────────────────────────────────────────
loadData();
setInterval(loadData,300000);
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────

def create_dashboard_app() -> Starlette:
    init_db()
    return Starlette(routes=[
        Route("/",                         dashboard),
        Route("/health",                   health),
        Route("/api/fixtures",             api_fixtures),
        Route("/api/fixture/{fixture_id}", api_fixture),
        Route("/api/scores",               api_scores),
        Route("/api/history",              api_history),
        Route("/api/learning",             api_learning),
        Route("/api/stats",                api_stats),
    ])
