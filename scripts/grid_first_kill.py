from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
SERIES_STATE_URL = "https://api-op.grid.gg/live-data-feed/series-state/graphql"

TEAM1 = "FaZe"
TEAM2 = "3DMAX"
TMIN = "2026-01-27T18:00:00Z"
TMAX = "2026-02-04T18:00:00Z"

OUT_PATH = Path("data/raw/grid_series_state.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

KEYWORDS = [
    "firstkill",
    "first_kill",
    "opening",
    "opener",
    "trade",
    "traded",
    "clutch",
    "multikill",
    "multi_kill",
    "multi",
]

TEAM_ALIASES = {
    "faze": ["faze clan", "faze"],
    "3dmax": ["3dmax"],
}

ORG_SUFFIXES = {
    "clan",
    "team",
    "esports",
    "e-sports",
    "gaming",
    "gg",
    "club",
    "sport",
    "sports",
    "academy",
    "international",
    "organisation",
    "organization",
}


def load_env_from_parent() -> Optional[Path]:
    here = Path(__file__).resolve()
    env_path = here.parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)
    return env_path


def auth_header_candidates(api_key: str) -> List[Tuple[str, Dict[str, str]]]:
    return [
        ("x-api-key", {"x-api-key": api_key}),
        ("authorization_raw", {"Authorization": api_key}),
        ("authorization_bearer", {"Authorization": f"Bearer {api_key}"}),
        ("x_api_key_caps", {"X-API-KEY": api_key}),
    ]


def post_graphql(url: str, headers: Dict[str, str], query: str, variables: Optional[Dict[str, Any]], timeout: int) -> requests.Response:
    h = dict(headers)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    payload: Dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    return requests.post(url, headers=h, json=payload, timeout=timeout)


def gql_post_authed(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 60) -> Tuple[Dict[str, Any], str]:
    last_err: Optional[str] = None
    last_head: Optional[str] = None

    for label, hdr in auth_header_candidates(api_key):
        r = post_graphql(url, hdr, query, variables, timeout)
        txt = r.text or ""
        head = txt[:300].replace("\n", "\\n")
        last_head = head

        try:
            data = r.json()
        except Exception:
            last_err = f"{label}: Non-JSON HTTP {r.status_code} HEAD: {head}"
            continue

        if r.status_code != 200:
            errs = data.get("errors") if isinstance(data, dict) else None
            if errs:
                last_err = f"{label}: HTTP {r.status_code} ERRORS: {json.dumps(errs, indent=2)}"
            else:
                last_err = f"{label}: HTTP {r.status_code} HEAD: {head}"
            continue

        if isinstance(data, dict) and data.get("errors"):
            msg = json.dumps(data["errors"], indent=2)
            last_err = f"{label}: ERRORS: {msg}"
            if any(
                isinstance(e, dict) and e.get("extensions", {}).get("errorType") in ("UNAUTHENTICATED", "PERMISSION_DENIED")
                for e in data["errors"]
            ):
                continue
            continue

        if not isinstance(data, dict) or "data" not in data:
            last_err = f"{label}: Missing 'data' in response HEAD: {head}"
            continue

        return data, label

    raise RuntimeError(f"All auth header variants failed for {url}.\nLAST: {last_err}\nHEAD: {last_head}")


def normalize_team_name(s: str) -> str:
    s2 = (s or "").strip().lower()
    s2 = s2.replace("&", " and ")
    s2 = re.sub(r"[^a-z0-9\s]+", " ", s2)
    s2 = re.sub(r"\s+", " ", s2).strip()
    toks = [t for t in s2.split(" ") if t and t not in ORG_SUFFIXES]
    s2 = " ".join(toks).strip()
    return s2


def expanded_team_keys(name: str) -> List[str]:
    base = normalize_team_name(name)
    keys = {base}
    alias_list = TEAM_ALIASES.get(base)
    if alias_list:
        for a in alias_list:
            keys.add(normalize_team_name(a))
    return sorted(k for k in keys if k)


def team_match(a: str, b: str) -> bool:
    a_n = normalize_team_name(a)
    b_n = normalize_team_name(b)
    if not a_n or not b_n:
        return False
    if a_n == b_n:
        return True
    if a_n in b_n or b_n in a_n:
        return True
    return False


def team_names_from_node(node: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for t in node.get("teams") or []:
        bi = t.get("baseInfo") or {}
        nm = bi.get("name")
        if nm:
            out.append(nm)
    return out


def fetch_titles_optional(api_key: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    q = "query Titles { titles { id name } }"
    try:
        data, used = gql_post_authed(CENTRAL_DATA_URL, api_key, q, None, timeout=60)
        return data["data"]["titles"], used
    except Exception:
        return [], None


def resolve_cs2_title_id(titles: List[Dict[str, Any]]) -> Optional[str]:
    for t in titles:
        nm = (t.get("name") or "").lower()
        if "counter strike 2" in nm:
            return str(t.get("id"))
    return None


def all_series_in_window(api_key: str, tmin: str, tmax: str, title_id: Optional[str], page_size: int = 50, max_pages: int = 60) -> Tuple[List[Dict[str, Any]], str]:
    q = """
    query AllSeries($first: Int!, $after: String, $filter: SeriesFilter!, $orderBy: SeriesOrderBy!, $orderDirection: OrderDirection!) {
      allSeries(first: $first, after: $after, filter: $filter, orderBy: $orderBy, orderDirection: $orderDirection) {
        totalCount
        edges {
          node {
            id
            startTimeScheduled
            type
            tournament { id name }
            title { id name }
            teams { baseInfo { id name } }
          }
        }
        pageInfo { endCursor hasNextPage }
      }
    }
    """.strip()

    filt: Dict[str, Any] = {"startTimeScheduled": {"gte": tmin, "lte": tmax}}
    if title_id is not None:
        filt["titleId"] = title_id

    series: List[Dict[str, Any]] = []
    after: Optional[str] = None
    used_label = ""

    for _ in range(max_pages):
        vars_ = {
            "first": page_size,
            "after": after,
            "filter": filt,
            "orderBy": "StartTimeScheduled",
            "orderDirection": "ASC",
        }
        data, used = gql_post_authed(CENTRAL_DATA_URL, api_key, q, vars_, timeout=60)
        used_label = used
        block = data["data"]["allSeries"]
        edges = block.get("edges") or []
        for e in edges:
            n = (e or {}).get("node")
            if n:
                series.append(n)

        pi = block.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        after = pi.get("endCursor")
        if not after:
            break

    return series, used_label


def pick_candidate(series: List[Dict[str, Any]], team1: str, team2: str) -> Optional[Dict[str, Any]]:
    t1_keys = expanded_team_keys(team1)
    t2_keys = expanded_team_keys(team2)

    for n in series:
        grid_names = team_names_from_node(n)
        ok1 = any(any(team_match(k, g) for g in grid_names) for k in t1_keys)
        ok2 = any(any(team_match(k, g) for g in grid_names) for k in t2_keys)
        if ok1 and ok2:
            return n
    return None


def introspect_series_state_root(api_key: str) -> Tuple[List[Dict[str, Any]], str]:
    q = """
    query IntrospectQueryFields {
      __schema {
        queryType {
          fields {
            name
            args { name type { kind name ofType { kind name ofType { kind name } } } }
            type { kind name ofType { kind name ofType { kind name } } }
          }
        }
      }
    }
    """.strip()
    data, used = gql_post_authed(SERIES_STATE_URL, api_key, q, None, timeout=60)
    return data["data"]["__schema"]["queryType"]["fields"], used


def unwrap_named_type(t: Dict[str, Any]) -> Tuple[str, str]:
    cur = t
    while isinstance(cur, dict):
        kind = cur.get("kind") or ""
        name = cur.get("name") or ""
        if name:
            return kind, name
        cur = cur.get("ofType")
    return "", ""


def introspect_type_fields(api_key: str, type_name: str) -> List[Dict[str, Any]]:
    q = """
    query TypeFields($name: String!) {
      __type(name: $name) {
        name
        kind
        fields {
          name
          type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        }
      }
    }
    """.strip()
    data, _ = gql_post_authed(SERIES_STATE_URL, api_key, q, {"name": type_name}, timeout=60)
    t = data["data"].get("__type")
    if not t:
        return []
    return t.get("fields") or []


def build_selection(api_key: str, root_type: str, depth: int = 6) -> str:
    scalar_kinds = {"SCALAR", "ENUM"}
    seen: set[str] = set()

    preferred_scalars = {
        "id",
        "startedAt",
        "started",
        "finished",
        "endedAt",
        "updatedAt",
        "name",
        "score",
        "won",
        "kills",
        "deaths",
        "round",
        "roundNumber",
        "map",
        "mapName",
        "side",
        "teamId",
        "playerId",
        "time",
        "timestamp",
    }

    prefer_objects = {
        "teams",
        "players",
        "games",
        "maps",
        "segments",
        "rounds",
        "events",
        "kills",
        "stats",
        "statistics",
        "firstKill",
        "firstKills",
        "opening",
        "openings",
        "trades",
        "trade",
        "clutches",
        "clutch",
        "multi",
        "multiKills",
    }

    def rec(tname: str, d: int) -> str:
        if d <= 0 or not tname:
            return "id"
        if tname in seen:
            return "id"
        seen.add(tname)

        flds = introspect_type_fields(api_key, tname)
        if not flds:
            return "id"

        scalars: List[str] = []
        objects: List[str] = []

        for f in flds:
            fn = f["name"]
            kind, name = unwrap_named_type(f["type"])
            low = fn.lower()

            if kind in scalar_kinds:
                if fn in preferred_scalars or any(k in low for k in KEYWORDS):
                    scalars.append(fn)
                continue

            if fn in prefer_objects or any(k in low for k in KEYWORDS):
                sub = rec(name, d - 1)
                objects.append(f"{fn} {{ {sub} }}")

        if "id" not in scalars and any(ff["name"] == "id" for ff in flds):
            scalars.insert(0, "id")

        if not scalars and not objects:
            fallback: List[str] = []
            for f in flds[:14]:
                fn = f["name"]
                kind, name = unwrap_named_type(f["type"])
                if kind in scalar_kinds:
                    fallback.append(fn)
                else:
                    fallback.append(f"{fn} {{ id }}")
            return " ".join(fallback) if fallback else "id"

        return " ".join(scalars + objects)

    return rec(root_type, depth)


def flatten_paths(obj: Any, prefix: str = "") -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(flatten_paths(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            out.extend(flatten_paths(v, p))
    else:
        out.append((prefix, obj))
    return out


def main() -> None:
    env_path = load_env_from_parent()
    if env_path:
        print(f"Loaded .env from: {env_path}")

    api_key = os.getenv("GRID_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GRID_API_KEY missing (set env var or put it in ../.env)")

    titles, titles_auth = fetch_titles_optional(api_key)
    if titles:
        print(f"Fetched {len(titles)} titles using auth='{titles_auth}'")
    else:
        print("Could not fetch titles (not required). Continuing without titles...")

    cs2_title_id = resolve_cs2_title_id(titles) if titles else None
    if cs2_title_id:
        print(f"Resolved CS2 titleId={cs2_title_id}")
    else:
        print("No CS2 titleId resolved. Will query allSeries without title filter and filter locally by teams.")

    series, used_auth = all_series_in_window(api_key, TMIN, TMAX, title_id=cs2_title_id, page_size=50, max_pages=60)
    print(f"Fetched {len(series)} series using auth='{used_auth}'")

    cand = pick_candidate(series, TEAM1, TEAM2)

    if not cand and cs2_title_id is not None:
        print("No candidate found with CS2 title filter. Retrying without titleId filter...")
        series, used_auth = all_series_in_window(api_key, TMIN, TMAX, title_id=None, page_size=50, max_pages=60)
        print(f"Fetched {len(series)} series (no title filter) using auth='{used_auth}'")
        cand = pick_candidate(series, TEAM1, TEAM2)

    if not cand:
        raise RuntimeError("No candidate series found matching both teams in that time window.")

    series_id = str(cand["id"])
    print("\nPicked candidate:")
    print(
        json.dumps(
            {
                "id": series_id,
                "startTimeScheduled": cand.get("startTimeScheduled"),
                "tournament": (cand.get("tournament") or {}).get("name"),
                "title": (cand.get("title") or {}).get("name"),
                "teams": team_names_from_node(cand),
                "match_keys": {
                    "team1_keys": expanded_team_keys(TEAM1),
                    "team2_keys": expanded_team_keys(TEAM2),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    fields, ss_auth = introspect_series_state_root(api_key)
    print(f"\nSeries State introspection auth='{ss_auth}' fields={len(fields)}")

    ss = next((f for f in fields if f.get("name") == "seriesState"), None)
    if not ss:
        raise RuntimeError("seriesState field not found on series-state endpoint.")

    _, root_type = unwrap_named_type(ss["type"])
    print(f"seriesState return type: {root_type}")

    selection = build_selection(api_key, root_type, depth=6)
    query = f"""
    query SeriesState($id: ID!) {{
      seriesState(id: $id) {{
        {selection}
      }}
    }}
    """.strip()

    data, used = gql_post_authed(SERIES_STATE_URL, api_key, query, {"id": series_id}, timeout=120)
    payload = data["data"]["seriesState"]
    if payload is None:
        raise RuntimeError("seriesState returned null")

    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote -> {OUT_PATH}")

    flat = flatten_paths(payload)
    kw_re = re.compile("|".join(re.escape(k) for k in KEYWORDS), re.IGNORECASE)
    hits = [(p, v) for (p, v) in flat if kw_re.search(p)]

    print(f"\nKeyword hits ({len(hits)}):")
    for p, v in hits[:250]:
        if isinstance(v, (dict, list)):
            preview = f"<{type(v).__name__} len={len(v)}>"
        else:
            preview = v
        print(f"- {p}: {preview}")

    if not hits:
        print("\nNo keyword paths matched. Open the JSON file and search for firstKill/opening/trade/clutch/multi.")


if __name__ == "__main__":
    main()
