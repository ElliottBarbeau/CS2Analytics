from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, and_, cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat
from app.db.models.player import Player


router = APIRouter(prefix="/players", tags=["players"])


_WEEKDAY_TO_DOW = {
    "sunday": 0,
    "monday": 1,
    "tuesday": 2,
    "wednesday": 3,
    "thursday": 4,
    "friday": 5,
    "saturday": 6,
}


@router.get("/")
def list_players(limit: int = Query(50, ge=1, le=500), q: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(Player.id, Player.name).order_by(Player.name.asc()).limit(limit)
    if q:
        stmt = stmt.where(func.lower(Player.name).like(f"%{q.strip().lower()}%"))
    rows = db.execute(stmt).all()
    return [{"id": int(r.id), "name": r.name} for r in rows]


@router.get("/by-name/{name}")
def player_by_name(name: str, db: Session = Depends(get_db)):
    nm = name.strip().lower()
    row = db.execute(select(Player.id, Player.name).where(func.lower(Player.name) == nm)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")
    return {"id": int(row.id), "name": row.name}


@router.get("/{player_id}/summary")
def player_summary(
    player_id: int,
    windows: str = Query("30,90,365"),
    weekday: Optional[str] = Query(None),
    map_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    w = []
    for part in windows.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            w.append(int(part))
        except Exception:
            continue
    w = [x for x in w if 1 <= x <= 3650]
    if not w:
        raise HTTPException(status_code=400, detail="Invalid windows")

    player_row = db.execute(select(Player.id, Player.name).where(Player.id == player_id)).first()
    if not player_row:
        raise HTTPException(status_code=404, detail="Player not found")

    dow = None
    if weekday:
        key = weekday.strip().lower()
        if key not in _WEEKDAY_TO_DOW:
            raise HTTPException(status_code=400, detail="weekday must be Monday..Sunday")
        dow = _WEEKDAY_TO_DOW[key]

    mn = map_name.strip().lower() if map_name else None

    def one_window(days: int):
        cutoff = int(time.time()) - days * 24 * 60 * 60

        rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)

        base = (
            select(
                func.count(func.distinct(PlayerMapStat.stats_match_id)).label("maps"),
                func.count(func.distinct(PlayerMapStat.match_id)).label("matches"),
                func.sum(rounds_expr).label("rounds"),
                func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
                func.sum(func.coalesce(PlayerMapStat.deaths, 0)).label("deaths"),
                func.sum(func.coalesce(PlayerMapStat.assists, 0)).label("assists"),
                (func.sum(cast(PlayerMapStat.rating3, Float) * rounds_expr) / func.nullif(func.sum(rounds_expr), 0)).label("rating3"),
                (func.sum(cast(PlayerMapStat.adr, Float) * rounds_expr) / func.nullif(func.sum(rounds_expr), 0)).label("adr"),
                (func.sum(cast(PlayerMapStat.kast, Float) * rounds_expr) / func.nullif(func.sum(rounds_expr), 0)).label("kast"),
            )
            .select_from(PlayerMapStat)
            .join(Match, Match.id == PlayerMapStat.match_id)
            .join(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                PlayerMapStat.map_name.is_not(None),
                Match.played_at >= cutoff,
            )
        )

        if mn:
            base = base.where(func.lower(PlayerMapStat.map_name) == mn)

        if dow is not None:
            base = base.where(func.extract("dow", func.to_timestamp(Match.played_at)) == dow)

        row = db.execute(base).first()
        maps = int(row.maps or 0)
        matches = int(row.matches or 0)
        rounds = int(row.rounds or 0)

        return {
            "window_days": days,
            "matches": matches,
            "maps": maps,
            "rounds": rounds,
            "rating3": float(row.rating3) if row.rating3 is not None else None,
            "adr": float(row.adr) if row.adr is not None else None,
            "kast": float(row.kast) if row.kast is not None else None,
            "kills": int(row.kills or 0),
            "deaths": int(row.deaths or 0),
            "assists": int(row.assists or 0),
        }

    return {
        "player_id": int(player_row.id),
        "player_name": player_row.name,
        "weekday": weekday,
        "map_name": map_name,
        "windows": [one_window(days) for days in w],
    }
