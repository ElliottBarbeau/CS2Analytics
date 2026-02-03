import time
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.veto_action import VetoAction

router = APIRouter(prefix="/teams", tags=["teams"])


def is_bo3_veto_shape(db: Session, match_id: int) -> bool:
    cnt = db.scalar(select(func.count()).select_from(VetoAction).where(VetoAction.match_id == match_id))
    if cnt != 7:
        return False
    has_leftover = db.scalar(
        select(func.count()).select_from(VetoAction).where(
            and_(VetoAction.match_id == match_id, VetoAction.action == "left_over")
        )
    )
    return bool(has_leftover)


@router.get("/{team_id}/permaban")
def get_permaban(
    team_id: int,
    window_days: int = Query(30, ge=1, le=365),
    min_matches: int = Query(5, ge=1, le=200),
    db: Session = Depends(get_db),
):
    cutoff = int(time.time()) - 30 * 24 * 60 * 60

    match_ids = db.scalars(
        select(Match.id).where(Match.played_at.is_not(None), Match.played_at >= cutoff)
    ).all()

    first_bans = []
    for mid in match_ids:
        if not is_bo3_veto_shape(db, mid):
            continue

        row = db.execute(
            select(VetoAction.map_name)
            .where(
                VetoAction.match_id == mid,
                VetoAction.team_id == team_id,
                VetoAction.action == "removed",
            )
            .order_by(VetoAction.order_index.asc())
            .limit(1)
        ).first()

        if row:
            first_bans.append(row[0])

    matches_considered = len(first_bans)
    if matches_considered < min_matches:
        raise HTTPException(
            status_code=404,
            detail=f"Not enough BO3 matches in last {window_days} days (have {matches_considered}, need {min_matches})",
        )

    counts = {}
    for m in first_bans:
        counts[m] = counts.get(m, 0) + 1

    sorted_maps = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)

    permaban_map, permaban_count = sorted_maps[0]
    breakdown = [
        {"map": m, "count": c, "rate": c / matches_considered}
        for m, c in sorted_maps
    ]

    return {
        "team_id": team_id,
        "window_days": window_days,
        "matches_considered": matches_considered,
        "permaban": {
            "map": permaban_map,
            "first_ban_count": permaban_count,
            "rate": permaban_count / matches_considered,
        },
        "breakdown": breakdown,
    }
