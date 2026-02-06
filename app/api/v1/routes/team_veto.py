import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.veto_action import VetoAction

router = APIRouter(prefix="/teams", tags=["teams"])


def is_bo3(db: Session, match_id: int) -> bool:
    total = db.scalar(select(func.count()).select_from(VetoAction).where(VetoAction.match_id == match_id))
    if total != 7:
        return False

    leftover = db.scalar(
        select(func.count())
        .select_from(VetoAction)
        .where(and_(VetoAction.match_id == match_id, VetoAction.action == "left_over"))
    )
    return leftover == 1


@router.get("/{team_id}/permaban")
def permaban_last_60_days(team_id: int, db: Session = Depends(get_db)):
    cutoff = int(time.time()) - 60 * 24 * 60 * 60

    match_ids = db.scalars(
        select(Match.id).where(
            Match.played_at >= cutoff,
            or_(Match.team1_id == team_id, Match.team2_id == team_id),
        )
    ).all()

    first_bans: list[str] = []
    for mid in match_ids:
        if not is_bo3(db, mid):
            continue

        row = db.execute(
            select(VetoAction.map_name)
            .where(
                VetoAction.match_id == mid,
                VetoAction.team_id == team_id,
                VetoAction.action == "removed",
                VetoAction.map_name.is_not(None),
            )
            .order_by(VetoAction.order_index.asc())
            .limit(1)
        ).first()

        if row and row[0]:
            first_bans.append(row[0])

    if len(first_bans) < 1:
        raise HTTPException(status_code=404, detail="No BO3 matches with vetoes in last 60 days")

    counts: dict[str, int] = {}
    for m in first_bans:
        counts[m] = counts.get(m, 0) + 1

    sorted_maps = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    total = len(first_bans)

    permaban_map, permaban_count = sorted_maps[0]
    breakdown = [{"map": m, "count": c, "rate": c / total} for m, c in sorted_maps]

    return {
        "team_id": team_id,
        "window_days": 60,
        "matches_considered": total,
        "permaban": {"map": permaban_map, "count": permaban_count, "rate": permaban_count / total},
        "breakdown": breakdown,
    }
