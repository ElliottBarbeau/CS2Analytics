from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import Match, MatchMap, VetoAction


def get_team_summary(db: Session, team_id: int, now_ts: Optional[int] = None) -> Dict[str, Any]:
    now = int(now_ts or time.time())
    cutoff_30 = now - 30 * 24 * 60 * 60

    permaban_row = db.execute(
        select(
            func.lower(VetoAction.map_name).label("map_name"),
            func.count().label("ban_count"),
        )
        .join(Match, Match.id == VetoAction.match_id)
        .where(
            Match.played_at >= cutoff_30,
            VetoAction.team_id == team_id,
            VetoAction.action == "removed",
            VetoAction.map_name.is_not(None),
        )
        .group_by(func.lower(VetoAction.map_name))
        .order_by(func.count().desc(), func.lower(VetoAction.map_name).asc())
        .limit(1)
    ).first()

    permaban = None
    if permaban_row:
        permaban = {"map": permaban_row.map_name, "ban_count": int(permaban_row.ban_count)}

    win_case = case((MatchMap.winner_team_id == team_id, 1), else_=0)

    map_rows = db.execute(
        select(
            func.lower(MatchMap.map_name).label("map_name"),
            func.count().label("played"),
            func.coalesce(func.sum(win_case), 0).label("wins"),
        )
        .join(Match, Match.id == MatchMap.match_id)
        .where(
            Match.played_at >= cutoff_30,
            MatchMap.map_name.is_not(None),
            (Match.team1_id == team_id) | (Match.team2_id == team_id),
        )
        .group_by(func.lower(MatchMap.map_name))
    ).all()

    maps = []
    for r in map_rows:
        played = int(r.played)
        wins = int(r.wins or 0)
        winrate = (wins / played) if played else None
        maps.append({"map": r.map_name, "played": played, "wins": wins, "winrate": winrate})

    eligible = [m for m in maps if m["played"] > 0 and m["winrate"] is not None]

    strongest = None
    weakest = None
    if eligible:
        strongest = sorted(eligible, key=lambda x: (x["winrate"], x["played"]), reverse=True)[0]
        weakest = sorted(eligible, key=lambda x: (x["winrate"], -x["played"]))[0]

    return {
        "team_id": team_id,
        "window_days": 30,
        "permaban": permaban,
        "strongest_map": strongest,
        "weakest_map": weakest,
        "maps": sorted(maps, key=lambda x: x["map"]),
    }
