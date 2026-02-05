import time
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Match, MatchMap

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/{team_id}/map-stats/{map_name}")
def team_map_stats(
    team_id: int,
    map_name: str,
    windows: str = Query(default="365,90,30"),
    db: Session = Depends(get_db),
) -> Dict:
    now = int(time.time())
    window_days: List[int] = []
    for w in windows.split(","):
        w = w.strip()
        if not w:
            continue
        window_days.append(int(w))

    out = {"team_id": team_id, "map": map_name, "windows": {}}

    for days in window_days:
        cutoff = now - days * 24 * 60 * 60

        row = db.execute(
            select(
                func.count().label("played"),
                func.sum(case((MatchMap.winner_team_id == team_id, 1), else_=0)).label("wins"),
            )
            .select_from(MatchMap)
            .join(Match, Match.id == MatchMap.match_id)
            .where(
                Match.played_at >= cutoff,
                (Match.team1_id == team_id) | (Match.team2_id == team_id),
                func.lower(MatchMap.map_name) == func.lower(map_name),
            )
        ).first()

        played = int(row.played or 0)
        wins = int(row.wins or 0)
        winrate = (wins / played) if played else None

        out["windows"][str(days)] = {"played": played, "wins": wins, "winrate": winrate}

    return out
