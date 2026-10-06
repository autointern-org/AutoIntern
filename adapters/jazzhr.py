from __future__ import annotations

import re
from typing import Any

from adapters.listing import Card, ListingAdapter, ListingBoard, job_posting_ld, strip_tags


LINK_RE = re.compile(r'<a href="(https?://[^"]+/apply/([A-Za-z0-9]+)/[^"]*)"[^>]*>(.*?)</a>', re.S)
LOCATION_RE = re.compile(r"<i class=['\"]fa fa-map-marker['\"]></i>(.*?)</li>", re.S)


class JazzHRAdapter(ListingAdapter):
    """JazzHR boards (<company>.applytojob.com/apply): one HTML list of every
    opening; job pages carry a JobPosting ld+json."""

    NAME = "jazzhr"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        html = self.get(f"https://{board.host}/apply").text
        return parse_list(html)

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        return job_posting_ld(self.get(card.url).text)


def parse_list(html: str) -> list[Card]:
    cards: list[Card] = []
    for block in re.split(r'<li class="list-group-item">', html or "")[1:]:
        link = LINK_RE.search(block)
        if not link:
            continue
        url, job_id, title = link.groups()
        location = LOCATION_RE.search(block)
        cards.append(
            Card(
                id=job_id,
                title=strip_tags(title),
                url=url.replace("http://", "https://", 1),
                location=strip_tags(location.group(1)) if location else "",
            )
        )
    return cards
