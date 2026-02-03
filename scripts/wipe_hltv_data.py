from __future__ import annotations

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    db = SessionLocal()
    try:
        db.execute(text("delete from match_maps"))
        db.execute(text("delete from veto_actions"))
        db.execute(text("delete from matches where source = 'hltv' or source is null"))
        db.commit()
        print("wiped match_maps, veto_actions, and HLTV matches")
    finally:
        db.close()


if __name__ == "__main__":
    main()
