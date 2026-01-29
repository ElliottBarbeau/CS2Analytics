from fastapi import FastAPI
from sqlalchemy import text
from app.db.session import SessionLocal
from app.api.v1.router import api_router

app = FastAPI(title="CS2 Analytics API")
app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/db-check")
def db_check():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"db": "ok"}
    finally:
        db.close()
