from __future__ import annotations

import argparse
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from app.core.config import get_env
from app.db.models.match import Match
from app.db.models.match_map import MatchMap
from app.db.models.match_stats import PlayerMapStat
from app.db.models.player import Player


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", default="broky")
    ap.add_argument("--match-ids", default="2381331,2379365")
    args = ap.parse_args()

    match_ids = []
    for x in args.match_ids.split(","):
        x = x.strip()
        if x:
            match_ids.append(int(x))

    db_url = get_env("DATABASE_URL")
    engine = create_engine(db_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with SessionLocal() as db:
        p = db.execute(
            select(Player.id, Player.name).where(func.lower(Player.name) == args.player.strip().lower())
        ).first()
        if not p:
            raise SystemExit(f"player not found: {args.player}")

        player_id = int(p.id)

        print(f"player_id={player_id} name={p.name}")
        print(f"match_ids={match_ids}")
        print()

        mm_rows = db.execute(
            select(
                MatchMap.match_id,
                MatchMap.map_name,
                MatchMap.team1_rounds,
                MatchMap.team2_rounds,
                (func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)).label("rounds"),
            )
            .where(MatchMap.match_id.in_(match_ids))
            .order_by(MatchMap.match_id.asc(), func.lower(MatchMap.map_name).asc())
        ).all()

        print("=== MatchMap rows ===")
        for r in mm_rows:
            print(
                f"match={r.match_id} map={r.map_name} t1={r.team1_rounds} t2={r.team2_rounds} rounds={int(r.rounds or 0)}"
            )
        print()

        stats_rows = db.execute(
            select(
                PlayerMapStat.match_id,
                PlayerMapStat.stats_match_id,
                PlayerMapStat.map_name,
                func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
                func.sum(func.coalesce(PlayerMapStat.deaths, 0)).label("deaths"),
                func.sum(func.coalesce(PlayerMapStat.assists, 0)).label("assists"),
            )
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                PlayerMapStat.match_id.in_(match_ids),
            )
            .group_by(PlayerMapStat.match_id, PlayerMapStat.stats_match_id, PlayerMapStat.map_name)
            .order_by(PlayerMapStat.match_id.asc(), PlayerMapStat.stats_match_id.asc())
        ).all()

        print("=== PlayerMapStat (raw totals per stats_match_id) ===")
        for r in stats_rows:
            print(
                f"match={r.match_id} stats_match_id={r.stats_match_id} map_name={r.map_name} "
                f"k={int(r.kills or 0)} d={int(r.deaths or 0)} a={int(r.assists or 0)}"
            )
        print()

        joined = db.execute(
            select(
                PlayerMapStat.match_id,
                PlayerMapStat.stats_match_id,
                PlayerMapStat.map_name,
                func.sum(func.coalesce(PlayerMapStat.kills, 0)).label("kills"),
                func.sum(
                    func.coalesce(MatchMap.team1_rounds, 0) + func.coalesce(MatchMap.team2_rounds, 0)
                ).label("rounds"),
            )
            .select_from(PlayerMapStat)
            .join(Match, Match.id == PlayerMapStat.match_id)
            .outerjoin(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                PlayerMapStat.match_id.in_(match_ids),
            )
            .group_by(PlayerMapStat.match_id, PlayerMapStat.stats_match_id, PlayerMapStat.map_name)
            .order_by(PlayerMapStat.match_id.asc(), PlayerMapStat.stats_match_id.asc())
        ).all()

        print("=== Joined to MatchMap (rounds) ===")
        total_k = 0
        total_r = 0
        for r in joined:
            k = int(r.kills or 0)
            rr = int(r.rounds or 0)
            total_k += k
            total_r += rr
            print(f"match={r.match_id} map={r.map_name} stats_match_id={r.stats_match_id} kills={k} rounds={rr}")

        print()
        print(f"TOTAL: kills={total_k} rounds={total_r}")

        bad = db.execute(
            select(
                PlayerMapStat.match_id,
                PlayerMapStat.stats_match_id,
                PlayerMapStat.map_name,
            )
            .select_from(PlayerMapStat)
            .outerjoin(
                MatchMap,
                and_(
                    MatchMap.match_id == PlayerMapStat.match_id,
                    PlayerMapStat.map_name.is_not(None),
                    func.lower(MatchMap.map_name) == func.lower(PlayerMapStat.map_name),
                ),
            )
            .where(
                PlayerMapStat.player_id == player_id,
                PlayerMapStat.segment == "total",
                PlayerMapStat.match_id.in_(match_ids),
                PlayerMapStat.map_name.is_not(None),
                MatchMap.match_id.is_(None),
            )
            .group_by(PlayerMapStat.match_id, PlayerMapStat.stats_match_id, PlayerMapStat.map_name)
            .order_by(PlayerMapStat.match_id.asc(), PlayerMapStat.stats_match_id.asc())
        ).all()

        if bad:
            print()
            print("=== Map-name mismatches (PlayerMapStat.map_name not found in MatchMap) ===")
            for r in bad:
                print(f"match={r.match_id} stats_match_id={r.stats_match_id} map_name={r.map_name}")


if __name__ == "__main__":
    main()
