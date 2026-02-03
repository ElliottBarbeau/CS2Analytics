from __future__ import annotations

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    db = SessionLocal()
    try:
        teams = db.execute(text("select count(*) from teams")).scalar_one()
        matches = db.execute(text("select count(*) from matches")).scalar_one()
        veto_actions = db.execute(text("select count(*) from veto_actions")).scalar_one()
        matches_with_veto = db.execute(text("select count(distinct match_id) from veto_actions")).scalar_one()
        print("teams", teams)
        print("matches", matches)
        print("veto_actions", veto_actions)
        print("matches_with_veto", matches_with_veto)
    finally:
        db.close()


if __name__ == "__main__":
    main()
