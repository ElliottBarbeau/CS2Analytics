from __future__ import annotations

import json
import os
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

SERIES_ID = "2891194"

API_BASE = "https://api.grid.gg"
LIST_URL = f"{API_BASE}/file-download/list/{{series_id}}"

OUT_DIR = Path("data/raw/grid")
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRADE_WINDOW_SECONDS = 5.0


def die(msg: str) -> None:
    raise RuntimeError(msg)


def env_api_key() -> str:
    k = (os.environ.get("GRID_API_KEY") or "").strip()
    if not k:
        die(f"GRID_API_KEY not set. Loaded .env from: {ENV_PATH}")
    return k


def http_get_json(url: str, api_key: str) -> Dict[str, Any]:
    headers = {"x-api-key": api_key, "accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        head = (r.text or "")[:800].replace("\n", "\\n")
        die(f"HTTP {r.status_code} for {url}\nHEAD: {head}")
    try:
        return r.json()
    except Exception:
        head = (r.text or "")[:800].replace("\n", "\\n")
        die(f"Non-JSON response from {url}\nHEAD: {head}")


def http_get_bytes(url: str, api_key: str) -> bytes:
    headers = {"x-api-key": api_key, "accept": "*/*"}
    r = requests.get(url, headers=headers, timeout=120)
    if r.status_code != 200:
        head = (r.text or "")[:800].replace("\n", "\\n")
        die(f"HTTP {r.status_code} for {url}\nHEAD: {head}")
    return r.content


def pick_events_file(list_payload: Dict[str, Any]) -> Dict[str, Any]:
    files = list_payload.get("files") or []
    if not isinstance(files, list):
        die(f"Unexpected list payload shape: keys={list(list_payload.keys())}")

    preferred = None
    for f in files:
        if not isinstance(f, dict):
            continue
        if f.get("id") == "events-grid":
            preferred = f
            break

    if preferred is None:
        ids = [x.get("id") for x in files if isinstance(x, dict)]
        die(f"No 'events-grid' file in list response. Available ids: {ids}")

    status = (preferred.get("status") or "").lower()
    if status and status not in {"ready", "available"}:
        die(f"'events-grid' exists but status='{preferred.get('status')}'. Full entry: {preferred}")

    full_url = preferred.get("fullURL") or preferred.get("fullUrl") or preferred.get("url")
    if not full_url:
        die(f"'events-grid' missing fullURL. Entry: {preferred}")

    return preferred


def unzip_first_jsonl(zip_bytes: bytes, out_dir: Path, series_id: str) -> Path:
    zip_path = out_dir / f"series_{series_id}_events.zip"
    zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        jsonl_candidates = [n for n in names if n.lower().endswith(".jsonl")]
        if not jsonl_candidates:
            die(f"No .jsonl in zip. Files: {names[:50]}")
        target = jsonl_candidates[0]
        out_path = out_dir / Path(target).name
        with z.open(target, "r") as src, out_path.open("wb") as dst:
            dst.write(src.read())
    return out_path


def parse_iso_ts(ts: str) -> Optional[int]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def to_ts_ms(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v > 10_000_000_000:
            return int(v)
        return int(v * 1000)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if s.isdigit():
            n = int(s)
            if n > 10_000_000_000:
                return n
            return n * 1000
        return parse_iso_ts(s)
    return None


def get_any(d: Any, paths: List[Tuple[str, ...]]) -> Any:
    for p in paths:
        cur = d
        ok = True
        for k in p:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def norm_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def event_type(ev: Dict[str, Any]) -> str:
    t = get_any(ev, [("type",), ("name",), ("eventType",), ("event", "type"), ("payload", "type")])
    return str(t).strip().lower() if t is not None else ""


def event_ts_ms(ev: Dict[str, Any]) -> Optional[int]:
    v = get_any(
        ev,
        [
            ("timestamp",),
            ("ts",),
            ("time",),
            ("createdAt",),
            ("occurredAt",),
            ("payload", "timestamp"),
            ("payload", "time"),
            ("eventTime",),
        ],
    )
    return to_ts_ms(v)


def extract_actor_target(ev: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    actor = get_any(
        ev,
        [
            ("actor", "id"),
            ("payload", "actor", "id"),
            ("payload", "killer", "id"),
            ("payload", "attacker", "id"),
            ("payload", "player", "id"),
            ("payload", "actorId"),
            ("actorId",),
        ],
    )
    target = get_any(
        ev,
        [
            ("target", "id"),
            ("payload", "target", "id"),
            ("payload", "victim", "id"),
            ("payload", "killed", "id"),
            ("payload", "defender", "id"),
            ("payload", "targetId"),
            ("targetId",),
        ],
    )
    return norm_str(actor), norm_str(target)


def extract_actor_target_team(ev: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    ateam = get_any(
        ev,
        [
            ("actor", "team", "id"),
            ("payload", "actor", "team", "id"),
            ("payload", "killer", "team", "id"),
            ("payload", "attacker", "team", "id"),
            ("payload", "actorTeamId"),
            ("payload", "team", "id"),
        ],
    )
    tteam = get_any(
        ev,
        [
            ("target", "team", "id"),
            ("payload", "target", "team", "id"),
            ("payload", "victim", "team", "id"),
            ("payload", "defender", "team", "id"),
            ("payload", "targetTeamId"),
        ],
    )
    return norm_str(ateam), norm_str(tteam)


def extract_round_id(ev: Dict[str, Any]) -> Optional[str]:
    rid = get_any(
        ev,
        [
            ("round", "id"),
            ("payload", "round", "id"),
            ("payload", "roundId"),
            ("roundId",),
            ("segment", "id"),
            ("payload", "segment", "id"),
            ("payload", "segmentId"),
        ],
    )
    if rid is not None:
        return norm_str(rid)

    rn = get_any(
        ev,
        [
            ("round", "number"),
            ("payload", "round", "number"),
            ("payload", "roundNumber"),
            ("roundNumber",),
            ("segment", "number"),
            ("payload", "segment", "number"),
        ],
    )
    if rn is None:
        return None
    return f"round_{rn}"


def looks_like_kill(t: str) -> bool:
    if not t:
        return False
    if "kill" in t:
        return True
    if t in {"playerkilled", "player_killed", "player-killed"}:
        return True
    return False


@dataclass
class Kill:
    ts_ms: int
    round_id: str
    killer: str
    victim: str
    killer_team: Optional[str]
    victim_team: Optional[str]


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def load_kills(jsonl_path: Path) -> List[Kill]:
    kills: List[Kill] = []
    for ev in iter_jsonl(jsonl_path):
        t = event_type(ev)
        if not looks_like_kill(t):
            continue
        ts = event_ts_ms(ev)
        rid = extract_round_id(ev)
        killer, victim = extract_actor_target(ev)
        kt, vt = extract_actor_target_team(ev)
        if ts is None or rid is None or killer is None or victim is None:
            continue
        kills.append(Kill(ts_ms=ts, round_id=rid, killer=killer, victim=victim, killer_team=kt, victim_team=vt))
    kills.sort(key=lambda k: k.ts_ms)
    return kills


def compute_openers(kills: List[Kill]) -> Dict[str, Kill]:
    first: Dict[str, Kill] = {}
    for k in kills:
        if k.round_id not in first:
            first[k.round_id] = k
    return first


def compute_multis(kills: List[Kill]) -> Dict[Tuple[str, str], int]:
    c: Dict[Tuple[str, str], int] = defaultdict(int)
    for k in kills:
        c[(k.round_id, k.killer)] += 1
    return dict(c)


def compute_trades(kills: List[Kill], window_s: float) -> List[Tuple[Kill, Kill]]:
    window_ms = int(window_s * 1000)
    by_round: Dict[str, List[Kill]] = defaultdict(list)
    for k in kills:
        by_round[k.round_id].append(k)

    trades: List[Tuple[Kill, Kill]] = []
    for rid, ks in by_round.items():
        ks = sorted(ks, key=lambda x: x.ts_ms)
        for i, k1 in enumerate(ks):
            for j in range(i + 1, min(i + 12, len(ks))):
                k2 = ks[j]
                if k2.ts_ms - k1.ts_ms > window_ms:
                    break
                if k2.victim != k1.killer:
                    continue
                if k1.victim_team and k2.killer_team and k1.victim_team != k2.killer_team:
                    continue
                trades.append((k1, k2))
                break
    return trades


def compute_clutches_heuristic(kills: List[Kill]) -> Dict[str, Dict[str, Any]]:
    by_round: Dict[str, List[Kill]] = defaultdict(list)
    for k in kills:
        by_round[k.round_id].append(k)

    out: Dict[str, Dict[str, Any]] = {}

    for rid, ks in by_round.items():
        teams = set()
        for k in ks:
            if k.killer_team:
                teams.add(k.killer_team)
            if k.victim_team:
                teams.add(k.victim_team)
        teams = [t for t in teams if t is not None]
        if len(teams) != 2:
            continue
        tA, tB = teams[0], teams[1]

        alive: Dict[str, set] = {tA: set(), tB: set()}
        for k in ks:
            if k.killer_team and k.killer_team in alive:
                alive[k.killer_team].add(k.killer)
            if k.victim_team and k.victim_team in alive:
                alive[k.victim_team].add(k.victim)

        aliveA = set(alive[tA])
        aliveB = set(alive[tB])

        possible: List[Tuple[int, str, str]] = []

        for k in ks:
            if k.killer_team == tA and k.victim_team == tB:
                aliveB.discard(k.victim)
            elif k.killer_team == tB and k.victim_team == tA:
                aliveA.discard(k.victim)

            if len(aliveA) == 1 and len(aliveB) >= 2:
                possible.append((k.ts_ms, next(iter(aliveA)), tA))
            if len(aliveB) == 1 and len(aliveA) >= 2:
                possible.append((k.ts_ms, next(iter(aliveB)), tB))

        if not possible:
            continue

        last_kill = ks[-1]
        winner = last_kill.killer_team
        if not winner:
            continue

        winners = [c for c in possible if c[2] == winner]
        if not winners:
            continue

        ts0, clutcher, team = winners[-1]
        out[rid] = {"teamId": team, "playerId": clutcher, "detectedAtMs": ts0}

    return out


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    api_key = env_api_key()
    print("Loaded .env from:", ENV_PATH)
    print("Using SERIES_ID:", SERIES_ID)

    list_url = LIST_URL.format(series_id=SERIES_ID)
    print("Listing files:", list_url)
    listing = http_get_json(list_url, api_key)
    events_entry = pick_events_file(listing)
    events_url = events_entry.get("fullURL") or events_entry.get("fullUrl") or events_entry.get("url")
    print("Downloading events:", events_url)

    zip_bytes = http_get_bytes(events_url, api_key)
    jsonl_path = unzip_first_jsonl(zip_bytes, OUT_DIR, SERIES_ID)
    print("Wrote:", jsonl_path)

    kills = load_kills(jsonl_path)
    print("Kills parsed:", len(kills))
    if not kills:
        die(
            "No kills parsed from the JSONL. Paste 3-5 raw lines from the extracted .jsonl so I can adjust the field paths."
        )

    openers = compute_openers(kills)
    multis = compute_multis(kills)
    trades = compute_trades(kills, TRADE_WINDOW_SECONDS)
    clutches = compute_clutches_heuristic(kills)

    top_multi = sorted(multis.items(), key=lambda x: x[1], reverse=True)[:30]

    out = {
        "series_id": SERIES_ID,
        "download": {
            "list_url": list_url,
            "events_file": {
                "id": events_entry.get("id"),
                "status": events_entry.get("status"),
                "fileName": events_entry.get("fileName"),
                "fullURL": events_url,
            },
        },
        "files": {"events_jsonl": str(jsonl_path)},
        "counts": {
            "kills": len(kills),
            "rounds_with_kills": len({k.round_id for k in kills}),
            "openers": len(openers),
            "trades": len(trades),
            "clutch_candidates": len(clutches),
        },
        "sample": {
            "first_kill": {
                "round_id": kills[0].round_id,
                "ts": ms_to_iso(kills[0].ts_ms),
                "killer": kills[0].killer,
                "victim": kills[0].victim,
                "killer_team": kills[0].killer_team,
                "victim_team": kills[0].victim_team,
            },
            "first_5_openers": [
                {
                    "round_id": rid,
                    "ts": ms_to_iso(k.ts_ms),
                    "killer": k.killer,
                    "victim": k.victim,
                    "killer_team": k.killer_team,
                    "victim_team": k.victim_team,
                }
                for rid, k in list(openers.items())[:5]
            ],
            "top_multikills": [
                {"round_id": rid, "playerId": pid, "kills_in_round": n} for (rid, pid), n in top_multi
            ],
            "first_10_trades": [
                {
                    "killed_at": ms_to_iso(k1.ts_ms),
                    "killer": k1.killer,
                    "victim": k1.victim,
                    "traded_at": ms_to_iso(k2.ts_ms),
                    "trader": k2.killer,
                }
                for k1, k2 in trades[:10]
            ],
            "first_10_clutch_candidates": [
                {"round_id": rid, **info} for rid, info in list(clutches.items())[:10]
            ],
        },
    }

    out_path = OUT_DIR / f"series_{SERIES_ID}_computed.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote:", out_path)


if __name__ == "__main__":
    main()
