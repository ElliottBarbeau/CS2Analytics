from fastapi import APIRouter

from app.api.v1.routes.players import router as players_router
from app.api.v1.routes.jobs import router as jobs_router

api_router = APIRouter()
api_router.include_router(players_router)
api_router.include_router(jobs_router)