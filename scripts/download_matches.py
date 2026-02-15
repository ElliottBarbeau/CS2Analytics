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
EVENT_ID_RE = re.compile(r"/events/(\d+)/", re.I)
STATS_MATCH_LINK_RE = re.compile(r"^/stats/matches/(?:(mapstatsid)/)?(\d+)/(.+)$", re.IGNORECASE)
STATS_MATCH_LINK_RE_ANY = re.compile(r'/stats/matches/(?:(mapstatsid)/)?(\d+)/([^"\'\s<>]+)', re.IGNORECASE)

CF_MARKERS = (
    "_cf_chl_opt",
    "cdn-cgi/challenge-platform",
    "<title>Just a moment",
    "Enable JavaScript and cookies to continue",
)

DASH_CHARS_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
NBSP_RE = re.compile(r"[\u00A0\u202F]")

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

SEEDING_VERB_RE = re.compile(
    r"\b(advance(?:s|d)?|proceed(?:s|ed)?|qualif(?:y|ies|ied)|go(?:es)?|progress(?:es|ed)?|secure(?:s|d)?|book(?:s|ed)?)\b",
    re.I,
)
SEMI_RE = re.compile(r"\b(playoff\s*)?semi(?:-|\s)?finals?\b", re.I)
QUARTER_RE = re.compile(r"\b(playoff\s*)?quarter(?:-|\s)?finals?\b", re.I)
WINNER_RE = re.compile(r"\bwinner\b", re.I)
LOSER_RE = re.compile(r"\b(loser|losing\s+team)\b", re.I)


def _norm(s: str) -> str:
    s = NBSP_RE.sub(" ", s or "")
    s = DASH_CHARS_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def is_blocked(html: str) -> bool:
    h = html or ""
    return any(m in h for m in CF_MARKERS)


def fetch(scraper: cloudscraper.CloudScraper, url: str, timeout: int) -> str:
    r = scraper.get(url, timeout=timeout)
    return r.text


def extract_event_id(match_html: str) -> Optional[int]:
    soup = BeautifulSoup(match_html, "html.parser")

    for a in soup.select('a[href^="/events/"]'):
        href = a.get("href") or ""
        m = EVENT_ID_RE.search(href)
        if m:
            return int(m.group(1))

    m2 = EVENT_ID_RE.search(match_html or "")
    if m2:
        return int(m2.group(1))

    return None


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


def extract_all_mapstats_refs(match_html: str) -> List[Tuple[str, int, str]]:
    soup = BeautifulSoup(match_html, "html.parser")

    refs: List[Tuple[str, int, str]] = []
    for a in soup.select('a[href^="/stats/matches/"]'):
        href = (a.get("href") or "").strip()
        m = STATS_MATCH_LINK_RE.match(href)
        if not m:
            continue
        kind = "mapstatsid" if m.group(1) else "matchid"
        stats_id = int(m.group(2))
        slug = m.group(3)
        refs.append((kind, stats_id, slug))

    if not refs:
        for m in STATS_MATCH_LINK_RE_ANY.finditer(match_html or ""):
            kind = "mapstatsid" if m.group(1) else "matchid"
            stats_id = int(m.group(2))
            slug = m.group(3)
            refs.append((kind, stats_id, slug))

    seen = set()
    out: List[Tuple[str, int, str]] = []
    for kind, sid, slug in refs:
        key = (kind, sid)
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, sid, slug))

    mapstats = [x for x in out if x[0] == "mapstatsid"]
    if mapstats:
        return mapstats

    if out:
        return [out[0]]

    return []


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

    for sel in (
        ".timeAndEvent [data-unix]",
        ".matchInfo [data-unix]",
        ".time [data-unix]",
        "[data-unix]",
    ):
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
            actions.append({"team": m.group("team"), "action": m.group("action").lower(), "map": m.group("map"), "raw": ln})
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

        out.append({"map": map_name, "team1_rounds": s1, "team2_rounds": s2, "winner": winner})

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
            out.append({"table_index": idx, "caption": caption, "columns": list(df.columns), "rows": df.fillna("").to_dict(orient="records")})
            continue

        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = None
        header_tr = None
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
            if header_tr is not None and tr == header_tr:
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

        out.append({"table_index": idx, "caption": caption, "columns": columns, "rows": data_rows})

    return out


def extract_map_name_from_stats_page(stats_html: str) -> Optional[str]:
    soup = BeautifulSoup(stats_html, "html.parser")

    for sel in (
        ".stats-match-header .stats-match-map",
        ".stats-match-header-map",
        ".stats-match-header-mapname",
        ".stats-match-map",
        ".stats-match-mapname",
        ".mapname",
        ".map-name",
        "[data-map]",
    ):
        el = soup.select_one(sel)
        if el:
            if el.has_attr("data-map"):
                v = str(el.get("data-map") or "").strip()
                if v:
                    return v
            t = el.get_text(" ", strip=True)
            if t:
                return t

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    m = re.search(r"\b(Nuke|Mirage|Inferno|Dust2|Ancient|Anubis|Overpass|Vertigo|Train|Cache|Cobblestone)\b", title, re.I)
    if m:
        return m.group(1)

    return None


def compact_total_table_for_one_map(
    base_tables: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    by_team: List[Dict[str, Any]] = []

    for t in base_tables:
        idx = t.get("table_index")
        if not isinstance(idx, int):
            try:
                idx = int(idx)
            except Exception:
                continue
        if idx % 3 != 0:
            continue

        cols = t.get("columns") or []
        rows = t.get("rows") or []
        if not isinstance(cols, list) or not isinstance(rows, list) or not cols:
            continue
        team_header = cols[0]
        if not isinstance(team_header, str) or not team_header.strip():
            continue

        out_rows: List[Dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            player = r.get(team_header)
            if not isinstance(player, str) or not player.strip():
                continue
            rr = dict(r)
            rr.pop(team_header, None)
            rr["Team"] = team_header
            rr["Player"] = player.strip()
            out_rows.append(rr)

        if out_rows:
            by_team.append({"team": team_header, "rows": out_rows})

    if not by_team:
        return None

    merged_rows: List[Dict[str, Any]] = []
    for block in by_team:
        merged_rows.extend(block["rows"])

    cols_union = []
    seen = set()
    for r in merged_rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols_union.append(k)

    preferred = ["Team", "Player"]
    cols_union = preferred + [c for c in cols_union if c not in preferred]

    return {"columns": cols_union, "rows": merged_rows}


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


def read_existing_match_ids(jsonl_path: Path) -> set[int]:
    existing: set[int] = set()
    if not jsonl_path.exists():
        return existing

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            match_id = obj.get("match_id")
            if isinstance(match_id, int):
                existing.add(match_id)
                continue
            if isinstance(match_id, str) and match_id.isdigit():
                existing.add(int(match_id))

    return existing


def _find_maps_box_text(match_html: str) -> Optional[str]:
    soup = BeautifulSoup(match_html, "html.parser")

    for box in soup.select(".standard-box"):
        headline = box.select_one(".standard-box-headline")
        if headline and (headline.get_text(" ", strip=True) or "").strip().lower() == "maps":
            return box.get_text("\n", strip=True) or None

    h = soup.find(lambda tag: tag.name in ("h1", "h2", "h3", "h4") and (tag.get_text(" ", strip=True) or "").strip().lower() == "maps")
    if h and h.parent:
        return h.parent.get_text("\n", strip=True) or None

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
            if re.search(pat, low2, re.I):
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
    dt = datetime.fromtimestamp(ts, tz=tz)
    return dt.strftime("%A")


def looks_like_no_stats_match(match_html: str) -> Tuple[bool, Optional[str]]:
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


def _collect_seeding_text_candidates(match_html: str) -> List[str]:
    soup = BeautifulSoup(match_html, "html.parser")
    cands: List[str] = []

    maps_text = _find_maps_box_text(match_html)
    if maps_text:
        cands.append(maps_text)

    for sel in (
        ".matchInfo",
        ".matchInfoBox",
        ".matchInfoBoxCon",
        ".match-info",
        ".match-info-box",
        ".match-info-box-con",
        ".timeAndEvent",
    ):
        for el in soup.select(sel):
            t = el.get_text(" ", strip=True)
            t = _norm(t)
            if t:
                cands.append(t)

    all_text = _norm(soup.get_text(" ", strip=True) or "")
    if all_text:
        cands.append(all_text)

    seen = set()
    out = []
    for t in cands:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _find_seeding_snippet(text: str) -> Optional[str]:
    t = _norm(text)
    if not t:
        return None

    low = t.lower()
    if not (WINNER_RE.search(low) and LOSER_RE.search(low) and SEMI_RE.search(low) and QUARTER_RE.search(low) and SEEDING_VERB_RE.search(low)):
        return None

    wpos = [m.start() for m in WINNER_RE.finditer(low)]
    lpos = [m.start() for m in LOSER_RE.finditer(low)]
    spos = [m.start() for m in SEMI_RE.finditer(low)]
    qpos = [m.start() for m in QUARTER_RE.finditer(low)]
    vpos = [m.start() for m in SEEDING_VERB_RE.finditer(low)]

    best = None
    best_span = None

    for w in wpos:
        for l in lpos:
            for s in spos:
                for q in qpos:
                    for v in vpos:
                        lo = min(w, l, s, q, v)
                        hi = max(w, l, s, q, v)
                        span = hi - lo
                        if span <= 700:
                            if best_span is None or span < best_span:
                                best_span = span
                                best = (lo, hi)

    if best is None:
        return None

    lo, hi = best
    start = max(0, lo - 200)
    end = min(len(t), hi + 260)
    return _norm(t[start:end])


def parse_seeding_info(match_html: str) -> Tuple[bool, Optional[str]]:
    for cand in _collect_seeding_text_candidates(match_html):
        snip = _find_seeding_snippet(cand)
        if snip:
            return True, snip

    html = match_html or ""
    for m in re.finditer(r"winner.{0,900}semi.{0,900}(loser|losing team).{0,900}quarter", html, re.I | re.S):
        chunk = re.sub(r"<[^>]+>", " ", html[m.start() : m.end()])
        chunk = _norm(chunk)
        if chunk and WINNER_RE.search(chunk) and LOSER_RE.search(chunk) and SEMI_RE.search(chunk) and QUARTER_RE.search(chunk) and SEEDING_VERB_RE.search(chunk):
            return True, chunk[:900]

    for m in re.finditer(r"(loser|losing team).{0,900}quarter.{0,900}winner.{0,900}semi", html, re.I | re.S):
        chunk = re.sub(r"<[^>]+>", " ", html[m.start() : m.end()])
        chunk = _norm(chunk)
        if chunk and WINNER_RE.search(chunk) and LOSER_RE.search(chunk) and SEMI_RE.search(chunk) and QUARTER_RE.search(chunk) and SEEDING_VERB_RE.search(chunk):
            return True, chunk[:900]

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
    existing_match_ids = read_existing_match_ids(out_path)
    if existing_match_ids:
        match_urls = [u for u in match_urls if (extract_match_id(u) or -1) not in existing_match_ids]
    if args.max_matches is not None:
        match_urls = match_urls[: max(0, args.max_matches)]

    blocked = 0
    failed = 0
    no_stats_ref = 0
    no_stats_skipped = 0
    written = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    first_url_hard_fail = False
    if match_urls:
        try:
            first_html = fetch(scraper, match_urls[0], args.timeout)
            refs0 = extract_all_mapstats_refs(first_html)
            if not refs0 and is_blocked(first_html):
                first_url_hard_fail = True
            elif not refs0 and not is_blocked(first_html):
                is_no_stats0, _ = looks_like_no_stats_match(first_html)
                if not is_no_stats0:
                    first_url_hard_fail = True
            elif refs0:
                kind0, stats_id0, slug0 = refs0[0]
                urls0 = build_stats_urls(kind0, stats_id0, slug0)
                base0 = fetch(scraper, urls0["base"], args.timeout)
                tables0 = parse_all_tables(base0)
                compact0 = compact_total_table_for_one_map(tables0)
                if compact0 is None and not is_blocked(base0):
                    first_url_hard_fail = True
        except Exception:
            first_url_hard_fail = True

    if first_url_hard_fail:
        raise SystemExit(3)

    with out_path.open("a", encoding="utf-8") as fout:
        for idx, match_url in enumerate(tqdm(match_urls, desc=f"Fetching HLTV team={args.team}")):
            match_id = extract_match_id(match_url)
            if match_id is None:
                continue

            try:
                match_html = fetch(scraper, match_url, args.timeout)

                if is_blocked(match_html):
                    blocked += 1

                team1_name, team2_name = parse_team_names(match_html)
                timestamp = parse_match_timestamp(match_html)

                country = extract_event_country(match_html)
                tz_name = resolve_timezone_name(country)
                weekday = weekday_in_event_tz(timestamp, tz_name)
                event_id = extract_event_id(match_html)

                is_seeding, seeding_note = parse_seeding_info(match_html)
                is_third_place, third_place_note = parse_third_place_decider(match_html)
                veto = parse_veto(match_html)
                map_results = parse_map_results(match_html)

                refs = extract_all_mapstats_refs(match_html)
                if not refs:
                    no_stats_ref += 1

                    is_no_stats, no_stats_note = looks_like_no_stats_match(match_html)

                    if idx == 0 and (is_blocked(match_html) or not is_no_stats):
                        raise SystemExit(3)

                    if is_no_stats:
                        no_stats_skipped += 1
                        fout.write(
                            json.dumps(
                                {
                                    "match_id": match_id,
                                    "match_url": match_url,
                                    "event_id": event_id,
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
                                "event_id": event_id,
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
                                "error": "NO_STATS_MATCH_REF_SKIPPED",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    written += 1
                    time.sleep(args.delay)
                    continue

                stats_maps: List[Dict[str, Any]] = []
                for i, (kind, stats_id, slug) in enumerate(refs):
                    stats_urls = build_stats_urls(kind, stats_id, slug)
                    base_html = fetch(scraper, stats_urls["base"], args.timeout)
                    base_tables = parse_all_tables(base_html)
                    compact = compact_total_table_for_one_map(base_tables)
                    if compact is None:
                        continue
                    map_name = extract_map_name_from_stats_page(base_html)
                    if not map_name and i < len(map_results):
                        map_name = map_results[i].get("map")
                    stats_maps.append(
                        {
                            "stats_match_id": stats_id,
                            "stats_match_slug": slug,
                            "stats_urls": stats_urls,
                            "map_name": map_name,
                            "table": compact,
                        }
                    )

                fout.write(
                    json.dumps(
                        {
                            "match_id": match_id,
                            "match_url": match_url,
                            "event_id": event_id,
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
                            "stats_maps": stats_maps,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1

            except SystemExit:
                raise
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"

                if "NO_STATS_MATCH_REF" in msg:
                    raise SystemExit(2)

                if "BLOCKED" in msg:
                    blocked += 1
                else:
                    failed += 1

                fout.write(json.dumps({"match_id": match_id, "match_url": match_url, "error": msg}, ensure_ascii=False) + "\n")
                written += 1

            time.sleep(args.delay)

    print(f"Wrote -> {out_path}")
    print(
        f"team={args.team} written={written} blocked={blocked} failed={failed} "
        f"no_stats_ref={no_stats_ref} no_stats_skipped={no_stats_skipped} total={len(match_urls)}"
    )


if __name__ == "__main__":
    main()
