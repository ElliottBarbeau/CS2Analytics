from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

CENTRAL_DATA_URL = "https://api.grid.gg/central-data/graphql"

SERIES_ID = "2891194"


def die(msg: str) -> None:
    raise RuntimeError(msg)


def env_api_key() -> str:
    k = (os.environ.get("GRID_API_KEY") or "").strip()
    if not k:
        die(f"GRID_API_KEY not set. Loaded .env from: {ENV_PATH}")
    return k


def gql_post(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "accept": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code != 200:
        head = (r.text or "")[:1200].replace("\n", "\\n")
        die(f"HTTP {r.status_code} for GraphQL\nHEAD: {head}")
    data = r.json()
    if "errors" in data and data["errors"]:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data


def schema_type(api_key: str, type_name: str) -> Dict[str, Any]:
    q = """
    query($name: String!) {
      __type(name: $name) {
        name
        kind
        fields {
          name
          type {
            kind
            name
            ofType { kind name ofType { kind name ofType { kind name } } }
          }
        }
      }
    }
    """
    return gql_post(CENTRAL_DATA_URL, api_key, q, {"name": type_name})["data"]["__type"]


def schema_query_fields(api_key: str) -> List[Dict[str, Any]]:
    q = """
    query {
      __schema {
        queryType {
          name
          fields {
            name
            args { name type { kind name ofType { kind name } } }
            type { kind name ofType { kind name ofType { kind name } } }
          }
        }
      }
    }
    """
    return gql_post(CENTRAL_DATA_URL, api_key, q)["data"]["__schema"]["queryType"]["fields"]


def flatten_type(t: Dict[str, Any]) -> str:
    parts = []
    cur = t
    while cur:
        k = cur.get("kind")
        n = cur.get("name")
        parts.append(f"{k}:{n}")
        cur = cur.get("ofType")
    return " -> ".join(parts)


def looks_eventy(name: str) -> bool:
    n = name.lower()
    needles = [
        "event", "events", "timeline", "round", "rounds", "game", "games",
        "kill", "kills", "damage", "clutch", "trade", "multi", "grenade",
        "bomb", "plant", "defuse", "state", "snapshot", "telemetry",
        "feed", "live",
    ]
    return any(x in n for x in needles)


def main() -> None:
    api_key = env_api_key()
    print("Loaded .env from:", ENV_PATH)
    print("CENTRAL_DATA_URL:", CENTRAL_DATA_URL)
    print("Using SERIES_ID:", SERIES_ID)
    print()

    fields = schema_query_fields(api_key)
    eventy = [f for f in fields if looks_eventy(f["name"])]

    print("=== Query fields that look like they might contain events/rounds/kills ===")
    for f in eventy:
        args = [(a["name"], flatten_type(a["type"])) for a in f.get("args") or []]
        ret = flatten_type(f["type"])
        print(f"- {f['name']}(" + ", ".join([f"{n}:{t}" for n, t in args]) + f") -> {ret}")
    print()

    candidates = ["series", "match", "game", "events", "seriesEvents", "eventFeed", "seriesState", "liveSeriesState"]
    present = {f["name"] for f in fields}

    print("=== Presence of common candidate fields ===")
    for c in candidates:
        print(f"{c:16}:", "YES" if c in present else "no")
    print()

    print("=== Dumping the Series type fields (you already saw some, but we’re searching for event-like ones) ===")
    series_t = schema_type(api_key, "Series")
    sfields = series_t.get("fields") or []
    eventy_series_fields = [sf for sf in sfields if looks_eventy(sf["name"])]
    for sf in eventy_series_fields:
        print(f"- Series.{sf['name']} -> {flatten_type(sf['type'])}")
    if not eventy_series_fields:
        print("(No event-like fields on Series in this schema.)")
    print()

    print("=== Trying a minimal series(id) query and printing what we get ===")
    q_series = """
    query($id: ID!) {
      series(id: $id) {
        id
        startTimeScheduled
        title { id name }
        tournament { id name }
        teams { baseInfo { id name } }
        format { id name nameShortened }
        productServiceLevels { productName serviceLevel }
        updatedAt
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q_series, {"id": SERIES_ID})["data"]["series"]
    print(json.dumps(data, indent=2))
    print()

    print("=== Next step hints ===")
    print("If you see ANY query field above like seriesState/events/... we can pivot to it immediately.")
    print("If nothing exists, your key likely only has Central Data GraphQL + live feeds (websocket/stream) for events.")


if __name__ == "__main__":
    main()
