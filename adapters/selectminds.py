from __future__ import annotations

import re
from typing import Any

from adapters.listing import Card, ListingAdapter, ListingBoard, country_from_text, strip_tags


MAX_PAGES = 30
TOKEN_RE = re.compile(r'id\s*=\s*"tsstoken"\s*value\s*="([^"]+)"')
ROW_RE = re.compile(r'<div id="job_list_(\d+)" class="job_list_row(.*?)<!-- jlr_content -->', re.S)
LINK_RE = re.compile(r'<a href="([^"]+)" class="job_link[^"]*">(.*?)</a>', re.S)
LOCATION_RE = re.compile(r'<span class="location">(.*?)</span>', re.S)
SNIPPET_RE = re.compile(r'<p class="jlr_description">(.*?)</p>', re.S)
DESCRIPTION_RE = re.compile(r'<div class="job_description"[^>]*>(.*?)</div>\s*(?:<!--|<div class="(?:job_|jdp_|ref_))', re.S)


class SelectMindsAdapter(ListingAdapter):
    """Oracle Taleo Social Sourcing (SelectMinds) sites: a keyword search is
    created with the page's tsstoken and read 10 rows per page; job pages
    hold the description. board.site is the site path (e.g. "ETP")."""

    NAME = "selectminds"

    def list_cards(self, board: ListingBoard) -> list[Card]:
        home = self.get(f"https://{board.host}/{board.site}").text
        token = TOKEN_RE.search(home)
        if not token:
            raise RuntimeError(f"selectminds {board.host}: no tsstoken on the home page")
        response = self.session.post(
            f"https://{board.host}/ajax/jobs/search/create",
            data={"keywords": board.search},
            headers={"X-Requested-With": "XMLHttpRequest", "tss-token": token.group(1), "Referer": f"https://{board.host}/{board.site}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        search_id = ((response.json() or {}).get("Result") or {}).get("JobSearch.id")
        if not search_id:
            raise RuntimeError(f"selectminds {board.host}: search was not created")
        cards: list[Card] = []
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            html = self.get(f"https://{board.host}/{board.site}/jobs/search/{search_id}/page{page}").text
            fresh = [c for c in parse_rows(html) if c.id not in seen]
            if not fresh:
                break
            seen.update(c.id for c in fresh)
            cards.extend(fresh)
        return cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        match = DESCRIPTION_RE.search(self.get(card.url).text)
        return {"description": strip_tags(match.group(1))} if match else {}


def parse_rows(html: str) -> list[Card]:
    cards: list[Card] = []
    for job_id, block in ROW_RE.findall(html or ""):
        link = LINK_RE.search(block)
        if not link:
            continue
        location = strip_tags(LOCATION_RE.search(block).group(1)) if LOCATION_RE.search(block) else ""
        first = location.split(" and ", 1)[0]
        snippet = SNIPPET_RE.search(block)
        cards.append(
            Card(
                id=job_id,
                title=strip_tags(link.group(2)),
                url=link.group(1),
                location=location,
                snippet=strip_tags(snippet.group(1)) if snippet else "",
                country_codes=country_from_text(first.rsplit(",", 1)[-1]) if "," in first else (),
            )
        )
    return cards
