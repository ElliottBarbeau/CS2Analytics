from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.services.ingest_hltv_match import ingest_hltv_match


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            yield lineno, json.loads(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--commit-every", type=int, default=200)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    db = SessionLocal()
    ok = 0
    skipped = 0
    failed = 0

    try:
        for lineno, payload in iter_jsonl(path):
            if lineno < args.start:
                continue
            if args.limit and (ok + skipped) >= args.limit:
                break

            try:
                with db.begin_nested():
                    inserted = ingest_hltv_match(db, payload)
                    if inserted:
                        ok += 1
                    else:
                        skipped += 1
                        raise RuntimeError("skipped")
            except Exception as e:
                if str(e) == "skipped":
                    pass
                else:
                    failed += 1
                    print(f"[FAIL] line {lineno} match_id={payload.get('match_id')} err={e}")

            if ok and ok % args.commit_every == 0:
                db.commit()
                print(f"[OK] committed {ok} matches... (skipped={skipped}, failed={failed})")

        db.commit()
        print(f"Done. ok={ok}, skipped={skipped}, failed={failed}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
