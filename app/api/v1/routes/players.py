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


def _parse_weekday(weekday: Optional[str]) -> Optional[int]:
    if not weekday:
        return None
    key = weekday.strip().lower()
    if key not in _WEEKDAY_TO_DOW:
        raise HTTPException(status_code=400, detail="weekday must be Monday..Sunday")
    return _WEEKDAY_TO_DOW[key]


def _player_id_by_name(db: Session, name: str) -> tuple[int, str]:
    nm = name.strip().lower()
    row = db.execute(select(Player.id, Player.name).where(func.lower(Player.name) == nm)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Player not found")
    return int(row.id), str(row.name)


def _parse_windows(windows: str) -> list[int]:
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
    return w


def _summary_for_player(
    db: Session,
    player_id: int,
    player_name: str,
    windows: list[int],
    weekday: Optional[str],
    map_name: Optional[str],
    third_place_only: bool,
):
    dow = _parse_weekday(weekday)
    mn = map_name.strip().lower() if map_name else None

    def one_window(days: int):
        cutoff = int(time.time()) - days * 24 * 60 * 60

        rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)
        weight = case((rounds_expr > 0, rounds_expr), else_=None)

        base = (
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

        if mn:
            base = base.where(PlayerMapStat.map_name.is_not(None), func.lower(PlayerMapStat.map_name) == mn)

        if dow is not None:
            base = base.where(func.extract("dow", func.to_timestamp(Match.played_at)) == dow)

        if third_place_only:
            base = base.where(Match.is_third_place_decider.is_(True))

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
            "kpr": float(row.kpr) if row.kpr is not None else None,
            "kills": int(row.kills or 0),
            "deaths": int(row.deaths or 0),
            "assists": int(row.assists or 0),
        }

    return {
        "player_id": int(player_id),
        "player_name": player_name,
        "weekday": weekday,
        "map_name": map_name,
        "third_place_only": bool(third_place_only),
        "windows": [one_window(days) for days in windows],
    }


def _kpr_delta_board(
    db: Session,
    days: int,
    weekday: str,
    limit: int,
    third_place_only: bool,
    direction: str,
    min_baseline_rounds: int,
):
    dow = _parse_weekday(weekday)
    cutoff = int(time.time()) - days * 24 * 60 * 60

    rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)
    weight = case((rounds_expr > 0, rounds_expr), else_=None)

    baseline = (
        select(
            PlayerMapStat.player_id.label("player_id"),
            func.sum(func.coalesce(weight, 0)).label("rounds"),
            func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
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
            PlayerMapStat.segment == "total",
            Match.played_at >= cutoff,
        )
        .group_by(PlayerMapStat.player_id)
        .subquery()
    )

    weekday_q = (
        select(
            PlayerMapStat.player_id.label("player_id"),
            func.sum(func.coalesce(weight, 0)).label("rounds"),
            func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
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
            PlayerMapStat.segment == "total",
            Match.played_at >= cutoff,
            func.extract("dow", func.to_timestamp(Match.played_at)) == dow,
        )
    )

    if third_place_only:
        weekday_q = weekday_q.where(Match.is_third_place_decider.is_(True))

    weekday_q = weekday_q.group_by(PlayerMapStat.player_id).subquery()

    baseline_kpr = (cast(baseline.c.kills, Float) / func.nullif(cast(baseline.c.rounds, Float), 0)).label("baseline_kpr")
    weekday_kpr = (cast(weekday_q.c.kills, Float) / func.nullif(cast(weekday_q.c.rounds, Float), 0)).label("weekday_kpr")
    delta = (weekday_kpr - baseline_kpr).label("delta_kpr")

    stmt = (
        select(
            Player.id.label("player_id"),
            Player.name.label("player_name"),
            cast(baseline.c.rounds, Float).label("baseline_rounds"),
            cast(weekday_q.c.rounds, Float).label("weekday_rounds"),
            baseline_kpr,
            weekday_kpr,
            delta,
        )
        .select_from(Player)
        .join(baseline, baseline.c.player_id == Player.id)
        .join(weekday_q, weekday_q.c.player_id == Player.id)
        .where(
            baseline.c.rounds >= min_baseline_rounds,
            weekday_q.c.rounds >= 200,
        )
    )

    if direction == "leaders":
        stmt = stmt.order_by(delta.desc())
    else:
        stmt = stmt.order_by(delta.asc())

    stmt = stmt.limit(limit)

    rows = db.execute(stmt).all()

    out = []
    for r in rows:
        out.append(
            {
                "player_id": int(r.player_id),
                "player_name": r.player_name,
                "window_days": days,
                "weekday": weekday,
                "third_place_only": third_place_only,
                "baseline_rounds": int(r.baseline_rounds),
                "weekday_rounds": int(r.weekday_rounds),
                "baseline_kpr": float(r.baseline_kpr),
                "weekday_kpr": float(r.weekday_kpr),
                "delta_kpr": float(r.delta_kpr),
            }
        )

    return {
        "window_days": days,
        "weekday": weekday,
        "third_place_only": third_place_only,
        "limit": limit,
        "weekday_rounds_min": 200,
        "baseline_rounds_min": min_baseline_rounds,
        "rows": out,
    }


def _weekday_kpr_board(
    db: Session,
    days: int,
    weekday: str,
    limit: int,
    third_place_only: bool,
    direction: str,
):
    dow = _parse_weekday(weekday)
    cutoff = int(time.time()) - days * 24 * 60 * 60

    rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)
    weight = case((rounds_expr > 0, rounds_expr), else_=None)

    q = (
        select(
            PlayerMapStat.player_id.label("player_id"),
            func.sum(func.coalesce(weight, 0)).label("weekday_rounds"),
            func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("weekday_kills"),
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
            PlayerMapStat.segment == "total",
            Match.played_at >= cutoff,
            func.extract("dow", func.to_timestamp(Match.played_at)) == dow,
        )
    )

    if third_place_only:
        q = q.where(Match.is_third_place_decider.is_(True))

    q = q.group_by(PlayerMapStat.player_id).subquery()

    weekday_kpr = (cast(q.c.weekday_kills, Float) / func.nullif(cast(q.c.weekday_rounds, Float), 0)).label("weekday_kpr")

    stmt = (
        select(
            Player.id.label("player_id"),
            Player.name.label("player_name"),
            cast(q.c.weekday_rounds, Float).label("weekday_rounds"),
            cast(q.c.weekday_kills, Float).label("weekday_kills"),
            weekday_kpr,
        )
        .select_from(Player)
        .join(q, q.c.player_id == Player.id)
        .where(q.c.weekday_rounds >= 200)
    )

    if direction == "leaders":
        stmt = stmt.order_by(weekday_kpr.desc())
    else:
        stmt = stmt.order_by(weekday_kpr.asc())

    stmt = stmt.limit(limit)

    rows = db.execute(stmt).all()

    out = []
    for r in rows:
        out.append(
            {
                "player_id": int(r.player_id),
                "player_name": r.player_name,
                "window_days": days,
                "weekday": weekday,
                "third_place_only": third_place_only,
                "weekday_rounds": int(r.weekday_rounds),
                "weekday_kills": int(r.weekday_kills),
                "weekday_kpr": float(r.weekday_kpr) if r.weekday_kpr is not None else None,
            }
        )

    return {
        "window_days": days,
        "weekday": weekday,
        "third_place_only": third_place_only,
        "limit": limit,
        "weekday_rounds_min": 200,
        "rows": out,
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
    pid, pname = _player_id_by_name(db, name)
    return {"id": pid, "name": pname}


@router.get("/{player_id}/summary")
def player_summary(
    player_id: int,
    windows: str = Query("30,90,365"),
    weekday: Optional[str] = Query(None),
    map_name: Optional[str] = Query(None),
    third_place_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    w = _parse_windows(windows)

    player_row = db.execute(select(Player.id, Player.name).where(Player.id == player_id)).first()
    if not player_row:
        raise HTTPException(status_code=404, detail="Player not found")

    return _summary_for_player(
        db=db,
        player_id=int(player_row.id),
        player_name=str(player_row.name),
        windows=w,
        weekday=weekday,
        map_name=map_name,
        third_place_only=third_place_only,
    )


@router.get("/summary/by-name/{name}")
def player_summary_by_name(
    name: str,
    windows: str = Query("30,90,365"),
    weekday: Optional[str] = Query(None),
    map_name: Optional[str] = Query(None),
    third_place_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    w = _parse_windows(windows)
    pid, pname = _player_id_by_name(db, name)

    return _summary_for_player(
        db=db,
        player_id=pid,
        player_name=pname,
        windows=w,
        weekday=weekday,
        map_name=map_name,
        third_place_only=third_place_only,
    )


@router.get("/kpr-delta-leaders")
def kpr_delta_leaders(
    days: int = Query(365, ge=1, le=3650),
    weekday: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    third_place_only: bool = Query(False),
    min_baseline_rounds: int = Query(200, ge=0, le=1000000),
    db: Session = Depends(get_db),
):
    return _kpr_delta_board(
        db=db,
        days=days,
        weekday=weekday,
        limit=limit,
        third_place_only=third_place_only,
        direction="leaders",
        min_baseline_rounds=min_baseline_rounds,
    )


@router.get("/kpr-delta-laggards")
def kpr_delta_laggards(
    days: int = Query(365, ge=1, le=3650),
    weekday: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    third_place_only: bool = Query(False),
    min_baseline_rounds: int = Query(200, ge=0, le=1000000),
    db: Session = Depends(get_db),
):
    return _kpr_delta_board(
        db=db,
        days=days,
        weekday=weekday,
        limit=limit,
        third_place_only=third_place_only,
        direction="laggards",
        min_baseline_rounds=min_baseline_rounds,
    )


@router.get("/weekday-kpr-leaders")
def weekday_kpr_leaders(
    days: int = Query(365, ge=1, le=3650),
    weekday: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    third_place_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    return _weekday_kpr_board(
        db=db,
        days=days,
        weekday=weekday,
        limit=limit,
        third_place_only=third_place_only,
        direction="leaders",
    )


@router.get("/weekday-kpr-laggards")
def weekday_kpr_laggards(
    days: int = Query(365, ge=1, le=3650),
    weekday: str = Query(...),
    limit: int = Query(10, ge=1, le=100),
    third_place_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    return _weekday_kpr_board(
        db=db,
        days=days,
        weekday=weekday,
        limit=limit,
        third_place_only=third_place_only,
        direction="laggards",
    )
