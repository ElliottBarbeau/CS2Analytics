from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.player import Player
from app.db.models.team import Team
from app.db.models.veto_action import VetoAction

router = APIRouter(prefix="/debug", tags=["debug"])


def _count(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/counts")
def counts(db: Session = Depends(get_db)):
    return {
        "teams": _count(db, Team),
        "players": _count(db, Player),
        "matches": _count(db, Match),
        "match_maps": _count(db, MatchMap),
        "veto_actions": _count(db, VetoAction),
    }


@router.get("/recent-matches")
def recent_matches(limit: int = 25, db: Session = Depends(get_db)):
    rows = db.execute(
        select(Match.id, Match.played_at, Match.team1_id, Match.team2_id, Match.event_id, Match.series_id)
        .order_by(Match.played_at.desc())
        .limit(limit)
    ).all()

    return [
        {
            "match_id": int(r.id),
            "played_at": int(r.played_at),
            "team1_id": int(r.team1_id),
            "team2_id": int(r.team2_id),
            "event_id": int(r.event_id) if r.event_id is not None else None,
            "series_id": int(r.series_id) if r.series_id is not None else None,
        }
        for r in rows
    ]


@router.get("/team/{team_id}/check")
def team_check(team_id: int, db: Session = Depends(get_db)):
    team = db.get(Team, team_id)
    if not team:
        return {"found": False, "team_id": team_id}

    matches = db.scalar(
        select(func.count()).select_from(Match).where((Match.team1_id == team_id) | (Match.team2_id == team_id))
    )
    veto_actions = db.scalar(select(func.count()).select_from(VetoAction).where(VetoAction.team_id == team_id))
    map_rows = db.scalar(select(func.count()).select_from(MatchMap).where(MatchMap.winner_team_id == team_id))

    return {
        "found": True,
        "team": {"id": team.id, "name": team.name},
        "matches_involved": int(matches or 0),
        "veto_actions_by_team": int(veto_actions or 0),
        "maps_won": int(map_rows or 0),
    }
