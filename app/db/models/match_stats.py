from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchStatsPage(Base):
    __tablename__ = "match_stats_pages"
    __table_args__ = (
        UniqueConstraint("stats_match_id", name="uq_match_stats_pages_stats_match_id"),
        Index("ix_match_stats_pages_match_id", "match_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    stats_match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stats_match_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    map_name: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PlayerMapStat(Base):
    __tablename__ = "player_map_stats"
    __table_args__ = (
        UniqueConstraint("stats_match_id", "segment", "player_id", name="uq_player_map_stats_key"),
        Index("ix_player_map_stats_match_id", "match_id"),
        Index("ix_player_map_stats_player_id", "player_id"),
        Index("ix_player_map_stats_team_id", "team_id"),
        Index("ix_player_map_stats_stats_match_id", "stats_match_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    stats_match_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hs_kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adr: Mapped[float | None] = mapped_column(nullable=True)
    kast: Mapped[float | None] = mapped_column(nullable=True)
    rating3: Mapped[float | None] = mapped_column(nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
