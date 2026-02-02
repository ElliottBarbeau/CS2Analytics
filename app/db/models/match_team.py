from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchTeam(Base):
    __tablename__ = "match_teams"
    __table_args__ = (UniqueConstraint("match_id", "team_id", name="uq_match_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
