from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

SERIES_ID = "2891194"

API_KEY = (os.environ.get("GRID_API_KEY") or "").strip()
if not API_KEY:
    raise RuntimeError(f"GRID_API_KEY not set. Loaded .env from: {ENV_PATH}")


def head(s: str, n: int = 400) -> str:
    return (s or "")[:n].replace("\n", "\\n")


def get(url: str, accept: str = "application/json") -> Tuple[int, str]:
    headers = {"x-api-key": API_KEY, "accept": accept}
    try:
        r = requests.get(url, headers=headers, timeout=25)
        return r.status_code, head(r.text)
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def post_gql(url: str, query: str, variables: Dict[str, Any]) -> Tuple[int, str]:
    headers = {"x-api-key": API_KEY, "content-type": "application/json", "accept": "application/json"}
    payload = {"query": query, "variables": variables}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=25)
        return r.status_code, head(r.text, 800)
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def main() -> None:
    print("Loaded .env from:", ENV_PATH)
    print("Using SERIES_ID:", SERIES_ID)
    print()

    bases = [
        "https://api.grid.gg",
        "https://api-op.grid.gg",
    ]

    endpoints: List[Tuple[str, str, str]] = []
    for b in bases:
        endpoints += [
            ("file-download list", f"{b}/file-download/list/{SERIES_ID}", "GET"),
            ("events zip (grid)", f"{b}/file-download/events/grid/series/{SERIES_ID}", "GET"),
            ("central-data gql", f"{b}/central-data/graphql", "GQL"),
        ]

    introspection = """
    query {
      __schema { queryType { name } }
    }
    """
    minimal_series = """
    query($id: ID!) { series(id: $id) { id } }
    """

    for name, url, kind in endpoints:
        print(f"=== {name} ===")
        print("URL:", url)
        if kind == "GET":
            code, txt = get(url)
            print("HTTP:", code)
            print("HEAD:", txt)
        else:
            code1, txt1 = post_gql(url, introspection, {})
            print("GQL introspection HTTP:", code1)
            print("HEAD:", txt1)
            code2, txt2 = post_gql(url, minimal_series, {"id": SERIES_ID})
            print("GQL series(id) HTTP:", code2)
            print("HEAD:", txt2)
        print()

    print("Done.")


if __name__ == "__main__":
    main()
