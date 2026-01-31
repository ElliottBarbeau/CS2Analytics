from rq import Worker

from app.workers.queue import redis_conn

if __name__ == "__main__":
    worker = Worker(["default"], connection=redis_conn)
    worker.work()
