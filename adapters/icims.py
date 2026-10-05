from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
import json
import re
from typing import Any
from urllib.parse import quote_plus

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import fetch_boards, new_session


MAX_PAGES = 30
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)
CARD_RE = re.compile(r'<li class="iCIMS_JobCardItem[^"]*">(.*?)</li>', re.S)
LINK_RE = re.compile(r'<a href="(https://[^"]+/jobs/(\d+)/[^"]*)"[^>]*class="iCIMS_Anchor"[^>]*>(.*?)</a>', re.S)
H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
LOCATION_RE = re.compile(r'Job Locations?</span>\s*<span[^>]*>(.*?)</span>', re.S)
DESCRIPTION_RE = re.compile(r'<div class="col-xs-12 description">(.*?)</div>', re.S)
PAGES_RE = re.compile(r"Page\s+\d+\s+of\s+(\d+)", re.IGNORECASE)
LDJSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)


@dataclass
class ICIMSBoard:
    company: str
    host: str  # e.g. careers-sas.icims.com
    search: str = "intern"


class ICIMSAdapter:
    """iCIMS career portals (classic /jobs/search pages). The listing is HTML
    cards paged with pr=N; detail pages carry a JobPosting ld+json whose
    datePosted is not the real posting date, so no posted date is reported."""

    def __init__(
        self,
        boards: Iterable[ICIMSBoard],
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

    def fetch(self) -> list[Job]:
        self.board_errors = []
        self.listing_counts = {}
        jobs: list[Job] = []
        for board, outcome in fetch_boards(self.boards, self._fetch_board):
            if isinstance(outcome, Exception):
                print(f"[icims] {board.company} fetch failed: {outcome}")
                self.board_errors.append((board.company, str(outcome)))
            else:
                jobs.extend(outcome)
        return jobs

    def _fetch_board(self, board: ICIMSBoard) -> list[Job]:
        cards = self._list(board)
        self.listing_counts[board.company] = len(cards)
        known_ids, intern_ids = self.known.get(board.company, (set(), set()))
        live = {c["id"] for c in cards}
        checked = known_ids & live
        budget = self.max_details
        jobs: list[Job] = []
        for card in cards:
            if not CANDIDATE_TITLE_RE.search(card["title"]):
                checked.add(card["id"])
                continue
            if card["id"] in known_ids and card["id"] not in intern_ids:
                continue
            non_us = bool(card["country_codes"]) and "US" not in card["country_codes"]
            if card["id"] not in intern_ids and not non_us:
                if budget <= 0:
                    continue
                budget -= 1
            # Rows whose structured country rules out the US skip the job page.
            detail = {} if non_us else self._detail(card["url"])
            checked.add(card["id"])
            jobs.append(
                Job(
                    id=f"icims:{board.company}:{card['id']}",
                    company=board.company,
                    title=card["title"],
                    location=detail.get("location") or card["location"] or "Unspecified",
                    url=card["url"],
                    jd_text=detail.get("description") or card["snippet"],
                    posted_at=None,
                    country_codes=tuple(dict.fromkeys((*detail.get("country_codes", ()), *card["country_codes"]))),
                    location_names=tuple(x for x in (card["location"], detail.get("location") or "") if x),
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _list(self, board: ICIMSBoard) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        seen: set[str] = set()
        pages = 1
        page = 0
        while page < min(pages, MAX_PAGES):
            url = f"https://{board.host}/jobs/search?ss=1&searchKeyword={quote_plus(board.search)}&in_iframe=1&pr={page}"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            html = response.text or ""
            if page == 0 and "iCIMS_JobsTable" not in html and "iCIMS_JobCardItem" not in html:
                raise RuntimeError(f"icims {board.host}: no job table in search page (HTTP {response.status_code})")
            match = PAGES_RE.search(html)
            if match:
                pages = int(match.group(1))
            fresh = [c for c in parse_cards(html) if c["id"] not in seen]
            if not fresh:
                break
            for card in fresh:
                seen.add(card["id"])
                cards.append(card)
            page += 1
        return cards

    def _detail(self, url: str) -> dict[str, Any]:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            print(f"[icims] detail {url} failed: {exc}")
            return {}
        return parse_detail(response.text or "")


def _strip(fragment: str) -> str:
    return compact_text(unescape(re.sub(r"<[^>]+>", " ", fragment or "")))


def parse_cards(html: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for block in CARD_RE.findall(html):
        link = LINK_RE.search(block)
        if not link:
            continue
        href, job_id, inner = link.groups()
        title_match = H3_RE.search(inner)
        title = _strip(title_match.group(1) if title_match else inner)
        location_match = LOCATION_RE.search(block)
        location = _strip(location_match.group(1)) if location_match else ""
        snippet_match = DESCRIPTION_RE.search(block)
        url = unescape(href).split("?", 1)[0]
        cards.append(
            {
                "id": job_id,
                "title": title,
                "url": url,
                "location": location,
                "snippet": _strip(snippet_match.group(1)) if snippet_match else "",
                "country_codes": location_country_codes(location),
            }
        )
    return cards


def location_country_codes(location: str) -> tuple[str, ...]:
    """iCIMS writes locations country-first: "US-TX-Southlake", "CA-ON-Toronto"."""
    codes: list[str] = []
    for part in re.split(r"[|;]", location or ""):
        head = part.strip().split("-", 1)[0].strip()
        code = normalize_country_code(head) if len(head) == 2 else None
        if code:
            codes.append(code)
    return tuple(dict.fromkeys(codes))


def parse_detail(html: str) -> dict[str, Any]:
    for raw in LDJSON_RE.findall(html):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            locations = item.get("jobLocation")
            parts: list[str] = []
            codes: list[str] = []
            for entry in locations if isinstance(locations, list) else [locations]:
                if not isinstance(entry, dict):
                    continue
                address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
                text = ", ".join(
                    compact_text(str(address.get(key)))
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                    if address.get(key)
                )
                if text:
                    parts.append(text)
                code = normalize_country_code(address.get("addressCountry"))
                if code:
                    codes.append(code)
            return {
                "description": html_to_text(str(item.get("description") or "")),
                "location": "; ".join(parts),
                "country_codes": tuple(dict.fromkeys(codes)),
            }
    return {}
