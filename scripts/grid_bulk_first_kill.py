from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "data" / "raw" / "hltv_match_veto_and_stats.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "derived" / "grid_first_kill_summary.jsonl"

CACHE_DIR = ROOT / "data" / "cache" / "grid"
CACHE_SERIES_WINDOWS = CACHE_DIR / "series_windows"
CACHE_SERIES_STATE = CACHE_DIR / "series_state"
CACHE_SERIES_WINDOWS.mkdir(parents=True, exist_ok=True)
CACHE_SERIES_STATE.mkdir(parents=True, exist_ok=True)
DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

WINDOW_PAD_HOURS = 8
MAX_TIME_DIFF_SECONDS = 12 * 3600
CS2_TITLE_ID = "28"
SLEEP_SECONDS = 0.08

ORG_SUFFIXES = {
    "clan", "team", "esports", "e-sports", "gaming", "gg", "club",
    "sport", "sports", "academy", "international", "organisation", "organization",
}

ALIASES = {
    "navi": "natus vincere",
    "g2": "g2 esports",
}


def load_env_from_parent() -> Optional[Path]:
    """Loads ../.env relative to scripts/ if present (you said it's 1 dir above scripts)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env_path


def iso_z(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dt_from_unix(ts: int) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def normalize_team_name(s: str) -> str:
    s2 = (s or "").strip().lower()
    s2 = ALIASES.get(s2, s2)
    s2 = s2.replace("&", " and ")
    s2 = re.sub(r"[^a-z0-9\s]+", " ", s2)
    s2 = re.sub(r"\s+", " ", s2).strip()
    toks = [t for t in s2.split(" ") if t and t not in ORG_SUFFIXES]
    return " ".join(toks).strip()


def team_match(a: str, b: str) -> bool:
    a_n = normalize_team_name(a)
    b_n = normalize_team_name(b)
    if not a_n or not b_n:
        return False
    if a_n == b_n:
        return True
    return (a_n in b_n) or (b_n in a_n)


def safe_int(x: Any) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def cache_get(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_put(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def gql_post(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    data = r.json() if r.content else {}

    return {
        "http": r.status_code,
        "data": data.get("data"),
        "errors": data.get("errors"),
        "raw": data,
    }


@dataclass(frozen=True)
class SeriesNode:
    id: str
    start: datetime
    t1: str
    t2: str
    tournament: str


def fetch_series_for_window(api_key: str, tmin: datetime, tmax: datetime, title_id: str = CS2_TITLE_ID) -> List[SeriesNode]:
    cache_key = f"{title_id}_{iso_z(tmin)}_{iso_z(tmax)}".replace(":", "").replace("-", "")
    cpath = CACHE_SERIES_WINDOWS / f"series_{cache_key}.json"
    cached = cache_get(cpath)
    if cached is not None:
        nodes = []
        for x in cached:
            try:
                nodes.append(
                    SeriesNode(
                        id=str(x["id"]),
                        start=dt_from_unix(int(x["start_ts"])),
                        t1=x["t1"],
                        t2=x["t2"],
                        tournament=x.get("tournament", "") or "",
                    )
                )
            except Exception:
                continue
        return nodes

    q = """
    query AllSeries($first:Int!, $after:String, $filter:SeriesFilter!, $orderBy:SeriesOrderBy!, $orderDirection:OrderDirection!) {
      allSeries(first:$first, after:$after, filter:$filter, orderBy:$orderBy, orderDirection:$orderDirection) {
        edges {
          node {
            id
            startTimeScheduled
            tournament { name }
            teams { baseInfo { name } }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
    """.strip()

    filt: Dict[str, Any] = {
        "startTimeScheduled": {"gte": iso_z(tmin), "lte": iso_z(tmax)},
        "titleId": title_id,
    }

    nodes: List[SeriesNode] = []
    after: Optional[str] = None
    page_size = 50
    max_pages = 120  # safety

    for _ in range(max_pages):
        resp = gql_post(
            CENTRAL_DATA_URL,
            api_key,
            q,
            {
                "first": page_size,
                "after": after,
                "filter": filt,
                "orderBy": "StartTimeScheduled",
                "orderDirection": "ASC",
            },
            timeout=60,
        )

        if resp["http"] != 200 or resp["errors"] or not resp["data"]:
            break

        block = (resp["data"] or {}).get("allSeries") or {}
        for e in block.get("edges") or []:
            n = (e or {}).get("node") or {}
            sid = n.get("id")
            st = n.get("startTimeScheduled")
            if not sid or not st:
                continue

            try:
                start_dt = datetime.fromisoformat(st.replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                continue

            team_names = []
            for t in (n.get("teams") or []):
                bi = (t or {}).get("baseInfo") or {}
                nm = bi.get("name")
                if nm:
                    team_names.append(nm)
            if len(team_names) < 2:
                continue

            tour = ((n.get("tournament") or {}) or {}).get("name") or ""
            nodes.append(SeriesNode(id=str(sid), start=start_dt, t1=team_names[0], t2=team_names[1], tournament=tour))

        pi = block.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        if not after:
            break

    cache_put(
        cpath,
        [
            {"id": s.id, "start_ts": int(s.start.timestamp()), "t1": s.t1, "t2": s.t2, "tournament": s.tournament}
            for s in nodes
        ],
    )
    return nodes


def build_index(series: List[SeriesNode]) -> Dict[Tuple[str, str], List[SeriesNode]]:
    idx: Dict[Tuple[str, str], List[SeriesNode]] = defaultdict(list)
    for s in series:
        a = normalize_team_name(s.t1)
        b = normalize_team_name(s.t2)
        if not a or not b:
            continue
        idx[tuple(sorted((a, b)))].append(s)
    for k in idx:
        idx[k].sort(key=lambda x: x.start)
    return idx


def pick_best_series(idx: Dict[Tuple[str, str], List[SeriesNode]], team1: str, team2: str, hltv_start: datetime) -> Optional[SeriesNode]:
    a = normalize_team_name(team1)
    b = normalize_team_name(team2)
    key = tuple(sorted((a, b)))

    candidates = idx.get(key, [])

    if not candidates:
        brute: List[SeriesNode] = []
        for lst in idx.values():
            for s in lst:
                if (team_match(team1, s.t1) or team_match(team1, s.t2)) and (team_match(team2, s.t1) or team_match(team2, s.t2)):
                    brute.append(s)
        candidates = brute

    best = None
    best_dt = 10**18
    for s in candidates:
        dt = abs(int((s.start - hltv_start).total_seconds()))
        if dt < best_dt:
            best_dt = dt
            best = s

    if best is None:
        return None
    if best_dt > MAX_TIME_DIFF_SECONDS:
        return None
    return best


def fetch_series_state(api_key: str, series_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    cpath = CACHE_SERIES_STATE / f"{series_id}.json"
    cached = cache_get(cpath)
    if cached is not None:
        return cached, None

    q = """
    query SS($id: ID!) {
      seriesState(id: $id) {
        started
        finished
        startedAt
        teams {
          won
          score
          firstKill
          players { firstKill kills deaths }
        }
        games {
          teams { firstKill players { firstKill } }
          segments {
            teams { firstKill players { firstKill } }
          }
        }
      }
    }
    """.strip()

    resp = gql_post(SERIES_STATE_URL, api_key, q, {"id": series_id}, timeout=120)

    if resp["http"] != 200:
        return None, f"HTTP_{resp['http']}"
    if resp["errors"]:
        # common: UNAUTHENTICATED, PERMISSION_DENIED, etc.
        return None, f"GQL_ERROR: {json.dumps(resp['errors'], ensure_ascii=False)[:500]}"
    ss = (resp["data"] or {}).get("seriesState")
    if ss is None:
        return None, "seriesState_null"

    cache_put(cpath, ss)
    return ss, None


def extract_opening_kills(series_state: Dict[str, Any]) -> Dict[str, Any]:
    team_openers = defaultdict(int)
    player_openers = defaultdict(int)

    games = series_state.get("games") or []
    seg_count = 0

    for g in games:
        segments = (g or {}).get("segments") or []
        for seg in segments:
            seg_count += 1
            teams = (seg or {}).get("teams") or []
            for ti, t in enumerate(teams):
                if (t or {}).get("firstKill") is True:
                    team_openers[str(ti)] += 1
                players = (t or {}).get("players") or []
                for pi, p in enumerate(players):
                    if (p or {}).get("firstKill") is True:
                        player_openers[f"{ti}:{pi}"] += 1

    return {
        "segments_count": seg_count,
        "team_openers_by_index": dict(team_openers),
        "player_openers_by_index": dict(player_openers),
    }


def main() -> None:
    env_path = load_env_from_parent()
    if env_path:
        print(f"Loaded .env from: {env_path}")

    api_key = (os.getenv("GRID_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GRID_API_KEY is missing (set env var or put it in ../.env).")

    in_path = Path(os.getenv("HLTV_MATCHES_PATH", str(DEFAULT_INPUT)))
    out_path = Path(os.getenv("GRID_FIRSTKILL_OUT", str(DEFAULT_OUTPUT)))

    if not in_path.exists():
        raise RuntimeError(f"Input file not found: {in_path}")

    matches = read_jsonl(in_path)
    print(f"Loaded {len(matches)} HLTV records from {in_path}")

    by_day: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)
    for m in matches:
        ts = safe_int(m.get("timestamp"))
        if ts is None:
            continue
        dt = dt_from_unix(ts)
        day = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        by_day[day].append(m)

    days = sorted(by_day.keys())
    print(f"Processing {len(days)} day-windows...")

    written = 0
    ok = 0
    no_match = 0
    ss_err = 0

    with out_path.open("w", encoding="utf-8") as f_out:
        for day in days:
            tmin = day - timedelta(hours=WINDOW_PAD_HOURS)
            tmax = day + timedelta(days=1) + timedelta(hours=WINDOW_PAD_HOURS)

            series_nodes = fetch_series_for_window(api_key, tmin, tmax, title_id=CS2_TITLE_ID)
            idx = build_index(series_nodes)

            for m in by_day[day]:
                match_id = m.get("match_id")
                match_url = m.get("match_url")
                team1 = m.get("team1_name") or ""
                team2 = m.get("team2_name") or ""
                ts = safe_int(m.get("timestamp"))

                if not team1 or not team2 or ts is None:
                    rec = {
                        "status": "bad_input_record",
                        "match_id": match_id,
                        "match_url": match_url,
                    }
                    f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    continue

                hltv_start = dt_from_unix(ts)
                best = pick_best_series(idx, team1, team2, hltv_start)

                if best is None:
                    rec = {
                        "status": "no_grid_match",
                        "match_id": match_id,
                        "match_url": match_url,
                        "hltv_teams": [team1, team2],
                        "timestamp": ts,
                    }
                    f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    no_match += 1
                    continue

                ss, err = fetch_series_state(api_key, best.id)
                if ss is None:
                    rec = {
                        "status": "series_state_error",
                        "match_id": match_id,
                        "match_url": match_url,
                        "grid_series_id": best.id,
                        "grid_startTimeScheduled": iso_z(best.start),
                        "grid_tournament": best.tournament,
                        "grid_teams": [best.t1, best.t2],
                        "hltv_teams": [team1, team2],
                        "timestamp": ts,
                        "time_diff_seconds": int(abs((best.start - hltv_start).total_seconds())),
                        "error": err,
                    }
                    f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                    ss_err += 1
                    time.sleep(SLEEP_SECONDS)
                    continue

                metrics = extract_opening_kills(ss)
                rec = {
                    "status": "ok",
                    "match_id": match_id,
                    "match_url": match_url,
                    "grid_series_id": best.id,
                    "grid_startTimeScheduled": iso_z(best.start),
                    "grid_tournament": best.tournament,
                    "grid_teams": [best.t1, best.t2],
                    "hltv_teams": [team1, team2],
                    "timestamp": ts,
                    "time_diff_seconds": int(abs((best.start - hltv_start).total_seconds())),
                    "opening_kills": metrics,
                }
                f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
                ok += 1

                time.sleep(SLEEP_SECONDS)

    print(f"Done. Wrote -> {out_path}")
    print(f"Summary: written={written} ok={ok} no_grid_match={no_match} series_state_error={ss_err}")


if __name__ == "__main__":
    main()
