from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


MAX_PAGES = 60
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice|placement)\b",
    re.IGNORECASE,
)
ARTICLE_RE = re.compile(r'<article class="article article--result"(.*?)</article>', re.S)
LINK_RE = re.compile(r'<a class="link" href="([^"]*?/JobDetail/[^"]+)"[^>]*>(.*?)</a>', re.S)
BLOOMBERG_LOCATION_RE = re.compile(r'class="list-item-location"[^>]*>(.*?)</span>', re.S)
TWOSIGMA_SPAN_RE = re.compile(r'class="paragraph_inner-span"[^>]*>(.*?)</span>', re.S)
DETAIL_BLOCK_RE = re.compile(r'<article class="article article--details"(.*?)</article>', re.S)


@dataclass
class AvatureBoard:
    company: str
    host: str  # e.g. bloomberg.avature.net or careers.twosigma.com
    path: str  # e.g. careers/SearchJobs or careers/OpenRoles


class AvatureAdapter:
    """Avature career portals (Bloomberg, Two Sigma). The listing is HTML,
    paginated by jobOffset (the portal decides the page size). Only
    intern-looking titles get a detail fetch, and those are remembered per
    company (checked_by_company) so each job page is read once."""

    def __init__(
        self,
        boards: Iterable[AvatureBoard],
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

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        for board in self.boards:
            try:
                jobs.extend(self._fetch_board(board))
            except Exception as exc:
                print(f"[avature] {board.company} fetch failed: {exc}")
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _fetch_board(self, board: AvatureBoard) -> list[Job]:
        cards = self._list(board)
        if not cards:
            raise RuntimeError(f"avature {board.host}: listing returned no jobs")
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
            description = self._detail(card["url"])
            checked.add(card["id"])
            jobs.append(
                Job(
                    id=f"avature:{board.company}:{card['id']}",
                    company=board.company,
                    title=card["title"],
                    location=card["location"] or "Unspecified",
                    url=card["url"],
                    jd_text=description,
                    posted_at=None,
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _list(self, board: AvatureBoard) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        offset = 0
        for _ in range(MAX_PAGES):
            url = f"https://{board.host}/{board.path.strip('/')}/?jobOffset={offset}"
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            page = parse_listing(response.text or "")
            fresh = [c for c in page if c["id"] not in seen]
            if not fresh:
                break
            for card in fresh:
                seen.add(card["id"])
                cards.append(card)
            offset += len(page)
        return cards

    def _detail(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            print(f"[avature] detail {url} failed: {exc}")
            return ""
        blocks = DETAIL_BLOCK_RE.findall(response.text or "")
        return html_to_text("\n".join(blocks)) if blocks else html_to_text(response.text or "")


def parse_listing(html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for block in ARTICLE_RE.findall(html):
        link = LINK_RE.search(block)
        if not link:
            continue  # "No jobs found" filler articles have no JobDetail link
        url = unescape(link.group(1))
        job_id = url.rstrip("/").rsplit("/", 1)[-1]
        title = compact_text(unescape(re.sub(r"<[^>]+>", " ", link.group(2))))
        location = ""
        match = BLOOMBERG_LOCATION_RE.search(block)
        if match:
            location = compact_text(unescape(re.sub(r"<[^>]+>", " ", match.group(1))))
        else:
            spans = [compact_text(unescape(re.sub(r"<[^>]+>", " ", s))) for s in TWOSIGMA_SPAN_RE.findall(block)]
            if spans:
                location = spans[0]
        cards.append({"id": job_id, "title": title, "url": url, "location": location})
    return cards
