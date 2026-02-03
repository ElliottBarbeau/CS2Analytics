import os
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import requests


CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

INPUT_JSONL = Path("data/raw/hltv_match_veto_and_stats.jsonl")
OUTPUT_JSONL = Path("data/raw/hltv_with_grid_openings.jsonl")


def load_env_one_level_up():
    here = Path(__file__).resolve()
    repo_root = here.parents[1]
    env_path = repo_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv(env_path)
            print(f"Loaded .env from: {env_path}")
        except Exception:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
            print(f"Loaded .env from: {env_path}")


def http_post_json(url: str, api_key: str, payload: Dict[str, Any], timeout: int = 60) -> Dict[str, Any]:
    r = requests.post(
        url,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        data=json.dumps(payload),
        timeout=timeout,
    )
    txt = r.text or ""
    if r.status_code != 200:
        head = txt[:200].replace("\n", " ")
        raise RuntimeError(f"HTTP {r.status_code} for {url} HEAD: {head}")
    try:
        return r.json()
    except Exception:
        head = txt[:200].replace("\n", " ")
        raise RuntimeError(f"Non-JSON response from {url} HEAD: {head}")


def gql_post(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Dict[str, Any]:
    payload = {"query": query, "variables": variables or {}}
    data = http_post_json(url, api_key, payload, timeout=timeout)
    if "errors" in data and data["errors"]:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data


def norm_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def team_keys(name: str) -> List[str]:
    n = norm_key(name)
    keys = set()
    if n:
        keys.add(n)
        keys.add(n.replace(" ", ""))
    aliases = {
        "faze clan": ["faze", "fazeclan"],
        "team vitality": ["vitality"],
        "natus vincere": ["navi", "natusvincere"],
        "g2 esports": ["g2"],
        "virtus pro": ["virtuspro", "vp"],
        "ninjas in pyjamas": ["nip"],
        "team liquid": ["liquid"],
        "team spirit": ["spirit"],
    }
    if n in aliases:
        for a in aliases[n]:
            keys.add(norm_key(a))
            keys.add(norm_key(a).replace(" ", ""))
    out = [k for k in keys if k]
    out.sort(key=len, reverse=True)
    return out


def any_key_in(name: str, keys: List[str]) -> bool:
    nk = norm_key(name)
    n0 = nk.replace(" ", "")
    for k in keys:
        if not k:
            continue
        if k in nk or k in n0:
            return True
    return False


def iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_titles(api_key: str) -> List[Dict[str, Any]]:
    q = """
    query {
      titles {
        id
        name
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q, None, timeout=60)
    return data["data"]["titles"]


def resolve_cs2_title_id(titles: List[Dict[str, Any]]) -> Optional[str]:
    for t in titles:
        if (t.get("name") or "").strip().lower() == "counter strike 2":
            return str(t.get("id"))
    return None


def fetch_order_enums(api_key: str) -> Tuple[str, str]:
    q = """
    query {
      __type(name: "OrderDirection") { enumValues { name } }
      __type(name: "SeriesOrderBy") { enumValues { name } }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q, None, timeout=60)
    od = [x["name"] for x in (data["data"]["__type"][0]["enumValues"] if isinstance(data["data"]["__type"], list) else data["data"]["__type"]["enumValues"])]
    return ("ASC" if "ASC" in od else od[0], "StartTimeScheduled")


def fetch_series_in_window(
    api_key: str,
    tmin: str,
    tmax: str,
    title_id: Optional[str],
    max_pages: int = 30,
    page_size: int = 50,
) -> List[Dict[str, Any]]:
    if page_size > 50:
        page_size = 50

    q = """
    query AllSeries($first: Int, $after: String, $filter: SeriesFilter, $orderBy: SeriesOrderBy!, $orderDirection: OrderDirection!) {
      allSeries(first: $first, after: $after, filter: $filter, orderBy: $orderBy, orderDirection: $orderDirection) {
        totalCount
        edges {
          node {
            id
            startTimeScheduled
            title { id name }
            tournament { id name }
            teams { baseInfo { id name } }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
    """

    order_direction = "ASC"
    order_by = "StartTimeScheduled"

    flt: Dict[str, Any] = {
        "startTimeScheduled": {"gte": tmin, "lte": tmax},
    }
    if title_id:
        flt["titleId"] = title_id

    out: List[Dict[str, Any]] = []
    after = None
    pages = 0

    while pages < max_pages:
        vars_ = {
            "first": page_size,
            "after": after,
            "filter": flt,
            "orderBy": order_by,
            "orderDirection": order_direction,
        }
        data = gql_post(CENTRAL_DATA_URL, api_key, q, vars_, timeout=90)
        block = data["data"]["allSeries"]
        edges = block.get("edges") or []
        for e in edges:
            n = e.get("node") or {}
            out.append(n)
        pi = block.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        pages += 1

    return out


def pick_candidate_series(
    series_list: List[Dict[str, Any]],
    team1: str,
    team2: str,
    approx_start: datetime,
) -> Optional[Dict[str, Any]]:
    k1 = team_keys(team1)
    k2 = team_keys(team2)

    best = None
    best_diff = None

    for s in series_list:
        teams = s.get("teams") or []
        team_names = []
        for t in teams:
            bi = (t or {}).get("baseInfo") or {}
            nm = bi.get("name")
            if nm:
                team_names.append(str(nm))
        if len(team_names) < 2:
            continue

        has1 = any(any_key_in(nm, k1) for nm in team_names)
        has2 = any(any_key_in(nm, k2) for nm in team_names)
        if not (has1 and has2):
            continue

        sts = s.get("startTimeScheduled")
        if not sts:
            continue
        try:
            st_dt = datetime.fromisoformat(sts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue

        diff = abs(int((st_dt - approx_start).total_seconds()))
        if best is None or (best_diff is not None and diff < best_diff) or best_diff is None:
            best = s
            best_diff = diff

    return best


def introspect_series_player_state_fields(api_key: str) -> List[str]:
    q = """
    query {
      __type(name: "SeriesPlayerState") {
        fields { name }
      }
    }
    """
    data = gql_post(SERIES_STATE_URL, api_key, q, None, timeout=60)
    t = data["data"]["__type"] or {}
    fields = t.get("fields") or []
    return [f.get("name") for f in fields if f.get("name")]


def series_state_query(include_first_death: bool) -> str:
    fd = "\n            firstDeath" if include_first_death else ""
    return f"""
    query SeriesState($id: ID!) {{
      seriesState(id: $id) {{
        startedAt
        started
        finished
        teams {{
          won
          score
          kills
          deaths
          firstKill
          players {{
            id
            name
            firstKill{fd}
            kills
            deaths
          }}
        }}
        games {{
          id
          teams {{
            id
            name
            firstKill
            players {{
              id
              name
              firstKill{fd}
            }}
          }}
          segments {{
            id
            teams {{
              id
              name
              players {{
                id
                name
                firstKill{fd}
              }}
            }}
          }}
        }}
      }}
    }}
    """


def aggregate_openings(series_state: Dict[str, Any]) -> Dict[str, Any]:
    ss = (series_state or {}).get("data", {}).get("seriesState") or {}
    games = ss.get("games") or []
    teams_series = ss.get("teams") or []

    player_names: Dict[str, str] = {}
    team_names: List[str] = []

    series_team_totals_fk: Dict[str, int] = {}
    series_team_totals_fd: Dict[str, int] = {}
    series_player_fk: Dict[str, int] = {}
    series_player_fd: Dict[str, int] = {}

    def bump(d: Dict[str, int], k: str, n: int = 1):
        d[k] = int(d.get(k, 0)) + n

    for ti, t in enumerate(teams_series):
        players = (t or {}).get("players") or []
        fk_series_bool = (t or {}).get("firstKill") is True
        tn = None
        for p in players:
            pid = p.get("id")
            if pid:
                player_names[str(pid)] = str(p.get("name") or player_names.get(str(pid)) or "")
        if fk_series_bool:
            pass

    per_game: Dict[str, Any] = {}
    for gi, g in enumerate(games):
        gid = g.get("id") or f"game_{gi}"
        segments = g.get("segments") or []
        fk: Dict[str, int] = {}
        fd: Dict[str, int] = {}
        for seg in segments:
            for team in (seg.get("teams") or []):
                for p in (team.get("players") or []):
                    pid = p.get("id")
                    if not pid:
                        continue
                    pid = str(pid)
                    if p.get("name"):
                        player_names[pid] = str(p.get("name"))
                    if p.get("firstKill") is True:
                        bump(fk, pid, 1)
                        bump(series_player_fk, pid, 1)
                    if p.get("firstDeath") is True:
                        bump(fd, pid, 1)
                        bump(series_player_fd, pid, 1)

        players_out = []
        all_pids = set(list(fk.keys()) + list(fd.keys()))
        for pid in all_pids:
            attempts = int(fk.get(pid, 0)) + int(fd.get(pid, 0))
            players_out.append(
                {
                    "player_id": pid,
                    "name": player_names.get(pid) or None,
                    "first_kills": int(fk.get(pid, 0)),
                    "first_deaths": int(fd.get(pid, 0)),
                    "opening_attempts": attempts,
                    "opening_success_rate": (int(fk.get(pid, 0)) / attempts) if attempts else None,
                }
            )
        players_out.sort(key=lambda x: (x["opening_attempts"], x["first_kills"]), reverse=True)

        per_game[gid] = {
            "game_id": gid,
            "segments_count": len(segments),
            "players": players_out,
        }

    series_players_out = []
    all_series_pids = set(list(series_player_fk.keys()) + list(series_player_fd.keys()))
    for pid in all_series_pids:
        attempts = int(series_player_fk.get(pid, 0)) + int(series_player_fd.get(pid, 0))
        series_players_out.append(
            {
                "player_id": pid,
                "name": player_names.get(pid) or None,
                "first_kills": int(series_player_fk.get(pid, 0)),
                "first_deaths": int(series_player_fd.get(pid, 0)),
                "opening_attempts": attempts,
                "opening_success_rate": (int(series_player_fk.get(pid, 0)) / attempts) if attempts else None,
            }
        )
    series_players_out.sort(key=lambda x: (x["opening_attempts"], x["first_kills"]), reverse=True)

    return {
        "series": {
            "startedAt": ss.get("startedAt"),
            "started": ss.get("started"),
            "finished": ss.get("finished"),
        },
        "per_game": per_game,
        "series_players": series_players_out,
    }


def read_processed_match_ids(path: Path) -> set:
    done = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            mid = obj.get("match_id")
            if mid is not None:
                done.add(int(mid))
    return done


def main():
    load_env_one_level_up()
    api_key = os.environ.get("GRID_API_KEY") or os.environ.get("GRID_APIKEY") or os.environ.get("GRID_KEY")
    if not api_key:
        raise RuntimeError("GRID_API_KEY is not set in environment variables.")

    if not INPUT_JSONL.exists():
        raise RuntimeError(f"Input not found: {INPUT_JSONL}")

    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)

    processed = read_processed_match_ids(OUTPUT_JSONL)
    print(f"Input:  {INPUT_JSONL}")
    print(f"Output: {OUTPUT_JSONL}")
    print(f"Already processed: {len(processed)}")

    print("Fetching titles from Central Data...")
    titles = fetch_titles(api_key)
    cs2_title_id = resolve_cs2_title_id(titles) or "28"
    print(f"Resolved CS2 titleId={cs2_title_id}")

    try:
        fields = introspect_series_player_state_fields(api_key)
    except Exception as e:
        print(f"Series State introspection failed, will proceed without firstDeath. Error: {e}")
        fields = []
    include_first_death = "firstDeath" in set(fields)
    print(f"Series State: firstDeath supported={include_first_death}")

    q_state = series_state_query(include_first_death)

    total = 0
    written = 0
    found = 0
    missed = 0
    errored = 0
    start_wall = time.time()

    out_f = OUTPUT_JSONL.open("a", encoding="utf-8")

    try:
        with INPUT_JSONL.open("r", encoding="utf-8") as f:
            for line in f:
                total += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue

                mid = rec.get("match_id")
                if mid is None:
                    continue
                try:
                    mid_int = int(mid)
                except Exception:
                    continue
                if mid_int in processed:
                    if total % 250 == 0:
                        elapsed = time.time() - start_wall
                        rate = (written / elapsed) if elapsed > 0 else 0.0
                        print(f"[skip] total={total} written={written} found={found} missed={missed} errors={errored} rate={rate:.2f}/s")
                    continue

                team1 = rec.get("team1_name") or ""
                team2 = rec.get("team2_name") or ""
                ts = rec.get("timestamp")
                if not team1 or not team2 or not ts:
                    rec["grid"] = {"found": False, "errors": [{"message": "missing team1/team2/timestamp"}]}
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out_f.flush()
                    processed.add(mid_int)
                    written += 1
                    missed += 1
                    continue

                try:
                    approx = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                except Exception:
                    rec["grid"] = {"found": False, "errors": [{"message": "bad timestamp"}]}
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out_f.flush()
                    processed.add(mid_int)
                    written += 1
                    missed += 1
                    continue

                window_hours = 72
                tmin = iso_z(approx - timedelta(hours=window_hours))
                tmax = iso_z(approx + timedelta(hours=window_hours))

                grid_obj: Dict[str, Any] = {
                    "found": False,
                    "series_id": None,
                    "series_startTimeScheduled": None,
                    "tournament": None,
                    "title": None,
                    "teams": None,
                    "first_kill_summary": None,
                    "errors": None,
                    "abs_time_diff_sec": None,
                }

                try:
                    series_list = fetch_series_in_window(api_key, tmin, tmax, cs2_title_id, max_pages=30, page_size=50)
                    cand = pick_candidate_series(series_list, team1, team2, approx)

                    if not cand:
                        series_list2 = fetch_series_in_window(api_key, tmin, tmax, None, max_pages=30, page_size=50)
                        cand = pick_candidate_series(series_list2, team1, team2, approx)

                    if not cand:
                        grid_obj["found"] = False
                        grid_obj["errors"] = [{"message": "no candidate series found"}]
                        rec["grid"] = grid_obj
                        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        out_f.flush()
                        processed.add(mid_int)
                        written += 1
                        missed += 1
                    else:
                        sid = str(cand.get("id"))
                        sts = cand.get("startTimeScheduled")
                        tour = (cand.get("tournament") or {}).get("name")
                        title = (cand.get("title") or {}).get("name")
                        tnames = []
                        for t in (cand.get("teams") or []):
                            bi = (t or {}).get("baseInfo") or {}
                            if bi.get("name"):
                                tnames.append(str(bi.get("name")))

                        abs_diff = None
                        try:
                            st_dt = datetime.fromisoformat(str(sts).replace("Z", "+00:00")).astimezone(timezone.utc)
                            abs_diff = abs(int((st_dt - approx).total_seconds()))
                        except Exception:
                            abs_diff = None

                        grid_obj.update(
                            {
                                "found": True,
                                "series_id": sid,
                                "series_startTimeScheduled": sts,
                                "tournament": tour,
                                "title": title,
                                "teams": tnames,
                                "abs_time_diff_sec": abs_diff,
                            }
                        )

                        try:
                            data_state = gql_post(SERIES_STATE_URL, api_key, q_state, {"id": sid}, timeout=120)
                            summary = aggregate_openings(data_state)
                            grid_obj["first_kill_summary"] = summary
                            grid_obj["errors"] = None
                        except Exception as e:
                            grid_obj["first_kill_summary"] = None
                            grid_obj["errors"] = [{"message": str(e)}]

                        rec["grid"] = grid_obj
                        out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        out_f.flush()
                        processed.add(mid_int)
                        written += 1
                        found += 1

                except Exception as e:
                    grid_obj["found"] = False
                    grid_obj["errors"] = [{"message": str(e)}]
                    rec["grid"] = grid_obj
                    out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    out_f.flush()
                    processed.add(mid_int)
                    written += 1
                    errored += 1

                if written % 25 == 0:
                    elapsed = time.time() - start_wall
                    rate = (written / elapsed) if elapsed > 0 else 0.0
                    print(
                        f"[progress] total_read={total} written={written} found={found} missed={missed} errors={errored} rate={rate:.2f}/s last_match_id={mid_int}"
                    )

    finally:
        out_f.close()

    elapsed = time.time() - start_wall
    rate = (written / elapsed) if elapsed > 0 else 0.0
    print(f"Done. total_read={total} written={written} found={found} missed={missed} errors={errored} rate={rate:.2f}/s")


if __name__ == "__main__":
    main()
