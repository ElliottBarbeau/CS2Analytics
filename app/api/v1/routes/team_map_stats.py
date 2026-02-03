import time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.match_map import MatchMap

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/{team_id}/map-stats/{map_name}")
def team_map_stats(
    team_id: int,
    map_name: str,
    windows: str = Query("365,90,30"),
    db: Session = Depends(get_db),
):
    now = int(time.time())
    window_days = [int(x) for x in windows.split(",") if x.strip()]

    out = []
    for days in window_days:
        cutoff = now - days * 24 * 60 * 60

        stmt = (
            select(
                func.count().label("played"),
                func.sum(func.case((MatchMap.winner_team_id == team_id, 1), else_=0)).label("wins"),
            )
            .select_from(MatchMap)
            .join(Match, Match.id == MatchMap.match_id)
            .where(
                Match.played_at >= cutoff,
                MatchMap.map_name == map_name,
                MatchMap.winner_team_id.is_not(None),
                or_(Match.team1_id == team_id, Match.team2_id == team_id),
            )
        )

        row = db.execute(stmt).one()
        played = int(row.played)
        wins = int(row.wins or 0)
        losses = played - wins
        winrate = (wins / played) if played > 0 else None

        out.append({"days": days, "played": played, "wins": wins, "losses": losses, "winrate": winrate})

    return {"team_id": team_id, "map": map_name, "windows": out}
