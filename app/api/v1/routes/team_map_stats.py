from fastapi import APIRouter, Depends
from sqlalchemy import func, select, case
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Match, MatchMap

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/{team_id}/map-stats/{map_name}")
def team_map_stats(
    team_id: int,
    map_name: str,
    windows: str = "30",
    db: Session = Depends(get_db),
):
    now = int(__import__("time").time())
    map_l = map_name.lower()
    window_days = [int(w) for w in windows.split(",") if w.strip()]

    results = {}

    for days in window_days:
        cutoff = now - days * 24 * 60 * 60

        row = db.execute(
            select(
                func.count().label("played"),
                func.sum(
                    case(
                        (MatchMap.winner_team_id == team_id, 1),
                        else_=0,
                    )
                ).label("wins"),
            )
            .select_from(MatchMap)
            .join(Match, Match.id == MatchMap.match_id)
            .where(
                Match.played_at >= cutoff,
                func.lower(MatchMap.map_name) == map_l,
                (Match.team1_id == team_id) | (Match.team2_id == team_id),
            )
        ).one()

        played = int(row.played or 0)
        wins = int(row.wins or 0)

        results[str(days)] = {
            "played": played,
            "wins": wins,
            "winrate": (wins / played) if played else None,
        }

    return {"team_id": team_id, "map": map_name, "windows": results}
