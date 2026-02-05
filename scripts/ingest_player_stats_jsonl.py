from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import Match, MatchStatsPage, Player, PlayerMapStat, Team


SEGMENTS = {
    0: "total",
    1: "t",
    2: "ct",
    3: "total",
    4: "t",
    5: "ct",
}


def _iter_lines(paths: List[Path]) -> Iterable[Tuple[str, int, str]]:
    for p in paths:
        with p.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                s = line.strip()
                if not s:
                    continue
                yield (str(p), i, s)


def _parse_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, int):
        return s
    try:
        return int(str(s).strip())
    except Exception:
        return None


def _parse_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).strip())
    except Exception:
        return None


def _parse_percent(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace("%", "")
    try:
        return float(t)
    except Exception:
        return None


def _parse_k_hs(s: Any) -> Tuple[Optional[int], Optional[int]]:
    if s is None:
        return (None, None)
    t = str(s).strip()
    if not t:
        return (None, None)
    if "(" in t and ")" in t:
        left, right = t.split("(", 1)
        k = _parse_int(left.strip())
        hs = _parse_int(right.replace(")", "").strip())
        return (k, hs)
    return (_parse_int(t), None)


def _get_or_create_team(db: Session, name: str) -> Team:
    t = db.scalar(select(Team).where(func.lower(Team.name) == func.lower(name)))
    if t:
        return t
    t = Team(name=name)
    db.add(t)
    db.flush()
    return t


def _get_or_create_player(db: Session, name: str) -> Player:
    p = db.scalar(select(Player).where(func.lower(Player.name) == func.lower(name)))
    if p:
        return p
    p = Player(name=name)
    db.add(p)
    db.flush()
    return p


def _upsert_match(
    db: Session,
    match_id: int,
    url: Optional[str],
    played_at: Optional[int],
    team1_id: int,
    team2_id: int,
    is_seeding_match: bool,
    is_third_place_decider: bool,
    event_id: Optional[int],
    series_id: Optional[int],
) -> None:
    stmt = insert(Match).values(
        id=match_id,
        source="hltv",
        url=url,
        played_at=played_at or 0,
        team1_id=team1_id,
        team2_id=team2_id,
        is_seeding_match=bool(is_seeding_match),
        is_third_place_decider=bool(is_third_place_decider),
        event_id=event_id,
        series_id=series_id,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=[Match.id])
    db.execute(stmt)


def _upsert_stats_page(db: Session, match_id: int, stats_match_id: int, stats_match_slug: Optional[str]) -> None:
    stmt = insert(MatchStatsPage).values(
        match_id=match_id,
        stats_match_id=stats_match_id,
        stats_match_slug=stats_match_slug,
        map_name=None,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=[MatchStatsPage.stats_match_id])
    db.execute(stmt)


def _insert_player_stats_rows(db: Session, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(PlayerMapStat).values(rows)
    stmt = stmt.on_conflict_do_nothing(
        constraint="uq_player_map_stats_key"
    )
    db.execute(stmt)


def _extract_table_team_name(table: Dict[str, Any]) -> Optional[str]:
    cols = table.get("columns") or []
    if not cols:
        return None
    return cols[0]


def _extract_player_name(row: Dict[str, Any], team_col: str) -> Optional[str]:
    v = row.get(team_col)
    if v is None:
        return None
    name = str(v).strip()
    return name or None


def _build_player_stat_row(
    match_id: int,
    stats_match_id: int,
    team_id: Optional[int],
    player_id: int,
    segment: str,
    raw_row: Dict[str, Any],
) -> Dict[str, Any]:
    kills, hs = _parse_k_hs(raw_row.get("K (hs)") or raw_row.get("eK (hs)"))
    deaths, _ = _parse_k_hs(raw_row.get("D (t)") or raw_row.get("eD (t)"))
    assists, _ = _parse_k_hs(raw_row.get("A (f)"))
    adr = _parse_float(raw_row.get("ADR") or raw_row.get("eADR"))
    kast = _parse_percent(raw_row.get("KAST") or raw_row.get("eKAST"))
    rating3 = _parse_float(raw_row.get("Rating3.0"))

    return {
        "match_id": match_id,
        "stats_match_id": stats_match_id,
        "team_id": team_id,
        "player_id": player_id,
        "segment": segment,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "hs_kills": hs,
        "adr": adr,
        "kast": kast,
        "rating3": rating3,
        "raw": raw_row,
    }


def _ingest_one(db: Session, payload: Dict[str, Any]) -> bool:
    match_id = _parse_int(payload.get("match_id"))
    if not match_id:
        return False

    team1_name = payload.get("team1_name")
    team2_name = payload.get("team2_name")
    if not team1_name or not team2_name:
        return False

    t1 = _get_or_create_team(db, str(team1_name))
    t2 = _get_or_create_team(db, str(team2_name))

    played_at = _parse_int(payload.get("timestamp"))
    url = payload.get("match_url")
    is_seeding_match = bool(payload.get("is_seeding_match") or False)
    is_third_place_decider = bool(payload.get("is_third_place_decider") or False)

    event_id = _parse_int(payload.get("event_id"))
    series_id = _parse_int(payload.get("series_id"))

    _upsert_match(
        db,
        match_id=match_id,
        url=str(url) if url else None,
        played_at=played_at,
        team1_id=t1.id,
        team2_id=t2.id,
        is_seeding_match=is_seeding_match,
        is_third_place_decider=is_third_place_decider,
        event_id=event_id,
        series_id=series_id,
    )

    stats_match_id = _parse_int(payload.get("stats_match_id"))
    if not stats_match_id:
        return False

    stats_match_slug = payload.get("stats_match_slug")
    _upsert_stats_page(db, match_id=match_id, stats_match_id=stats_match_id, stats_match_slug=str(stats_match_slug) if stats_match_slug else None)

    base_tables = payload.get("base_tables") or []
    if not isinstance(base_tables, list) or not base_tables:
        return False

    rows_to_insert: List[Dict[str, Any]] = []

    for table in base_tables:
        if not isinstance(table, dict):
            continue
        table_index = _parse_int(table.get("table_index"))
        if table_index is None:
            continue

        team_col = _extract_table_team_name(table)
        if not team_col:
            continue

        team_id = None
        if func.lower(team_col) is not None:
            if str(team_col).strip().lower() == str(team1_name).strip().lower():
                team_id = t1.id
            elif str(team_col).strip().lower() == str(team2_name).strip().lower():
                team_id = t2.id
            else:
                team_id = _get_or_create_team(db, str(team_col)).id

        segment = SEGMENTS.get(table_index, f"seg{table_index}")
        table_rows = table.get("rows") or []
        if not isinstance(table_rows, list):
            continue

        for r in table_rows:
            if not isinstance(r, dict):
                continue
            player_name = _extract_player_name(r, team_col)
            if not player_name:
                continue
            player = _get_or_create_player(db, player_name)
            rows_to_insert.append(
                _build_player_stat_row(
                    match_id=match_id,
                    stats_match_id=stats_match_id,
                    team_id=team_id,
                    player_id=player.id,
                    segment=segment,
                    raw_row=r,
                )
            )

    _insert_player_stats_rows(db, rows_to_insert)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", nargs="+", required=True)
    ap.add_argument("--commit-every", type=int, default=200)
    args = ap.parse_args()

    paths = [Path(p) for p in args.jsonl]

    ok = 0
    skipped = 0
    failed = 0

    db = SessionLocal()
    try:
        for idx, (src, line_no, line) in enumerate(_iter_lines(paths), start=1):
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    skipped += 1
                    continue
                did = _ingest_one(db, payload)
                if did:
                    ok += 1
                else:
                    skipped += 1
            except Exception:
                db.rollback()
                failed += 1

            if idx % args.commit_every == 0:
                db.commit()
                print(f"[OK] committed {idx} lines... (ok={ok}, skipped={skipped}, failed={failed})")

        db.commit()
    finally:
        db.close()

    print(f"Done. ok={ok}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
