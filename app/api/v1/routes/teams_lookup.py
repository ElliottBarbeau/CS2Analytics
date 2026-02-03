from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.team import Team

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("/by-name/{name}")
def team_by_name(name: str, db: Session = Depends(get_db)):
    team = db.scalar(select(Team).where(Team.name == name))
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"id": team.id, "name": team.name, "grid_team_id": team.grid_team_id}
