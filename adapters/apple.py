from __future__ import annotations

from math import ceil
from typing import Any

import requests

from adapters.base import DEFAULT_USER_AGENT, Job, compact_text, html_to_text
from core.http import new_session


SEARCH_URL = "https://jobs.apple.com/api/v1/search"
CSRF_URL = "https://jobs.apple.com/api/v1/csrfToken"
PAGE_SIZE = 20
MAX_PAGES = 30

_FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
_COVERAGE_FILTERS: dict[str, Any] = {"locations": ["postLocation-USA"]}
_PRECISION_FILTERS: dict[str, Any] = {
    "locations": ["postLocation-USA"],
    "teams": [{"team": "teamsAndSubTeams-STDNT", "subTeam": "subTeam-INTRN"}],
}


class AppleAdapter:
    API = SEARCH_URL

    def __init__(self, *, timeout: int = 60, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        headers = self._post_headers()
        jobs: list[Job] = []
        seen: set[str] = set()
        for filters in (_COVERAGE_FILTERS, _PRECISION_FILTERS):
            for raw in self._search(filters, headers):
                job = self._normalize(raw)
                if job.id in seen:
                    continue
                seen.add(job.id)
                jobs.append(job)
        return jobs

    def _post_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://jobs.apple.com",
            "Referer": "https://jobs.apple.com/en-us/search",
            "User-Agent": DEFAULT_USER_AGENT,
        }
        try:
            response = retry_once(lambda: self.session.get(CSRF_URL, timeout=self.timeout))
            token = _header(response, "x-apple-csrf-token") or _header(response, "X-Apple-CSRF-Token")
            if token:
                headers["X-Apple-CSRF-Token"] = token
        except Exception:
            pass
        return headers

    def _search(self, filters: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        while page <= min(total_pages, MAX_PAGES):
            body = {
                "query": "intern",
                "filters": filters,
                "page": page,
                "locale": "en-us",
                "sort": "",
                "format": _FORMAT,
            }
            response = retry_once(lambda: self.session.post(SEARCH_URL, json=body, headers=headers, timeout=self.timeout))
            data = _parse_search(response)
            envelope = data["res"] if isinstance(data.get("res"), dict) else data
            results = envelope.get("searchResults") or []
            if page == 1:
                total_records = _as_int(envelope.get("totalRecords"))
                total_pages = min(MAX_PAGES, max(1, ceil(total_records / PAGE_SIZE))) if total_records else 1
            for raw in results:
                if isinstance(raw, dict):
                    rows.append(raw)
            if not results:
                break
            page += 1
        return rows

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("reqId")
        title = raw.get("postingTitle") or raw.get("title")
        slug = raw.get("transformedPostingTitle") or ""
        team = raw.get("team") if isinstance(raw.get("team"), dict) else {}
        team_code = team.get("teamCode") or ""
        url = f"https://jobs.apple.com/en-us/details/{job_id}"
        if slug:
            url += f"/{slug}"
        if team_code:
            url += f"?team={team_code}"
        return Job(
            id=f"apple:{job_id}",
            company="apple",
            title=compact_text(str(title or "")),
            location=_location(raw) or "Unspecified",
            url=compact_text(url),
            jd_text=html_to_text(str(raw.get("jobSummary") or raw.get("description") or "")),
            posted_at=raw.get("postDateInGMT") or raw.get("postDate"),
        )


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get(name)
    except Exception:
        return None
    return str(value) if value else None


def _parse_search(response: Any) -> dict[str, Any]:
    status = getattr(response, "status_code", 200)
    headers = getattr(response, "headers", None) or {}
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "")
    text = getattr(response, "text", "") or ""
    data: Any = None
    try:
        data = response.json()
    except Exception:
        data = None
    has_shape = isinstance(data, dict) and ("res" in data or "searchResults" in data)
    if "json" not in content_type.lower() or not has_shape:
        raise RuntimeError(f"{status} {text[:200]}")
    return data


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _location(raw: dict[str, Any]) -> str:
    locations = raw.get("locations") or []
    if not isinstance(locations, list):
        locations = [locations] if locations else []
    parts: list[str] = []
    for item in locations:
        if isinstance(item, str):
            text = compact_text(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if name:
                text = compact_text(str(name))
            else:
                text = ", ".join(
                    compact_text(str(item[key]))
                    for key in ("city", "stateProvince", "countryName")
                    if item.get(key)
                )
        else:
            text = ""
        if text:
            parts.append(text)
    return "; ".join(parts)


def retry_once(request: Any) -> Any:
    """jobs.apple.com occasionally stalls past the read timeout; one retry
    is enough in practice and keeps the whole board from being skipped."""
    try:
        return request()
    except (requests.Timeout, requests.ConnectionError) as exc:
        print(f"[apple] retrying after {exc.__class__.__name__}")
        return request()
