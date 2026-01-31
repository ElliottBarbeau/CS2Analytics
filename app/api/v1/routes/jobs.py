from fastapi import APIRouter, HTTPException
from rq.job import Job

from app.workers.queue import redis_conn

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "id": job.id,
        "status": job.get_status(),
        "result": job.result,
        "enqueued_at": job.enqueued_at,
        "ended_at": job.ended_at,
    }
