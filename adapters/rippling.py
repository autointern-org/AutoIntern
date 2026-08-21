from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


PAGE_SIZE = 100
MAX_PAGES = 30
# Job descriptions cost one request each, so only intern-looking titles fetch detail.
CANDIDATE_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|co-?ops?|student|campus|apprentice)\b",
    re.IGNORECASE,
)


class RipplingAdapter:
    """Rippling ATS public board: /api/v2/board/{board}/jobs (paged, 0-based)
    plus /api/v2/board/{board}/jobs/{id} for the description."""

    LIST_API = "https://ats.rippling.com/api/v2/board/{board}/jobs"

    def __init__(
        self,
        boards: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        for board in self.boards:
            name = self.company_names.get(board, board)
            try:
                jobs.extend(self._fetch_board(board))
            except Exception as exc:
                print(f"[rippling] {name} fetch failed: {exc}")
                self.board_errors.append((name, str(exc)))
        return jobs

    def _fetch_board(self, board: str) -> list[Job]:
        base = self.LIST_API.format(board=board)
        candidates: dict[str, dict[str, Any]] = {}
        for page in range(MAX_PAGES):
            response = self.session.get(f"{base}?page={page}&pageSize={PAGE_SIZE}", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                break
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                job_id = str(raw.get("id") or "")
                if job_id and CANDIDATE_TITLE_RE.search(str(raw.get("name") or "")):
                    candidates.setdefault(job_id, raw)
            total_pages = payload.get("totalPages")
            if isinstance(total_pages, int):
                if page + 1 >= total_pages:
                    break
            elif len(items) < PAGE_SIZE:
                break
        jobs: list[Job] = []
        for job_id, raw in candidates.items():
            detail: dict[str, Any] = {}
            try:
                response = self.session.get(f"{base}/{job_id}", timeout=self.timeout)
                response.raise_for_status()
                detail = response.json() if isinstance(response.json(), dict) else {}
            except Exception as exc:
                print(f"[rippling] {board} detail {job_id} failed: {exc}")
            jobs.append(self._normalize(board, raw, detail))
        return jobs

    def _normalize(self, board: str, raw: dict[str, Any], detail: dict[str, Any]) -> Job:
        job_id = raw.get("id")
        codes, country_names, location_names = structured_locations(raw.get("locations") or detail.get("workLocations"))
        return Job(
            id=f"rippling:{board}:{job_id}",
            company=self.company_names.get(board, board),
            title=compact_text(str(raw.get("name") or detail.get("name") or "")),
            location="; ".join(location_names) if location_names else "Unspecified",
            url=compact_text(str(raw.get("url") or detail.get("url") or f"https://ats.rippling.com/{board}/jobs/{job_id}")),
            jd_text=html_to_text(_description_html(detail.get("description"))),
            posted_at=detail.get("createdOn"),
            country_codes=codes,
            country_names=country_names,
            location_names=location_names,
        )


def _description_html(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(str(part) for part in value.values() if isinstance(part, str))
    return ""


def structured_locations(value: Any) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(country_codes, country_names, location_names) from Rippling `locations[]`.

    Each entry: {"name": "San Francisco, CA", "country": "United States",
    "countryCode": "US", "state": ..., "city": ..., "workplaceType": "REMOTE"}.
    """
    codes: list[str] = []
    countries: list[str] = []
    names: list[str] = []
    for entry in value if isinstance(value, list) else []:
        if not isinstance(entry, dict):
            continue
        code = normalize_country_code(entry.get("countryCode"))
        if code:
            codes.append(code)
        country = compact_text(str(entry.get("country") or ""))
        if country:
            countries.append(country)
        name = compact_text(str(entry.get("name") or ""))
        if not name:
            name = ", ".join(compact_text(str(entry.get(key) or "")) for key in ("city", "state", "country") if entry.get(key))
        if name:
            names.append(name)
        if str(entry.get("workplaceType") or "").upper() == "REMOTE":
            names.append(f"Remote ({country})" if country else "Remote")
    return tuple(dict.fromkeys(codes)), tuple(dict.fromkeys(countries)), tuple(dict.fromkeys(names))
