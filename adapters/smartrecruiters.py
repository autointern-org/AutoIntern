from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


LIMIT = 100
MAX_PAGES = 30
# The list endpoint's `q` parameter matches the whole posting, not the title,
# so titles are pre-filtered here before fetching each job's detail.
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)


class SmartRecruitersAdapter:
    LIST_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"

    def __init__(
        self,
        company_slugs: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.company_slugs = list(company_slugs)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        for slug in self.company_slugs:
            name = self.company_names.get(slug, slug)
            try:
                jobs.extend(self._fetch_company(slug))
            except Exception as exc:
                print(f"[smartrecruiters] {name} fetch failed: {exc}")
                self.board_errors.append((name, str(exc)))
        return jobs

    def _fetch_company(self, slug: str) -> list[Job]:
        base = self.LIST_API.format(slug=slug)
        candidates: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            response = self.session.get(f"{base}?limit={LIMIT}&offset={offset}", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                break
            for raw in rows:
                if isinstance(raw, dict) and CANDIDATE_TITLE_RE.search(str(raw.get("name") or "")):
                    candidates.append(raw)
            offset += len(rows)
            total = payload.get("totalFound")
            if isinstance(total, int) and offset >= total:
                break
            if len(rows) < LIMIT:
                break
        jobs: list[Job] = []
        for raw in candidates:
            detail: dict[str, Any] = {}
            try:
                response = self.session.get(f"{base}/{raw.get('id')}", timeout=self.timeout)
                response.raise_for_status()
                detail = response.json() if isinstance(response.json(), dict) else {}
            except Exception as exc:
                print(f"[smartrecruiters] {slug} detail {raw.get('id')} failed: {exc}")
            jobs.append(self._normalize(slug, raw, detail))
        return jobs

    def _normalize(self, slug: str, raw: dict[str, Any], detail: dict[str, Any]) -> Job:
        job_id = raw.get("id")
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        codes, location_names = structured_locations(location)
        sections = ((detail.get("jobAd") or {}).get("sections") or {}) if isinstance(detail.get("jobAd"), dict) else {}
        jd_parts = [
            str((sections.get(key) or {}).get("text") or "")
            for key in ("jobDescription", "qualifications", "additionalInformation")
            if isinstance(sections.get(key), dict)
        ]
        url = detail.get("postingUrl") or raw.get("ref") or f"https://jobs.smartrecruiters.com/{slug}/{job_id}"
        return Job(
            id=f"smartrecruiters:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(str(raw.get("name") or "")),
            location=location_names[0] if location_names else "Unspecified",
            url=compact_text(str(url)),
            jd_text=html_to_text("\n".join(jd_parts)),
            posted_at=raw.get("releasedDate"),
            country_codes=codes,
            location_names=location_names,
        )


def structured_locations(location: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(country_codes, location_names) from a SmartRecruiters posting location.

    `country` is a lowercase ISO code ("us"); `fullLocation` is text
    ("San Diego, California, United States"); `remote` is a boolean.
    """
    codes: list[str] = []
    names: list[str] = []
    code = normalize_country_code(location.get("country"))
    if code:
        codes.append(code)
    full = compact_text(str(location.get("fullLocation") or ""))
    if not full:
        full = ", ".join(compact_text(str(location.get(key) or "")) for key in ("city", "region") if location.get(key))
    if full:
        names.append(full)
    if location.get("remote") is True:
        names.append("Remote")
    return tuple(codes), tuple(dict.fromkeys(names))
