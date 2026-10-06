from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
import json
import re
from time import sleep
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import fetch_boards, new_session


CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice|summer analyst|summer associate)\b",
    re.IGNORECASE,
)
LDJSON_RE = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)


@dataclass
class ListingBoard:
    company: str
    host: str
    # Platform-specific board identifier (Paylocity company GUID, Taleo career
    # section, Yello board id, Jobvite company path, ...).
    site: str = ""
    search: str = "intern"


@dataclass
class Card:
    id: str
    title: str
    url: str
    location: str = ""
    snippet: str = ""
    country_codes: tuple[str, ...] = ()
    posted_at: str | None = None
    # False when the listing already carries the full description.
    needs_detail: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


class ListingAdapter:
    """Shared flow for small job boards that list every posting (or every
    keyword match) on one page or API and keep the description on each job's
    page: keep intern-looking titles, read job pages within a per-run budget,
    and remember which ids were already read so a quiet board costs one list
    request per tick. Subclasses implement list_cards and, when the listing
    lacks descriptions, detail."""

    NAME = "listing"
    # Boards on one shared host (Paylocity) fetch one at a time with a pause
    # between job pages; distinct company hosts fetch a few at a time.
    BOARD_WORKERS = 4
    DETAIL_PAUSE_SECONDS = 0.0

    def __init__(
        self,
        boards: Iterable[ListingBoard],
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
        for board, outcome in fetch_boards(self.boards, self._fetch_board, workers=self.BOARD_WORKERS):
            if isinstance(outcome, Exception):
                print(f"[{self.NAME}] {board.company} fetch failed: {outcome}")
                self.board_errors.append((board.company, str(outcome)))
            else:
                jobs.extend(outcome)
        return jobs

    def list_cards(self, board: ListingBoard) -> list[Card]:
        raise NotImplementedError

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        """Keys: description, location, country_codes, posted_at (all optional)."""
        return {}

    def _fetch_board(self, board: ListingBoard) -> list[Job]:
        cards = _unique(self.list_cards(board))
        self.listing_counts[board.company] = len(cards)
        known_ids, intern_ids = self.known.get(board.company, (set(), set()))
        live = {card.id for card in cards}
        checked = known_ids & live
        budget = self.max_details
        jobs: list[Job] = []
        for card in cards:
            if not CANDIDATE_TITLE_RE.search(card.title):
                checked.add(card.id)
                continue
            if card.id in known_ids and card.id not in intern_ids:
                continue
            non_us = bool(card.country_codes) and "US" not in card.country_codes
            fetch_detail = card.needs_detail and not non_us
            if fetch_detail and card.id not in intern_ids:
                if budget <= 0:
                    continue
                budget -= 1
            info = self._detail_with_retry(board, card) if fetch_detail else {}
            checked.add(card.id)
            codes = tuple(dict.fromkeys((*info.get("country_codes", ()), *card.country_codes)))
            location = info.get("location") or card.location
            jobs.append(
                Job(
                    id=f"{self.NAME}:{board.company}:{card.id}",
                    company=board.company,
                    title=card.title,
                    location=location or "Unspecified",
                    url=card.url,
                    jd_text=info.get("description") or card.snippet,
                    posted_at=info.get("posted_at") or card.posted_at,
                    country_codes=codes,
                    location_names=tuple(x for x in dict.fromkeys((card.location, info.get("location") or "")) if x),
                )
            )
        self.checked_by_company[board.company] = (checked, {job.id.rsplit(":", 1)[1] for job in jobs})
        return jobs

    def _detail_with_retry(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        for attempt in range(2):
            if self.DETAIL_PAUSE_SECONDS:
                sleep(self.DETAIL_PAUSE_SECONDS)
            try:
                return self.detail(board, card) or {}
            except Exception as exc:  # noqa: BLE001 - the listing row still counts
                throttled = "429" in str(exc)
                if attempt == 0 and throttled:
                    sleep(3)
                    continue
                print(f"[{self.NAME}] detail {card.url} failed: {exc}")
        return {}

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response


def _unique(cards: list[Card]) -> list[Card]:
    seen: set[str] = set()
    out: list[Card] = []
    for card in cards:
        if card.id and card.id not in seen:
            seen.add(card.id)
            out.append(card)
    return out


def strip_tags(fragment: str | None) -> str:
    return compact_text(unescape(re.sub(r"<[^>]+>", " ", fragment or "")))


def country_from_text(*values: object) -> tuple[str, ...]:
    """ISO codes for country fields such as "USA", "US", "United States"."""
    codes: list[str] = []
    for value in values:
        text = compact_text(str(value or ""))
        if not text:
            continue
        code = normalize_country_code(text)
        if code is None and text.lower() in ("united states", "united states of america", "usa", "u.s.", "u.s.a."):
            code = "US"
        if code:
            codes.append(code)
    return tuple(dict.fromkeys(codes))


def job_posting_ld(html: str) -> dict[str, Any]:
    """Description, location, country codes and datePosted from a page's
    schema.org JobPosting block."""
    for raw in LDJSON_RE.findall(html or ""):
        try:
            payload = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else payload.get("@graph", [payload]) if isinstance(payload, dict) else []
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            parts: list[str] = []
            codes: list[str] = []
            locations = item.get("jobLocation")
            for entry in locations if isinstance(locations, list) else [locations]:
                if not isinstance(entry, dict):
                    continue
                address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
                country = address.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name") or country.get("@id")
                text = ", ".join(
                    compact_text(str(value))
                    for value in (address.get("addressLocality"), address.get("addressRegion"), country)
                    if value
                )
                if text:
                    parts.append(text)
                codes.extend(country_from_text(country))
            return {
                "description": html_to_text(unescape(str(item.get("description") or ""))),
                "location": "; ".join(parts),
                "country_codes": tuple(dict.fromkeys(codes)),
                "posted_at": item.get("datePosted") or None,
            }
    return {}
