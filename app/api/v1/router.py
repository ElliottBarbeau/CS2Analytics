from fastapi import APIRouter

from app.api.v1.routes.players import router as players_router
from app.api.v1.routes.jobs import router as jobs_router
from app.api.v1.routes.elo import router as elo_router
from app.api.v1.routes.teams import router as teams_router
from app.api.v1.routes.team_map_stats import router as team_map_stats_router
from app.api.v1.routes.teams_lookup import router as teams_lookup_router
from app.api.v1.routes.team_veto import router as team_veto_router

api_router = APIRouter()
api_router.include_router(players_router)
api_router.include_router(jobs_router)
api_router.include_router(elo_router)
api_router.include_router(teams_router)
api_router.include_router(team_map_stats_router)
api_router.include_router(teams_lookup_router)
api_router.include_router(team_veto_router)