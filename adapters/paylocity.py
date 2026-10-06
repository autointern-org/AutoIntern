from __future__ import annotations

import json
import re
from typing import Any

from adapters.base import compact_text
from adapters.listing import Card, ListingAdapter, ListingBoard, country_from_text, job_posting_ld


PAGE_DATA_RE = re.compile(r"window\.pageData\s*=\s*")


class PaylocityAdapter(ListingAdapter):
    """Paylocity Recruiting job boards. The board page embeds every open job
    as JSON (window.pageData.Jobs); each job page has a JobPosting ld+json.
    board.site is the company GUID from /Recruiting/Jobs/All/<guid>."""

    NAME = "paylocity"
    BOARD_WORKERS = 1
    DETAIL_PAUSE_SECONDS = 1.0
    BASE = "https://recruiting.paylocity.com/Recruiting/Jobs"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        html = self.get(f"{self.BASE}/All/{board.site}").text
        return parse_board(html, self.BASE)

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        return job_posting_ld(self.get(card.url).text)


def page_data(html: str) -> dict[str, Any]:
    match = PAGE_DATA_RE.search(html or "")
    if not match:
        raise RuntimeError("paylocity: no window.pageData on the board page")
    data, _ = json.JSONDecoder().raw_decode(html, match.end())
    return data if isinstance(data, dict) else {}


def parse_board(html: str, base: str) -> list[Card]:
    cards: list[Card] = []
    for raw in page_data(html).get("Jobs") or []:
        if not isinstance(raw, dict) or raw.get("IsInternal") or not raw.get("JobId"):
            continue
        place = raw.get("JobLocation") if isinstance(raw.get("JobLocation"), dict) else {}
        location = ", ".join(compact_text(str(place.get(k))) for k in ("City", "State", "Country") if place.get(k))
        cards.append(
            Card(
                id=str(raw["JobId"]),
                title=compact_text(raw.get("JobTitle")),
                url=f"{base}/Details/{raw['JobId']}",
                location=location or compact_text(raw.get("LocationName")),
                snippet=compact_text(raw.get("Description")),
                country_codes=country_from_text(place.get("Country")),
                posted_at=raw.get("PublishedDate"),
            )
        )
    return cards
