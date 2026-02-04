from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup
from tqdm import tqdm

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


OUT_DIR_DEFAULT = "data/raw"

MATCH_ID_RE = re.compile(r"/matches/(\d+)/", re.IGNORECASE)
STATS_MATCH_LINK_RE = re.compile(r"^/stats/matches/(?:(mapstatsid)/)?(\d+)/(.+)$", re.IGNORECASE)

CF_MARKERS = (
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "<title>Just a moment",
    "Enable JavaScript and cookies to continue",
)

THIRD_PLACE_PATTERNS = [
    r"\b3rd\s*place\b",
    r"\bthird\s*place\b",
    r"\b3rd\s*place\s*decider\b",
    r"\bthird\s*place\s*decider\b",
    r"\bbronze\s*match\b",
    r"\bbronze\s*final\b",
    r"\bmatch\s*for\s*third\b",
    r"\bplayoff\s*for\s*third\b",
]

SEEDING_NEGATIVE_PATTERNS = [
    r"\blower\s+bracket\b",
    r"\blower-bracket\b",
    r"\blower\s+final\b",
    r"\blower\s+semi\b",
    r"\blower\s+round\b",
    r"\beliminat(?:ed|ion)\b",
    r"\bknock(?:ed)?\s+out\b",
    r"\bdecider\b",
    r"\bplay\s+in\b",
]

SEEDING_POSITIVE_PATTERNS = [
    r"\bboth\s+teams\b.*\bplay-?offs\b",
    r"\bboth\s+teams\b.*\badvance\b",
    r"\bboth\s+teams\b.*\bqualified\b",
    r"\bboth\s+teams\b.*\bsecure\b.*\bplay-?offs\b",
    r"\bwinner\b.*\bsemi[-\s]?finals?\b.*\b(loser|losing\s+team)\b.*\bquarter[-\s]?finals?\b",
    r"\bwinner\b.*\bquarter[-\s]?finals?\b.*\b(loser|losing\s+team)\b.*\bquarter[-\s]?finals?\b",
    r"\bwinner\b.*\bplay-?offs?\b.*\b(loser|losing\s+team)\b.*\bplay-?offs?\b",
    r"\b(loser|losing\s+team)\b.*\bquarter[-\s]?finals?\b.*\bwinner\b.*\bsemi[-\s]?finals?\b",
]

COUNTRY_TZ = {
    "poland": "Europe/Warsaw",
    "germany": "Europe/Berlin",
    "denmark": "Europe/Copenhagen",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "finland": "Europe/Helsinki",
    "france": "Europe/Paris",
    "spain": "Europe/Madrid",
    "portugal": "Europe/Lisbon",
    "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "belgium": "Europe/Brussels",
    "switzerland": "Europe/Zurich",
    "austria": "Europe/Vienna",
    "czech republic": "Europe/Prague",
    "czechia": "Europe/Prague",
    "slovakia": "Europe/Bratislava",
    "hungary": "Europe/Budapest",
    "romania": "Europe/Bucharest",
    "bulgaria": "Europe/Sofia",
    "serbia": "Europe/Belgrade",
    "croatia": "Europe/Zagreb",
    "slovenia": "Europe/Ljubljana",
    "ukraine": "Europe/Kyiv",
    "russia": "Europe/Moscow",
    "turkey": "Europe/Istanbul",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "ireland": "Europe/Dublin",
    "united states": "America/New_York",
    "usa": "America/New_York",
    "canada": "America/Toronto",
    "mexico": "America/Mexico_City",
    "brazil": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires",
    "chile": "America/Santiago",
    "colombia": "America/Bogota",
    "peru": "America/Lima",
    "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "taiwan": "Asia/Taipei",
    "japan": "Asia/Tokyo",
    "south korea": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "singapore": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur",
    "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "philippines": "Asia/Manila",
    "india": "Asia/Kolkata",
    "united arab emirates": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "saudi arabia": "Asia/Riyadh",
    "qatar": "Asia/Qatar",
    "australia": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
    "south africa": "Africa/Johannesburg",
    "egypt": "Africa/Cairo",
}

NO_STATS_FATAL = False

NO_STATS_KEYWORDS = [
    "forfeit",
    "walkover",
    "default win",
    "technical win",
    "match was not played",
    "not played",
    "awarded",
    "won by default",
    "team withdrew",
    "withdrew",
    "withdrawn",
    "disqualified",
    "dq",
    "cancelled",
    "canceled",
]


def is_blocked(html: str) -> bool:
    h = html or ""
    return any(m in h for m in CF_MARKERS)


def fetch(scraper: cloudscraper.CloudScraper, url: str, timeout: int) -> str:
    r = scraper.get(url, timeout=timeout)
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


def parse_match_timestamp(match_html: str) -> Optional[int]:
    soup = BeautifulSoup(match_html, "html.parser")

    candidates = [
        ".timeAndEvent [data-unix]",
        ".matchInfo [data-unix]",
        ".time [data-unix]",
        "[data-unix]",
    ]

    for sel in candidates:
        el = soup.select_one(sel)
        if el and el.has_attr("data-unix"):
            v = str(el["data-unix"]).strip()
            if v.isdigit():
                ts = int(v)
                if ts > 10_000_000_000:
                    ts //= 1000
                return ts

    m = re.search(r'data-unix\s*=\s*"(\d+)"', match_html)
    if m:
        ts = int(m.group(1))
        if ts > 10_000_000_000:
            ts //= 1000
        return ts

    return None


def parse_team_names(match_html: str) -> Tuple[Optional[str], Optional[str]]:
    soup = BeautifulSoup(match_html, "html.parser")

    t1 = soup.select_one(".team1 .teamName, .team1 .teamName a")
    t2 = soup.select_one(".team2 .teamName, .team2 .teamName a")

    team1 = t1.get_text(" ", strip=True) if t1 else None
    team2 = t2.get_text(" ", strip=True) if t2 else None

    return team1, team2


def parse_veto(match_html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(match_html, "html.parser")
    boxes = soup.select(".veto-box")
    if not boxes:
        return {"raw_lines": [], "actions": []}

    def score(box) -> int:
        t = box.get_text(" ", strip=True).lower()
        return sum(k in t for k in (" removed ", " picked ", " banned ", " left over"))

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

    out: List[Dict[str, Any]] = []
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


def parse_seeding_info(match_html: str) -> Tuple[bool, Optional[str]]:
    soup = BeautifulSoup(match_html, "html.parser")
    text = soup.get_text(" ", strip=True) or (match_html or "")
    low = text.lower()

    for pat in SEEDING_NEGATIVE_PATTERNS:
        if re.search(pat, low, re.I):
            return False, None

    for pat in SEEDING_POSITIVE_PATTERNS:
        m = re.search(pat, low, re.I)
        if m:
            start = max(0, m.start() - 160)
            end = min(len(text), m.end() + 240)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            return True, snippet

    return False, None


def _find_maps_box_text(match_html: str) -> Optional[str]:
    """
    Only inspect the 'Maps' box (where HLTV shows '+ 3rd place decider match').
    """
    soup = BeautifulSoup(match_html, "html.parser")

    for box in soup.select(".standard-box"):
        headline = box.select_one(".standard-box-headline")
        if headline and (headline.get_text(" ", strip=True) or "").strip().lower() == "maps":
            return box.get_text("\n", strip=True) or None

    h = soup.find(
        lambda tag: tag.name in ("h1", "h2", "h3", "h4")
        and (tag.get_text(" ", strip=True) or "").strip().lower() == "maps"
    )
    if h:
        parent = h.parent
        if parent:
            return parent.get_text("\n", strip=True) or None

    return None


def parse_third_place_decider(match_html: str) -> Tuple[bool, Optional[str]]:
    maps_text = _find_maps_box_text(match_html)
    if maps_text:
        low = maps_text.lower()
        for pat in THIRD_PLACE_PATTERNS:
            m = re.search(pat, low, re.I)
            if m:
                start = max(0, m.start() - 140)
                end = min(len(maps_text), m.end() + 220)
                snippet = re.sub(r"\s+", " ", maps_text[start:end]).strip()
                return True, snippet

    soup = BeautifulSoup(match_html, "html.parser")
    veto_box = soup.select_one(".veto-box")
    if veto_box:
        veto_text = veto_box.get_text(" ", strip=True) or ""
        low2 = veto_text.lower()
        for pat in THIRD_PLACE_PATTERNS:
            m = re.search(pat, low2, re.I)
            if m:
                return True, veto_text

    return False, None


def extract_event_country(match_html: str) -> Optional[str]:
    soup = BeautifulSoup(match_html, "html.parser")
    flag = soup.select_one(".timeAndEvent .event .flag, .event .flag, .event-holder .flag, .matchInfo .flag")
    if flag:
        for k in ("title", "alt"):
            v = flag.get(k)
            if v:
                v2 = re.sub(r"\s+", " ", str(v)).strip()
                if v2:
                    return v2

    m = re.search(r'class="flag[^"]*"\s+title="([^"]+)"', match_html or "", re.I)
    if m:
        v2 = re.sub(r"\s+", " ", m.group(1)).strip()
        return v2 or None

    return None


def resolve_timezone_name(country: Optional[str]) -> str:
    if not country:
        return "UTC"
    key = re.sub(r"\s+", " ", country.strip().lower())
    return COUNTRY_TZ.get(key, "UTC")


def weekday_in_event_tz(ts: Optional[int], tz_name: str) -> Optional[str]:
    if ts is None:
        return None
    if ZoneInfo is None:
        return datetime.utcfromtimestamp(ts).strftime("%A")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    dt = datetime.fromtimestamp(ts, tz=tz)
    return dt.strftime("%A")


def looks_like_no_stats_match(match_html: str) -> Tuple[bool, Optional[str]]:
    """
    Detect matches that won't have HLTV stats: forfeit/WO/DQ/cancelled/not played.
    Returns (True, note/snippet) if it looks like a no-stats match.
    """
    soup = BeautifulSoup(match_html, "html.parser")
    text = soup.get_text(" ", strip=True) or ""
    low = text.lower()

    if re.search(r"\b(w\/o|wo)\b", low):
        return True, "WO"

    for kw in NO_STATS_KEYWORDS:
        if kw == "dq":
            if re.search(r"\bdq\b", low):
                return True, "DQ"
            continue

        idx = low.find(kw)
        if idx != -1:
            start = max(0, idx - 120)
            end = min(len(text), idx + 240)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            return True, snippet

    return False, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, required=True)
    ap.add_argument("--url-column", default="match_url")
    ap.add_argument("--out-dir", default=OUT_DIR_DEFAULT)
    ap.add_argument("--max-matches", type=int, default=None)
    ap.add_argument("--delay", type=float, default=0.1)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    csv_path = Path(f"data/processed/hltv_matches_{args.team}.csv")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"hltv_match_veto_and_stats_{args.team}.jsonl"

    scraper = cloudscraper.create_scraper()

    match_urls = read_match_urls_from_csv(csv_path, args.url_column)
    if args.max_matches is not None:
        match_urls = match_urls[: max(0, args.max_matches)]

    blocked = 0
    failed = 0
    no_stats_ref = 0
    no_stats_skipped = 0
    written = 0

    with out_path.open("w", encoding="utf-8") as fout:
        for match_url in tqdm(match_urls, desc=f"Fetching HLTV team={args.team}"):
            match_id = extract_match_id(match_url)
            if match_id is None:
                continue

            try:
                match_html = fetch(scraper, match_url, args.timeout)
                print(match_url)

                if is_blocked(match_html):
                    blocked += 1

                team1_name, team2_name = parse_team_names(match_html)
                timestamp = parse_match_timestamp(match_html)

                country = extract_event_country(match_html)
                tz_name = resolve_timezone_name(country)
                weekday = weekday_in_event_tz(timestamp, tz_name)

                is_seeding, seeding_note = parse_seeding_info(match_html)
                is_third_place, third_place_note = parse_third_place_decider(match_html)
                veto = parse_veto(match_html)

                ref = extract_stats_match_ref(match_html)
                if not ref:
                    no_stats_ref += 1

                    is_no_stats, no_stats_note = looks_like_no_stats_match(match_html)
                    if is_no_stats:
                        no_stats_skipped += 1
                        fout.write(
                            json.dumps(
                                {
                                    "match_id": match_id,
                                    "match_url": match_url,
                                    "team1_name": team1_name,
                                    "team2_name": team2_name,
                                    "timestamp": timestamp,
                                    "event_country": country,
                                    "event_timezone": tz_name,
                                    "weekday_local": weekday,
                                    "is_seeding_match": is_seeding,
                                    "seeding_note": seeding_note,
                                    "is_third_place_decider": is_third_place,
                                    "third_place_note": third_place_note,
                                    "veto": veto,
                                    "error": "NO_STATS_FORFEIT_OR_CANCELLED",
                                    "no_stats_note": no_stats_note,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        written += 1
                        time.sleep(args.delay)
                        continue

                    if NO_STATS_FATAL:
                        raise RuntimeError("NO_STATS_MATCH_REF")

                    fout.write(
                        json.dumps(
                            {
                                "match_id": match_id,
                                "match_url": match_url,
                                "team1_name": team1_name,
                                "team2_name": team2_name,
                                "timestamp": timestamp,
                                "event_country": country,
                                "event_timezone": tz_name,
                                "weekday_local": weekday,
                                "is_seeding_match": is_seeding,
                                "seeding_note": seeding_note,
                                "is_third_place_decider": is_third_place,
                                "third_place_note": third_place_note,
                                "veto": veto,
                                "error": "NO_STATS_MATCH_REF_SKIPPED",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    written += 1
                    time.sleep(args.delay)
                    continue

                kind, stats_id, slug = ref
                stats_urls = build_stats_urls(kind, stats_id, slug)

                base_html = fetch(scraper, stats_urls["base"], args.timeout)
                base_tables = parse_all_tables(base_html)
                map_results = parse_map_results(match_html)

                fout.write(
                    json.dumps(
                        {
                            "match_id": match_id,
                            "match_url": match_url,
                            "team1_name": team1_name,
                            "team2_name": team2_name,
                            "timestamp": timestamp,
                            "event_country": country,
                            "event_timezone": tz_name,
                            "weekday_local": weekday,
                            "is_seeding_match": is_seeding,
                            "seeding_note": seeding_note,
                            "is_third_place_decider": is_third_place,
                            "third_place_note": third_place_note,
                            "veto": veto,
                            "map_results": map_results,
                            "stats_match_id": stats_id,
                            "stats_match_slug": slug,
                            "stats_urls": stats_urls,
                            "base_tables": base_tables,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1

            except Exception as e:
                msg = f"{type(e).__name__}: {e}"

                if "NO_STATS_MATCH_REF" in msg:
                    raise SystemExit(2)

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
                written += 1

            time.sleep(args.delay)

    print(f"Wrote -> {out_path}")
    print(
        f"team={args.team} written={written} blocked={blocked} failed={failed} "
        f"no_stats_ref={no_stats_ref} no_stats_skipped={no_stats_skipped} total={len(match_urls)}"
    )


if __name__ == "__main__":
    main()
