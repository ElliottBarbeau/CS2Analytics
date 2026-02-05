from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import MatchStatsPage, PlayerMapStat
from app.db.models.player import Player
from app.db.models.team import Team
from app.db.models.veto_action import VetoAction


router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/counts")
def counts(db: Session = Depends(get_db)) -> Dict[str, int]:
    return {
        "teams": int(db.scalar(select(func.count()).select_from(Team)) or 0),
        "players": int(db.scalar(select(func.count()).select_from(Player)) or 0),
        "matches": int(db.scalar(select(func.count()).select_from(Match)) or 0),
        "match_maps": int(db.scalar(select(func.count()).select_from(MatchMap)) or 0),
        "veto_actions": int(db.scalar(select(func.count()).select_from(VetoAction)) or 0),
        "match_stats_pages": int(db.scalar(select(func.count()).select_from(MatchStatsPage)) or 0),
        "player_map_stats": int(db.scalar(select(func.count()).select_from(PlayerMapStat)) or 0),
    }


@router.get("/teams")
def list_teams(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    stmt = select(Team).order_by(Team.id.asc())
    if q:
        stmt = stmt.where(func.lower(Team.name).contains(q.strip().lower()))
    rows = db.scalars(stmt.limit(limit)).all()
    return [{"id": t.id, "name": t.name, "grid_team_id": getattr(t, "grid_team_id", None)} for t in rows]


@router.get("/players")
def list_players(
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    stmt = select(Player).order_by(Player.id.asc())
    if q:
        stmt = stmt.where(func.lower(Player.name).contains(q.strip().lower()))
    rows = db.scalars(stmt.limit(limit)).all()
    return [{"id": p.id, "name": p.name} for p in rows]


@router.get("/matches/latest")
def latest_matches(
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    ms = db.scalars(select(Match).order_by(desc(Match.played_at)).limit(limit)).all()
    team_ids = {m.team1_id for m in ms} | {m.team2_id for m in ms}
    teams = db.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    tmap = {t.id: t.name for t in teams}

    out = []
    for m in ms:
        out.append(
            {
                "match_id": m.id,
                "played_at": m.played_at,
                "team1_id": m.team1_id,
                "team1_name": tmap.get(m.team1_id),
                "team2_id": m.team2_id,
                "team2_name": tmap.get(m.team2_id),
                "is_seeding_match": getattr(m, "is_seeding_match", None),
                "is_third_place_decider": getattr(m, "is_third_place_decider", None),
                "event_id": getattr(m, "event_id", None),
                "series_id": getattr(m, "series_id", None),
                "url": m.url,
            }
        )
    return out


@router.get("/matches/{match_id}")
def match_detail(match_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    m = db.get(Match, match_id)
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")

    t1 = db.get(Team, m.team1_id)
    t2 = db.get(Team, m.team2_id)

    maps = db.execute(
        select(MatchMap)
        .where(MatchMap.match_id == match_id)
        .order_by(MatchMap.id.asc())
    ).scalars().all()

    veto = db.execute(
        select(VetoAction)
        .where(VetoAction.match_id == match_id)
        .order_by(VetoAction.order_index.asc())
    ).scalars().all()

    page = db.execute(select(MatchStatsPage).where(MatchStatsPage.match_id == match_id)).scalar_one_or_none()

    stats_count = 0
    sample_stats = []
    if page:
        stats_count = int(db.scalar(select(func.count()).select_from(PlayerMapStat).where(PlayerMapStat.stats_match_id == page.stats_match_id)) or 0)
        sample_stats = db.execute(
            select(PlayerMapStat).where(PlayerMapStat.stats_match_id == page.stats_match_id).order_by(PlayerMapStat.id.asc()).limit(10)
        ).scalars().all()

    pids = {s.player_id for s in sample_stats}
    players = db.scalars(select(Player).where(Player.id.in_(pids))).all()
    pname = {p.id: p.name for p in players}

    return {
        "match": {
            "id": m.id,
            "played_at": m.played_at,
            "url": m.url,
            "team1": {"id": m.team1_id, "name": t1.name if t1 else None},
            "team2": {"id": m.team2_id, "name": t2.name if t2 else None},
            "is_seeding_match": getattr(m, "is_seeding_match", None),
            "is_third_place_decider": getattr(m, "is_third_place_decider", None),
            "event_id": getattr(m, "event_id", None),
            "series_id": getattr(m, "series_id", None),
        },
        "maps": [
            {
                "map": x.map_name,
                "team1_rounds": x.team1_rounds,
                "team2_rounds": x.team2_rounds,
                "winner_team_id": x.winner_team_id,
            }
            for x in maps
        ],
        "veto": [
            {
                "order": x.order_index,
                "team_id": x.team_id,
                "action": x.action,
                "map": x.map_name,
            }
            for x in veto
        ],
        "stats_page": (
            {
                "stats_match_id": page.stats_match_id,
                "stats_match_slug": page.stats_match_slug,
                "map_name": page.map_name,
                "player_rows": stats_count,
                "sample_player_rows": [
                    {
                        "segment": s.segment,
                        "team_id": s.team_id,
                        "player_id": s.player_id,
                        "player_name": pname.get(s.player_id),
                        "kills": s.kills,
                        "deaths": s.deaths,
                        "assists": s.assists,
                        "hs_kills": s.hs_kills,
                        "adr": s.adr,
                        "kast": s.kast,
                        "rating3": s.rating3,
                    }
                    for s in sample_stats
                ],
            }
            if page
            else None
        ),
    }


@router.get("/teams/{team_id}/veto/summary")
def team_veto_summary(
    team_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    rows = db.execute(
        select(VetoAction.action, VetoAction.map_name, func.count().label("n"))
        .where(VetoAction.team_id == team_id, VetoAction.map_name.is_not(None))
        .group_by(VetoAction.action, VetoAction.map_name)
        .order_by(desc(func.count()), VetoAction.action.asc(), VetoAction.map_name.asc())
        .limit(limit)
    ).all()

    out = [{"action": r.action, "map": r.map_name, "count": int(r.n)} for r in rows]
    return {"team_id": team_id, "team_name": team.name, "summary": out}


@router.get("/teams/{team_id}/players/top")
def top_players_for_team(
    team_id: int,
    segment: str = Query(default="total"),
    limit: int = Query(default=25, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    seg = segment.strip().lower()
    if seg not in {"total", "t", "ct"}:
        raise HTTPException(status_code=400, detail="segment must be one of: total, t, ct")

    rows = db.execute(
        select(
            PlayerMapStat.player_id,
            func.count().label("rows"),
            func.avg(PlayerMapStat.rating3).label("avg_rating3"),
            func.avg(PlayerMapStat.adr).label("avg_adr"),
            func.avg(PlayerMapStat.kast).label("avg_kast"),
        )
        .where(PlayerMapStat.team_id == team_id, PlayerMapStat.segment == seg)
        .group_by(PlayerMapStat.player_id)
        .order_by(desc(func.avg(PlayerMapStat.rating3)), desc(func.count()))
        .limit(limit)
    ).all()

    pids = [int(r.player_id) for r in rows]
    players = db.scalars(select(Player).where(Player.id.in_(pids))).all()
    pname = {p.id: p.name for p in players}

    return {
        "team_id": team_id,
        "team_name": team.name,
        "segment": seg,
        "players": [
            {
                "player_id": int(r.player_id),
                "player_name": pname.get(int(r.player_id)),
                "rows": int(r.rows),
                "avg_rating3": float(r.avg_rating3) if r.avg_rating3 is not None else None,
                "avg_adr": float(r.avg_adr) if r.avg_adr is not None else None,
                "avg_kast": float(r.avg_kast) if r.avg_kast is not None else None,
            }
            for r in rows
        ],
    }
