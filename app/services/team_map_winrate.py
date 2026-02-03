from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchMap


def _window_stats(db: Session, team_id: int, map_name: str, cutoff_ts: int) -> Dict[str, Any]:
    win_case = case((MatchMap.winner_team_id == team_id, 1), else_=0)

    row = db.execute(
        select(
            func.count().label("played"),
            func.coalesce(func.sum(win_case), 0).label("wins"),
        )
        .select_from(MatchMap)
        .join(Match, Match.id == MatchMap.match_id)
        .where(
            Match.played_at >= cutoff_ts,
            func.lower(MatchMap.map_name) == map_name.lower(),
            (Match.team1_id == team_id) | (Match.team2_id == team_id),
        )
    ).first()

    played = int(row.played or 0)
    wins = int(row.wins or 0)
    winrate = (wins / played) if played else None

    return {"played": played, "wins": wins, "winrate": winrate}


def get_team_map_winrate(db: Session, team_id: int, map_name: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    now = int(now_ts or time.time())
    cut_30 = now - 30 * 24 * 60 * 60
    cut_90 = now - 90 * 24 * 60 * 60
    cut_365 = now - 365 * 24 * 60 * 60

    last_30 = _window_stats(db, team_id, map_name, cut_30)
    last_90 = _window_stats(db, team_id, map_name, cut_90)
    last_365 = _window_stats(db, team_id, map_name, cut_365)

    return {
        "team_id": team_id,
        "map": map_name.lower(),
        "windows": {
            "last_30_days": last_30,
            "last_90_days": last_90,
            "last_365_days": last_365,
        },
    }
