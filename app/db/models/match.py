from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True, default="hltv")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    played_at: Mapped[int] = mapped_column(Integer, index=True)

    team1_id: Mapped[int] = mapped_column(Integer, index=True)
    team2_id: Mapped[int] = mapped_column(Integer, index=True)

    event_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    series_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    is_seeding_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_third_place_decider: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
