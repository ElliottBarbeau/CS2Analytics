from __future__ import annotations

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    db = SessionLocal()
    try:
        q = [
            ("teams", "select count(*) from teams"),
            ("matches", "select count(*) from matches"),
            ("veto_actions", "select count(*) from veto_actions"),
            ("match_maps", "select count(*) from match_maps"),
            (
                "matches_with_veto",
                "select count(distinct match_id) from veto_actions",
            ),
            (
                "avg_veto_actions_per_match",
                "select round(avg(cnt)::numeric, 2) from (select match_id, count(*) cnt from veto_actions group by match_id) x",
            ),
            (
                "veto_action_types",
                "select action, count(*) from veto_actions group by action order by count(*) desc",
            ),
            (
                "latest_match_ts",
                "select max(played_at) from matches",
            ),
            (
                "earliest_match_ts",
                "select min(played_at) from matches",
            ),
        ]

        for name, sql in q:
            rows = db.execute(text(sql)).all()
            print(name)
            for r in rows:
                print("  ", r)
    finally:
        db.close()


if __name__ == "__main__":
    main()
