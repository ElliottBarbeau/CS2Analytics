from __future__ import annotations

import argparse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from app.core.config import get_env
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat
from app.db.models.player import Player


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("player_name")
    args = ap.parse_args()

    engine = create_engine(get_env("DATABASE_URL"), pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        pid = db.execute(
            select(Player.id).where(func.lower(Player.name) == args.player_name.strip().lower())
        ).scalar_one_or_none()
        if not pid:
            raise SystemExit("player not found")

        rounds_expr = func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)

        q = (
            select(
                PlayerMapStat.match_id.label("match_id"),
                func.count(func.distinct(PlayerMapStat.stats_match_id)).label("maps"),
                func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
                func.sum(rounds_expr).label("rounds"),
            )
            .select_from(PlayerMapStat)
            .join(Match, Match.id == PlayerMapStat.match_id)
            .join(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.player_id == pid,
                PlayerMapStat.segment == "total",
                Match.is_third_place_decider.is_(True),
            )
            .group_by(PlayerMapStat.match_id)
            .order_by(PlayerMapStat.match_id.asc())
        )

        rows = db.execute(q).all()
        total_k = 0
        total_r = 0
        print(f"player={args.player_name} player_id={pid}")
        print("third_place_matches:")
        for r in rows:
            k = int(r.kills or 0)
            rd = int(r.rounds or 0)
            total_k += k
            total_r += rd
            print(f"  match_id={int(r.match_id)} maps={int(r.maps or 0)} kills={k} rounds={rd}")

        print(f"TOTAL: kills={total_k} rounds={total_r} kpr={(total_k/total_r if total_r else None)}")


if __name__ == "__main__":
    main()
