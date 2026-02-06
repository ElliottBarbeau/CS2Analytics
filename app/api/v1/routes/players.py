from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.player import Player
from app.db.models.match_stats import PlayerMapStat

router = APIRouter(prefix="/players", tags=["players"])

DOW = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}


@router.get("/")
def list_players(
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Player.id, Player.name).order_by(Player.name.asc()).limit(limit)
    if q and q.strip():
        qq = q.strip().lower()
        stmt = (
            select(Player.id, Player.name)
            .where(func.lower(Player.name).contains(qq))
            .order_by(Player.name.asc())
            .limit(limit)
        )
    rows = db.execute(stmt).all()
    return [{"id": int(r.id), "name": r.name} for r in rows]


@router.get("/by-name/{name}")
def player_by_name(name: str, db: Session = Depends(get_db)):
    nm = name.strip().lower()
    row = db.execute(select(Player.id, Player.name).where(func.lower(Player.name) == nm)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")
    return {"id": int(row.id), "name": row.name}


def _dow_filter(dow: str | None):
    if not dow:
        return None
    key = dow.strip().lower()
    if key not in DOW:
        raise HTTPException(status_code=400, detail="weekday must be Sunday..Saturday")
    return DOW[key]


@router.get("/{player_id}/summary")
def player_summary(
    player_id: int,
    windows: str = Query("30,90,365"),
    weekday: str | None = Query(None),
    map_name: str | None = Query(None),
    db: Session = Depends(get_db),
):
    w = []
    for part in windows.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v = int(part)
        except Exception:
            continue
        if 1 <= v <= 3650:
            w.append(v)
    if not w:
        w = [30, 90, 365]

    dow_val = _dow_filter(weekday)
    map_lc = map_name.strip().lower() if map_name and map_name.strip() else None

    now = int(db.scalar(select(func.max(Match.played_at))) or 0)
    if not now:
        raise HTTPException(status_code=404, detail="No matches in DB")

    player = db.execute(select(Player.id, Player.name).where(Player.id == player_id)).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    def one_window(days: int):
        cutoff = now - days * 24 * 60 * 60

        stmt = (
            select(
                func.count().label("maps"),
                func.avg(PlayerMapStat.rating3).label("avg_rating3"),
                func.avg(PlayerMapStat.adr).label("avg_adr"),
                func.avg(PlayerMapStat.kast).label("avg_kast"),
                func.sum(PlayerMapStat.kills).label("kills"),
                func.sum(PlayerMapStat.deaths).label("deaths"),
                func.sum(PlayerMapStat.assists).label("assists"),
            )
            .select_from(PlayerMapStat)
            .join(Match, Match.id == PlayerMapStat.match_id)
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                Match.played_at >= cutoff,
            )
        )

        if dow_val is not None:
            stmt = stmt.where(func.extract("dow", func.to_timestamp(Match.played_at)) == dow_val)

        if map_lc is not None:
            stmt = stmt.where(func.lower(PlayerMapStat.map_name) == map_lc)

        row = db.execute(stmt).first()
        maps = int(row.maps or 0)

        return {
            "window_days": days,
            "maps": maps,
            "avg_rating3": float(row.avg_rating3) if row.avg_rating3 is not None else None,
            "avg_adr": float(row.avg_adr) if row.avg_adr is not None else None,
            "avg_kast": float(row.avg_kast) if row.avg_kast is not None else None,
            "kills": int(row.kills or 0),
            "deaths": int(row.deaths or 0),
            "assists": int(row.assists or 0),
        }

    out = [one_window(days) for days in sorted(set(w))]
    return {
        "player_id": int(player.id),
        "player_name": player.name,
        "weekday": weekday,
        "map_name": map_name,
        "windows": out,
    }
