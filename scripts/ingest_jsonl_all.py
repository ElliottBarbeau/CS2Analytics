from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.player import Player
from app.db.models.team import Team
from app.db.models.veto_action import VetoAction
from app.db.models.match_stats import MatchStatsPage, PlayerMapStat


KHS_RE = re.compile(r"^\s*(\d+)\s*\(\s*(\d+)\s*\)\s*$")
NUM_RE = re.compile(r"^\s*(\d+)\s*$")
PCT_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*$")


def _lower(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return s.strip().lower()


def _parse_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, float):
        return int(s)
    if isinstance(s, str):
        m = NUM_RE.match(s.strip())
        if m:
            return int(m.group(1))
    return None


def _parse_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        try:
            return float(s.strip())
        except Exception:
            return None
    return None


def _parse_pct_to_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        m = PCT_RE.match(s)
        if not m:
            return None
        return float(m.group(1)) / 100.0
    return None


def _parse_k_hs(v: Any) -> Tuple[Optional[int], Optional[int]]:
    if v is None:
        return None, None
    if isinstance(v, str):
        m = KHS_RE.match(v)
        if m:
            return int(m.group(1)), int(m.group(2))
        i = _parse_int(v)
        return i, None
    if isinstance(v, int):
        return v, None
    return None, None


def _parse_deaths(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if ":" in s:
            s = s.split(":")[0].strip()
        m = re.match(r"^\s*(\d+)", s)
        if m:
            return int(m.group(1))
    return None


def _pick_segment(table_index: int) -> str:
    mod = table_index % 3
    if mod == 0:
        return "total"
    if mod == 1:
        return "t"
    return "ct"


def _iter_jsonl(paths: List[Path]) -> Iterable[Tuple[Path, int, Dict[str, Any]]]:
    for p in paths:
        with p.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield p, i, obj


def _ensure_team(db: Session, name: str) -> int:
    stmt = (
        pg_insert(Team)
        .values(name=name)
        .on_conflict_do_update(index_elements=[Team.name], set_={"name": name})
        .returning(Team.id)
    )
    tid = db.execute(stmt).scalar_one()
    return int(tid)


def _ensure_player(db: Session, name: str) -> int:
    stmt = (
        pg_insert(Player)
        .values(name=name)
        .on_conflict_do_update(index_elements=[Player.name], set_={"name": name})
        .returning(Player.id)
    )
    pid = db.execute(stmt).scalar_one()
    return int(pid)


def _upsert_match(
    db: Session,
    match_id: int,
    url: Optional[str],
    played_at: int,
    team1_id: int,
    team2_id: int,
    is_seeding_match: bool,
    is_third_place_decider: bool,
    event_id: Optional[int],
    series_id: Optional[int],
) -> None:
    stmt = (
        pg_insert(Match)
        .values(
            id=match_id,
            source="hltv",
            url=url,
            played_at=played_at,
            team1_id=team1_id,
            team2_id=team2_id,
            is_seeding_match=bool(is_seeding_match),
            is_third_place_decider=bool(is_third_place_decider),
            event_id=event_id,
            series_id=series_id,
        )
        .on_conflict_do_update(
            index_elements=[Match.id],
            set_={
                "url": url,
                "played_at": played_at,
                "team1_id": team1_id,
                "team2_id": team2_id,
                "is_seeding_match": bool(is_seeding_match),
                "is_third_place_decider": bool(is_third_place_decider),
                "event_id": event_id,
                "series_id": series_id,
            },
        )
    )
    db.execute(stmt)


def _replace_veto_actions(db: Session, match_id: int, actions: List[Dict[str, Any]], team_name_to_id: Dict[str, int]) -> None:
    if not actions:
        return

    rows = []
    for idx, a in enumerate(actions, start=1):
        action = a.get("action")
        map_name = a.get("map")
        if not action or not map_name:
            continue
        tname = a.get("team")
        team_id = None
        if isinstance(tname, str):
            team_id = team_name_to_id.get(_lower(tname) or "")
        rows.append(
            {
                "match_id": match_id,
                "order_index": idx,
                "team_id": team_id,
                "action": str(action),
                "map_name": str(map_name),
            }
        )

    if not rows:
        return

    ins = pg_insert(VetoAction).values(rows)
    upd = ins.on_conflict_do_update(
        constraint="uq_veto_order",
        set_={
            "team_id": ins.excluded.team_id,
            "action": ins.excluded.action,
            "map_name": ins.excluded.map_name,
        },
    )
    db.execute(upd)


def _replace_match_maps(
    db: Session,
    match_id: int,
    maps: List[Dict[str, Any]],
    team1_name: str,
    team2_name: str,
    team1_id: int,
    team2_id: int,
) -> None:
    db.execute(delete(MatchMap).where(MatchMap.match_id == match_id))

    if not maps:
        return

    t1 = _lower(team1_name) or ""
    t2 = _lower(team2_name) or ""
    out = []
    for m in maps:
        map_name = m.get("map")
        if not map_name:
            continue
        w = _lower(m.get("winner")) if isinstance(m.get("winner"), str) else None
        winner_team_id = None
        if w == t1:
            winner_team_id = team1_id
        elif w == t2:
            winner_team_id = team2_id

        out.append(
            MatchMap(
                match_id=match_id,
                map_name=str(map_name),
                team1_rounds=_parse_int(m.get("team1_rounds")),
                team2_rounds=_parse_int(m.get("team2_rounds")),
                winner_team_id=winner_team_id,
            )
        )

    if out:
        db.add_all(out)


def _upsert_stats_page(
    db: Session,
    match_id: int,
    stats_match_id: int,
    stats_match_slug: Optional[str],
    map_name: Optional[str],
) -> None:
    stmt = (
        pg_insert(MatchStatsPage)
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


def _replace_player_stats(
    db: Session,
    match_id: int,
    stats_match_id: int,
    base_tables: List[Dict[str, Any]],
    team_name_to_id: Dict[str, int],
) -> None:
    db.execute(delete(PlayerMapStat).where(PlayerMapStat.stats_match_id == stats_match_id))

    if not base_tables:
        return

    bulk: List[PlayerMapStat] = []

    for t in base_tables:
        table_index = _parse_int(t.get("table_index"))
        if table_index is None:
            continue
        rows = t.get("rows") or []
        cols = t.get("columns") or []
        if not isinstance(rows, list) or not isinstance(cols, list) or not cols:
            continue

        team_col = cols[0]
        team_id = None
        if isinstance(team_col, str):
            team_id = team_name_to_id.get(_lower(team_col) or "")

        segment = _pick_segment(int(table_index))

        for r in rows:
            if not isinstance(r, dict):
                continue
            player_name = r.get(team_col)
            if not isinstance(player_name, str) or not player_name.strip():
                continue

            pid = _ensure_player(db, player_name.strip())

            k, hs = _parse_k_hs(r.get("K (hs)"))
            d = _parse_deaths(r.get("D (t)"))
            a = _parse_deaths(r.get("A (f)"))
            adr = _parse_float(r.get("ADR"))
            kast = _parse_pct_to_float(r.get("KAST"))
            rating3 = _parse_float(r.get("Rating3.0"))

            bulk.append(
                PlayerMapStat(
                    match_id=match_id,
                    stats_match_id=stats_match_id,
                    team_id=team_id,
                    player_id=pid,
                    segment=segment,
                    kills=k,
                    deaths=d,
                    assists=a,
                    hs_kills=hs,
                    adr=adr,
                    kast=kast,
                    rating3=rating3,
                    raw=r,
                )
            )

    if bulk:
        db.add_all(bulk)


def _pick_map_name_for_stats(payload: Dict[str, Any]) -> Optional[str]:
    mr = payload.get("map_results")
    if isinstance(mr, list) and mr:
        m0 = mr[0]
        if isinstance(m0, dict) and isinstance(m0.get("map"), str):
            return m0["map"]
    return None


def ingest_files(db: Session, jsonl_paths: List[Path], commit_every: int) -> Tuple[int, int, int]:
    ok = 0
    skipped = 0
    failed = 0

    for n, (p, line_no, payload) in enumerate(_iter_jsonl(jsonl_paths), start=1):
        match_id = _parse_int(payload.get("match_id"))
        team1_name = payload.get("team1_name")
        team2_name = payload.get("team2_name")
        ts = _parse_int(payload.get("timestamp"))

        if not match_id or not isinstance(team1_name, str) or not isinstance(team2_name, str) or not ts:
            skipped += 1
            if n % commit_every == 0:
                db.commit()
            continue

        try:
            t1_id = _ensure_team(db, team1_name.strip())
            t2_id = _ensure_team(db, team2_name.strip())

            name_to_id = {
                (_lower(team1_name) or ""): t1_id,
                (_lower(team2_name) or ""): t2_id,
            }

            is_seeding = bool(payload.get("is_seeding_match") or False)
            is_third = bool(payload.get("is_third_place_decider") or False)
            event_id = _parse_int(payload.get("event_id") or payload.get("eventId"))
            series_id = _parse_int(payload.get("series_id") or payload.get("seriesId"))

            _upsert_match(
                db,
                match_id=match_id,
                url=payload.get("match_url") if isinstance(payload.get("match_url"), str) else None,
                played_at=ts,
                team1_id=t1_id,
                team2_id=t2_id,
                is_seeding_match=is_seeding,
                is_third_place_decider=is_third,
                event_id=event_id,
                series_id=series_id,
            )

            veto = payload.get("veto") if isinstance(payload.get("veto"), dict) else None
            veto_actions = veto.get("actions") if veto and isinstance(veto.get("actions"), list) else []
            _replace_veto_actions(db, match_id=match_id, actions=veto_actions, team_name_to_id=name_to_id)

            _replace_match_maps(
                db,
                match_id=match_id,
                maps=payload.get("map_results") if isinstance(payload.get("map_results"), list) else [],
                team1_name=team1_name,
                team2_name=team2_name,
                team1_id=t1_id,
                team2_id=t2_id,
            )

            stats_match_id = _parse_int(payload.get("stats_match_id"))
            if stats_match_id:
                _upsert_stats_page(
                    db,
                    match_id=match_id,
                    stats_match_id=stats_match_id,
                    stats_match_slug=payload.get("stats_match_slug") if isinstance(payload.get("stats_match_slug"), str) else None,
                    map_name=_pick_map_name_for_stats(payload),
                )

                base_tables = payload.get("base_tables") if isinstance(payload.get("base_tables"), list) else []
                _replace_player_stats(
                    db,
                    match_id=match_id,
                    stats_match_id=stats_match_id,
                    base_tables=base_tables,
                    team_name_to_id=name_to_id,
                )

            ok += 1

            if n % commit_every == 0:
                db.commit()

        except Exception:
            db.rollback()
            failed += 1

    db.commit()
    return ok, skipped, failed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", nargs="+")
    ap.add_argument("--commit-every", type=int, default=200)
    args = ap.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    paths = [Path(x) for x in args.jsonl]
    for p in paths:
        if not p.exists():
            raise SystemExit(f"missing file: {p}")

    with SessionLocal() as db:
        ok, skipped, failed = ingest_files(db, paths, commit_every=int(args.commit_every))

    print(f"Done. ok={ok}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
