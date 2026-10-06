from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from adapters.listing import Card, ListingAdapter, ListingBoard, strip_tags


MAX_PAGES = 10
ROW_RE = re.compile(
    r'<td class="jv-job-list-name">\s*<a href="(/[^"]+/job/([A-Za-z0-9]+))"[^>]*>(.*?)</a>\s*</td>\s*'
    r'<td class="jv-job-list-location">(.*?)</td>',
    re.S,
)
META_RE = re.compile(r'<p class="jv-job-detail-meta">(.*?)</p>', re.S)
DESCRIPTION_RE = re.compile(r'<div class="jv-job-detail-description"[^>]*>(.*?)</div>\s*(?:<div class="jv-job-detail-bottom|<p class="jv-job-detail-bottom|<a class="jv-button)', re.S)


class JobviteAdapter(ListingAdapter):
    """Jobvite boards (jobs.jobvite.com/<company>): the keyword search page
    lists matches as an HTML table; job pages hold location and description.
    board.site is the company path; board.search may list several keywords
    separated by commas."""

    NAME = "jobvite"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        cards: list[Card] = []
        for keyword in [k.strip() for k in board.search.split(",") if k.strip()]:
            seen: set[str] = set()
            for page in range(MAX_PAGES):
                html = self.get(f"https://jobs.jobvite.com/{board.site}/search?q={quote_plus(keyword)}&p={page}").text
                fresh = [c for c in parse_rows(html) if c.id not in seen]
                if not fresh:
                    break
                seen.update(c.id for c in fresh)
                cards.extend(fresh)
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        return parse_detail(self.get(card.url).text)


def parse_rows(html: str) -> list[Card]:
    cards: list[Card] = []
    for path, job_id, title, location in ROW_RE.findall(html or ""):
        cards.append(
            Card(
                id=job_id,
                title=strip_tags(title),
                url=f"https://jobs.jobvite.com{path}",
                location=strip_tags(location),
            )
        )
    return cards


def parse_detail(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    meta = META_RE.search(html or "")
    if meta:
        # "<category><span class='jv-inline-separator'></span> City, State <br>Salary ..."
        text = re.split(r"<br\s*/?>", meta.group(1))[0]
        parts = re.split(r"<span class=['\"]jv-inline-separator['\"]></span>", text)
        out["location"] = strip_tags(parts[-1])
    description = DESCRIPTION_RE.search(html or "")
    if description:
        out["description"] = strip_tags(re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", description.group(1)))
    return out
