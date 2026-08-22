from __future__ import annotations

from html import unescape
import re
import time
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


# LinkedIn's public "guest" job search. It returns HTML job cards, ten per
# page, and rate-limits aggressively (HTTP 429 after roughly ten quick
# requests), so every call is paced and this adapter is meant to run on the
# laptop runner, not on GitHub-hosted IPs.
# sortBy=DD = newest first, so a capped page count still sees every new posting.
# (The f_JT=I "internship" filter is silently ignored by this endpoint.)
LIST_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?f_C={company_id}&sortBy=DD&start={start}"
)
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MAX_PAGES = 30
MAX_DETAILS_PER_RUN = 15
REQUEST_GAP_SECONDS = 6.0

CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)
URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
TITLE_RE = re.compile(r'class="base-search-card__title">\s*(.*?)\s*<', re.S)
LOCATION_RE = re.compile(r'class="job-search-card__location">\s*(.*?)\s*<', re.S)
DATE_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')
LINK_RE = re.compile(r'href="(https://www\.linkedin\.com/jobs/view/[^"?]+)')
DESCRIPTION_RE = re.compile(r'class="show-more-less-html__markup[^"]*">(.*?)</div>', re.S)
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)


class LinkedInAdapter:
    """One LinkedIn company page (f_C = numeric company id), newest first.
    Titles are filtered for intern-like words here; detail pages (for the description) are fetched only
    for ids not seen before; `checked_ids`/`intern_ids` are persisted by the
    pipeline the same way as for Meta."""

    def __init__(
        self,
        company_id: str,
        *,
        company: str = "linkedin",
        timeout: int = 30,
        session: requests.Session | None = None,
        known_ids: set[str] | None = None,
        intern_ids: set[str] | None = None,
        max_details: int = MAX_DETAILS_PER_RUN,
        request_gap: float = REQUEST_GAP_SECONDS,
        sleep: Any = time.sleep,
    ) -> None:
        self.company_id = str(company_id)
        self.company = company
        self.timeout = timeout
        self.session = session or requests.Session()
        headers = getattr(self.session, "headers", None)
        if isinstance(headers, dict) or hasattr(headers, "setdefault"):
            headers.setdefault("User-Agent", USER_AGENT)
        self.known_ids = set(known_ids or set())
        self.intern_ids = set(intern_ids or set())
        self.max_details = max_details
        self.request_gap = request_gap
        self._sleep = sleep
        self.checked_ids: set[str] = set()
        self.backlog = 0
        self.listing_counts: dict[str, int] = {}

    def fetch(self) -> list[Job]:
        cards = self._list_cards()
        if not cards:
            raise RuntimeError("LinkedIn guest search returned no job cards")
        live = {card["id"] for card in cards}
        self.listing_counts = {self.company: len(cards)}
        candidates = [c for c in cards if CANDIDATE_TITLE_RE.search(c["title"])]
        known_live = self.known_ids & live
        to_fetch = [c for c in candidates if c["id"] in self.intern_ids or c["id"] not in self.known_ids]
        new = [c for c in to_fetch if c["id"] not in self.intern_ids]
        self.backlog = max(0, len(new) - self.max_details)
        if self.backlog:
            print(f"[linkedin] {len(new)} unchecked ids; fetching {self.max_details} this run")
        budget = self.max_details
        jobs: list[Job] = []
        checked = set(known_live)
        for card in candidates:
            job_id = card["id"]
            if job_id in self.known_ids and job_id not in self.intern_ids:
                continue
            if job_id not in self.intern_ids:
                if budget <= 0:
                    continue
                budget -= 1
            description = self._detail(job_id)
            if description is None:
                continue
            checked.add(job_id)
            jobs.append(self._normalize(card, description))
        # Cards that did not look like internships are "checked" without a detail fetch.
        checked.update(c["id"] for c in cards if not CANDIDATE_TITLE_RE.search(c["title"]))
        self.checked_ids = checked
        self.intern_ids = {job.id.split(":", 2)[2] for job in jobs}
        return jobs

    def _list_cards(self) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        start = 0
        for _ in range(MAX_PAGES):
            html = self._get(LIST_URL.format(company_id=self.company_id, start=start))
            if html is None:
                break
            page = _parse_cards(html)
            fresh = [c for c in page if c["id"] not in seen]
            if not fresh:
                break
            for card in fresh:
                seen.add(card["id"])
                cards.append(card)
            start += len(page)
        return cards

    def _detail(self, job_id: str) -> str | None:
        html = self._get(DETAIL_URL.format(job_id=job_id))
        if html is None:
            return None
        match = DESCRIPTION_RE.search(html)
        return html_to_text(match.group(1)) if match else ""

    def _get(self, url: str) -> str | None:
        for attempt in range(3):
            response = self.session.get(url, timeout=self.timeout)
            self._sleep(self.request_gap)
            if response.status_code == 429:
                wait = _retry_after(response, default=20.0 * (attempt + 1))
                print(f"[linkedin] 429; waiting {wait:.0f}s")
                self._sleep(wait)
                continue
            if response.status_code == 200:
                return response.text or ""
            print(f"[linkedin] {url} -> {response.status_code}")
            return None
        return None

    def _normalize(self, card: dict[str, str], description: str) -> Job:
        return Job(
            id=f"linkedin:{self.company}:{card['id']}",
            company=self.company,
            title=card["title"],
            location=card["location"] or "Unspecified",
            url=card["url"] or f"https://www.linkedin.com/jobs/view/{card['id']}",
            jd_text=description,
            posted_at=card["date"] or None,
        )


def _parse_cards(html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for chunk in CARD_RE.findall(html):
        urn = URN_RE.search(chunk)
        if not urn:
            continue
        title = TITLE_RE.search(chunk)
        location = LOCATION_RE.search(chunk)
        date = DATE_RE.search(chunk)
        link = LINK_RE.search(chunk)
        cards.append(
            {
                "id": urn.group(1),
                "title": compact_text(unescape(re.sub(r"<[^>]+>", " ", title.group(1)))) if title else "",
                "location": compact_text(unescape(location.group(1))) if location else "",
                "date": date.group(1) if date else "",
                "url": link.group(1) if link else "",
            }
        )
    return cards


def _retry_after(response: requests.Response, *, default: float) -> float:
    raw = response.headers.get("Retry-After") if getattr(response, "headers", None) else None
    try:
        return min(float(raw), 120.0) if raw is not None else default
    except (TypeError, ValueError):
        return default
