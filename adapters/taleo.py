from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from adapters.base import html_to_text
from adapters.icims import location_country_codes
from adapters.listing import Card, ListingAdapter, ListingBoard, strip_tags


MAX_PAGES = 20
PORTAL_RE = re.compile(r"portal=(\d+)|portalNo\s*:\s*'(\d+)'")
DESC_RE = re.compile(r"api\.fillList\('requisitionDescriptionInterface', 'descRequisition', \[(.*?)\]\);", re.S)
QUOTED_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


class TaleoAdapter(ListingAdapter):
    """Oracle Taleo career sections (<company>.taleo.net/careersection/<site>).
    The section page names its portal number; the jobboard REST search pages
    keyword matches 25 at a time; job pages embed the description URL-encoded
    in the requisition fillList call. board.search may list several keywords
    separated by commas."""

    NAME = "taleo"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        page_url = f"https://{board.host}/careersection/{board.site}/jobsearch.ftl?lang=en"
        match = PORTAL_RE.search(self.get(page_url).text)
        if not match:
            raise RuntimeError(f"taleo {board.host}/{board.site}: no portal number on the search page")
        portal = match.group(1) or match.group(2)
        cards: list[Card] = []
        seen: set[str] = set()
        for keyword in [k.strip() for k in board.search.split(",") if k.strip()]:
            cards.extend(self._search(board, portal, keyword, page_url, seen))
        return cards

    def _search(self, board: ListingBoard, portal: str, keyword: str, page_url: str, seen: set[str]) -> list[Card]:
        """Newest first; a section's search stops returning new rows after its
        result cap, so the pages are read until nothing new arrives."""
        cards: list[Card] = []
        for page in range(1, MAX_PAGES + 1):
            response = self.session.post(
                f"https://{board.host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal}",
                json=search_body(keyword, page),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                    "tz": "GMT-05:00",
                    "Referer": page_url,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            fresh = [c for c in parse_search(response.json(), board) if c.id not in seen]
            if not fresh:
                break
            seen.update(c.id for c in fresh)
            cards.extend(fresh)
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        return {"description": parse_description(self.get(card.url).text)}


def search_body(keyword: str, page: int) -> dict[str, Any]:
    return {
        "multilineEnabled": False,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": keyword, "LOCATION": "", "CATEGORY": ""}, "valid": True},
        "filterSelectionParam": {"searchFilterSelections": []},
        "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
        "pageNo": page,
    }


def parse_search(payload: dict[str, Any], board: ListingBoard) -> list[Card]:
    cards: list[Card] = []
    for raw in payload.get("requisitionList") or []:
        columns = raw.get("column") or []
        if not raw.get("contestNo") or not columns:
            continue
        title_index = raw.get("linkedColumn") or 0
        location_indexes = raw.get("locationsColumns") or []
        locations: list[str] = []
        for index in location_indexes:
            if index < len(columns):
                try:
                    value = json.loads(columns[index])
                except (TypeError, ValueError):
                    value = [columns[index]]
                locations.extend(str(v) for v in (value if isinstance(value, list) else [value]))
        others = [c for i, c in enumerate(columns) if i != title_index and i not in location_indexes]
        location = "; ".join(strip_tags(x) for x in locations if x)
        cards.append(
            Card(
                id=str(raw["contestNo"]),
                title=strip_tags(columns[title_index] if title_index < len(columns) else ""),
                url=f"https://{board.host}/careersection/{board.site}/jobdetail.ftl?job={raw['contestNo']}&lang=en",
                location=location,
                country_codes=location_country_codes(location.replace("; ", "|")),
                posted_at=others[-1] if others else None,
            )
        )
    return cards


def parse_description(html: str) -> str:
    match = DESC_RE.search(html or "")
    if not match:
        return ""
    parts = [
        html_to_text(unquote(value[3:]))
        for value in QUOTED_RE.findall(match.group(1))
        if value.startswith("!*!")
    ]
    return " ".join(p for p in parts if p)
