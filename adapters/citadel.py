from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
import json
import re
import time
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session
from adapters.radancy import ld_locations


PER_PAGE = 100
MAX_PAGES = 10
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)
CARD_RE = re.compile(
    r'<a[^>]*class="[^"]*careers-listing-card[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)
POSITION_RE = re.compile(r'data-position="([^"]*)"')
H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
LOCATION_RE = re.compile(r'class="careers-listing-card__location"[^>]*>(.*?)</span>', re.S)
LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


@dataclass
class CitadelBoard:
    company: str
    host: str  # www.citadel.com or www.citadelsecurities.com


class CitadelAdapter:
    """Citadel and Citadel Securities share a WordPress careers theme whose
    search endpoint is admin-ajax.php returning JSON with an HTML card list.
    Cloudflare challenges are intermittent, so requests retry with a pause."""

    def __init__(
        self,
        boards: Iterable[CitadelBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
        known: dict[str, tuple[set[str], set[str]]] | None = None,
        max_details: int = 15,
        sleep: Any = time.sleep,
    ) -> None:
        self.boards = list(boards)
        self.timeout = timeout
        self.session = session or new_session()
        self.known = known or {}
        self.max_details = max_details
        self._sleep = sleep
        self.board_errors: list[tuple[str, str]] = []
        self.checked_by_company: dict[str, tuple[set[str], set[str]]] = {}

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        for board in self.boards:
            try:
                jobs.extend(self._fetch_board(board))
            except Exception as exc:
                print(f"[citadel] {board.company} fetch failed: {exc}")
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _fetch_board(self, board: CitadelBoard) -> list[Job]:
        cards = self._list(board)
        if not cards:
            raise RuntimeError(f"citadel {board.host}: listing returned no jobs")
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
            if card["id"] not in intern_ids:
                if budget <= 0:
                    continue
                budget -= 1
            detail = self._detail(card["url"])
            checked.add(card["id"])
            jobs.append(
                Job(
                    id=f"citadel:{board.company}:{card['id']}",
                    company=board.company,
                    title=card["title"],
                    location=detail.get("location") or card["location"] or "Unspecified",
                    url=card["url"],
                    jd_text=detail.get("description", ""),
                    posted_at=detail.get("posted_at"),
                    country_codes=tuple(detail.get("country_codes") or ()),
                    country_names=tuple(detail.get("country_names") or ()),
                    location_names=tuple(x for x in (card["location"], detail.get("location") or "") if x),
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _list(self, board: CitadelBoard) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            payload = self._post_listing(board, page)
            content = payload.get("content") if isinstance(payload, dict) else None
            page_cards = parse_cards(str(content or ""))
            fresh = [c for c in page_cards if c["id"] not in seen]
            if not fresh:
                break
            for card in fresh:
                seen.add(card["id"])
                cards.append(card)
            if len(page_cards) < PER_PAGE:
                break
        return cards

    def _post_listing(self, board: CitadelBoard, page: int) -> Any:
        url = f"https://{board.host}/wp-admin/admin-ajax.php"
        data = {
            "action": "careers_listing_filter",
            "search": "",
            "current_page": str(page),
            "sort_order": "DESC",
            "per_page": str(PER_PAGE),
        }
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://{board.host}/careers/open-opportunities/",
        }
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.post(url, data=data, headers=headers, timeout=self.timeout)
                if response.status_code == 403:
                    raise RuntimeError("403 (Cloudflare challenge)")
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last = exc
                self._sleep(3.0 * (attempt + 1))
        raise RuntimeError(f"citadel {board.host} listing page {page}: {last}")

    def _detail(self, url: str) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=self.timeout)
                if response.status_code == 403:
                    raise RuntimeError("403 (Cloudflare challenge)")
                response.raise_for_status()
                return parse_detail(response.text or "")
            except Exception as exc:
                print(f"[citadel] detail {url} attempt {attempt + 1} failed: {exc}")
                self._sleep(3.0 * (attempt + 1))
        return {}


def parse_cards(html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for href, inner in CARD_RE.findall(html):
        url = unescape(href)
        job_id = url.rstrip("/").rsplit("/", 1)[-1]
        title_match = H2_RE.search(inner)
        title = compact_text(unescape(re.sub(r"<[^>]+>", " ", title_match.group(1)))) if title_match else ""
        if not title:
            pos = POSITION_RE.search(html[html.find(href) - 400 : html.find(href) + 400])
            title = compact_text(unescape(pos.group(1))) if pos else job_id
        loc = LOCATION_RE.search(inner)
        location = compact_text(unescape(re.sub(r"<[^>]+>", " ", loc.group(1)))) if loc else ""
        cards.append({"id": job_id, "title": title, "url": url, "location": location})
    return cards


def parse_detail(html: str) -> dict[str, Any]:
    for raw in LDJSON_RE.findall(html):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                parts, codes, names = ld_locations(item.get("jobLocation"))
                return {
                    "description": html_to_text(str(item.get("description") or "")),
                    "posted_at": item.get("datePosted"),
                    "location": "; ".join(parts),
                    "country_codes": codes,
                    "country_names": names,
                }
    desc = re.search(r'class="single-job-application__description"[^>]*>(.*?)</div>\s*</div>', html, re.S)
    return {"description": html_to_text(desc.group(1)) if desc else ""}
