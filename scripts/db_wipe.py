from __future__ import annotations

import argparse

from sqlalchemy import create_engine, text

from app.core.config import get_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="public")
    ap.add_argument("--keep-alembic", action="store_true")
    args = ap.parse_args()

    db_url = get_env("DATABASE_URL")
    engine = create_engine(db_url, pool_pre_ping=True)

    with engine.begin() as conn:
        q = text(
            """
            select tablename
            from pg_catalog.pg_tables
            where schemaname = :schema
            order by tablename
            """
        )
        rows = conn.execute(q, {"schema": args.schema}).all()
        tables = [r[0] for r in rows]

        if args.keep_alembic:
            tables = [t for t in tables if t != "alembic_version"]

        if not tables:
            print("No tables found to wipe.")
            return

        ident = ", ".join([f'"{args.schema}"."{t}"' for t in tables])
        conn.execute(text(f"TRUNCATE TABLE {ident} RESTART IDENTITY CASCADE"))

    print(f"Wiped {len(tables)} tables in schema '{args.schema}'.")


if __name__ == "__main__":
    main()
