from __future__ import annotations

import argparse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from app.core.config import get_env
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    args = ap.parse_args()

    engine = create_engine(get_env("DATABASE_URL"), pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        mm_names = db.execute(
            select(MatchMap.map_name).where(MatchMap.match_id == args.match_id).order_by(MatchMap.map_name.asc())
        ).all()
        ps_names = db.execute(
            select(PlayerMapStat.map_name)
            .where(PlayerMapStat.match_id == args.match_id, PlayerMapStat.segment == "total")
            .group_by(PlayerMapStat.map_name)
            .order_by(PlayerMapStat.map_name.asc())
        ).all()

        mm_set = {str(r[0]).strip().lower() for r in mm_names if r[0]}
        ps_set = {str(r[0]).strip().lower() for r in ps_names if r[0]}

        print(f"match_id={args.match_id}")
        print(f"MatchMap map_names ({len(mm_set)}): {sorted(mm_set)}")
        print(f"PlayerMapStat map_names ({len(ps_set)}): {sorted(ps_set)}")
        print(f"In stats but not in match_maps: {sorted(ps_set - mm_set)}")
        print(f"In match_maps but not in stats: {sorted(mm_set - ps_set)}")

        not_joinable = db.execute(
            select(func.count())
            .select_from(PlayerMapStat)
            .outerjoin(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.match_id == args.match_id,
                PlayerMapStat.segment == "total",
                MatchMap.match_id.is_(None),
            )
        ).scalar_one()

        print(f"total-segment stat rows NOT joinable to MatchMap: {int(not_joinable)}")


if __name__ == "__main__":
    main()
