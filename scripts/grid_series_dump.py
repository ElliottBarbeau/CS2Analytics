from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

CENTRAL_DATA_URL = "https://api-op.grid.gg/central-data/graphql"
SERIES_ID = "2891194"

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def gql_post(url: str, api_key: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {"x-api-key": api_key, "content-type": "application/json"}
    payload = {"query": query, "variables": variables or {}}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        data = r.json()
    except Exception:
        print("HTTP", r.status_code, r.text[:2000], file=sys.stderr)
        raise
    if "errors" in data:
        raise RuntimeError(json.dumps(data["errors"], indent=2))
    return data["data"]


def unwrap_type(t: Dict[str, Any]) -> Tuple[str, List[str]]:
    kinds: List[str] = []
    base = ""
    cur = t
    while cur:
        kind = cur.get("kind")
        name = cur.get("name")
        if kind:
            kinds.append(kind)
        if name:
            base = name
        nxt = cur.get("ofType")
        if not nxt:
            break
        cur = nxt
    return base, kinds


def introspect_type(api_key: str, type_name: str) -> Dict[str, Any]:
    q = """
    query T($name: String!) {
      __type(name: $name) {
        name
        kind
        fields {
          name
          type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        }
        inputFields {
          name
          type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
        }
        enumValues { name }
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q, {"name": type_name})
    return data.get("__type") or {}


def is_scalar_or_enum(api_key: str, type_name: str) -> bool:
    t = introspect_type(api_key, type_name)
    return t.get("kind") in ("SCALAR", "ENUM")


def scalar_field_names(api_key: str, obj_type: str) -> List[str]:
    t = introspect_type(api_key, obj_type)
    out: List[str] = []
    for f in t.get("fields") or []:
        base, kinds = unwrap_type(f.get("type") or {})
        if not base:
            continue
        if "OBJECT" in kinds or "INPUT_OBJECT" in kinds:
            continue
        if is_scalar_or_enum(api_key, base) or ("SCALAR" in kinds) or ("ENUM" in kinds):
            out.append(f["name"])
    return out


def pick_fields(existing: List[str], preferred: List[str], limit: int) -> List[str]:
    picked: List[str] = []
    for p in preferred:
        if p in existing and p not in picked:
            picked.append(p)
    for n in existing:
        if n not in picked:
            picked.append(n)
        if len(picked) >= limit:
            break
    return picked[:limit]


def build_series_query_with_discovered_fields(
    api_key: str,
    include_format: bool,
    include_external_links: bool,
    include_psl: bool,
    include_streams: bool,
) -> str:
    fmt_fields: List[str] = []
    el_fields: List[str] = []
    psl_fields: List[str] = []
    vs_fields: List[str] = []

    if include_format:
        fmt_fields = pick_fields(
            scalar_field_names(api_key, "SeriesFormat"),
            preferred=["type", "bestOf", "numMaps", "mapCount", "id", "name"],
            limit=12,
        )

    if include_external_links:
        el_fields = pick_fields(
            scalar_field_names(api_key, "ExternalLink"),
            preferred=["url", "href", "link", "type", "provider", "name", "id"],
            limit=12,
        )

    if include_psl:
        psl_fields = pick_fields(
            scalar_field_names(api_key, "ProductServiceLevel"),
            preferred=["id", "code", "key", "value", "tier", "level", "product", "service"],
            limit=12,
        )

    if include_streams:
        vs_fields = pick_fields(
            scalar_field_names(api_key, "VideoStream"),
            preferred=["url", "href", "provider", "language", "id", "type", "title", "channel"],
            limit=12,
        )

    fmt_sel = f"format {{ {' '.join(fmt_fields) if fmt_fields else ' __typename ' } }}" if include_format else ""
    el_sel = (
        f"externalLinks {{ {' '.join(el_fields) if el_fields else ' __typename ' } }}"
        if include_external_links
        else ""
    )
    psl_sel = (
        f"productServiceLevels {{ {' '.join(psl_fields) if psl_fields else ' __typename ' } }}"
        if include_psl
        else ""
    )
    vs_sel = f"streams {{ {' '.join(vs_fields) if vs_fields else ' __typename ' } }}" if include_streams else ""

    q = f"""
    query Q($id: ID!) {{
      series(id: $id) {{
        id
        startTimeScheduled
        type
        private
        updatedAt
        title {{ id name }}
        tournament {{ id name }}
        teams {{ baseInfo {{ id name }} }}
        {fmt_sel}
        {el_sel}
        {psl_sel}
        {vs_sel}
      }}
    }}
    """
    return q


def series_minimal(api_key: str, sid: str) -> Dict[str, Any]:
    q = """
    query Q($id: ID!) {
      series(id: $id) {
        id
        startTimeScheduled
        type
        private
        updatedAt
        title { id name }
        tournament { id name }
        teams { baseInfo { id name } }
      }
    }
    """
    data = gql_post(CENTRAL_DATA_URL, api_key, q, {"id": sid})
    return data.get("series") or {}


def main() -> None:
    api_key = (os.environ.get("GRID_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(f"GRID_API_KEY not set. Loaded .env from {ENV_PATH}")

    print("Loaded .env from:", ENV_PATH)
    print("Using SERIES_ID:", SERIES_ID)

    s0 = series_minimal(api_key, SERIES_ID)
    if not s0:
        raise RuntimeError("series(id) returned null (bad id or no access)")
    print("\n=== series(id) MINIMAL ===")
    print(json.dumps(s0, ensure_ascii=False, indent=2)[:20000])

    print("\n=== Schema: scalar fields on nested types ===")
    for tn in ["SeriesFormat", "ExternalLink", "ProductServiceLevel", "VideoStream"]:
        t = introspect_type(api_key, tn)
        if not t:
            print(f"{tn}: not found in schema")
            continue
        sf = scalar_field_names(api_key, tn)
        print(f"{tn}: {sf}")

    q2 = build_series_query_with_discovered_fields(
        api_key,
        include_format=True,
        include_external_links=True,
        include_psl=True,
        include_streams=True,
    )

    s1 = gql_post(CENTRAL_DATA_URL, api_key, q2, {"id": SERIES_ID}).get("series") or {}
    print("\n=== series(id) WITH DISCOVERED SUBFIELDS ===")
    print(json.dumps(s1, ensure_ascii=False, indent=2)[:20000])

    print("\nDone.")


if __name__ == "__main__":
    main()
