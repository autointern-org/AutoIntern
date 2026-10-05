from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text
from core.http import new_session


API = "https://api-higher.gs.com/gateway/api/v1/graphql"
ROLE_URL = "https://higher.gs.com/roles/{source_id}?type=students"
PAGE_SIZE = 20
MAX_PAGES = 40
QUERY = """
query GetCampusRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId
      corporateTitle
      jobTitle
      locations { primary state country city }
      status
      division
      externalSource { sourceId }
      startDate
    }
  }
}
"""


class GoldmanAdapter:
    """Goldman Sachs campus roles from higher.gs.com's GraphQL API (the
    query the site's own results page runs, experiences=CAMPUS)."""

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()
        self.source_totals: dict[str, tuple[int, int]] = {}

    def fetch(self) -> list[Job]:
        self.source_totals = {}
        jobs: list[Job] = []
        total = 0
        for page in range(MAX_PAGES):
            data = self._query(page)
            search = (data.get("data") or {}).get("roleSearch") or {}
            items = search.get("items") or []
            total = int(search.get("totalCount") or total or 0)
            jobs.extend(normalize(item) for item in items if isinstance(item, dict))
            if len(items) < PAGE_SIZE or (total and len(jobs) >= total):
                break
        if not jobs and not total:
            raise RuntimeError("higher.gs.com campus search returned no roles")
        if total:
            self.source_totals["goldman-sachs"] = (total, len(jobs))
        return jobs

    def _query(self, page: int) -> dict[str, Any]:
        body = {
            "operationName": "GetCampusRoles",
            "query": QUERY,
            "variables": {
                "searchQueryInput": {
                    "page": {"pageSize": PAGE_SIZE, "pageNumber": page},
                    "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                    "filters": [],
                    "experiences": ["CAMPUS"],
                    "searchTerm": "",
                }
            },
        }
        response = self.session.post(
            API,
            json=body,
            headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"higher.gs.com graphql error: {str(payload['errors'])[:200]}")
        return payload


def normalize(item: dict[str, Any]) -> Job:
    title = compact_text(str(item.get("jobTitle") or ""))
    division = compact_text(str(item.get("division") or ""))
    segments = [part.strip().lower() for part in title.split("|")]
    # GS names its software engineering division "Engineering"; the title
    # filter only recognizes software/SWE wording, so say it explicitly.
    if division.lower() == "engineering division" or "engineering" in segments:
        title = f"{title} (Engineering division: software & strats)"
    locations = [loc for loc in item.get("locations") or [] if isinstance(loc, dict)]
    names = [
        ", ".join(str(loc.get(key)) for key in ("city", "state", "country") if loc.get(key))
        for loc in locations
    ]
    countries = tuple(dict.fromkeys(str(loc["country"]) for loc in locations if loc.get("country")))
    source_id = str((item.get("externalSource") or {}).get("sourceId") or item.get("roleId") or "")
    return Job(
        id=f"goldman:{item.get('roleId') or source_id}",
        company="goldman-sachs",
        title=title,
        location="; ".join(n for n in names if n) or "Unspecified",
        url=ROLE_URL.format(source_id=source_id),
        jd_text=division,
        posted_at=None,
        country_names=countries,
        location_names=tuple(n for n in names if n),
    )
