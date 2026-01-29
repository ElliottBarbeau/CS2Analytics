from dotenv import load_dotenv
import os

load_dotenv()


def get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"{key} is not set in environment")
    return value
