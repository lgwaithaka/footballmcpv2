"""
schedule_data.py — v2.3
────────────────────────
Pre-loaded fixture schedule + live-fixture registry.

Static fixtures  : April 11-12, 2026 (Betika/Provider B schedule)
                   April 12-17, 2026 (EPL Matchday 33 + UEFA CL/EL QF 2nd legs)

Live fixtures    : Fetched at startup by live_schedule.py from football-data.org
                   Registered via register_live_fixtures() and merged transparently
                   into all_fixtures() / fixture_by_id().

Provider A  = Schedule source with market lines (provider_a_id)
Provider B  = Alternate schedule source with market lines (provider_b_id)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MarketLines:
    """Decimal market lines for a three-way outcome + goal markets."""
    home: float
    draw: float
    away: float
    over_2_5: float
    under_2_5: float
    both_score_yes: float
    both_score_no: float


@dataclass
class ScheduledMatch:
    """A single fixture with optional market data from one or both providers."""
    provider_a_id: Optional[int]   # Source A fixture identifier
    provider_b_id: Optional[int]   # Source B fixture identifier
    date: str                       # DD/MM/YYYY
    kickoff: str                    # HH:MM UTC
    home_team: str
    away_team: str
    league: str
    country: str
    lines_a: Optional[MarketLines] = None
    lines_b: Optional[MarketLines] = None


# ─────────────────────────────────────────────────────────────────────────────
# Existing fixtures  — April 11-12, 2026
# ─────────────────────────────────────────────────────────────────────────────

FIXTURES: list[ScheduledMatch] = [
    # ── Serie A ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=5348, provider_b_id=10158,
        date="11/04/2026", kickoff="21:45",
        home_team="Atalanta BC", away_team="Juventus",
        league="Serie A", country="Italy",
        lines_a=MarketLines(3.15, 3.40, 2.32, 1.83, 1.91, 1.67, 2.11),
        lines_b=MarketLines(3.20, 3.45, 2.34, 1.88, 1.97, 1.69, 2.19),
    ),
    # ── La Liga ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=13597,
        date="11/04/2026", kickoff="22:00",
        home_team="Sevilla", away_team="Atletico Madrid",
        league="La Liga", country="Spain",
        lines_a=None,
        lines_b=MarketLines(2.32, 3.55, 3.15, 1.94, 1.90, 1.75, 2.13),
    ),
    # ── Eredivisie ────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=50668,
        date="11/04/2026", kickoff="22:00",
        home_team="Heracles Almelo", away_team="Ajax",
        league="Eredivisie", country="Netherlands",
        lines_a=None,
        lines_b=MarketLines(5.40, 4.60, 1.55, 1.49, 2.60, 1.61, 2.28),
    ),
    ScheduledMatch(
        provider_a_id=1568, provider_b_id=35623,
        date="11/04/2026", kickoff="21:00",
        home_team="FC Groningen", away_team="Go Ahead Eagles",
        league="Eredivisie", country="Netherlands",
        lines_a=MarketLines(1.73, 4.10, 4.10, 1.49, 2.44, 1.53, 2.40),
        lines_b=MarketLines(1.77, 4.10, 4.20, 1.52, 2.50, 1.54, 2.43),
    ),
    # ── Ekstraklasa ──────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=3974, provider_b_id=60129,
        date="11/04/2026", kickoff="21:15",
        home_team="Legia Warszawa", away_team="Gornik Zabrze",
        league="Ekstraklasa", country="Poland",
        lines_a=MarketLines(2.08, 3.25, 3.20, 1.97, 1.75, 1.76, 1.88),
        lines_b=MarketLines(2.15, 3.35, 3.40, 2.01, 1.78, 1.81, 1.93),
    ),
    # ── Slovenia ──────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=5730, provider_b_id=33646,
        date="11/04/2026", kickoff="21:15",
        home_team="NK Radomlje", away_team="NK Celje",
        league="PrvaLiga", country="Slovenia",
        lines_a=MarketLines(5.40, 4.50, 1.41, 1.37, 2.85, 1.54, 2.22),
        lines_b=MarketLines(5.80, 4.80, 1.45, 1.42, 2.75, 1.58, 2.30),
    ),
    # ── 2. Bundesliga ─────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=5670, provider_b_id=49241,
        date="11/04/2026", kickoff="21:30",
        home_team="SV Darmstadt 98", away_team="Hannover 96",
        league="2. Bundesliga", country="Germany",
        lines_a=MarketLines(2.70, 3.50, 2.40, 1.64, 2.12, 1.56, 2.34),
        lines_b=MarketLines(2.75, 3.55, 2.47, 1.67, 2.17, 1.57, 2.36),
    ),
    # ── Argentina ─────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=3475, provider_b_id=82201,
        date="11/04/2026", kickoff="21:30",
        home_team="Independiente Rivadavia", away_team="Argentinos Juniors",
        league="Primera Division", country="Argentina",
        lines_a=MarketLines(2.75, 2.70, 2.60, 2.33, 1.54, 1.96, 1.69),
        lines_b=MarketLines(2.85, 2.85, 2.70, 2.46, 1.51, 2.02, 1.74),
    ),
    # ── Egypt ─────────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=5857, provider_b_id=91431,
        date="11/04/2026", kickoff="21:00",
        home_team="Al Ahly SC", away_team="Smouha SC",
        league="Premier League", country="Egypt",
        lines_a=MarketLines(1.30, 4.30, 8.40, 1.85, 1.86, 2.31, 1.50),
        lines_b=MarketLines(1.35, 4.60, 8.80, 1.92, 1.82, 2.36, 1.55),
    ),
    ScheduledMatch(
        provider_a_id=2863, provider_b_id=2662,
        date="11/04/2026", kickoff="21:00",
        home_team="Enppi Club", away_team="Ceramica Cleopatra",
        league="Premier League", country="Egypt",
        lines_a=MarketLines(2.90, 2.65, 2.50, 2.80, 1.39, 2.26, 1.52),
        lines_b=MarketLines(3.05, 2.80, 2.60, 2.95, 1.37, 2.34, 1.55),
    ),
    # ── Saudi Arabia ──────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=3281, provider_b_id=73363,
        date="11/04/2026", kickoff="21:00",
        home_team="Al-Okhdood Club", away_team="Al-Nassr FC",
        league="Saudi Pro League", country="Saudi Arabia",
        lines_a=MarketLines(19.00, 9.80, 1.07, 1.18, 4.40, 1.91, 1.80),
        lines_b=MarketLines(21.00, 11.00, 1.09, 1.19, 4.60, 1.93, 1.81),
    ),
    # ── Portugal ──────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=37791,
        date="11/04/2026", kickoff="22:30",
        home_team="Estrela Amadora", away_team="Sporting CP",
        league="Primeira Liga", country="Portugal",
        lines_a=None,
        lines_b=MarketLines(11.00, 6.60, 1.25, 1.48, 2.65, 1.97, 1.81),
    ),
    # ── Belgium ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=56042,
        date="11/04/2026", kickoff="21:45",
        home_team="St. Truidense VV", away_team="Club Brugge",
        league="First Division A", country="Belgium",
        lines_a=None,
        lines_b=MarketLines(3.80, 4.10, 1.89, 1.49, 2.65, 1.48, 2.60),
    ),
    # ── France ────────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=4697, provider_b_id=95487,
        date="11/04/2026", kickoff="21:00",
        home_team="Saint-Etienne", away_team="Dunkerque",
        league="Ligue 2", country="France",
        lines_a=MarketLines(1.51, 4.30, 5.40, 1.57, 2.26, 1.69, 2.05),
        lines_b=MarketLines(1.52, 4.40, 5.40, 1.58, 2.28, 1.70, 2.07),
    ),
    ScheduledMatch(
        provider_a_id=None, provider_b_id=19299,
        date="11/04/2026", kickoff="22:05",
        home_team="Rennes", away_team="Angers",
        league="Ligue 1", country="France",
        lines_a=None,
        lines_b=MarketLines(1.35, 5.60, 9.00, 1.65, 2.31, 2.02, 1.80),
    ),
    # ── South Africa ──────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=3047, provider_b_id=89201,
        date="11/04/2026", kickoff="21:00",
        home_team="Chippa United FC", away_team="Polokwane City",
        league="Premier League", country="South Africa",
        lines_a=MarketLines(2.50, 2.80, 2.85, 2.49, 1.41, 2.07, 1.60),
        lines_b=MarketLines(2.55, 2.90, 2.90, 2.60, 1.44, 2.14, 1.63),
    ),
    # ── USA MLS ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=1714, provider_b_id=85740,
        date="11/04/2026", kickoff="21:30",
        home_team="CF Montreal", away_team="Philadelphia Union",
        league="Major League Soccer", country="USA",
        lines_a=MarketLines(2.37, 3.25, 2.65, 1.78, 1.93, 1.61, 2.08),
        lines_b=MarketLines(2.50, 3.40, 2.75, 1.81, 1.97, 1.67, 2.17),
    ),
    ScheduledMatch(
        provider_a_id=5496, provider_b_id=22076,
        date="11/04/2026", kickoff="21:30",
        home_team="Austin FC", away_team="Los Angeles Galaxy",
        league="Major League Soccer", country="USA",
        lines_a=MarketLines(2.17, 3.40, 2.80, 1.66, 2.09, 1.54, 2.21),
        lines_b=MarketLines(2.30, 3.60, 2.95, 1.69, 2.14, 1.59, 2.31),
    ),
    # ── Uruguay ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=2444, provider_b_id=76601,
        date="11/04/2026", kickoff="21:30",
        home_team="Defensor Sporting", away_team="CA Boston River",
        league="Primera Division", country="Uruguay",
        lines_a=MarketLines(1.95, 3.00, 3.60, 2.26, 1.57, 2.03, 1.64),
        lines_b=MarketLines(2.03, 3.20, 3.80, 2.38, 1.54, 2.10, 1.68),
    ),
    # ── Ireland ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=3386, provider_b_id=52658,
        date="11/04/2026", kickoff="21:30",
        home_team="Longford Town FC", away_team="Bray Wanderers AFC",
        league="First Division", country="Ireland",
        lines_a=MarketLines(2.47, 3.20, 2.48, 1.78, 1.93, 1.65, 2.02),
        lines_b=MarketLines(2.55, 3.30, 2.55, 1.82, 1.85, 1.66, 2.04),
    ),
    # ── Brazil ────────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=28237,
        date="11/04/2026", kickoff="22:30",
        home_team="Clube do Remo PA", away_team="CR Vasco da Gama",
        league="Brasileiro Serie B", country="Brazil",
        lines_a=None,
        lines_b=MarketLines(2.85, 3.25, 2.48, 1.98, 1.80, 1.76, 2.00),
    ),
    ScheduledMatch(
        provider_a_id=None, provider_b_id=25377,
        date="11/04/2026", kickoff="22:30",
        home_team="EC Vitoria", away_team="Sao Paulo FC",
        league="Brasileiro Serie A", country="Brazil",
        lines_a=None,
        lines_b=MarketLines(3.05, 3.05, 2.47, 2.44, 1.54, 2.05, 1.72),
    ),
    ScheduledMatch(
        provider_a_id=None, provider_b_id=86392,
        date="12/04/2026", kickoff="02:30",
        home_team="SC Internacional", away_team="Gremio FB Porto Alegrense",
        league="Brasileiro Serie A", country="Brazil",
        lines_a=None,
        lines_b=MarketLines(1.86, 3.50, 4.20, 1.98, 1.80, 1.87, 1.87),
    ),
    ScheduledMatch(
        provider_a_id=None, provider_b_id=89262,
        date="12/04/2026", kickoff="02:00",
        home_team="Santos SP", away_team="Atletico Mineiro MG",
        league="Brasileiro Serie A", country="Brazil",
        lines_a=None,
        lines_b=MarketLines(2.31, 3.20, 3.20, 2.08, 1.73, 1.83, 1.92),
    ),
    # ── More MLS ──────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=41237,
        date="12/04/2026", kickoff="02:30",
        home_team="Inter Miami CF", away_team="New York Red Bulls",
        league="Major League Soccer", country="USA",
        lines_a=None,
        lines_b=MarketLines(1.45, 5.20, 6.00, 1.30, 3.45, 1.46, 2.70),
    ),
    # ── Spain ─────────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=28465,
        date="11/04/2026", kickoff="22:00",
        home_team="Cordoba CF", away_team="Zaragoza",
        league="Segunda Division", country="Spain",
        lines_a=None,
        lines_b=MarketLines(1.97, 3.50, 3.75, 1.76, 2.03, 1.66, 2.14),
    ),
    # ── Bosnia ────────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=4481, provider_b_id=93604,
        date="11/04/2026", kickoff="20:45",
        home_team="NK Celik Zenica", away_team="NK Bratstvo Gracanica",
        league="Prva Liga FBiH", country="Bosnia",
        lines_a=MarketLines(1.18, 5.20, 13.00, 1.81, 1.89, 2.85, 1.34),
        lines_b=MarketLines(1.20, 5.60, 14.00, 1.87, 1.84, 2.95, 1.35),
    ),
    # ── Hungary ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=1109, provider_b_id=97377,
        date="11/04/2026", kickoff="21:00",
        home_team="Nyiregyhaza", away_team="Paksi FC",
        league="NB I", country="Hungary",
        lines_a=MarketLines(2.41, 3.45, 2.42, 1.56, 2.28, 1.50, 2.30),
        lines_b=MarketLines(2.50, 3.60, 2.55, 1.62, 2.22, 1.54, 2.39),
    ),
    # ── Morocco ───────────────────────────────────────────────
    ScheduledMatch(
        provider_a_id=None, provider_b_id=75865,
        date="11/04/2026", kickoff="22:00",
        home_team="AS FAR Rabat", away_team="RS Berkane",
        league="Botola Pro", country="Morocco",
        lines_a=None,
        lines_b=MarketLines(2.27, 2.85, 3.45, 2.95, 1.35, 2.39, 1.51),
    ),

    # ═════════════════════════════════════════════════════════
    # English Premier League — Matchday 33  (April 12-13, 2026)
    # ═════════════════════════════════════════════════════════
    ScheduledMatch(
        provider_a_id=60001, provider_b_id=None,
        date="12/04/2026", kickoff="14:00",
        home_team="Arsenal", away_team="Crystal Palace",
        league="Premier League", country="England",
        lines_a=MarketLines(1.35, 5.50, 8.00, 1.65, 2.28, 1.72, 2.08),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60002, provider_b_id=None,
        date="12/04/2026", kickoff="14:00",
        home_team="Chelsea", away_team="Brentford",
        league="Premier League", country="England",
        lines_a=MarketLines(1.60, 3.90, 5.50, 1.80, 2.00, 1.80, 1.95),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60003, provider_b_id=None,
        date="12/04/2026", kickoff="14:00",
        home_team="Manchester City", away_team="Fulham",
        league="Premier League", country="England",
        lines_a=MarketLines(1.28, 5.80, 9.50, 1.55, 2.40, 1.60, 2.20),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60004, provider_b_id=None,
        date="12/04/2026", kickoff="14:00",
        home_team="Liverpool", away_team="Wolverhampton Wanderers",
        league="Premier League", country="England",
        lines_a=MarketLines(1.40, 4.80, 7.00, 1.70, 2.15, 1.75, 2.00),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60005, provider_b_id=None,
        date="12/04/2026", kickoff="16:30",
        home_team="Tottenham Hotspur", away_team="Nottingham Forest",
        league="Premier League", country="England",
        lines_a=MarketLines(1.85, 3.50, 4.40, 1.85, 1.95, 1.78, 1.95),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60006, provider_b_id=None,
        date="12/04/2026", kickoff="16:30",
        home_team="Aston Villa", away_team="Everton",
        league="Premier League", country="England",
        lines_a=MarketLines(1.50, 4.10, 6.00, 1.90, 1.90, 1.85, 1.90),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60007, provider_b_id=None,
        date="13/04/2026", kickoff="14:00",
        home_team="Newcastle United", away_team="Brighton & Hove Albion",
        league="Premier League", country="England",
        lines_a=MarketLines(1.95, 3.40, 3.90, 1.80, 2.00, 1.70, 2.05),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60008, provider_b_id=None,
        date="13/04/2026", kickoff="14:00",
        home_team="Manchester United", away_team="AFC Bournemouth",
        league="Premier League", country="England",
        lines_a=MarketLines(1.65, 3.70, 5.20, 1.85, 1.95, 1.75, 2.00),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60009, provider_b_id=None,
        date="13/04/2026", kickoff="14:00",
        home_team="West Ham United", away_team="Leicester City",
        league="Premier League", country="England",
        lines_a=MarketLines(2.00, 3.30, 3.80, 1.75, 2.05, 1.65, 2.15),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=60010, provider_b_id=None,
        date="13/04/2026", kickoff="16:30",
        home_team="Ipswich Town", away_team="Southampton",
        league="Premier League", country="England",
        lines_a=MarketLines(2.10, 3.20, 3.50, 1.85, 1.95, 1.80, 1.95),
        lines_b=None,
    ),

    # ═════════════════════════════════════════════════════════
    # UEFA Champions League — Quarter-Finals 2nd Legs
    # April 15-16, 2026
    # ═════════════════════════════════════════════════════════
    ScheduledMatch(
        provider_a_id=70001, provider_b_id=None,
        date="15/04/2026", kickoff="20:00",
        home_team="Arsenal", away_team="Real Madrid",
        league="UEFA Champions League", country="Europe",
        lines_a=MarketLines(2.20, 3.40, 3.20, 2.05, 1.80, 1.72, 2.05),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=70002, provider_b_id=None,
        date="15/04/2026", kickoff="20:00",
        home_team="Paris Saint-Germain", away_team="Bayern Munich",
        league="UEFA Champions League", country="Europe",
        lines_a=MarketLines(2.10, 3.45, 3.55, 1.80, 2.00, 1.68, 2.10),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=70003, provider_b_id=None,
        date="16/04/2026", kickoff="20:00",
        home_team="FC Barcelona", away_team="Club Atletico de Madrid",
        league="UEFA Champions League", country="Europe",
        lines_a=MarketLines(1.75, 3.75, 4.50, 1.90, 1.90, 1.70, 2.08),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=70004, provider_b_id=None,
        date="16/04/2026", kickoff="20:00",
        home_team="Manchester City", away_team="Inter Milan",
        league="UEFA Champions League", country="Europe",
        lines_a=MarketLines(1.65, 3.80, 5.00, 1.85, 1.95, 1.72, 2.05),
        lines_b=None,
    ),

    # ═════════════════════════════════════════════════════════
    # UEFA Europa League — Quarter-Finals 2nd Legs
    # April 17, 2026
    # ═════════════════════════════════════════════════════════
    ScheduledMatch(
        provider_a_id=80001, provider_b_id=None,
        date="17/04/2026", kickoff="20:00",
        home_team="Tottenham Hotspur", away_team="SS Lazio",
        league="UEFA Europa League", country="Europe",
        lines_a=MarketLines(1.90, 3.50, 4.00, 1.95, 1.85, 1.75, 2.00),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=80002, provider_b_id=None,
        date="17/04/2026", kickoff="20:00",
        home_team="Bayer 04 Leverkusen", away_team="AFC Ajax",
        league="UEFA Europa League", country="Europe",
        lines_a=MarketLines(1.80, 3.60, 4.30, 1.85, 1.95, 1.72, 2.05),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=80003, provider_b_id=None,
        date="17/04/2026", kickoff="20:00",
        home_team="AS Roma", away_team="Villarreal CF",
        league="UEFA Europa League", country="Europe",
        lines_a=MarketLines(2.00, 3.30, 3.80, 1.95, 1.85, 1.80, 1.95),
        lines_b=None,
    ),
    ScheduledMatch(
        provider_a_id=80004, provider_b_id=None,
        date="17/04/2026", kickoff="18:45",
        home_team="Fenerbahce", away_team="Olympiacos",
        league="UEFA Europa League", country="Europe",
        lines_a=MarketLines(2.30, 3.20, 3.20, 2.00, 1.80, 1.85, 1.90),
        lines_b=None,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Live fixture registry — populated at startup by live_schedule.py
# ─────────────────────────────────────────────────────────────────────────────

_LIVE_FIXTURES: list[ScheduledMatch] = []
_LIVE_IDS: set[str] = set()          # de-duplication key set


def register_live_fixtures(matches: list[ScheduledMatch]) -> int:
    """
    Merge API-fetched fixtures into the live registry.
    Skips duplicates by matching on home_team + away_team + date.
    Returns the number of new fixtures added.
    """
    global _LIVE_FIXTURES, _LIVE_IDS
    added = 0
    for m in matches:
        key = f"{m.home_team.lower()}|{m.away_team.lower()}|{m.date}"
        if key in _LIVE_IDS:
            continue
        # Also skip if already in static FIXTURES
        static_key = {
            f"{f.home_team.lower()}|{f.away_team.lower()}|{f.date}"
            for f in FIXTURES
        }
        if key in static_key:
            continue
        _LIVE_FIXTURES.append(m)
        _LIVE_IDS.add(key)
        added += 1
    return added


def clear_live_fixtures() -> None:
    global _LIVE_FIXTURES, _LIVE_IDS
    _LIVE_FIXTURES = []
    _LIVE_IDS = set()


# ─────────────────────────────────────────────────────────────────────────────
# Lookup helpers
# ─────────────────────────────────────────────────────────────────────────────

def all_fixtures() -> list[ScheduledMatch]:
    """Return static + live fixtures combined."""
    return FIXTURES + _LIVE_FIXTURES


def fixture_by_id(fixture_id: int) -> Optional[ScheduledMatch]:
    for f in all_fixtures():
        if f.provider_a_id == fixture_id or f.provider_b_id == fixture_id:
            return f
    return None


def fixtures_by_country(country: str) -> list[ScheduledMatch]:
    return [f for f in all_fixtures() if country.lower() in f.country.lower()]


def fixtures_by_league(league: str) -> list[ScheduledMatch]:
    return [f for f in all_fixtures() if league.lower() in f.league.lower()]
