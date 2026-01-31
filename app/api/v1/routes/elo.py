from fastapi import APIRouter

from app.workers.queue import default_queue
from app.workers.jobs.elo import recompute_player_elo

router = APIRouter(prefix="/elo", tags=["elo"])


@router.post("/recompute")
def enqueue_recompute():
    job = default_queue.enqueue(recompute_player_elo)
    return {"job_id": job.id}
