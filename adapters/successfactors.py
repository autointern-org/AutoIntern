from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from html import unescape
import re
from typing import Any
from urllib.parse import quote_plus

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


MAX_PAGES = 40
CANDIDATE_TITLE_RE = re.compile(r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b", re.IGNORECASE)
ROW_SPLIT_RE = re.compile(r'<tr class="data-row"')
LINK_RE = re.compile(r'<a[^>]*href="([^"]*/job/[^"]+)"[^>]*class="jobTitle-link"[^>]*>(.*?)</a>|<a[^>]*class="jobTitle-link"[^>]*href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>', re.S)
LOCATION_RE = re.compile(r'<span class="jobLocation">(.*?)</span>', re.S)
TOTAL_RE = re.compile(r"of\s*<b>\s*([\d,]+)\s*</b>", re.IGNORECASE)
DESCRIPTION_RE = re.compile(r'<span[^>]*itemprop="description"[^>]*>(.*?)</span>\s*</div>', re.S)
DATE_RE = re.compile(r'itemprop="datePosted"[^>]*content="([^"]+)"')


@dataclass
class SuccessFactorsBoard:
    company: str
    host: str  # e.g. jobs.ulalaunch.com
    prefix: str = ""  # e.g. "/We_Energies" when the site lives under a brand path


class SuccessFactorsAdapter:
    """SAP SuccessFactors career sites (Career Site Builder): /search/?q=
    returns an HTML results table paged by startrow; job pages carry the
    description and a datePosted meta tag."""

    def __init__(
        self,
        boards: Iterable[SuccessFactorsBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
        known: dict[str, tuple[set[str], set[str]]] | None = None,
        max_details: int = 25,
    ) -> None:
        self.boards = list(boards)
        self.timeout = timeout
        self.session = session or new_session()
        self.known = known or {}
        self.max_details = max_details
        self.board_errors: list[tuple[str, str]] = []
        self.checked_by_company: dict[str, tuple[set[str], set[str]]] = {}
        self.listing_counts: dict[str, int] = {}
        self.source_totals: dict[str, tuple[int, int]] = {}

    def fetch(self) -> list[Job]:
        self.board_errors = []
        self.listing_counts = {}
        self.source_totals = {}
        jobs: list[Job] = []
        for board in self.boards:
            try:
                jobs.extend(self._fetch_board(board))
            except Exception as exc:
                print(f"[successfactors] {board.company} fetch failed: {exc}")
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _fetch_board(self, board: SuccessFactorsBoard) -> list[Job]:
        cards, total = self._list(board)
        self.listing_counts[board.company] = len(cards)
        if total:
            self.source_totals[board.company] = (total, len(cards))
        known_ids, intern_ids = self.known.get(board.company, (set(), set()))
        checked = known_ids & {c["id"] for c in cards}
        budget = self.max_details
        jobs: list[Job] = []
        for card in cards:
            if not CANDIDATE_TITLE_RE.search(card["title"]):
                checked.add(card["id"])
                continue
            if card["id"] in known_ids and card["id"] not in intern_ids:
                continue
            if card["id"] not in intern_ids:
                if budget <= 0:
                    continue
                budget -= 1
            detail = self._detail(card["url"])
            checked.add(card["id"])
            jobs.append(
                Job(
                    id=f"successfactors:{board.company}:{card['id']}",
                    company=board.company,
                    title=card["title"],
                    location=card["location"] or "Unspecified",
                    url=card["url"],
                    jd_text=detail.get("description", ""),
                    posted_at=detail.get("posted_at"),
                    country_codes=card["country_codes"],
                    location_names=(card["location"],) if card["location"] else (),
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _list(self, board: SuccessFactorsBoard) -> tuple[list[dict[str, Any]], int]:
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        start = 0
        for _ in range(MAX_PAGES):
            url = f"https://{board.host}{board.prefix}/search/?q={quote_plus('intern')}&startrow={start}"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            html = response.text or ""
            if start == 0 and "jobTitle-link" not in html:
                raise RuntimeError(f"successfactors {board.host}: no results table (HTTP {response.status_code})")
            match = TOTAL_RE.search(html)
            if match:
                total = int(match.group(1).replace(",", ""))
            page = parse_rows(html, board.host)
            fresh = [c for c in page if c["id"] not in seen]
            if not fresh:
                break
            for card in fresh:
                seen.add(card["id"])
                cards.append(card)
            start += len(page)
            if total and start >= total:
                break
        return cards, total

    def _detail(self, url: str) -> dict[str, Any]:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            print(f"[successfactors] detail {url} failed: {exc}")
            return {}
        html = response.text or ""
        description = DESCRIPTION_RE.search(html)
        date = DATE_RE.search(html)
        return {
            "description": html_to_text(description.group(1)) if description else "",
            "posted_at": _iso_date(date.group(1)) if date else None,
        }


TILE_RE = re.compile(r'<li class="job-tile[^"]*"[^>]*data-url="([^"]+)"(.*?)(?=<li class="job-tile|</ul>)', re.S)
TILE_TITLE_RE = re.compile(r'<a[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*>(.*?)</a>', re.S)
SLUG_US_RE = re.compile(r"-([A-Z]{2})-(\d{5})(?:-\d+)?/\d{5,}/?$")


def parse_tiles(html: str, host: str) -> list[dict[str, Any]]:
    """Tile-layout sites render only titles server-side; the job URL slug
    carries the location ("...-Rosemead-...-CA-91770-3714/1424643100/")."""
    cards: list[dict[str, Any]] = []
    for href, body in TILE_RE.findall(html):
        href = unescape(href)
        job_id = re.search(r"/(\d{5,})/?$", href.split("?", 1)[0])
        title = TILE_TITLE_RE.search(body)
        if not job_id or not title:
            continue
        us = SLUG_US_RE.search(href.split("?", 1)[0])
        location = f"{us.group(1)}, US, {us.group(2)}" if us else ""
        cards.append(
            {
                "id": job_id.group(1),
                "title": compact_text(unescape(re.sub(r"<[^>]+>", " ", title.group(1)))),
                "url": href if href.startswith("http") else f"https://{host}{href}",
                "location": location,
                "country_codes": ("US",) if us else (),
            }
        )
    return cards


def parse_rows(html: str, host: str) -> list[dict[str, Any]]:
    if 'class="job-tile' in html and '<tr class="data-row"' not in html:
        return parse_tiles(html, host)
    cards: list[dict[str, Any]] = []
    for chunk in ROW_SPLIT_RE.split(html)[1:]:
        row = chunk.split("</tr>", 1)[0]
        link = LINK_RE.search(row)
        if not link:
            continue
        href = link.group(1) or link.group(3)
        title_html = link.group(2) if link.group(1) else link.group(4)
        href = unescape(href)
        job_id_match = re.search(r"/(\d{5,})/?$", href.split("?", 1)[0])
        if not job_id_match:
            continue
        location_match = LOCATION_RE.search(row)
        location = ""
        if location_match:
            location = compact_text(unescape(re.sub(r"<small.*?</small>|<[^>]+>", " ", location_match.group(1), flags=re.S)))
        cards.append(
            {
                "id": job_id_match.group(1),
                "title": compact_text(unescape(re.sub(r"<[^>]+>", " ", title_html))),
                "url": href if href.startswith("http") else f"https://{host}{href}",
                "location": location,
                "country_codes": location_country(location),
            }
        )
    return cards


def location_country(location: str) -> tuple[str, ...]:
    """CSB writes "City, ST, CC, ZIP" (or "City, CC"); the country code is the
    2-letter token after the region."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    for index in (2, 1):
        if len(parts) > index and len(parts[index]) == 2 and parts[index].isalpha():
            code = normalize_country_code(parts[index])
            if code and (index == 2 or len(parts) == 2):
                return (code,)
    return ()


def _iso_date(value: str) -> str | None:
    for fmt in ("%a %b %d %H:%M:%S UTC %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None
