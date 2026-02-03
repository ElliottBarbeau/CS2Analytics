from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import text
from app.db.session import SessionLocal
from app.services.ingest_hltv_match import should_ingest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--sample", type=int, default=25)
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    valid_ids: list[int] = []
    total = 0
    valid = 0
    skipped = 0

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            payload = json.loads(line)
            if should_ingest(payload):
                valid += 1
                mid = payload.get("match_id")
                if mid is not None:
                    valid_ids.append(int(mid))
            else:
                skipped += 1

    unique_valid_ids = set(valid_ids)

    db = SessionLocal()
    try:
        db_count = db.execute(text("select count(*) from matches")).scalar_one()
        db_ids = set(r[0] for r in db.execute(text("select id from matches")).all())

        missing = sorted(list(unique_valid_ids - db_ids))
        extra = sorted(list(db_ids - unique_valid_ids))

        print("jsonl_total_lines", total)
        print("jsonl_valid_payloads", valid)
        print("jsonl_skipped_payloads", skipped)
        print("jsonl_valid_match_ids", len(valid_ids))
        print("jsonl_unique_valid_match_ids", len(unique_valid_ids))
        print("db_matches_count", db_count)
        print("db_unique_match_ids", len(db_ids))
        print("missing_in_db", len(missing))
        print("extra_in_db", len(extra))

        if missing:
            print("missing_sample", missing[: args.sample])
        if extra:
            print("extra_sample", extra[: args.sample])
    finally:
        db.close()


if __name__ == "__main__":
    main()
