from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
import re
from typing import Any
from urllib.parse import urlparse

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


CANDIDATE_RE = re.compile(r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b", re.IGNORECASE)
LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>")
UUID_SUFFIX_RE = re.compile(r"[_-][0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
OG_TITLE_RE = re.compile(r'<meta[^>]*property="og:title"[^>]*content="([^"]+)"', re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.S)
MAIN_RE = re.compile(r"<main\b.*?</main>", re.IGNORECASE | re.S)
_UPPER = {"us": "US", "uk": "UK", "ml": "ML", "ai": "AI", "it": "IT", "qa": "QA", "ui": "UI", "ux": "UX", "smb": "SMB", "api": "API"}


@dataclass
class SitemapBoard:
    company: str
    sitemap_url: str  # e.g. https://www.shopify.com/careers/sitemap.xml


class SitemapAdapter:
    """Career sites whose only machine-readable listing is a sitemap of job
    pages (Shopify). Titles come from each URL slug; job pages are fetched
    only for intern-looking slugs, once each (checked ids are remembered)."""

    def __init__(
        self,
        boards: Iterable[SitemapBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
        known: dict[str, tuple[set[str], set[str]]] | None = None,
        max_details: int = 15,
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
        for board in self.boards:
            try:
                jobs.extend(self._fetch_board(board))
            except Exception as exc:
                print(f"[sitemap] {board.company} fetch failed: {exc}")
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _fetch_board(self, board: SitemapBoard) -> list[Job]:
        response = self.session.get(board.sitemap_url, timeout=self.timeout)
        response.raise_for_status()
        cards = sitemap_job_cards(response.text or "", board.sitemap_url)
        if not cards:
            raise RuntimeError(f"sitemap {board.sitemap_url} listed no job pages")
        self.listing_counts[board.company] = len(cards)
        known_ids, intern_ids = self.known.get(board.company, (set(), set()))
        checked = known_ids & {c["id"] for c in cards}
        budget = self.max_details
        jobs: list[Job] = []
        for card in cards:
            if not CANDIDATE_RE.search(card["title"]):
                checked.add(card["id"])
                continue
            if card["id"] in known_ids and card["id"] not in intern_ids:
                continue
            if card["id"] not in intern_ids:
                if budget <= 0:
                    continue
                budget -= 1
            page = self._page(card["url"])
            checked.add(card["id"])
            jobs.append(
                Job(
                    id=f"sitemap:{board.company}:{card['id']}",
                    company=board.company,
                    title=page.get("title") or card["title"],
                    location=card["location"] or "Unspecified",
                    url=card["url"],
                    jd_text=page.get("text", ""),
                    posted_at=None,
                    country_codes=card["country_codes"],
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _page(self, url: str) -> dict[str, str]:
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except Exception as exc:
            print(f"[sitemap] page {url} failed: {exc}")
            return {}
        html = response.text or ""
        title = OG_TITLE_RE.search(html)
        if not title:
            match = TITLE_RE.search(html)
            title_text = match.group(1).rsplit(" - ", 1)[0] if match else ""
        else:
            title_text = title.group(1)
        main = MAIN_RE.search(html)
        return {"title": compact_text(unescape(title_text)), "text": html_to_text(main.group(0) if main else html)[:8000]}


def sitemap_job_cards(xml: str, sitemap_url: str) -> list[dict[str, Any]]:
    base = sitemap_url.rsplit("/", 1)[0].rstrip("/")
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in LOC_RE.findall(xml):
        url = unescape(url).strip()
        if url.rstrip("/") == base or not url.startswith(base + "/"):
            continue
        slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        job_id = slug
        if job_id in seen or not slug:
            continue
        seen.add(job_id)
        words = UUID_SUFFIX_RE.sub("", slug).split("-")
        title = " ".join(_UPPER.get(w.lower(), w.capitalize()) for w in words if w)
        us = bool(words) and words[0].lower() == "us"
        cards.append(
            {
                "id": job_id,
                "title": title,
                "url": url,
                "location": "United States" if us else "",
                "country_codes": ("US",) if us else (),
            }
        )
    return cards
