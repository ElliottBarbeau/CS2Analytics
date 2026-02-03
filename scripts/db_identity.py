from __future__ import annotations

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                select
                  current_database() as db,
                  current_user as usr,
                  inet_server_addr() as server_ip,
                  inet_server_port() as server_port,
                  version() as version
                """
            )
        ).all()

        for r in rows:
            print(r)

        rows2 = db.execute(text("select count(*) from matches")).all()
        print("matches_count", rows2[0][0])
    finally:
        db.close()


if __name__ == "__main__":
    main()
