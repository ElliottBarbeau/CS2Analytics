from __future__ import annotations

import argparse
import time

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--team-id", type=int, required=True)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    cutoff = int(time.time()) - args.days * 24 * 60 * 60

    sql = text(
        """
        select
          va.map_name,
          count(*) as ban_count
        from veto_actions va
        join matches m on m.id = va.match_id
        where
          va.action = 'removed'
          and va.team_id = :team_id
          and m.played_at >= :cutoff
        group by va.map_name
        order by ban_count desc, va.map_name asc
        limit 10
        """
    )

    db = SessionLocal()
    try:
        rows = db.execute(sql, {"team_id": args.team_id, "cutoff": cutoff}).all()
        print(f"team_id={args.team_id} window_days={args.days}")
        for map_name, ban_count in rows:
            print(map_name, ban_count)
    finally:
        db.close()


if __name__ == "__main__":
    main()
