from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TrackedTeam(Base):
    __tablename__ = "tracked_teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # What you call it (manual label)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)

    # The GRID identifier once you know it (nullable until mapped)
    grid_team_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
