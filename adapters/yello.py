from __future__ import annotations

from html import unescape
import re
from typing import Any
from urllib.parse import quote_plus

from adapters.base import html_to_text
from adapters.listing import Card, ListingAdapter, ListingBoard, country_from_text, strip_tags


MAX_PAGES = 40
US_ANSWER_RE = re.compile(r'\{"id":(\d+),"label":"United States(?: of America)?"\}')
ROW_RE = re.compile(
    r'<a class="search-results__req_title"[^>]*href="/jobs/([A-Za-z0-9_-]+)\?[^"]*"[^>]*>(.*?)</a>\s*<div><span>([^<]*)</span>',
    re.S,
)
H1_RE = re.compile(r'<div class="details-top__title[^"]*">\s*<h1[^>]*>(.*?)</h1>(.*?)</div>', re.S)
SPAN_RE = re.compile(r"<span>(.*?)</span>", re.S)
DESCRIPTION_RE = re.compile(r'<section class="job-details__description[^"]*">(.*?)</section>', re.S)
# recsolu "external requisition" pages (no job board): labelled field list.
FIELDS_RE = re.compile(r'<ul class="unstyled external_requisition_fields"[^>]*>(.*?)</ul></li></ul></div>', re.S)
FIELD_RE = re.compile(r'<strong[^>]*>([^<:]+):</strong>\s*<ul class="unstyled">(.*?)</ul>', re.S)


class YelloAdapter(ListingAdapter):
    """Yello (recsolu) job boards (<company>.yello.co/job_boards/<id>).
    The keyword search returns 25 rows per page as an HTML fragment; when the
    board has a Country/Region filter, only its "United States" answer is
    requested. Job pages hold locations and the description. board.site is
    the job board id."""

    NAME = "yello"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        board_url = f"https://{board.host}/job_boards/{board.site}"
        us_filter = US_ANSWER_RE.search(unescape(self.get(board_url).text))
        query = f"{board_url}/search?query={quote_plus(board.search)}"
        if us_filter:
            query += f"&filters={us_filter.group(1)}"
        cards: list[Card] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            payload = self.get(f"{query}&page_number={page}", headers={"X-Requested-With": "XMLHttpRequest"}).json()
            fresh = [c for c in parse_rows(payload.get("html") or "", board, us_only=bool(us_filter)) if c.id not in seen]
            seen.update(c.id for c in fresh)
            cards.extend(fresh)
            if not fresh or not payload.get("more_requisitions"):
                break
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        return parse_detail(self.get(card.url).text)


def parse_rows(html: str, board: ListingBoard, *, us_only: bool) -> list[Card]:
    return [
        Card(
            id=token,
            title=strip_tags(title),
            url=f"https://{board.host}/jobs/{token}?job_board_id={board.site}",
            country_codes=("US",) if us_only else (),
            extra={"req": strip_tags(req)},
        )
        for token, title, req in ROW_RE.findall(html or "")
    ]


def parse_detail(html: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    head = H1_RE.search(html or "")
    if head:
        spans = [strip_tags(s) for s in SPAN_RE.findall(head.group(2))]
        # First span is the requisition number, the second the location list.
        locations = [s for s in spans if s and not s.isdigit()]
        if locations:
            out["location"] = locations[0]
    description = DESCRIPTION_RE.search(html or "")
    if description:
        out["description"] = html_to_text(description.group(1))
        return out
    fields = FIELDS_RE.search(html or "")
    if fields:
        values = {label.strip().lower(): strip_tags(value) for label, value in FIELD_RE.findall(fields.group(1))}
        place = ", ".join(v for v in (values.get("city"), values.get("country")) if v)
        if place:
            out["location"] = place
        if values.get("country"):
            out["country_codes"] = country_from_text(values["country"])
        out["description"] = html_to_text(fields.group(1))
    return out
