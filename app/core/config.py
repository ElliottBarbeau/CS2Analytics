from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)

def get_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"{key} is not set")
    return value
