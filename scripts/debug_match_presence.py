from __future__ import annotations

import argparse
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_env
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("match_id", type=int)
    args = ap.parse_args()

    engine = create_engine(get_env("DATABASE_URL"), pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        m = db.execute(select(Match).where(Match.id == args.match_id)).scalar_one_or_none()
        if not m:
            print(f"match_id={args.match_id} NOT in Match table")
            return

        print(
            f"match_id={m.id} played_at={m.played_at} is_third_place_decider={m.is_third_place_decider} "
            f"team1_id={m.team1_id} team2_id={m.team2_id}"
        )

        mm = db.execute(select(func.count()).select_from(MatchMap).where(MatchMap.match_id == args.match_id)).scalar_one()
        ps = db.execute(select(func.count()).select_from(PlayerMapStat).where(PlayerMapStat.match_id == args.match_id)).scalar_one()

        print(f"MatchMap rows={int(mm)}")
        print(f"PlayerMapStat rows={int(ps)}")


if __name__ == "__main__":
    main()
