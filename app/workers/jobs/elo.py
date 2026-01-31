from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def recompute_player_elo() -> dict:
    db: Session = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "message": "Elo recompute placeholder ran"}
    finally:
        db.close()
