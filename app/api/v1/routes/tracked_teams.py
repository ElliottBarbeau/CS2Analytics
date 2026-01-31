from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.tracked_team import TrackedTeam
from app.schemas.tracked_team import TrackedTeamRead, TrackedTeamUpdate

router = APIRouter(prefix="/tracked-teams", tags=["tracked-teams"])


@router.get("", response_model=list[TrackedTeamRead])
def list_tracked_teams(db: Session = Depends(get_db)):
    teams = db.scalars(select(TrackedTeam).order_by(TrackedTeam.name.asc())).all()
    return teams


@router.patch("/{team_id}", response_model=TrackedTeamRead)
def update_tracked_team(team_id: int, payload: TrackedTeamUpdate, db: Session = Depends(get_db)):
    team = db.get(TrackedTeam, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Tracked team not found")

    if payload.grid_team_id is not None:
        team.grid_team_id = payload.grid_team_id
    if payload.is_active is not None:
        team.is_active = payload.is_active

    db.commit()
    db.refresh(team)
    return team
