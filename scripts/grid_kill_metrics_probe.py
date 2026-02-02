from __future__ import annotations

import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, TextIOWrapper
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


CENTRAL_DATA_URL = os.getenv("CENTRAL_DATA_URL", "https://api-op.grid.gg/central-data/graphql")
FILE_DOWNLOAD_BASES = [
    "https://api.grid.gg",
    "https://api-op.grid.gg",
]

SERIES_ID = "2891194"
TRADE_WINDOW_SECONDS = 5


def load_env_from_parent() -> Optional[Path]:
    here = Path(__file__).resolve()
    env_path = here.parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
    return env_path


def die(msg: str) -> None:
    raise RuntimeError(msg)


def http_get(url: str, api_key: str, timeout: int = 30) -> requests.Response:
    headers_variants = [
        {"x-api-key": api_key, "accept": "*/*", "user-agent": "Mozilla/5.0"},
        {"authorization": f"Bearer {api_key}", "accept": "*/*", "user-agent": "Mozilla/5.0"},
    ]
    last = None
    for hdrs in headers_variants:
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)
            if r.status_code not in (401, 403):
                return r
            last = r
        except Exception as e:
            last = e
    if isinstance(last, requests.Response):
        return last
    raise last  # type: ignore[misc]


def gql_post(url: str, api_key: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    r = requests.post(url, headers=headers, json={"query": query, "variables": variables}, timeout=30)
    try:
        data = r.json()
    except Exception:
        die(f"GQL non-JSON response HTTP {r.status_code}: {r.text[:500]}")
    if "errors" in data and data["errors"]:
        die(json.dumps(data["errors"], indent=2))
    return data


def series_product_levels(api_key: str, sid: str) -> Dict[str, Any]:
    q = """
    query($id: ID!) {
      series(id: $id) {
        id
        title { id name }
        tournament { id name }
        startTimeScheduled
        teams { baseInfo { id name } }
        productServiceLevels { productName serviceLevel }
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q, {"id": sid})
    s = data["data"]["series"]
    return s


def file_download_list(api_key: str, base: str, sid: str) -> Tuple[int, str]:
    url = f"{base}/file-download/list/{sid}"
    r = http_get(url, api_key)
    head = r.text[:200].replace("\n", " ")
    return r.status_code, head


def download_events_zip(api_key: str, base: str, sid: str) -> Tuple[int, bytes, str]:
    url = f"{base}/file-download/events/grid/series/{sid}"
    r = http_get(url, api_key)
    head = r.text[:200].replace("\n", " ")
    return r.status_code, r.content, head


def parse_iso(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            return None
    return None


def iter_jsonl_from_zip(zip_bytes: bytes) -> Iterable[Dict[str, Any]]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as z:
        names = z.namelist()
        jsonl_names = [n for n in names if n.lower().endswith(".jsonl")]
        if not jsonl_names:
            json_names = [n for n in names if n.lower().endswith(".json")]
            target = json_names[0] if json_names else names[0]
            with z.open(target) as f:
                raw = f.read()
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return
            if isinstance(obj, list):
                for x in obj:
                    if isinstance(x, dict):
                        yield x
            elif isinstance(obj, dict):
                yield obj
            return

        target = jsonl_names[0]
        with z.open(target) as bf:
            tf = TextIOWrapper(bf, encoding="utf-8", errors="replace")
            for line in tf:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj


def is_kill_event(e: Dict[str, Any]) -> bool:
    t = (e.get("type") or e.get("eventType") or e.get("name") or "").lower()
    if "kill" in t and "skill" not in t:
        return True
    if "player_kill" in t or "playerkill" in t:
        return True
    return False


def get_round_key(e: Dict[str, Any]) -> Optional[str]:
    for k in ("round", "roundNumber", "roundIndex", "roundNo"):
        if k in e and e[k] is not None:
            return str(e[k])
    r = e.get("round")
    if isinstance(r, dict):
        for k in ("number", "index", "roundNumber"):
            if k in r and r[k] is not None:
                return str(r[k])
    return None


def get_map_key(e: Dict[str, Any]) -> str:
    for k in ("map", "mapName", "gameMap", "level"):
        v = e.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            name = v.get("name") or v.get("mapName")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return "unknown"


def get_kill_fields(e: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[datetime]]:
    killer = None
    victim = None
    assister = None

    for k in ("killerName", "killer", "attackerName", "attacker"):
        v = e.get(k)
        if isinstance(v, str) and v.strip():
            killer = v.strip()
            break
        if isinstance(v, dict):
            n = v.get("name")
            if isinstance(n, str) and n.strip():
                killer = n.strip()
                break

    for k in ("victimName", "victim", "targetName", "target"):
        v = e.get(k)
        if isinstance(v, str) and v.strip():
            victim = v.strip()
            break
        if isinstance(v, dict):
            n = v.get("name")
            if isinstance(n, str) and n.strip():
                victim = n.strip()
                break

    for k in ("assisterName", "assister", "assistantName", "assistant"):
        v = e.get(k)
        if isinstance(v, str) and v.strip():
            assister = v.strip()
            break
        if isinstance(v, dict):
            n = v.get("name")
            if isinstance(n, str) and n.strip():
                assister = n.strip()
                break

    ts = None
    for k in ("timestamp", "ts", "time", "eventTime", "occurredAt"):
        if k in e:
            ts = parse_iso(e.get(k))
            if ts:
                break
    if not ts and isinstance(e.get("meta"), dict):
        ts = parse_iso(e["meta"].get("timestamp"))

    return killer, victim, assister, ts


@dataclass
class Kill:
    map_name: str
    round_key: str
    killer: str
    victim: str
    assister: Optional[str]
    ts: Optional[datetime]


def compute_metrics(kills: List[Kill]) -> Dict[str, Any]:
    kills_by_mr = defaultdict(list)
    for k in kills:
        kills_by_mr[(k.map_name, k.round_key)].append(k)

    openers = Counter()
    multikills = Counter()
    trade_kills = Counter()

    for (map_name, round_key), ks in kills_by_mr.items():
        ks_sorted = sorted(ks, key=lambda x: x.ts.timestamp() if x.ts else 0.0)

        if ks_sorted:
            openers[(map_name, round_key, ks_sorted[0].killer)] += 1

        per_player = Counter([x.killer for x in ks_sorted])
        for player, cnt in per_player.items():
            if cnt >= 2:
                multikills[(map_name, round_key, player, cnt)] += 1

        if any(x.ts for x in ks_sorted):
            deaths = defaultdict(list)
            for x in ks_sorted:
                if x.ts:
                    deaths[x.victim].append(x.ts)

            for x in ks_sorted:
                if not x.ts:
                    continue
                killer_deaths = deaths.get(x.killer, [])
                for dts in killer_deaths:
                    dt = abs((dts - x.ts).total_seconds())
                    if 0 < dt <= TRADE_WINDOW_SECONDS:
                        trade_kills[(map_name, round_key, x.killer)] += 1
                        break

    summary = {
        "openers_total": sum(openers.values()),
        "unique_opener_players": len({k[2] for k in openers.keys()}),
        "multikill_rounds_total": sum(multikills.values()),
        "trade_kills_total": sum(trade_kills.values()),
        "top_openers": [
            {"player": p, "count": c}
            for (p, c) in Counter({k[2]: v for k, v in openers.items()}).most_common(20)
        ],
        "top_trade_killers": [
            {"player": p, "count": c}
            for (p, c) in Counter({k[2]: v for k, v in trade_kills.items()}).most_common(20)
        ],
        "multikill_distribution": dict(Counter([k[3] for k in multikills.keys()])),
    }
    return summary


def main() -> None:
    env_path = load_env_from_parent()
    if env_path:
        print(f"Loaded .env from: {env_path}")
    else:
        print("No .env found in repo root (one directory above scripts/).")

    api_key = os.getenv("GRID_API_KEY", "").strip()
    if not api_key:
        die("GRID_API_KEY is not set in environment.")

    print(f"CENTRAL_DATA_URL: {CENTRAL_DATA_URL}")
    print(f"Using SERIES_ID: {SERIES_ID}")

    print("\n=== Central Data: series productServiceLevels ===")
    s = series_product_levels(api_key, SERIES_ID)
    print(json.dumps(s, indent=2))

    print("\n=== File Download entitlement check ===")
    for base in FILE_DOWNLOAD_BASES:
        code, head = file_download_list(api_key, base, SERIES_ID)
        print(f"{base}/file-download/list/{SERIES_ID} -> HTTP {code} HEAD: {head}")

    print("\n=== Attempting to download events-grid zip ===")
    zip_bytes = None
    zip_from = None
    for base in FILE_DOWNLOAD_BASES:
        code, content, head = download_events_zip(api_key, base, SERIES_ID)
        print(f"{base}/file-download/events/grid/series/{SERIES_ID} -> HTTP {code} HEAD: {head}")
        if code == 200 and content[:4] == b"PK\x03\x04":
            zip_bytes = content
            zip_from = base
            break

    if not zip_bytes:
        print(
            "\nCould not download events-grid. If you see 403 'request is forbidden', "
            "your key likely does not include Series Events access (paid product)."
        )
        return

    print(f"\nDownloaded events zip from: {zip_from} size={len(zip_bytes)} bytes")

    raw_events = list(iter_jsonl_from_zip(zip_bytes))
    print(f"Parsed {len(raw_events)} JSON objects from zip")

    kill_objs = [e for e in raw_events if isinstance(e, dict) and is_kill_event(e)]
    print(f"Detected {len(kill_objs)} kill-like events")

    kills: List[Kill] = []
    for e in kill_objs:
        rk = get_round_key(e)
        if rk is None:
            continue
        map_name = get_map_key(e)
        killer, victim, assister, ts = get_kill_fields(e)
        if not killer or not victim:
            continue
        kills.append(Kill(map_name=map_name, round_key=rk, killer=killer, victim=victim, assister=assister, ts=ts))

    print(f"Normalized {len(kills)} kill events with (map, round, killer, victim)")

    metrics = compute_metrics(kills)
    print("\n=== Metrics summary (approx) ===")
    print(json.dumps(metrics, indent=2))

    out_dir = Path("data/raw/grid_series_events")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"{SERIES_ID}_kill_metrics.json"
    out_json.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"\nWrote: {out_json}")


if __name__ == "__main__":
    main()
