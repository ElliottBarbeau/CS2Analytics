from __future__ import annotations

import csv
import re
import time
import cloudscraper
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from bs4 import BeautifulSoup


BASE = "https://www.hltv.org"
RANKING_URL = f"{BASE}/valve-ranking/teams"
RESULTS_URL = f"{BASE}/results"

TEAM_RE = re.compile(r"^/team/(\d+)/")
MATCH_RE = re.compile(r"^/matches/(\d+)/")


@dataclass(frozen=True)
class Team:
    team_id: int
    team_name: str
    team_url: str


def fetch_html(url: str, scraper) -> Tuple[Optional[str], int]:
    r = scraper.get(url)
    return r.text


def parse_top30_teams(html: str) -> List[Team]:
    soup = BeautifulSoup(html, "lxml")

    teams: List[Team] = []
    seen: Set[int] = set()

    for a in soup.select('a[href^="/team/"]'):
        href = a.get("href") or ""
        m = TEAM_RE.match(href)
        if not m:
            continue
        team_id = int(m.group(1))
        if team_id in seen:
            continue

        name = a.get_text(strip=True) or ""
        if not name:
            continue

        seen.add(team_id)
        teams.append(Team(team_id=team_id, team_name=name, team_url=BASE + href))
        if len(teams) >= 30:
            break

    return teams


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


def main() -> None:
    out_csv = Path("data/processed/hltv_top30_match_urls.csv")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    scraper = cloudscraper.create_scraper()

    print(f"url:  {RANKING_URL}")
    ranking_html = fetch_html(RANKING_URL, scraper)
    if not ranking_html:
        print(f"failed fetching ranking")
        return

    teams = parse_top30_teams(ranking_html)
    if not teams:
        print("could not parse top 30 teams from ranking page")
        return

    all_rows: List[Dict[str, str]] = []
    global_seen_matches: Set[int] = set()

    for i, t in enumerate(teams, start=1):
        print(f"[TEAM {i:02d}/30] {t.team_name} ({t.team_id})")
        offset = 0
        pages = 0

        while True:
            url = f"{RESULTS_URL}?team={t.team_id}&offset={offset}"
            print(url)
            html = fetch_html(url, scraper)

            if not html:
                print("fetch failed for match history")
                break

            links = parse_match_links(html)
            if not links:
                break

            new_on_page = 0
            for match_id, match_url in links:
                if match_id in global_seen_matches:
                    continue
                global_seen_matches.add(match_id)
                new_on_page += 1
                all_rows.append(
                    {
                        "team_id": str(t.team_id),
                        "team_name": t.team_name,
                        "match_id": str(match_id),
                        "match_url": match_url,
                    }
                )

            pages += 1
            if new_on_page == 0:
                break

            offset += 100
            if pages >= 50 or offset > 100:
                break

            time.sleep(1.2)

        time.sleep(2.0)

    all_rows.sort(key=lambda r: int(r["match_id"]), reverse=False)

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["team_id", "team_name", "match_id", "match_url"])
        w.writeheader()
        w.writerows(all_rows)

    print(f"[DONE] wrote {len(all_rows)} unique matches -> {out_csv}")


if __name__ == "__main__":
    main()
