from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Team


def list_teams(db: Session):
    rows = db.execute(
        select(Team.id, Team.name).order_by(Team.name)
    ).all()

    return [{"id": r.id, "name": r.name} for r in rows]
