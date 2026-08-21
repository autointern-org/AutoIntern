from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, normalize_country_code
from core.http import new_session


LIMIT = 25
MAX_PAGES = 20


@dataclass
class OracleBoard:
    company: str
    host: str
    site_number: str


class OracleAdapter:
    def __init__(
        self,
        boards: Iterable[OracleBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.boards:
            jobs.extend(self._fetch_board(board))
        return jobs

    def _fetch_board(self, board: OracleBoard) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        for _ in range(MAX_PAGES):
            url = _requisition_url(board.host, board.site_number, offset)
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            block = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
            rows = block.get("requisitionList") or []
            total = _as_int(block.get("TotalJobsCount"))
            if not isinstance(rows, list) or not rows:
                break
            for raw in rows:
                if isinstance(raw, dict):
                    jobs.append(self._normalize(board, raw))
            offset += LIMIT
            if len(rows) < LIMIT or (total and offset >= total):
                break
        return jobs

    def _normalize(self, board: OracleBoard, raw: dict[str, Any]) -> Job:
        job_id = raw.get("Id") or raw.get("id")
        url = (
            raw.get("Url")
            or raw.get("jobUrl")
            or raw.get("ExternalJobUrl")
            or raw.get("AppliedJobUrl")
        )
        if not url:
            url = f"https://careers.oracle.com/en/sites/jobsearch/job/{job_id}"
        description = raw.get("JobDescription") or raw.get("ShortDescription") or raw.get("Description") or ""
        country_codes, location_names = structured_locations(raw)
        return Job(
            id=f"oracle:{board.company}:{job_id}",
            company=board.company,
            title=compact_text(raw.get("Title") or raw.get("title")),
            location=compact_text(str(raw.get("PrimaryLocation") or raw.get("location") or "Unspecified")),
            url=compact_text(str(url)),
            jd_text=html_to_text(str(description)),
            posted_at=raw.get("PostedDate") or raw.get("postedDate"),
            country_codes=country_codes,
            location_names=location_names,
        )


def structured_locations(raw: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(country_codes, location_names) from an Oracle Recruiting requisition.

    PrimaryLocationCountry is an ISO code ("US"); PrimaryLocation is text
    ("Chicago, IL, United States"). secondaryLocations[] carry CountryCode
    and Name ("Brazil") for additional offices.
    """
    codes: list[str] = []
    names: list[str] = []
    primary = normalize_country_code(raw.get("PrimaryLocationCountry"))
    if primary:
        codes.append(primary)
    primary_text = compact_text(str(raw.get("PrimaryLocation") or ""))
    if primary_text:
        names.append(primary_text)
    secondary = raw.get("secondaryLocations")
    for entry in secondary if isinstance(secondary, list) else []:
        if not isinstance(entry, dict):
            continue
        code = normalize_country_code(entry.get("CountryCode"))
        if code:
            codes.append(code)
        name = compact_text(str(entry.get("Name") or ""))
        if name:
            names.append(name)
    return tuple(dict.fromkeys(codes)), tuple(dict.fromkeys(names))


def _requisition_url(host: str, site_number: str, offset: int) -> str:
    finder = (
        f"findReqs;siteNumber={site_number},keyword=intern,limit={LIMIT}"
        f"{f',offset={offset}' if offset else ''}"
        ",sortBy=POSTING_DATES_DESC"
    )
    return (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true&expand=requisitionList.secondaryLocations&finder={finder}"
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
