from __future__ import annotations

from typing import Any

from adapters.base import compact_text, html_to_text
from adapters.listing import Card, ListingAdapter, ListingBoard


class PinpointAdapter(ListingAdapter):
    """Pinpoint career sites: /postings.json lists every opening with its
    full description, so no job pages are read."""

    NAME = "pinpoint"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        payload = self.get(f"https://{board.host}/postings.json", headers={"Accept": "application/json"}).json()
        return parse_postings(payload)


def parse_postings(payload: dict[str, Any]) -> list[Card]:
    cards: list[Card] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        place = ", ".join(compact_text(str(location.get(k))) for k in ("city", "province", "name") if location.get(k))
        text = " ".join(
            html_to_text(str(raw.get(key) or ""))
            for key in ("description", "key_responsibilities", "skills_knowledge_expertise")
        )
        cards.append(
            Card(
                id=str(raw["id"]),
                title=compact_text(raw.get("title")),
                url=str(raw.get("url") or ""),
                location=place,
                snippet=compact_text(text),
                needs_detail=False,
            )
        )
    return cards
