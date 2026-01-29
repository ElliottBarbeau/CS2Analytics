from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models.player import Player
from app.schemas.player import PlayerCreate, PlayerRead

router = APIRouter(prefix="/players", tags=["players"])

@router.post("", response_model=PlayerRead, status_code=status.HTTP_201_CREATED)
def create_player(payload: PlayerCreate, db: Session = Depends(get_db)):
    player = Player(handle=payload.handle)
    db.add(player)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Player already exists in the database")

    db.refresh(player)
    return player


@router.get("", response_model=list[PlayerRead])
def list_players(db: Session = Depends(get_db)):
    return db.query(Player).order_by(Player.id.asc()).all()
