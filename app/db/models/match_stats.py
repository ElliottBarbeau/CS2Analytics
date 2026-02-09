from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlayerMapStat(Base):
    __tablename__ = "player_map_stats"
    __table_args__ = (
        UniqueConstraint("stats_match_id", "player_id", "segment", name="uq_player_map_stats_row"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    match_id: Mapped[int] = mapped_column(Integer, index=True)
    stats_match_id: Mapped[int] = mapped_column(Integer, index=True)

    team_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    player_id: Mapped[int] = mapped_column(Integer, index=True)

    map_name: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    segment: Mapped[str] = mapped_column(String(8), index=True)

    kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deaths: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hs_kills: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adr: Mapped[float | None] = mapped_column(Float, nullable=True)
    kast: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating3: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
