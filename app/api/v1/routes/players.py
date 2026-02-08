from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, and_, case, cast, func, select
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


def _player_summary_impl(
    player_id: int,
    player_name_echo: Optional[str],
    windows: str,
    weekday: Optional[str],
    map_name: Optional[str],
    third_place_decider: Optional[bool],
    db: Session,
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
        weight = case((rounds_expr > 0, rounds_expr), else_=None)

        stmt = (
            select(
                func.count(func.distinct(PlayerMapStat.stats_match_id)).label("maps"),
                func.count(func.distinct(PlayerMapStat.match_id)).label("matches"),
                func.sum(func.coalesce(weight, 0)).label("rounds"),
                func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
                func.sum(func.coalesce(PlayerMapStat.deaths, 0)).label("deaths"),
                func.sum(func.coalesce(PlayerMapStat.assists, 0)).label("assists"),
                (func.sum(cast(PlayerMapStat.rating3, Float) * weight) / func.nullif(func.sum(weight), 0)).label("rating3"),
                (func.sum(cast(PlayerMapStat.adr, Float) * weight) / func.nullif(func.sum(weight), 0)).label("adr"),
                (func.sum(cast(PlayerMapStat.kast, Float) * weight) / func.nullif(func.sum(weight), 0)).label("kast"),
                (func.sum(cast(PlayerMapStat.kills, Float)) / func.nullif(func.sum(weight), 0)).label("kpr"),
            )
            .select_from(PlayerMapStat)
            .join(Match, Match.id == PlayerMapStat.match_id)
            .outerjoin(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                Match.played_at.is_not(None),
                Match.played_at >= cutoff,
            )
        )

        if third_place_decider is not None:
            stmt = stmt.where(Match.is_third_place_decider == bool(third_place_decider))

        if mn:
            stmt = stmt.where(PlayerMapStat.map_name.is_not(None), func.lower(PlayerMapStat.map_name) == mn)

        if dow is not None:
            stmt = stmt.where(func.extract("dow", func.to_timestamp(Match.played_at)) == dow)

        row = db.execute(stmt).first()

        return {
            "window_days": days,
            "matches": int(row.matches or 0),
            "maps": int(row.maps or 0),
            "rounds": int(row.rounds or 0),
            "rating3": float(row.rating3) if row.rating3 is not None else None,
            "adr": float(row.adr) if row.adr is not None else None,
            "kast": float(row.kast) if row.kast is not None else None,
            "kpr": float(row.kpr) if row.kpr is not None else None,
            "kills": int(row.kills or 0),
            "deaths": int(row.deaths or 0),
            "assists": int(row.assists or 0),
        }

    return {
        "player_id": int(player_row.id),
        "player_name": player_row.name,
        "player_query": player_name_echo,
        "weekday": weekday,
        "map_name": map_name,
        "third_place_decider": third_place_decider,
        "windows": [one_window(days) for days in w],
    }


@router.get("/{player_id}/summary")
def player_summary(
    player_id: int,
    windows: str = Query("30,90,365"),
    weekday: Optional[str] = Query(None),
    map_name: Optional[str] = Query(None),
    third_place_decider: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    return _player_summary_impl(
        player_id=player_id,
        player_name_echo=None,
        windows=windows,
        weekday=weekday,
        map_name=map_name,
        third_place_decider=third_place_decider,
        db=db,
    )


@router.get("/summary/by-name/{name}")
def player_summary_by_name(
    name: str,
    windows: str = Query("30,90,365"),
    weekday: Optional[str] = Query(None),
    map_name: Optional[str] = Query(None),
    third_place_decider: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    nm = name.strip().lower()
    row = db.execute(select(Player.id).where(func.lower(Player.name) == nm)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")
    return _player_summary_impl(
        player_id=int(row.id),
        player_name_echo=name,
        windows=windows,
        weekday=weekday,
        map_name=map_name,
        third_place_decider=third_place_decider,
        db=db,
    )


@router.get("/leaders/kpr-delta")
def kpr_delta_leaders(
    weekday: str = Query(...),
    window_days: int = Query(365, ge=1, le=3650),
    limit: int = Query(10, ge=1, le=100),
    map_name: Optional[str] = Query(None),
    third_place_decider: Optional[bool] = Query(None),
    min_rounds_all: int = Query(100, ge=0, le=1000000),
    min_rounds_weekday: int = Query(30, ge=0, le=1000000),
    db: Session = Depends(get_db),
):
    key = weekday.strip().lower()
    if key not in _WEEKDAY_TO_DOW:
        raise HTTPException(status_code=400, detail="weekday must be Monday..Sunday")
    dow = _WEEKDAY_TO_DOW[key]

    cutoff = int(time.time()) - int(window_days) * 24 * 60 * 60
    mn = map_name.strip().lower() if map_name else None

    rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)
    weight = case((rounds_expr > 0, rounds_expr), else_=None)

    base_filters = [
        PlayerMapStat.segment == "total",
        Match.played_at.is_not(None),
        Match.played_at >= cutoff,
    ]

    if third_place_decider is not None:
        base_filters.append(Match.is_third_place_decider == bool(third_place_decider))

    if mn:
        base_filters.append(PlayerMapStat.map_name.is_not(None))
        base_filters.append(func.lower(PlayerMapStat.map_name) == mn)

    base_q = (
        select(
            PlayerMapStat.player_id.label("player_id"),
            func.sum(func.coalesce(weight, 0)).label("rounds_all"),
            (func.sum(cast(PlayerMapStat.kills, Float)) / func.nullif(func.sum(weight), 0)).label("kpr_all"),
        )
        .select_from(PlayerMapStat)
        .join(Match, Match.id == PlayerMapStat.match_id)
        .outerjoin(
            MatchMap,
            and_(
                MatchMap.match_id == PlayerMapStat.match_id,
                PlayerMapStat.map_name.is_not(None),
                func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
            ),
        )
        .where(*base_filters)
        .group_by(PlayerMapStat.player_id)
    ).subquery("base")

    wd_filters = list(base_filters)
    wd_filters.append(func.extract("dow", func.to_timestamp(Match.played_at)) == dow)

    wd_q = (
        select(
            PlayerMapStat.player_id.label("player_id"),
            func.sum(func.coalesce(weight, 0)).label("rounds_weekday"),
            (func.sum(cast(PlayerMapStat.kills, Float)) / func.nullif(func.sum(weight), 0)).label("kpr_weekday"),
        )
        .select_from(PlayerMapStat)
        .join(Match, Match.id == PlayerMapStat.match_id)
        .outerjoin(
            MatchMap,
            and_(
                MatchMap.match_id == PlayerMapStat.match_id,
                PlayerMapStat.map_name.is_not(None),
                func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
            ),
        )
        .where(*wd_filters)
        .group_by(PlayerMapStat.player_id)
    ).subquery("wd")

    delta_expr = cast(wd_q.c.kpr_weekday, Float) - cast(base_q.c.kpr_all, Float)

    stmt = (
        select(
            Player.id.label("player_id"),
            Player.name.label("player_name"),
            cast(base_q.c.kpr_all, Float).label("kpr_all"),
            cast(wd_q.c.kpr_weekday, Float).label("kpr_weekday"),
            cast(base_q.c.rounds_all, Float).label("rounds_all"),
            cast(wd_q.c.rounds_weekday, Float).label("rounds_weekday"),
            delta_expr.label("kpr_delta"),
        )
        .select_from(Player)
        .join(base_q, base_q.c.player_id == Player.id)
        .join(wd_q, wd_q.c.player_id == Player.id)
        .where(
            base_q.c.kpr_all.is_not(None),
            wd_q.c.kpr_weekday.is_not(None),
            base_q.c.rounds_all >= int(min_rounds_all),
            wd_q.c.rounds_weekday >= int(min_rounds_weekday),
        )
        .order_by(delta_expr.desc())
        .limit(int(limit))
    )

    rows = db.execute(stmt).all()
    return {
        "weekday": weekday,
        "window_days": int(window_days),
        "map_name": map_name,
        "third_place_decider": third_place_decider,
        "min_rounds_all": int(min_rounds_all),
        "min_rounds_weekday": int(min_rounds_weekday),
        "leaders": [
            {
                "player_id": int(r.player_id),
                "player_name": r.player_name,
                "kpr_all": float(r.kpr_all) if r.kpr_all is not None else None,
                "kpr_weekday": float(r.kpr_weekday) if r.kpr_weekday is not None else None,
                "kpr_delta": float(r.kpr_delta) if r.kpr_delta is not None else None,
                "rounds_all": int(r.rounds_all or 0),
                "rounds_weekday": int(r.rounds_weekday or 0),
            }
            for r in rows
        ],
    }
