from __future__ import annotations

import argparse
import time

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    cutoff = int(time.time()) - args.days * 24 * 60 * 60

    sql = text(
        """
        select
          m.id,
          m.played_at,
          t1.name as team1,
          t2.name as team2
        from matches m
        join teams t1 on t1.id = m.team1_id
        join teams t2 on t2.id = m.team2_id
        where
          m.played_at >= :cutoff
          and (m.team1_id = :team_id or m.team2_id = :team_id)
        order by m.played_at desc
        limit :limit
        """
    )

    db = SessionLocal()
    try:
        rows = db.execute(sql, {"team_id": args.team_id, "cutoff": cutoff, "limit": args.limit}).all()
        for match_id, played_at, team1, team2 in rows:
            print(match_id, played_at, team1, "vs", team2)
    finally:
        db.close()


if __name__ == "__main__":
    main()
