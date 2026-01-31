import re
import time
import random
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from io import StringIO

import pandas as pd
from bs4 import BeautifulSoup
from tqdm import tqdm
import cloudscraper


CSV_PATH = "data/processed/hltv_top30_match_urls.csv"
OUT_VETO_JSONL = "data/raw/hltv_veto.jsonl"
OUT_STATS_JSONL = "data/raw/hltv_player_stats.jsonl"

MIN_SLEEP = 0.8
MAX_SLEEP = 1.8
MATCH_ID_RE = re.compile(r"/matches/(\d+)/")


def sleep_jitter():
    time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))


def fetch(scraper, url: str) -> str:
    last_err = None
    try:
        r = scraper.get(url)
        return r.text
    except Exception as e:
        last_err = e
    raise last_err


def extract_match_id(match_url: str) -> Optional[int]:
    m = MATCH_ID_RE.search(match_url)
    return int(m.group(1)) if m else None


def parse_veto(match_html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(match_html, "lxml")

    veto_box = soup.select_one(".veto-box")
    if not veto_box:
        veto_candidates = soup.find_all(
            lambda tag: tag.name in ("div", "section")
            and tag.get("class")
            and any("veto" in c for c in tag.get("class", []))
        )
        veto_box = veto_candidates[0] if veto_candidates else None

    raw_lines: List[str] = []
    if veto_box:
        text = veto_box.get_text("\n", strip=True)
        raw_lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    actions = []
    for ln in raw_lines:
        m = re.match(r"^(?P<team>.+?)\s+(?P<action>removed|banned|picked)\s+(?P<map>.+?)$", ln, re.I)
        if m:
            actions.append({
                "team": m.group("team"),
                "action": m.group("action").lower(),
                "map": m.group("map"),
                "raw": ln
            })
            continue
        m2 = re.match(r"^(?P<map>.+?)\s+was\s+left\s+over$", ln, re.I)
        if m2:
            actions.append({
                "team": None,
                "action": "left_over",
                "map": m2.group("map"),
                "raw": ln
            })
            continue

        actions.append({"team": None, "action": None, "map": None, "raw": ln})

    return {"raw_lines": raw_lines, "actions": actions}


def parse_player_tables_from_match_page(match_html):
    soup = BeautifulSoup(match_html, "lxml")
    tables = soup.select("table.totalstats")
    if not tables:
        return []

    out = []

    for i, table in enumerate(tables):
        header_row = table.select_one("tr.header-row")
        headers = []
        if header_row:
            for j, cell in enumerate(header_row.find_all(["td", "th"])):
                txt = cell.get_text(" ", strip=True)
                headers.append("Player" if j == 0 else txt)

        try:
            df = pd.read_html(StringIO(str(table)))[0]
        except ValueError:
            continue

        if headers and len(headers) == len(df.columns):
            df.columns = headers
        else:
            cols = list(df.columns)
            cols[0] = "Player"
            df.columns = cols

        team = None
        map_name = None

        team_row = table.find_previous("div", class_="statsTeamHeader")
        if team_row:
            team = team_row.get_text(" ", strip=True)

        map_row = table.find_previous("div", class_="mapname")
        if map_row:
            map_name = map_row.get_text(" ", strip=True)

        for row in df.to_dict(orient="records"):
            if all(str(v).strip() == k for k, v in row.items()):
                continue

            player = str(row.get("Player", "")).strip()
            kd = str(row.get("K-D", "")).strip()

            if player and kd and re.match(r"^\d+\s*-\s*\d+$", kd):
                out.append({"table_index": i, "team": team, "map": map_name, "row": row})

    return out


def main():
    df = pd.read_csv(CSV_PATH)

    urls = df["match_url"].dropna().astype(str).tolist()

    scraper = cloudscraper.create_scraper()

    fetch(scraper, "https://www.hltv.org/")
    sleep_jitter()

    with open(OUT_VETO_JSONL, "w", encoding="utf-8") as fveto, \
         open(OUT_STATS_JSONL, "w", encoding="utf-8") as fstats:

        for match_url in tqdm(urls, desc="Scraping match pages"):
            match_id = extract_match_id(match_url)

            try:
                match_html = fetch(scraper, match_url)
                with open("debug_match.html", "w", encoding="utf-8") as f:
                    f.write(match_html)
                print("URL:", match_url)
                print("has <table>:", "<table" in match_html.lower())
                print("len(html):", len(match_html))
                print("contains 'player':", "player" in match_html.lower())
                print("contains 'veto':", "veto" in match_html.lower())
                print("contains 'statistics':", "statistics" in match_html.lower())


                veto = parse_veto(match_html)
                fveto.write(json.dumps({
                    "match_id": match_id,
                    "match_url": match_url,
                    "veto": veto,
                }, ensure_ascii=False) + "\n")

                player_rows = parse_player_tables_from_match_page(match_html)
                fstats.write(json.dumps({
                    "match_id": match_id,
                    "match_url": match_url,
                    "player_stats_rows": player_rows,
                }, ensure_ascii=False) + "\n")

            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                fveto.write(json.dumps({
                    "match_id": match_id,
                    "match_url": match_url,
                    "error": err,
                }, ensure_ascii=False) + "\n")
                fstats.write(json.dumps({
                    "match_id": match_id,
                    "match_url": match_url,
                    "error": err,
                }, ensure_ascii=False) + "\n")

            sleep_jitter()

    print("Done.")
    print(f"Wrote veto -> {OUT_VETO_JSONL}")
    print(f"Wrote stats -> {OUT_STATS_JSONL}")


if __name__ == "__main__":
    main()
