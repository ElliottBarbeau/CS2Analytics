from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VetoAction(Base):
    __tablename__ = "veto_actions"
    __table_args__ = (UniqueConstraint("match_id", "order_index", name="uq_veto_order"),)
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer, index=True) 
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), index=True, nullable=True)
    action: Mapped[str] = mapped_column(String(16), index=True)
    map_name: Mapped[str] = mapped_column(String(32), index=True)
