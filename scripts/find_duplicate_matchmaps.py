from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_env
from app.db.models.match_map import MatchMap


def main() -> None:
    db_url = get_env("DATABASE_URL")
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        rows = db.execute(
            select(
                MatchMap.match_id,
                func.lower(MatchMap.map_name).label("map_lc"),
                func.count().label("n"),
            )
            .group_by(MatchMap.match_id, func.lower(MatchMap.map_name))
            .having(func.count() > 1)
            .order_by(func.count().desc(), MatchMap.match_id.asc())
            .limit(200)
        ).all()

        for r in rows:
            print(f"match_id={r.match_id} map={r.map_lc} dupes={int(r.n)}")


if __name__ == "__main__":
    main()
