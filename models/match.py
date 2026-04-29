"""
models/match.py
---------------
Pydantic v2 data models.  These form the canonical schema for every piece
of data the scraper produces.  Using typed models guarantees that partial
or malformed scraped data is caught at parse time rather than silently
corrupting stored JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Schedule / Fixture list ───────────────────────────────────────────────────

class MatchSummary(BaseModel):
    """Lightweight record from the fixtures/match-list page."""
    match_id:    str
    match_slug:  str                        # e.g. "ind-vs-aus-1st-odi-abc123"
    detail_url:  str
    team_a:      str
    team_b:      str
    series_name: str
    match_type:  str                        # T20 | ODI | Test | Other
    venue:       Optional[str] = None
    start_time:  Optional[datetime] = None  # UTC-normalised
    status:      Literal["upcoming", "live", "completed", "unknown"] = "unknown"
    scraped_at:  datetime = Field(default_factory=datetime.utcnow)


# ── Match Info tab ────────────────────────────────────────────────────────────

class MatchInfo(BaseModel):
    match_id:       str
    series:         str
    match_number:   Optional[str] = None   # "1st ODI", "Final", etc.
    venue:          Optional[str] = None
    city:           Optional[str] = None
    date:           Optional[str] = None   # raw string as displayed
    start_time_utc: Optional[datetime] = None
    toss:           Optional[str] = None   # "India won the toss and elected to bat"
    umpires:        list[str] = []
    match_referee:  Optional[str] = None
    result:         Optional[str] = None   # filled after match ends
    player_of_match: Optional[str] = None
    scraped_at:     datetime = Field(default_factory=datetime.utcnow)


# ── Squads tab ────────────────────────────────────────────────────────────────

class Player(BaseModel):
    name:       str
    role:       Optional[str] = None        # Batsman | Bowler | All-rounder | WK
    is_captain: bool = False
    is_keeper:  bool = False
    batting_order: Optional[int] = None


class TeamSquad(BaseModel):
    team_name: str
    players:   list[Player] = []
    bench:     list[Player] = []            # named reserves / impact subs


class Squads(BaseModel):
    match_id:  str
    teams:     list[TeamSquad] = []
    announced: bool = False                 # False → squad not yet released
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


# ── Scorecard tab ─────────────────────────────────────────────────────────────

class BattingEntry(BaseModel):
    player:       str
    dismissal:    Optional[str] = None      # "c Root b Broad" or "not out"
    runs:         Optional[int] = None
    balls:        Optional[int] = None
    fours:        Optional[int] = None
    sixes:        Optional[int] = None
    strike_rate:  Optional[float] = None


class BowlingEntry(BaseModel):
    player:      str
    overs:       Optional[float] = None
    maidens:     Optional[int] = None
    runs:        Optional[int] = None
    wickets:     Optional[int] = None
    no_balls:    Optional[int] = None
    wides:       Optional[int] = None
    economy:     Optional[float] = None


class Extras(BaseModel):
    wides:    int = 0
    no_balls: int = 0
    byes:     int = 0
    leg_byes: int = 0
    penalty:  int = 0
    total:    int = 0


class FallOfWicket(BaseModel):
    wicket_number: int
    runs_at_fall:  int
    player:        str
    overs_at_fall: Optional[float] = None


class Innings(BaseModel):
    innings_number: int                     # 1-based; >2 means Super Over / follow-on
    batting_team:   str
    bowling_team:   str
    total:          Optional[str] = None    # "287/6"
    overs:          Optional[str] = None    # "50.0"
    run_rate:       Optional[float] = None
    target:         Optional[int] = None    # set for chasing innings
    dls_target:     Optional[int] = None
    batting:        list[BattingEntry] = []
    bowling:        list[BowlingEntry] = []
    extras:         Extras = Field(default_factory=Extras)
    fall_of_wickets: list[FallOfWicket] = []
    is_super_over:  bool = False


class Scorecard(BaseModel):
    match_id:   str
    innings:    list[Innings] = []
    is_partial: bool = True                 # True while match is in progress
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


# ── Live / Commentary tab ─────────────────────────────────────────────────────

class Ball(BaseModel):
    over:        str                        # "14.3"
    runs:        int
    is_wicket:   bool = False
    is_boundary: bool = False
    is_six:      bool = False
    extras:      Optional[str] = None       # "wide", "no ball", etc.
    commentary:  Optional[str] = None


class LiveScore(BaseModel):
    match_id:           str
    status_text:        str                 # "IND need 47 runs in 30 balls"
    batting_team:       str
    bowling_team:       str
    current_score:      Optional[str] = None  # "240/4"
    current_overs:      Optional[str] = None  # "38.2"
    run_rate:           Optional[float] = None
    required_run_rate:  Optional[float] = None
    last_5_overs:       Optional[str] = None
    current_partnership: Optional[str] = None
    batters_on_crease:  list[BattingEntry] = []
    current_bowler:     Optional[BowlingEntry] = None
    recent_balls:       list[Ball] = []     # last ~20 deliveries
    interruption_reason: Optional[str] = None  # e.g. "Rain", "DLS applied", "Reduced overs"
    scraped_at:         datetime = Field(default_factory=datetime.utcnow)
