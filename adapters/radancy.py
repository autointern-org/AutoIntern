from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from html import unescape
import json
import re
from typing import Any
from urllib.parse import urlencode

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


RECORDS_PER_PAGE = 40  # some tenants (Arm) cap pages at ~43 regardless of the request
MAX_PAGES = 40
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)
LI_RE = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S)
ANCHOR_RE = re.compile(r'<a[^>]*href="([^"]+)"[^>]*data-job-id="([^"]+)"[^>]*>(.*?)</a>', re.S)
ANCHOR_ALT_RE = re.compile(r'<a[^>]*data-job-id="([^"]+)"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)
LOCATION_RE = re.compile(r'class="(?:job-location|location)"[^>]*>(.*?)</span>', re.S)
DATE_RE = re.compile(r'class="(?:job-date-posted|content-date)"[^>]*>(.*?)</span>', re.S)
TOTAL_PAGES_RE = re.compile(r'data-total-pages="(\d+)"')
LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


@dataclass
class RadancyBoard:
    company: str
    host: str  # jobs.intuit.com
    prefix: str = ""  # "/en" for tenants whose routes are locale-prefixed


class RadancyAdapter:
    """Radancy/TalentBrew career sites: /search-jobs/results returns JSON whose
    `results` field is an HTML list; job pages carry a JobPosting ld+json."""

    def __init__(
        self,
        boards: Iterable[RadancyBoard],
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
                print(f"[radancy] {board.company} fetch failed: {exc}")
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _fetch_board(self, board: RadancyBoard) -> list[Job]:
        cards = self._list(board)
        if not cards:
            raise RuntimeError(f"radancy {board.host}: listing returned no jobs")
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
                    id=f"radancy:{board.company}:{card['id']}",
                    company=board.company,
                    title=card["title"],
                    location=card["location"] or detail.get("location") or "Unspecified",
                    url=card["url"],
                    jd_text=detail.get("description", ""),
                    posted_at=detail.get("posted_at") or card["date"] or None,
                    country_codes=tuple(detail.get("country_codes") or ()),
                    country_names=tuple(detail.get("country_names") or ()),
                    location_names=tuple(x for x in (card["location"], detail.get("location") or "") if x),
                )
            )
        self.checked_by_company[board.company] = (checked, {j.id.rsplit(":", 1)[1] for j in jobs})
        return jobs

    def _list(self, board: RadancyBoard) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        total_pages = 1
        page = 1
        while page <= min(total_pages, MAX_PAGES):
            params = {
                "ActiveFacetID": "0",
                "CurrentPage": str(page),
                "RecordsPerPage": str(RECORDS_PER_PAGE),
                "Keywords": "",
                "SearchResultsModuleName": "Search Results",
                "SearchFiltersModuleName": "Search Filters",
                "SortCriteria": "0",
                "SortDirection": "1",
                "SearchType": "5",
            }
            url = f"https://{board.host}{board.prefix}/search-jobs/results?{urlencode(params)}"
            response = self.session.get(
                url,
                headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(f"radancy {board.host} non-JSON listing: {(response.text or '')[:120]}") from exc
            results = str((payload or {}).get("results") or "")
            match = TOTAL_PAGES_RE.search(results)
            if match:
                total_pages = int(match.group(1))
            page_cards = parse_results(results, host=board.host)
            fresh = [c for c in page_cards if c["id"] not in seen]
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
            print(f"[radancy] detail {url} failed: {exc}")
            return {}
        return parse_detail(response.text or "")


def parse_results(html: str, *, host: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for li in LI_RE.findall(html):
        anchor = ANCHOR_RE.search(li)
        if anchor:
            href, job_id, inner = anchor.groups()
        else:
            alt = ANCHOR_ALT_RE.search(li)
            if not alt:
                continue
            job_id, href, inner = alt.groups()
        h2 = H2_RE.search(inner)
        title = compact_text(unescape(re.sub(r"<[^>]+>", " ", h2.group(1) if h2 else inner)))
        if not title:
            continue
        loc = LOCATION_RE.search(li)
        date = DATE_RE.search(li)
        href = unescape(href)
        url = href if href.startswith("http") else f"https://{host}{href}"
        cards.append(
            {
                "id": str(job_id),
                "title": title,
                "url": url,
                "location": compact_text(unescape(re.sub(r"<[^>]+>", " ", loc.group(1)))) if loc else "",
                "date": compact_text(unescape(date.group(1))) if date else "",
            }
        )
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
    return {}


def ld_locations(value: Any) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    """(location texts, country codes, country names) from a JobPosting jobLocation."""
    items = value if isinstance(value, list) else [value]
    parts: list[str] = []
    codes: list[str] = []
    names: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address") if isinstance(entry.get("address"), dict) else entry
        text = ", ".join(
            compact_text(str(address.get(k))) for k in ("addressLocality", "addressRegion", "addressCountry") if address.get(k)
        )
        if text:
            parts.append(text)
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        code = normalize_country_code(country)
        if code:
            codes.append(code)
        elif country:
            names.append(compact_text(str(country)))
    return parts, tuple(dict.fromkeys(codes)), tuple(dict.fromkeys(names))
