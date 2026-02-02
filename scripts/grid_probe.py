from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cloudscraper
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"

HLTV_MATCH_URL = "https://www.hltv.org/matches/2389642/faze-vs-3dmax-iem-krakw-2026"

WINDOW_HOURS = 96
PAGE_SIZE = 50
MAX_PAGES = 60

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def gql_post(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {"x-api-key": api_key, "content-type": "application/json"}
    payload = {"query": query, "variables": variables or {}}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        print("HTTP", r.status_code, r.text[:1500], file=sys.stderr)
        raise
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]


def normalize(s: str) -> str:
    return " ".join((s or "").lower().strip().split())


def parse_iso_utc(s: str) -> dt.datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)


def iso_utc_from_unix_ms(ms: int) -> str:
    d = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return d.isoformat().replace("+00:00", "Z")


def time_window_strings(center_utc: dt.datetime, hours: int) -> Tuple[str, str]:
    start = center_utc - dt.timedelta(hours=hours)
    end = center_utc + dt.timedelta(hours=hours)
    return (
        start.isoformat().replace("+00:00", "Z"),
        end.isoformat().replace("+00:00", "Z"),
    )


def get_enum_values(api_key: str, enum_name: str) -> List[str]:
    query = """
    query E($name: String!) {
      __type(name: $name) {
        kind
        enumValues { name }
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, query, {"name": enum_name})
    t = data.get("__type")
    if not t or t.get("kind") != "ENUM":
        return []
    return [v["name"] for v in (t.get("enumValues") or []) if v.get("name")]


def pick(values: List[str], preferred: List[str]) -> str:
    s = set(values)
    for p in preferred:
        if p in s:
            return p
    if not values:
        raise RuntimeError("Enum has no values")
    return values[0]


def fetch_titles(api_key: str) -> List[Dict[str, Any]]:
    query = """
    query {
      titles { id name }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, query)
    raw = data.get("titles")
    if not isinstance(raw, list):
        raise RuntimeError(f"Expected titles to be a list, got {type(raw)}")
    out = []
    for t in raw:
        if isinstance(t, dict) and t.get("id") is not None and t.get("name") is not None:
            out.append({"id": str(t["id"]), "name": str(t["name"])})
    return out


def title_id_by_name(titles: List[Dict[str, Any]], want: str) -> Optional[str]:
    w = normalize(want)
    for t in titles:
        if normalize(t.get("name", "")) == w:
            return str(t["id"])
    return None


def query_series_page_with_title(
    api_key: str,
    tmin: str,
    tmax: str,
    title_id: str,
    order_by: str,
    order_dir: str,
    after: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    query = """
    query Q($tmin: String!, $tmax: String!, $titleId: ID!, $first: Int!, $orderBy: SeriesOrderBy!, $orderDirection: OrderDirection!, $after: String) {
      allSeries(
        first: $first
        after: $after
        orderBy: $orderBy
        orderDirection: $orderDirection
        filter: {
          startTimeScheduled: { gte: $tmin, lte: $tmax }
          titleId: $titleId
        }
      ) {
        pageInfo { endCursor hasNextPage }
        edges {
          node {
            id
            startTimeScheduled
            type
            tournament { id name }
            teams { baseInfo { id name } }
          }
        }
      }
    }
    """
    variables = {
        "tmin": tmin,
        "tmax": tmax,
        "titleId": title_id,
        "first": PAGE_SIZE,
        "orderBy": order_by,
        "orderDirection": order_dir,
        "after": after,
    }
    data = gql_post(CENTRAL_DATA_URL, api_key, query, variables)
    root = data["allSeries"]
    nodes = [e["node"] for e in root["edges"] if e.get("node")]
    end_cursor = (root.get("pageInfo") or {}).get("endCursor")
    has_next = bool((root.get("pageInfo") or {}).get("hasNextPage"))
    return nodes, end_cursor, has_next


def query_series_page_no_title(
    api_key: str,
    tmin: str,
    tmax: str,
    order_by: str,
    order_dir: str,
    after: Optional[str],
) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    query = """
    query Q($tmin: String!, $tmax: String!, $first: Int!, $orderBy: SeriesOrderBy!, $orderDirection: OrderDirection!, $after: String) {
      allSeries(
        first: $first
        after: $after
        orderBy: $orderBy
        orderDirection: $orderDirection
        filter: {
          startTimeScheduled: { gte: $tmin, lte: $tmax }
        }
      ) {
        pageInfo { endCursor hasNextPage }
        edges {
          node {
            id
            startTimeScheduled
            type
            tournament { id name }
            teams { baseInfo { id name } }
            title { id name }
          }
        }
      }
    }
    """
    variables = {
        "tmin": tmin,
        "tmax": tmax,
        "first": PAGE_SIZE,
        "orderBy": order_by,
        "orderDirection": order_dir,
        "after": after,
    }
    data = gql_post(CENTRAL_DATA_URL, api_key, query, variables)
    root = data["allSeries"]
    nodes = [e["node"] for e in root["edges"] if e.get("node")]
    end_cursor = (root.get("pageInfo") or {}).get("endCursor")
    has_next = bool((root.get("pageInfo") or {}).get("hasNextPage"))
    return nodes, end_cursor, has_next


def node_team_names(n: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for t in (n.get("teams") or []):
        bi = t.get("baseInfo") or {}
        if bi.get("name"):
            out.append(str(bi["name"]))
    return out


def fuzzy_match(team: str, names: List[str]) -> bool:
    t = normalize(team)
    for n in names:
        nn = normalize(n)
        if t in nn or nn in t:
            return True
    return False


def filter_candidates(nodes: List[Dict[str, Any]], team1: str, team2: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for n in nodes:
        names = node_team_names(n)
        exact_joined = " ".join(normalize(x) for x in names)
        exact = normalize(team1) in exact_joined and normalize(team2) in exact_joined
        fuzzy = fuzzy_match(team1, names) and fuzzy_match(team2, names)
        if exact or fuzzy:
            out.append(n)
    return out


def filter_cs_titles(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for n in nodes:
        title = n.get("title") or {}
        name = normalize(str(title.get("name") or ""))
        if "counter strike" in name or "counter-strike" in name or "csgo" in name or "cs:go" in name or "cs2" in name:
            out.append(n)
    return out


def fetch_hltv_match_info(url: str) -> Tuple[str, str, str]:
    scraper = cloudscraper.create_scraper()
    r = scraper.get(url, timeout=30)
    html = r.text

    soup = BeautifulSoup(html, "html.parser")

    t1 = soup.select_one(".team1 .teamName, .team1 .teamName a")
    t2 = soup.select_one(".team2 .teamName, .team2 .teamName a")
    team1 = t1.get_text(" ", strip=True) if t1 else ""
    team2 = t2.get_text(" ", strip=True) if t2 else ""

    ts_ms = None
    time_el = soup.select_one(".time[data-unix]")
    if time_el and time_el.get("data-unix"):
        v = re.sub(r"[^\d]", "", str(time_el.get("data-unix")))
        if v.isdigit():
            ts_ms = int(v)

    if ts_ms is None:
        m = re.search(r"data-unix\s*=\s*\"(\d+)\"", html)
        if m:
            ts_ms = int(m.group(1))

    if not team1 or not team2 or ts_ms is None:
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        raise RuntimeError(
            "Could not parse HLTV match info "
            f"(team1='{team1}', team2='{team2}', ts_ms='{ts_ms}', title='{title}', http={r.status_code})"
        )

    approx_start_utc = iso_utc_from_unix_ms(ts_ms)
    return team1, team2, approx_start_utc


def fetch_all_series_try_title(
    api_key: str,
    tmin: str,
    tmax: str,
    title_id: str,
    order_by: str,
    order_dir: str,
) -> List[Dict[str, Any]]:
    all_nodes: List[Dict[str, Any]] = []
    after: Optional[str] = None
    for page in range(1, MAX_PAGES + 1):
        nodes, after, has_next = query_series_page_with_title(api_key, tmin, tmax, title_id, order_by, order_dir, after)
        all_nodes.extend(nodes)
        print(f"  Page {page}: +{len(nodes)} (total {len(all_nodes)}) hasNext={has_next}")
        if not has_next or not after:
            break
    return all_nodes


def fetch_all_series_no_title(
    api_key: str,
    tmin: str,
    tmax: str,
    order_by: str,
    order_dir: str,
) -> List[Dict[str, Any]]:
    all_nodes: List[Dict[str, Any]] = []
    after: Optional[str] = None
    for page in range(1, MAX_PAGES + 1):
        nodes, after, has_next = query_series_page_no_title(api_key, tmin, tmax, order_by, order_dir, after)
        all_nodes.extend(nodes)
        print(f"  Page {page}: +{len(nodes)} (total {len(all_nodes)}) hasNext={has_next}")
        if not has_next or not after:
            break
    return all_nodes


def main() -> None:
    api_key = (os.environ.get("GRID_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"GRID_API_KEY not set. Loaded .env from {ENV_PATH}")

    print("Loaded .env from:", ENV_PATH)
    print("HLTV match:", HLTV_MATCH_URL)

    team1, team2, approx_start_utc = fetch_hltv_match_info(HLTV_MATCH_URL)
    print("Parsed from HLTV:")
    print("  TEAM1:", team1)
    print("  TEAM2:", team2)
    print("  START:", approx_start_utc)

    order_dirs = get_enum_values(api_key, "OrderDirection")
    order_bys = get_enum_values(api_key, "SeriesOrderBy")
    order_dir = pick(order_dirs, ["ASC"])
    order_by = pick(order_bys, ["StartTimeScheduled"])

    print("\nOrderDirection:", order_dirs, "picked:", order_dir)
    print("SeriesOrderBy:", order_bys, "picked:", order_by)

    titles = fetch_titles(api_key)
    print(f"\nFetched {len(titles)} titles.")
    print("Titles:")
    print(json.dumps(titles, indent=2)[:12000])

    cs2_id = title_id_by_name(titles, "Counter Strike 2")
    csgo_id = title_id_by_name(titles, "Counter Strike: Global Offensive")
    csgo_2v2_id = title_id_by_name(titles, "Counter Strike: Global Offensive - 2v2")

    print("\nResolved titleIds:")
    print("  CS2:", cs2_id)
    print("  CSGO:", csgo_id)
    print("  CSGO 2v2:", csgo_2v2_id)

    center = parse_iso_utc(approx_start_utc)
    tmin, tmax = time_window_strings(center, WINDOW_HOURS)

    print("\nTime window:")
    print("  tmin:", tmin)
    print("  tmax:", tmax)

    title_try_order = [x for x in [cs2_id, csgo_id, csgo_2v2_id] if x]

    for tid in title_try_order:
        print(f"\nSearching allSeries with titleId={tid} ...")
        nodes = fetch_all_series_try_title(api_key, tmin, tmax, tid, order_by, order_dir)
        print(f"  Total series for titleId={tid}: {len(nodes)}")
        cands = filter_candidates(nodes, team1, team2)
        print(f"  Candidates matching teams: {len(cands)}")
        if cands:
            print("\nTop candidates:")
            for n in cands[:15]:
                print(
                    json.dumps(
                        {
                            "id": n.get("id"),
                            "startTimeScheduled": n.get("startTimeScheduled"),
                            "tournament": (n.get("tournament") or {}).get("name"),
                            "teams": node_team_names(n),
                        },
                        ensure_ascii=False,
                    )
                )
            return

    print("\nNo candidates found with CS titleIds. Falling back to no-title query (bigger set) ...")
    nodes = fetch_all_series_no_title(api_key, tmin, tmax, order_by, order_dir)
    print(f"Total series (no title filter): {len(nodes)}")

    cs_nodes = filter_cs_titles(nodes)
    print(f"Total series that look like CS (local filter): {len(cs_nodes)}")

    cands = filter_candidates(cs_nodes, team1, team2)
    print(f"Candidates matching teams (local CS filter): {len(cands)}")

    if cands:
        print("\nTop candidates:")
        for n in cands[:15]:
            print(
                json.dumps(
                    {
                        "id": n.get("id"),
                        "startTimeScheduled": n.get("startTimeScheduled"),
                        "title": (n.get("title") or {}).get("name"),
                        "tournament": (n.get("tournament") or {}).get("name"),
                        "teams": node_team_names(n),
                    },
                    ensure_ascii=False,
                )
            )
        return

    print(
        "\nStill no candidates.\n"
        "This means either:\n"
        "  1) your GRID key/account doesn’t have this tournament/series in Central Data, or\n"
        "  2) the series exists but team names are stored differently (rare for FaZe/3DMAX).\n"
        "\nNext best debug step: print a slice of CS-series around the start time and manually pick a likely series id.\n",
        file=sys.stderr,
    )

    for n in cs_nodes[:80]:
        print(
            json.dumps(
                {
                    "id": n.get("id"),
                    "startTimeScheduled": n.get("startTimeScheduled"),
                    "title": (n.get("title") or {}).get("name"),
                    "tournament": (n.get("tournament") or {}).get("name"),
                    "teams": node_team_names(n),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
