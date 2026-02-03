from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.team import Team
from app.db.models.match import Match
from app.db.models.veto_action import VetoAction
from app.db.models.match_map import MatchMap


def get_or_create_team(db: Session, name: str) -> Team:
    team = db.scalar(select(Team).where(Team.name == name))
    if team:
        return team
    team = Team(name=name)
    db.add(team)
    db.flush()
    return team


def should_ingest(payload: dict) -> bool:
    if not payload.get("team1_name") or not payload.get("team2_name"):
        return False
    if payload.get("timestamp") is None:
        return False

    actions = payload.get("veto", {}).get("actions")
    if not actions or not isinstance(actions, list):
        return False

    for a in actions:
        if not isinstance(a, dict):
            return False
        if not a.get("action") or not a.get("map"):
            return False

    return True


def ingest_hltv_match(db: Session, payload: dict) -> bool:
    if not should_ingest(payload):
        return False

    match_id = int(payload["match_id"])
    t1 = get_or_create_team(db, payload["team1_name"])
    t2 = get_or_create_team(db, payload["team2_name"])
    played_at = int(payload["timestamp"])

    match = db.get(Match, match_id)
    if not match:
        match = Match(
            id=match_id,
            source="hltv",
            url=payload.get("match_url"),
            played_at=played_at,
            team1_id=t1.id,
            team2_id=t2.id,
        )
        db.add(match)
    else:
        match.source = match.source or "hltv"
        match.url = match.url or payload.get("match_url")
        match.played_at = match.played_at or played_at
        match.team1_id = match.team1_id or t1.id
        match.team2_id = match.team2_id or t2.id

    db.query(VetoAction).filter(VetoAction.match_id == match_id).delete()
    actions = payload.get("veto", {}).get("actions", [])
    for i, a in enumerate(actions, start=1):
        team_name = a.get("team")
        team_id = get_or_create_team(db, team_name).id if team_name else None
        db.add(
            VetoAction(
                match_id=match_id,
                order_index=i,
                team_id=team_id,
                action=a["action"],
                map_name=a["map"],
            )
        )

    db.query(MatchMap).filter(MatchMap.match_id == match_id).delete()
    for mr in payload.get("map_results", []):
        winner_name = mr.get("winner")
        winner_id = get_or_create_team(db, winner_name).id if winner_name else None
        db.add(
            MatchMap(
                match_id=match_id,
                map_name=mr["map"],
                team1_rounds=mr.get("team1_rounds"),
                team2_rounds=mr.get("team2_rounds"),
                winner_team_id=winner_id,
            )
        )

    return True
