from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.db.models import Match, Team
from app.db.models.match_stats import MatchStatsPage, PlayerMapStat
from app.db.models.player import Player


FLOAT_RE = re.compile(r"(-?\d+(?:\.\d+)?)")
INT_RE = re.compile(r"(-?\d+)")


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    m = FLOAT_RE.search(s.replace(",", ""))
    return float(m.group(1)) if m else None


def _to_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    if not s:
        return None
    m = INT_RE.search(s.replace(",", ""))
    return int(m.group(1)) if m else None


def _pct_to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace("%", "").strip()
    return _to_float(s)


def _parse_k_hs(v: Any) -> Tuple[Optional[int], Optional[int]]:
    if v is None:
        return None, None
    s = str(v)
    m1 = re.search(r"(-?\d+)", s)
    if not m1:
        return None, None
    kills = int(m1.group(1))
    m2 = re.search(r"\(\s*(-?\d+)\s*\)", s)
    hs = int(m2.group(1)) if m2 else None
    return kills, hs


def _infer_segment_from_table_index(table_index: int) -> str:
    if table_index % 3 == 0:
        return "overall"
    if table_index % 3 == 1:
        return "t"
    return "ct"


def _team_name_from_columns(columns: List[str]) -> Optional[str]:
    if not columns:
        return None
    return str(columns[0]).strip() or None


def _player_name_from_row(team_col: str, row: Dict[str, Any]) -> Optional[str]:
    v = row.get(team_col)
    if v is None:
        return None
    name = str(v).strip()
    return name or None


def _find_team_id_case_insensitive(db: Session, name: str) -> Optional[int]:
    if not name:
        return None
    return db.scalar(select(Team.id).where(Team.name.ilike(name)))


def _upsert_player(db: Session, name: str) -> int:
    stmt = (
        insert(Player)
        .values(name=name)
        .on_conflict_do_update(
            constraint="uq_players_name",
            set_={"name": name},
        )
        .returning(Player.id)
    )
    return int(db.execute(stmt).scalar_one())


def _infer_map_name_from_map_results(payload: Dict[str, Any]) -> Optional[str]:
    mr = payload.get("map_results")
    if not isinstance(mr, list) or not mr:
        return None
    m0 = mr[0]
    if isinstance(m0, dict) and m0.get("map"):
        s = str(m0["map"]).strip()
        return s or None
    return None


def _upsert_match(db: Session, payload: Dict[str, Any]) -> None:
    match_id = payload.get("match_id")
    ts = payload.get("timestamp")
    if match_id is None or ts is None:
        return

    team1_name = payload.get("team1_name")
    team2_name = payload.get("team2_name")
    team1_id = _find_team_id_case_insensitive(db, str(team1_name)) if team1_name else None
    team2_id = _find_team_id_case_insensitive(db, str(team2_name)) if team2_name else None

    v = {
        "id": int(match_id),
        "played_at": int(ts),
        "match_url": payload.get("match_url"),
        "team1_id": team1_id,
        "team2_id": team2_id,
        "team1_name": str(team1_name) if team1_name is not None else None,
        "team2_name": str(team2_name) if team2_name is not None else None,
        "is_seeding_match": bool(payload.get("is_seeding_match") or False),
        "seeding_note": payload.get("seeding_note"),
        "is_third_place_decider": bool(payload.get("is_third_place_decider") or False),
        "third_place_note": payload.get("third_place_note"),
    }

    stmt = (
        insert(Match)
        .values(**v)
        .on_conflict_do_update(
            index_elements=[Match.id],
            set_=v,
        )
    )
    db.execute(stmt)


def _upsert_stats_page(
    db: Session,
    match_id: int,
    stats_match_id: int,
    stats_match_slug: Optional[str],
    map_name: Optional[str],
) -> None:
    stmt = (
        insert(MatchStatsPage)
        .values(
            match_id=match_id,
            stats_match_id=stats_match_id,
            stats_match_slug=stats_match_slug,
            map_name=map_name,
        )
        .on_conflict_do_update(
            constraint="uq_match_stats_pages_stats_match_id",
            set_={
                "match_id": match_id,
                "stats_match_slug": stats_match_slug,
                "map_name": map_name,
            },
        )
    )
    db.execute(stmt)


def _parse_stat_row(row: Dict[str, Any]) -> Dict[str, Any]:
    kills, hs = _parse_k_hs(row.get("K (hs)") or row.get("K (hs) "))
    deaths = _to_int(row.get("D (t)") or row.get("D"))
    assists = _to_int(row.get("A (f)") or row.get("A"))
    adr = _to_float(row.get("ADR"))
    kast = _pct_to_float(row.get("KAST") or row.get("KAST.1"))
    rating3 = _to_float(row.get("Rating3.0"))

    return {
        "kills": kills,
        "hs_kills": hs,
        "deaths": deaths,
        "assists": assists,
        "adr": adr,
        "kast": kast,
        "rating3": rating3,
    }


def _upsert_player_map_stat(
    db: Session,
    match_id: int,
    stats_match_id: int,
    segment: str,
    team_id: Optional[int],
    player_id: int,
    parsed: Dict[str, Any],
    raw: Dict[str, Any],
) -> None:
    stmt = (
        insert(PlayerMapStat)
        .values(
            match_id=match_id,
            stats_match_id=stats_match_id,
            segment=segment,
            team_id=team_id,
            player_id=player_id,
            kills=parsed.get("kills"),
            deaths=parsed.get("deaths"),
            assists=parsed.get("assists"),
            hs_kills=parsed.get("hs_kills"),
            adr=parsed.get("adr"),
            kast=parsed.get("kast"),
            rating3=parsed.get("rating3"),
            raw=raw,
        )
        .on_conflict_do_update(
            constraint="uq_player_map_stats_key",
            set_={
                "match_id": match_id,
                "team_id": team_id,
                "kills": parsed.get("kills"),
                "deaths": parsed.get("deaths"),
                "assists": parsed.get("assists"),
                "hs_kills": parsed.get("hs_kills"),
                "adr": parsed.get("adr"),
                "kast": parsed.get("kast"),
                "rating3": parsed.get("rating3"),
                "raw": raw,
            },
        )
    )
    db.execute(stmt)


def ingest_file(db: Session, path: Path, commit_every: int) -> Tuple[int, int]:
    ok = 0
    skipped = 0

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            payload = json.loads(line)

            match_id = payload.get("match_id")
            stats_match_id = payload.get("stats_match_id")
            base_tables = payload.get("base_tables")

            if match_id is None or stats_match_id is None or not isinstance(base_tables, list) or not base_tables:
                skipped += 1
                continue

            _upsert_match(db, payload)

            map_name = _infer_map_name_from_map_results(payload)
            _upsert_stats_page(
                db,
                match_id=int(match_id),
                stats_match_id=int(stats_match_id),
                stats_match_slug=payload.get("stats_match_slug"),
                map_name=map_name,
            )

            for t in base_tables:
                if not isinstance(t, dict):
                    continue
                cols = t.get("columns") or []
                rows = t.get("rows") or []
                if not isinstance(cols, list) or not isinstance(rows, list) or not rows:
                    continue

                team_col = _team_name_from_columns(cols)
                if not team_col:
                    continue

                team_id = _find_team_id_case_insensitive(db, team_col)
                segment = _infer_segment_from_table_index(int(t.get("table_index") or 0))

                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    player_name = _player_name_from_row(team_col, r)
                    if not player_name:
                        continue
                    player_id = _upsert_player(db, player_name)
                    parsed = _parse_stat_row(r)
                    _upsert_player_map_stat(
                        db,
                        match_id=int(match_id),
                        stats_match_id=int(stats_match_id),
                        segment=segment,
                        team_id=team_id,
                        player_id=player_id,
                        parsed=parsed,
                        raw=r,
                    )

            ok += 1
            if ok % commit_every == 0:
                db.commit()

    db.commit()
    return ok, skipped


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("jsonl_path")
    p.add_argument("--commit-every", type=int, default=250)
    args = p.parse_args()

    path = Path(args.jsonl_path)
    if not path.exists():
        raise SystemExit(f"file not found: {path}")

    with SessionLocal() as db:
        ok, skipped = ingest_file(db, path, args.commit_every)
        print(f"done ok={ok} skipped={skipped}")


if __name__ == "__main__":
    main()
