from __future__ import annotations

from typing import Any

from adapters.base import compact_text, html_to_text
from adapters.listing import Card, ListingAdapter, ListingBoard, country_from_text


class BambooHRAdapter(ListingAdapter):
    """BambooHR career sites: /careers/list (JSON, every opening) and
    /careers/<id>/detail (JSON with description and datePosted)."""

    NAME = "bamboohr"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        payload = self.get(f"https://{board.host}/careers/list", headers={"Accept": "application/json"}).json()
        return parse_list(payload, board.host)

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        payload = self.get(f"https://{board.host}/careers/{card.id}/detail", headers={"Accept": "application/json"}).json()
        opening = (payload.get("result") or {}).get("jobOpening") or {}
        location = opening.get("location") if isinstance(opening.get("location"), dict) else {}
        country = location.get("addressCountry") or (opening.get("atsLocation") or {}).get("country")
        return {
            "description": html_to_text(opening.get("description")),
            "location": _place(location, country),
            "country_codes": country_from_text(country),
            "posted_at": opening.get("datePosted"),
        }


def _place(location: dict[str, Any], country: object = None) -> str:
    return ", ".join(compact_text(str(v)) for v in (location.get("city"), location.get("state"), country) if v)


def parse_list(payload: dict[str, Any], host: str) -> list[Card]:
    cards: list[Card] = []
    for raw in payload.get("result") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        ats = raw.get("atsLocation") if isinstance(raw.get("atsLocation"), dict) else {}
        place = _place(location) or _place(ats)
        if str(raw.get("isRemote") or "").lower() in ("1", "true"):
            place = f"{place} (Remote)" if place else "Remote"
        cards.append(
            Card(
                id=str(raw["id"]),
                title=compact_text(raw.get("jobOpeningName")),
                url=f"https://{host}/careers/{raw['id']}",
                location=place,
                country_codes=country_from_text(ats.get("country")),
            )
        )
    return cards
