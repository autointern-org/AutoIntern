from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


class WorkableAdapter:
    """Workable public widget API: one GET per account returns every published job
    with its description when details=true."""

    API = "https://www.workable.com/api/accounts/{slug}?details=true"

    def __init__(
        self,
        account_slugs: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.account_slugs = list(account_slugs)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        for slug in self.account_slugs:
            name = self.company_names.get(slug, slug)
            try:
                response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                print(f"[workable] {name} fetch failed: {exc}")
                self.board_errors.append((name, str(exc)))
                continue
            rows = payload.get("jobs") if isinstance(payload, dict) else payload
            for raw in rows if isinstance(rows, list) else []:
                if isinstance(raw, dict):
                    jobs.append(self._normalize(slug, raw))
        return jobs

    def _normalize(self, slug: str, raw: dict[str, Any]) -> Job:
        job_id = raw.get("shortcode") or raw.get("id")
        codes, country_names, location_names = structured_locations(raw)
        description = raw.get("description") or ""
        requirements = raw.get("requirements") or ""
        return Job(
            id=f"workable:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(str(raw.get("title") or "")),
            location=location_names[0] if location_names else "Unspecified",
            url=compact_text(str(raw.get("url") or raw.get("shortlink") or raw.get("application_url") or "")),
            jd_text=html_to_text(f"{description}\n{requirements}"),
            posted_at=raw.get("published_on") or raw.get("created_at"),
            country_codes=codes,
            country_names=country_names,
            location_names=location_names,
        )


def structured_locations(raw: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(country_codes, country_names, location_names) from a Workable job.

    Top-level `country`/`city`/`state` describe the primary office;
    `locations[]` carry `countryCode` ("FR") and `country` ("France").
    """
    codes: list[str] = []
    countries: list[str] = []
    names: list[str] = []
    primary = ", ".join(compact_text(str(raw.get(key) or "")) for key in ("city", "state", "country") if raw.get(key))
    if primary:
        names.append(primary)
    if raw.get("country"):
        countries.append(compact_text(str(raw["country"])))
    locations = raw.get("locations")
    for entry in locations if isinstance(locations, list) else []:
        if not isinstance(entry, dict):
            continue
        code = normalize_country_code(entry.get("countryCode"))
        if code:
            codes.append(code)
        if entry.get("country"):
            countries.append(compact_text(str(entry["country"])))
        text = ", ".join(compact_text(str(entry.get(key) or "")) for key in ("city", "region", "country") if entry.get(key))
        if text:
            names.append(text)
    return tuple(dict.fromkeys(codes)), tuple(dict.fromkeys(countries)), tuple(dict.fromkeys(names))
