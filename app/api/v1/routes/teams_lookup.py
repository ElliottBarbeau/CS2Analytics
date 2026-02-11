

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.team import Team

router = APIRouter(tags=["Teams"], prefix="/teams")

@router.get("/by_name/{name}")
def get_team(name: str, db: Session=Depends(get_db)):
    team = db.scalar(select(Team).where(func.lower(Team.name) == func.lower(name)))
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"id": team.id, "name": team.name}

