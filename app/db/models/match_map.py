from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchMap(Base):
    __tablename__ = "match_maps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)

    map_name: Mapped[str] = mapped_column(String(32), index=True)

    team1_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team2_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    winner_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True, nullable=True)
