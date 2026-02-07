from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat
from app.db.models.player import Player


router = APIRouter(prefix="/debug/match-stats", tags=["debug"])


@router.get("/counts")
def counts(db: Session = Depends(get_db)):
    total = int(db.scalar(select(func.count()).select_from(PlayerMapStat)) or 0)
    total_segment = dict(
        db.execute(
            select(PlayerMapStat.segment, func.count())
            .group_by(PlayerMapStat.segment)
            .order_by(func.count().desc())
        ).all()
    )

    null_map_name = int(
        db.scalar(select(func.count()).select_from(PlayerMapStat).where(PlayerMapStat.map_name.is_(None))) or 0
    )

    distinct_matches = int(
        db.scalar(select(func.count(func.distinct(PlayerMapStat.match_id))).select_from(PlayerMapStat)) or 0
    )
    distinct_stats_pages = int(
        db.scalar(select(func.count(func.distinct(PlayerMapStat.stats_match_id))).select_from(PlayerMapStat)) or 0
    )
    distinct_players = int(
        db.scalar(select(func.count(func.distinct(PlayerMapStat.player_id))).select_from(PlayerMapStat)) or 0
    )

    return {
        "player_map_stats_rows": total,
        "distinct_matches_in_stats": distinct_matches,
        "distinct_stats_match_ids": distinct_stats_pages,
        "distinct_players_in_stats": distinct_players,
        "rows_with_null_map_name": null_map_name,
        "segment_counts": {str(k): int(v) for k, v in total_segment.items()},
    }


@router.get("/player/{player_id}/counts")
def player_counts(player_id: int, db: Session = Depends(get_db)):
    player_row = db.execute(select(Player.id, Player.name).where(Player.id == player_id)).first()
    if not player_row:
        raise HTTPException(status_code=404, detail="Player not found")

    total = int(
        db.scalar(select(func.count()).select_from(PlayerMapStat).where(PlayerMapStat.player_id == player_id)) or 0
    )
    by_segment = dict(
        db.execute(
            select(PlayerMapStat.segment, func.count())
            .where(PlayerMapStat.player_id == player_id)
            .group_by(PlayerMapStat.segment)
            .order_by(func.count().desc())
        ).all()
    )
    null_map_name = int(
        db.scalar(
            select(func.count())
            .select_from(PlayerMapStat)
            .where(PlayerMapStat.player_id == player_id, PlayerMapStat.map_name.is_(None))
        )
        or 0
    )
    distinct_stats_pages = int(
        db.scalar(
            select(func.count(func.distinct(PlayerMapStat.stats_match_id)))
            .select_from(PlayerMapStat)
            .where(PlayerMapStat.player_id == player_id)
        )
        or 0
    )
    distinct_matches = int(
        db.scalar(
            select(func.count(func.distinct(PlayerMapStat.match_id)))
            .select_from(PlayerMapStat)
            .where(PlayerMapStat.player_id == player_id)
        )
        or 0
    )

    return {
        "player_id": int(player_row.id),
        "player_name": player_row.name,
        "rows": total,
        "distinct_matches": distinct_matches,
        "distinct_stats_match_ids": distinct_stats_pages,
        "rows_with_null_map_name": null_map_name,
        "segment_counts": {str(k): int(v) for k, v in by_segment.items()},
    }


@router.get("/match-maps/rounds-health")
def match_maps_rounds_health(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    total = int(db.scalar(select(func.count()).select_from(MatchMap)) or 0)

    null_any = int(
        db.scalar(
            select(func.count())
            .select_from(MatchMap)
            .where(MatchMap.team1_rounds.is_(None), MatchMap.team2_rounds.is_(None))
        )
        or 0
    )

    zero_any = int(
        db.scalar(
            select(func.count())
            .select_from(MatchMap)
            .where(func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0) == 0)
        )
        or 0
    )

    worst = db.execute(
        select(
            MatchMap.match_id,
            func.count().label("maps"),
            func.sum(case((MatchMap.team1_rounds.is_(None), 1), else_=0)).label("t1_null"),
            func.sum(case((MatchMap.team2_rounds.is_(None), 1), else_=0)).label("t2_null"),
            func.sum(
                case(
                    ((func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0) == 0), 1),
                    else_=0,
                )
            ).label("rounds_zero"),
        )
        .group_by(MatchMap.match_id)
        .order_by(func.sum(case(((func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0) == 0), 1), else_=0)).desc())
        .limit(limit)
    ).all()

    return {
        "match_map_rows": total,
        "rows_where_both_rounds_null": null_any,
        "rows_where_total_rounds_zero": zero_any,
        "worst_matches_by_zero_round_maps": [
            {
                "match_id": int(r.match_id),
                "maps": int(r.maps),
                "team1_rounds_null": int(r.t1_null or 0),
                "team2_rounds_null": int(r.t2_null or 0),
                "maps_with_zero_rounds": int(r.rounds_zero or 0),
            }
            for r in worst
        ],
    }


@router.get("/rounds-join-health")
def rounds_join_health(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)

    total_total = int(
        db.scalar(select(func.count()).select_from(PlayerMapStat).where(PlayerMapStat.segment == "total")) or 0
    )

    joinable_total = int(
        db.scalar(
            select(func.count())
            .select_from(PlayerMapStat)
            .join(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(PlayerMapStat.segment == "total")
        )
        or 0
    )

    joinable_with_zero_rounds = int(
        db.scalar(
            select(func.count())
            .select_from(PlayerMapStat)
            .join(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(PlayerMapStat.segment == "total", rounds_expr == 0)
        )
        or 0
    )

    not_joinable = total_total - joinable_total

    worst = db.execute(
        select(
            PlayerMapStat.match_id.label("match_id"),
            func.count().label("total_rows"),
            func.sum(case((PlayerMapStat.map_name.is_(None), 1), else_=0)).label("null_map_name_rows"),
        )
        .where(PlayerMapStat.segment == "total")
        .group_by(PlayerMapStat.match_id)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()

    return {
        "segment_total_rows": total_total,
        "segment_total_rows_joinable_to_match_maps": joinable_total,
        "segment_total_rows_not_joinable": not_joinable,
        "joinable_rows_with_zero_rounds": joinable_with_zero_rounds,
        "top_matches_by_total_segment_rows": [
            {
                "match_id": int(r.match_id),
                "rows": int(r.total_rows),
                "null_map_name_rows": int(r.null_map_name_rows or 0),
            }
            for r in worst
        ],
    }


@router.get("/match/{match_id}/stats-shape")
def match_stats_shape(match_id: int, db: Session = Depends(get_db)):
    m = db.execute(select(Match.id, Match.url).where(Match.id == match_id)).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    stats_rows = db.execute(
        select(
            PlayerMapStat.stats_match_id,
            PlayerMapStat.map_name,
            PlayerMapStat.segment,
            func.count().label("rows"),
        )
        .where(PlayerMapStat.match_id == match_id)
        .group_by(PlayerMapStat.stats_match_id, PlayerMapStat.map_name, PlayerMapStat.segment)
        .order_by(PlayerMapStat.stats_match_id.asc(), PlayerMapStat.map_name.asc(), PlayerMapStat.segment.asc())
    ).all()

    maps = db.execute(
        select(MatchMap.map_name, MatchMap.team1_rounds, MatchMap.team2_rounds)
        .where(MatchMap.match_id == match_id)
        .order_by(MatchMap.map_name.asc())
    ).all()

    return {
        "match_id": int(m.id),
        "match_url": m.url,
        "player_map_stats_groups": [
            {
                "stats_match_id": int(r.stats_match_id),
                "map_name": r.map_name,
                "segment": r.segment,
                "rows": int(r.rows),
            }
            for r in stats_rows
        ],
        "match_maps": [
            {
                "map_name": r.map_name,
                "team1_rounds": int(r.team1_rounds) if r.team1_rounds is not None else None,
                "team2_rounds": int(r.team2_rounds) if r.team2_rounds is not None else None,
            }
            for r in maps
        ],
    }


@router.get("/suspect-matches")
def suspect_matches(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(
            PlayerMapStat.match_id.label("match_id"),
            func.count(func.distinct(PlayerMapStat.stats_match_id)).label("stats_pages"),
            func.count().label("rows"),
            func.sum(case((PlayerMapStat.map_name.is_(None), 1), else_=0)).label("null_map_name_rows"),
        )
        .where(PlayerMapStat.segment == "total")
        .group_by(PlayerMapStat.match_id)
        .order_by(func.count(func.distinct(PlayerMapStat.stats_match_id)).asc(), func.count().asc())
        .limit(limit)
    ).all()

    return [
        {
            "match_id": int(r.match_id),
            "distinct_stats_pages": int(r.stats_pages),
            "total_segment_rows": int(r.rows),
            "null_map_name_rows": int(r.null_map_name_rows or 0),
        }
        for r in rows
    ]
