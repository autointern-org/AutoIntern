from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from adapters.base import compact_text, html_to_text
from adapters.listing import Card, ListingAdapter, ListingBoard, country_from_text


DOMAIN_ID_RE = re.compile(r"domainId\s*:\s*(\d+)|\"domain_id\"\s*:\s*\"(\d{3,})\"")


class ApplicantProAdapter(ListingAdapter):
    """ApplicantPro sites (<company>.applicantpro.com). The jobs page names
    the site's numeric domain id; /core/jobs/<id> lists openings as JSON and
    /core/jobs/<id>/<job>/job-details returns the description."""

    NAME = "applicantpro"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        domain_id = board.site or self._domain_id(board.host)
        payload = self.get(
            f"https://{board.host}/core/jobs/{domain_id}?getParams={quote(json.dumps({}))}",
            headers={"Accept": "application/json"},
        ).json()
        cards = parse_jobs(payload)
        for card in cards:
            card.extra["domain_id"] = domain_id
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        payload = self.get(
            f"https://{board.host}/core/jobs/{card.extra['domain_id']}/{card.id}/job-details",
            headers={"Accept": "application/json"},
        ).json()
        data = payload.get("data") or {}
        return {"description": html_to_text(data.get("description") or data.get("advertisingDescriptionHtml"))}

    def _domain_id(self, host: str) -> str:
        html = self.get(f"https://{host}/jobs/").text
        for match in DOMAIN_ID_RE.finditer(html):
            value = match.group(1) or match.group(2)
            if value and value != "4":
                return value
        raise RuntimeError(f"applicantpro {host}: no domain id on the jobs page")


def parse_jobs(payload: dict[str, Any]) -> list[Card]:
    cards: list[Card] = []
    for raw in (payload.get("data") or {}).get("jobs") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        place = ", ".join(compact_text(str(raw.get(k))) for k in ("city", "abbreviation") if raw.get(k))
        cards.append(
            Card(
                id=str(raw["id"]),
                title=compact_text(raw.get("title")),
                url=str(raw.get("jobUrl") or ""),
                location=place or compact_text(raw.get("jobLocation")),
                country_codes=country_from_text(raw.get("iso3")),
                posted_at=raw.get("startDateRef"),
            )
        )
    return cards
