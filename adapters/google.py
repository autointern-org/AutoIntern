from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

import requests

from adapters.base import DEFAULT_USER_AGENT, Job, compact_text, html_to_text


RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results"
INTERN_EMPLOYMENT_TYPE = 4
RECORD_LEN = 21
COMPANY_INDEX = 7
EMPLOYMENT_INDEX = 11
MAX_PAGES = 10
CARD_HREF_RE = re.compile(
    r"/about/careers/applications/jobs/results/(\d+)-([a-z0-9-]+)",
    re.IGNORECASE,
)


class GoogleAdapter:
    API = f"{RESULTS_URL}?q=intern"

    def __init__(self, *, timeout: int = 30, session: Any | None = None) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            headers.setdefault("User-Agent", DEFAULT_USER_AGENT)

    def fetch(self) -> list[Job]:
        jobs_by_id: dict[str, Job] = {}
        total: int | None = None
        parsed_any = False
        for page in range(1, MAX_PAGES + 1):
            html, status = self._get_page(page)
            if status != 200:
                raise RuntimeError(f"Google careers returned HTTP {status} for page {page}")
            blob_jobs, page_total, blob_ok = _jobs_from_blob(html)
            if blob_ok:
                parsed_any = True
                if page_total is not None:
                    total = page_total
                for job in blob_jobs:
                    jobs_by_id[job.id] = job
            else:
                html_jobs = _jobs_from_html_cards(html)
                if html_jobs:
                    parsed_any = True
                    for job in html_jobs:
                        jobs_by_id[job.id] = job
                elif page == 1:
                    raise RuntimeError(
                        "Google careers returned HTTP 200 but no job records were parsed "
                        f"(page length {len(html)})"
                    )
            if total is not None and len(jobs_by_id) >= total:
                break
            if blob_ok and not blob_jobs:
                break
        if not parsed_any:
            raise RuntimeError("Google careers returned HTTP 200 but no job records were parsed")
        return list(jobs_by_id.values())

    def _get_page(self, page: int) -> tuple[str, int]:
        params = {"q": "intern"}
        if page > 1:
            params["page"] = str(page)
        response = self.session.get(f"{RESULTS_URL}?{urlencode(params)}", timeout=self.timeout)
        status = getattr(response, "status_code", 200)
        if status >= 400:
            return getattr(response, "text", "") or "", status
        return getattr(response, "text", "") or "", status


def _jobs_from_blob(html: str) -> tuple[list[Job], int | None, bool]:
    payload = _extract_ds1_data(html)
    if payload is None:
        return [], None, False
    records = payload[0] if payload else []
    if not isinstance(records, list):
        raise RuntimeError("Google ds:1 data[0] is not a job list")
    total = payload[2] if len(payload) > 2 and isinstance(payload[2], int) else None
    jobs: list[Job] = []
    for record in records:
        if not isinstance(record, list) or len(record) != RECORD_LEN or record[COMPANY_INDEX] != "Google":
            raise RuntimeError("Google ds:1 record failed shape check (len==21 and company Google)")
        if _employment_type(record[EMPLOYMENT_INDEX]) != INTERN_EMPLOYMENT_TYPE:
            continue
        jobs.append(_normalize_blob_record(record))
    return jobs, total, True


def _extract_ds1_data(html: str) -> list[Any] | None:
    marker = html.find("ds:1")
    if marker < 0:
        return None
    data_idx = html.find("data:", marker)
    if data_idx < 0:
        data_idx = html.find('"data"', marker)
    if data_idx < 0:
        return None
    start = html.find("[", data_idx)
    if start < 0:
        return None
    depth = 0
    for index, char in enumerate(html[start:], start):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                raw = html[start : index + 1]
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    try:
                        data = json.loads(raw.replace("'", '"').replace("None", "null"))
                    except json.JSONDecodeError:
                        return None
                return data if isinstance(data, list) else None
    return None


def _jobs_from_html_cards(html: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for match in CARD_HREF_RE.finditer(html):
        job_id = match.group(1)
        slug = match.group(2)
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append(
            Job(
                id=f"google:{job_id}",
                company="google",
                title=compact_text(slug.replace("-", " ")),
                location="Unspecified",
                url=f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{slug}",
                jd_text="",
                posted_at=None,
            )
        )
    return jobs


def _normalize_blob_record(record: list[Any]) -> Job:
    job_id = record[0]
    title = record[1]
    apply_url = record[2]
    location = _location_text(record[9]) or "Unspecified"
    jd_text = html_to_text(
        " ".join(
            _join_nested_text(part)
            for part in (record[3], record[4], record[10])
            if part
        )
    )
    posted = _posted_at(record[14]) or _posted_at(record[12]) or _posted_at(record[13])
    url = compact_text(str(apply_url or ""))
    if url and url.startswith("/"):
        url = f"https://www.google.com{url}"
    if not url:
        url = f"https://www.google.com/about/careers/applications/jobs/results/{job_id}/"
    return Job(
        id=f"google:{job_id}",
        company="google",
        title=compact_text(str(title or "")),
        location=location,
        url=url,
        jd_text=jd_text,
        posted_at=posted,
    )


def _employment_type(value: Any) -> Any:
    if isinstance(value, list) and value:
        return _employment_type(value[0])
    return value


def _posted_at(value: Any) -> str | None:
    if isinstance(value, list) and value:
        return _posted_at(value[0])
    if isinstance(value, (int, float)) and value:
        return str(int(value))
    return None


def _location_text(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, list):
        if value and isinstance(value[0], str):
            display = compact_text(value[0])
            extras = [compact_text(str(item)) for item in value[2:6] if item]
            return display or ", ".join(part for part in extras if part)
        parts = [_location_text(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        return compact_text(value.get("display") or value.get("name") or value.get("city"))
    return ""


def _join_nested_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_join_nested_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_join_nested_text(item) for item in value.values())
    return ""
