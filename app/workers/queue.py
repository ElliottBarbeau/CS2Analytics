from redis import Redis
from rq import Queue

from app.core.config import get_env

redis_conn = Redis.from_url(get_env("REDIS_URL"))
default_queue = Queue("default", connection=redis_conn)
