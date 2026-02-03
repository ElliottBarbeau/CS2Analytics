from __future__ import annotations

import argparse
import csv
import re
import time
import cloudscraper
from pathlib import Path
from typing import List, Optional, Set, Tuple
from bs4 import BeautifulSoup
from tqdm import tqdm


BASE = "https://www.hltv.org"
RESULTS_URL = f"{BASE}/results"

TEAM_RE = re.compile(r"^/team/(\d+)/")
MATCH_RE = re.compile(r"^/matches/(\d+)/")
UNIX_RE = re.compile(r'data-unix\s*=\s*"(\d+)"', re.I)

TOP_30 = [
    8297,  # Furia
    11283,  # Falcons
    7020,  # Spirit
    4914,  # 3DMax
    4608,  # Navi
    13286,  # FUT
    6665,  # Astralis
    4494,  # Mouz
    6667,  # Faze
    9565,  # Vitality
    11861,  # Aurora
    12467,  # Parivision
    5995,  # G2
    5973,  # Liquid
    11241,  # B8
    12468,  # Legacy
    6673,  # NRG
    9928,  # GamerLegion
    4773,  # Pain
    12736,  # M80
    4411,  # NIP
    12878,  # BC.Game
    12394,  # Betboom
    7175,  # Heroic
    9215,  # MIBR
    13404,  # Gentle Mates
    11581,  # Hotu
    12426,  # Passion UA
    7532,  # BIG
    6248,  # Mongolz
]


def fetch_html(url: str, scraper, timeout: int) -> Optional[str]:
    try:
        r = scraper.get(url, timeout=timeout)
        return r.text
    except Exception:
        return None


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


def parse_match_unix(match_html: str) -> Optional[int]:
    m = UNIX_RE.search(match_html or "")
    if not m:
        return None
    ts = int(m.group(1))
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def load_last_ts(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    try:
        s = path.read_text(encoding="utf-8").strip()
        if s.isdigit():
            return int(s)
    except Exception:
        return None
    return None


def save_last_ts(path: Path, ts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(ts), encoding="utf-8")


def load_existing_match_ids(csv_path: Path) -> Set[int]:
    if not csv_path.exists():
        return set()
    out: Set[int] = set()
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                mid = (row.get("match_id") or "").strip()
                if mid.isdigit():
                    out.add(int(mid))
    except Exception:
        return set()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, required=True)
    ap.add_argument("--out-dir", default="data/processed")
    ap.add_argument("--delay", type=float, default=1.2)
    ap.add_argument("--match-delay", type=float, default=0.25)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args()

    team_id = args.team
    if team_id not in set(TOP_30):
        raise RuntimeError("team id not in TOP_30 list")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_csv = out_dir / f"hltv_matches_{team_id}.csv"
    last_ts_path = out_dir / f"hltv_last_ts_{team_id}.txt"

    existing_match_ids = load_existing_match_ids(out_csv)
    last_ts = load_last_ts(last_ts_path)

    scraper = cloudscraper.create_scraper()
    top_set: Set[int] = set(TOP_30)

    write_header = not out_csv.exists() or out_csv.stat().st_size == 0

    newest_ts_seen: Optional[int] = None
    wrote = 0
    stopped_on_ts = False
    completed = False

    pbar = tqdm(total=args.max_pages, desc=f"HLTV pages team={team_id}", unit="page")

    try:
        with out_csv.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["seed_team_id", "match_id", "match_url"])
            if write_header:
                writer.writeheader()
                f.flush()

            print(f"[TEAM] {team_id} last_ts={last_ts if last_ts is not None else 'NONE'}")
            offset = 0
            pages = 0

            while pages < args.max_pages:
                url = f"{RESULTS_URL}?team={team_id}&offset={offset}"

                html = fetch_html(url, scraper, args.timeout)
                if not html:
                    break

                links = parse_match_links(html)
                if not links:
                    break

                for match_id, match_url in links:
                    if match_id in existing_match_ids:
                        continue

                    match_html = fetch_html(match_url, scraper, args.timeout)
                    if not match_html:
                        continue

                    ts = parse_match_unix(match_html)
                    if ts is not None:
                        if newest_ts_seen is None or ts > newest_ts_seen:
                            newest_ts_seen = ts
                        if last_ts is not None and ts < last_ts:
                            stopped_on_ts = True
                            break

                    t1_id, t2_id = parse_match_team_ids(match_html)
                    if t1_id is None or t2_id is None:
                        time.sleep(args.match_delay)
                        continue

                    if t1_id not in top_set or t2_id not in top_set:
                        time.sleep(args.match_delay)
                        continue

                    writer.writerow(
                        {
                            "seed_team_id": str(team_id),
                            "match_id": str(match_id),
                            "match_url": match_url,
                        }
                    )
                    f.flush()
                    existing_match_ids.add(match_id)
                    wrote += 1

                    time.sleep(args.match_delay)

                pages += 1
                offset += 100
                pbar.update(1)

                if stopped_on_ts:
                    break

                time.sleep(args.delay)

        completed = True
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] ctrl+c detected; not saving timestamp")
    finally:
        pbar.close()

    if completed and newest_ts_seen is not None:
        save_last_ts(last_ts_path, newest_ts_seen)

    print(
        f"[DONE] wrote={wrote} out={out_csv} last_ts_saved={newest_ts_seen if (completed and newest_ts_seen is not None) else 'NO'}"
    )


if __name__ == "__main__":
    main()
