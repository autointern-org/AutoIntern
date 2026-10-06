from __future__ import annotations

import json
from typing import Any

from adapters.base import compact_text, normalize_country_code
from adapters.listing import Card, ListingAdapter, ListingBoard
from adapters.yello import parse_detail


API = "https://api-deutschebank.beesite.de/graduatesearch/"
PAGE_SIZE = 100
FIELDS = [
    "PositionID",
    "PositionTitle",
    "PositionURI",
    "PositionLocation.CountryCode",
    "PositionLocation.CityName",
    "PublicationStartDate",
]


class DeutscheBankAdapter(ListingAdapter):
    """Deutsche Bank student programmes from careers.db.com's search API
    (internships, graduate and summer programmes worldwide). US programmes
    link to recsolu (Yello) requisition pages for the description."""

    NAME = "deutschebank"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__([ListingBoard(company="deutsche-bank", host="careers.db.com")], **kwargs)

    def list_cards(self, board: ListingBoard) -> list[Card]:
        cards: list[Card] = []
        first = 1
        while True:
            query = {
                "LanguageCode": "en",
                "SearchParameters": {"FirstItem": first, "CountItem": PAGE_SIZE, "MatchedObjectDescriptor": FIELDS},
                "SearchCriteria": [],
            }
            payload = self.get(API, params={"data": json.dumps(query)}).json()
            result = payload.get("SearchResult") or {}
            items = result.get("SearchResultItems") or []
            cards.extend(parse_items(items))
            first += PAGE_SIZE
            if not items or first > int(result.get("SearchResultCountAll") or 0) or first > 1000:
                break
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        if "recsolu.com" not in card.url:
            return {}
        return parse_detail(self.get(card.url).text)


def parse_items(items: list[dict[str, Any]]) -> list[Card]:
    cards: list[Card] = []
    for item in items:
        raw = item.get("MatchedObjectDescriptor") or {}
        if not raw.get("PositionID"):
            continue
        places = [p for p in raw.get("PositionLocation") or [] if isinstance(p, dict)]
        codes = tuple(dict.fromkeys(c for c in (normalize_country_code(p.get("CountryCode")) for p in places) if c))
        url = str(raw.get("PositionURI") or "")
        if url.startswith("/"):
            url = f"https://careers.db.com/professionals/search-roles/#/professional/job/{raw['PositionID']}"
        cards.append(
            Card(
                id=str(raw["PositionID"]),
                title=compact_text(raw.get("PositionTitle")),
                url=url,
                location="; ".join(
                    ", ".join(x for x in (compact_text(p.get("CityName")), p.get("CountryCode")) if x) for p in places
                ),
                country_codes=codes,
                posted_at=raw.get("PublicationStartDate"),
            )
        )
    return cards
