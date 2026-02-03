from __future__ import annotations

import argparse
import csv
import re
import time
import cloudscraper
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from bs4 import BeautifulSoup


BASE = "https://www.hltv.org"
RESULTS_URL = f"{BASE}/results"

TEAM_RE = re.compile(r"^/team/(\d+)/")
MATCH_RE = re.compile(r"^/matches/(\d+)/")

TOP_30 = [
    8297,  # Furia
    11283,  # Falcons
    7020, # Spirit
    4914,  # 3DMax
    4608,  # Navi
    13286,  # FUT
    6665,  # Astralis
    4494,  # Mouz
    6667,  # Faze
    9565,  # Vitality
    11861,  # Aurora
    12467, # Parivision
    5995, # G2
    5973, # Liquid
    11241, # B8
    12468, # Legacy
    6673, # NRG
    9928, # GamerLegion
    4773, # Pain
    12736, # M80
    4411, # NIP
    12878, # BC.Game
    12394, # Betboom
    7175, # Heroic
    9215, # MIBR
    13404, # Gentle Mates
    11581, # Hotu
    12426, # Passion UA
    7532, # BIG
    6248, # Mongolz
]


def fetch_html(url: str, scraper) -> Optional[str]:
    r = scraper.get(url)
    return r.text


def parse_match_links(html: str) -> List[Tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: List[Tuple[int, str]] = []
    seen: Set[int] = set()
    for a in soup.select('a[href^="/matches/"]'):
        href = a.get("href") or ""
        m = MATCH_RE.match(href)
        if not m:
            continue
        match_id = int(m.group(1))
        if match_id in seen:
            continue
        seen.add(match_id)
        out.append((match_id, BASE + href))
    return out


def parse_match_team_ids(match_html: str) -> Tuple[Optional[int], Optional[int]]:
    soup = BeautifulSoup(match_html, "lxml")
    ids: List[int] = []
    for a in soup.select('a[href^="/team/"]'):
        href = a.get("href") or ""
        m = TEAM_RE.match(href)
        if not m:
            continue
        ids.append(int(m.group(1)))
    if not ids:
        m2 = re.findall(r'"/team/(\d+)/', match_html)
        ids = [int(x) for x in m2]
    uniq: List[int] = []
    for tid in ids:
        if tid not in uniq:
            uniq.append(tid)
        if len(uniq) >= 2:
            break
    if len(uniq) >= 2:
        return uniq[0], uniq[1]
    if len(uniq) == 1:
        return uniq[0], None
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, required=True)
    ap.add_argument("--out-dir", default="data/processed")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--match-delay", type=float, default=0.25)
    args = ap.parse_args()

    team_id = args.team
    if team_id not in TOP_30:
        raise RuntimeError("team id not in TOP_30 list")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_csv = out_dir / f"hltv_matches_{team_id}.csv"

    scraper = cloudscraper.create_scraper()

    top_set: Set[int] = set(TOP_30)
    seen_matches: Set[int] = set()

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["seed_team_id", "match_id", "match_url"])
        writer.writeheader()

        print(f"[TEAM] {team_id}")
        offset = 0
        pages = 0

        while True:
            url = f"{RESULTS_URL}?team={team_id}&offset={offset}"
            print(url)

            html = fetch_html(url, scraper)
            if not html:
                break

            links = parse_match_links(html)
            if not links:
                break

            new_on_page = 0

            for match_id, match_url in links:
                if match_id in seen_matches:
                    continue

                match_html = fetch_html(match_url, scraper)
                seen_matches.add(match_id)

                if not match_html:
                    continue

                t1_id, t2_id = parse_match_team_ids(match_html)
                if t1_id is None or t2_id is None:
                    continue

                if t1_id not in top_set or t2_id not in top_set:
                    continue

                new_on_page += 1

                writer.writerow(
                    {
                        "seed_team_id": str(team_id),
                        "match_id": str(match_id),
                        "match_url": match_url,
                    }
                )
                f.flush()

                time.sleep(args.match_delay)

            pages += 1
            if new_on_page == 0:
                break

            offset += 100
            if pages >= 50 or offset > 100:
                break

            time.sleep(args.delay)

    print(f"[DONE] wrote -> {out_csv}")


if __name__ == "__main__":
    main()
