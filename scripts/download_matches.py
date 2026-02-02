from __future__ import annotations

import argparse
import csv
import json
import re
import time
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup
from tqdm import tqdm


CSV_PATH_DEFAULT = "data/processed/hltv_top30_match_urls.csv"
OUT_JSONL_DEFAULT = "data/raw/hltv_match_veto_and_stats.jsonl"

MATCH_ID_RE = re.compile(r"/matches/(\d+)/", re.IGNORECASE)
STATS_MATCH_LINK_RE = re.compile(r"^/stats/matches/(?:(mapstatsid)/)?(\d+)/(.+)$", re.IGNORECASE)

CF_MARKERS = (
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "<title>Just a moment",
    "Enable JavaScript and cookies to continue",
)


def is_blocked(html: str) -> bool:
    h = html or ""
    return any(m in h for m in CF_MARKERS)


def fetch(scraper: cloudscraper.CloudScraper, url: str) -> str:
    r = scraper.get(url)
    return r.text


def extract_match_id(match_url: str) -> Optional[int]:
    m = MATCH_ID_RE.search(match_url)
    return int(m.group(1)) if m else None


def extract_stats_match_ref(match_html: str) -> Optional[Tuple[str, int, str]]:
    soup = BeautifulSoup(match_html, "html.parser")

    for a in soup.select('a[href^="/stats/matches/"]'):
        href = (a.get("href") or "").strip()
        m = STATS_MATCH_LINK_RE.match(href)
        if m:
            kind = "mapstatsid" if m.group(1) else "matchid"
            return kind, int(m.group(2)), m.group(3)

    m2 = re.search(r'"/stats/matches/(?:(mapstatsid)/)?(\d+)/([^"]+)"', match_html)
    if m2:
        kind = "mapstatsid" if m2.group(1) else "matchid"
        return kind, int(m2.group(2)), m2.group(3)

    return None


def build_stats_urls(kind: str, stats_id: int, slug: str) -> Dict[str, str]:
    if kind == "mapstatsid":
        base = f"https://www.hltv.org/stats/matches/mapstatsid/{stats_id}/{slug}"
        perf = f"https://www.hltv.org/stats/matches/performance/mapstatsid/{stats_id}/{slug}"
        return {"base": base, "performance": perf}
    base = f"https://www.hltv.org/stats/matches/{stats_id}/{slug}"
    perf = f"https://www.hltv.org/stats/matches/performance/{stats_id}/{slug}"
    return {"base": base, "performance": perf}


def parse_veto(match_html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(match_html, "html.parser")
    boxes = soup.select(".veto-box")
    if not boxes:
        return {"raw_lines": [], "actions": []}

    def score(box) -> int:
        t = box.get_text(" ", strip=True).lower()
        return (
            sum(k in t for k in (" removed ", " picked ", " banned ", " left over"))
            + (1 if re.search(r"\b1\.\s", t) else 0)
        )

    box = max(boxes, key=score)
    lines: List[str] = []

    for div in box.select(".padding > div"):
        t = div.get_text(" ", strip=True)
        if t:
            lines.append(t)

    if not lines:
        text = box.get_text("\n", strip=True)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    actions: List[Dict[str, Any]] = []
    for ln in lines:
        ln2 = re.sub(r"^\s*\d+\.\s*", "", ln).strip()

        m = re.match(r"^(?P<team>.+?)\s+(?P<action>removed|banned|picked)\s+(?P<map>.+?)$", ln2, re.I)
        if m:
            actions.append(
                {
                    "team": m.group("team"),
                    "action": m.group("action").lower(),
                    "map": m.group("map"),
                    "raw": ln,
                }
            )
            continue

        m2 = re.match(r"^(?P<map>.+?)\s+was\s+left\s+over$", ln2, re.I)
        if m2:
            actions.append({"team": None, "action": "left_over", "map": m2.group("map"), "raw": ln})
            continue

        actions.append({"team": None, "action": None, "map": None, "raw": ln})

    return {"raw_lines": lines, "actions": actions}


def parse_map_results(match_html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(match_html, "html.parser")

    t1 = soup.select_one(".team1 .teamName, .team1 .teamName a")
    t2 = soup.select_one(".team2 .teamName, .team2 .teamName a")

    team1 = t1.get_text(" ", strip=True) if t1 else None
    team2 = t2.get_text(" ", strip=True) if t2 else None

    out = []

    holders = soup.select(".mapholder") or soup.select(".mapholder.small")

    for h in holders:
        name_el = h.select_one(".mapname") or h.select_one(".mapname span")
        map_name = name_el.get_text(" ", strip=True) if name_el else None

        scores = [s.get_text(strip=True) for s in h.select(".results-team-score")]
        s1 = int(scores[0]) if len(scores) >= 1 and scores[0].isdigit() else None
        s2 = int(scores[1]) if len(scores) >= 2 and scores[1].isdigit() else None

        winner = None
        if s1 is not None and s2 is not None and team1 and team2:
            if s1 > s2:
                winner = team1
            elif s2 > s1:
                winner = team2

        out.append(
            {
                "map": map_name,
                "team1_rounds": s1,
                "team2_rounds": s2,
                "winner": winner,
            }
        )

    return out


def normalize_columns(cols: List[Any]) -> List[str]:
    out: List[str] = []
    for c in cols:
        s = str(c).strip()
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return out


def parse_all_tables(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    out: List[Dict[str, Any]] = []

    for idx, table in enumerate(soup.find_all("table")):
        caption = ""
        cap = table.find("caption")
        if cap:
            caption = cap.get_text(" ", strip=True)

        df = None
        try:
            dfs = pd.read_html(StringIO(str(table)))
            if dfs:
                df = dfs[0]
        except Exception:
            df = None

        if df is not None:
            df.columns = normalize_columns(list(df.columns))
            out.append(
                {
                    "table_index": idx,
                    "caption": caption,
                    "columns": list(df.columns),
                    "rows": df.fillna("").to_dict(orient="records"),
                }
            )
            continue

        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = None
        for tr in rows[:3]:
            ths = tr.find_all("th")
            if ths:
                header_cells = ths
                header_tr = tr
                break

        if header_cells is None:
            header_tr = rows[0]
            header_cells = header_tr.find_all(["th", "td"])

        columns = [c.get_text(" ", strip=True) for c in header_cells]
        columns = normalize_columns(columns)

        data_rows = []
        for tr in rows:
            if tr == header_tr:
                continue
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            vals = [c.get_text(" ", strip=True) for c in cells]
            vals = [re.sub(r"\s+", " ", v).strip() for v in vals]
            if all(v == "" for v in vals):
                continue
            if len(vals) < len(columns):
                vals = vals + [""] * (len(columns) - len(vals))
            if len(vals) > len(columns):
                vals = vals[: len(columns)]
            data_rows.append(dict(zip(columns, vals)))

        if not columns or not data_rows:
            continue

        out.append(
            {
                "table_index": idx,
                "caption": caption,
                "columns": columns,
                "rows": data_rows,
            }
        )

    return out


def read_match_urls_from_csv(path: Path, url_column: str) -> List[str]:
    urls: List[str] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames or url_column not in r.fieldnames:
            raise ValueError(f"CSV must contain a '{url_column}' column")
        for row in r:
            u = (row.get(url_column) or "").strip()
            if u:
                urls.append(u)
    return urls


def main() -> None:
    scraper = cloudscraper.create_scraper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV_PATH_DEFAULT)
    ap.add_argument("--url-column", default="match_url")
    ap.add_argument("--out", default=OUT_JSONL_DEFAULT)
    ap.add_argument("--max-matches", type=int, default=None)
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    csv_path = Path(args.csv)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    match_urls = read_match_urls_from_csv(csv_path, args.url_column)
    if args.max_matches is not None:
        match_urls = match_urls[: max(0, args.max_matches)]

    blocked = 0
    failed = 0
    no_stats_ref = 0

    with out_path.open("w", encoding="utf-8") as fout:
        for match_url in tqdm(match_urls, desc="Fetching HLTV"):
            match_id = extract_match_id(match_url)

            try:
                match_html = fetch(scraper, match_url)
                veto = parse_veto(match_html)

                ref = extract_stats_match_ref(match_html)
                if not ref:
                    print("FAILED")
                    no_stats_ref += 1
                    fout.write(
                        json.dumps(
                            {
                                "match_id": match_id,
                                "match_url": match_url,
                                "veto": veto,
                                "error": "NO_STATS_MATCH_REF",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    time.sleep(args.delay)
                    continue

                kind, stats_id, slug = ref
                stats_urls = build_stats_urls(kind, stats_id, slug)

                base_html = fetch(scraper, stats_urls["base"])
                #perf_html = fetch(scraper, stats_urls["performance"])

                #perf_table_count = perf_html.lower().count("<table")
                #if perf_table_count == 0:
                    #with open("debug_performance.html", "w", encoding="utf-8") as f:
                        #f.write(perf_html)


                base_tables = parse_all_tables(base_html)
                #perf_tables = parse_all_tables(perf_html)

                map_results = parse_map_results(match_html)

                #print(perf_tables)

                fout.write(json.dumps({
                    "match_id": match_id,
                    "match_url": match_url,
                    "veto": veto,
                    "map_results": map_results,
                    "stats_match_id": stats_id,
                    "stats_match_slug": slug,
                    "stats_urls": stats_urls,
                    "base_tables": base_tables,
                    #"performance_tables": perf_tables,
                }, ensure_ascii=False) + "\n")

            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if "BLOCKED" in msg:
                    blocked += 1
                else:
                    failed += 1

                fout.write(
                    json.dumps(
                        {
                            "match_id": match_id,
                            "match_url": match_url,
                            "error": msg,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            time.sleep(args.delay)

    print(f"Wrote -> {out_path}")
    print(f"blocked={blocked} failed={failed} no_stats_ref={no_stats_ref} total={len(match_urls)}")


if __name__ == "__main__":
    main()
