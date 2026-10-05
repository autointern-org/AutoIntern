from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, posted_at_value
from core.http import new_session


API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
LIMIT = 100
MAX_PAGES = 20
RECRUITMENT_IDS = ["202"]
HEADERS = {
    "Content-Type": "application/json",
    "accept-language": "en",
    "origin": "https://lifeattiktok.com",
    "Referer": "https://lifeattiktok.com/search",
    "website-path": "tiktok",
}


class TikTokAdapter:
    """TikTok's careers search (ByteDance's supplier job API). Recruitment
    type 202 is internships; ByteDanceAdapter reads the same API family for
    ByteDance's own postings."""

    API = API
    HEADERS = HEADERS
    COMPANY = "tiktok"
    JOB_URL = "https://lifeattiktok.com/search/{job_id}"

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()
        self.source_totals: dict[str, tuple[int, int]] = {}

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        for _ in range(MAX_PAGES):
            body = {
                "keyword": "",
                "limit": LIMIT,
                "offset": offset,
                "recruitment_id_list": RECRUITMENT_IDS,
            }
            response = self.session.post(self.API, json=body, headers=self.HEADERS, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows = _job_rows(payload)
            count = (payload.get("data") or {}).get("count") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else None
            if not rows:
                break
            for raw in rows:
                jobs.append(self._normalize(raw))
            if isinstance(count, int) and count:
                self.source_totals[self.COMPANY] = (count, len(jobs))
            if len(rows) < LIMIT:
                break
            offset += LIMIT
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("job_id") or raw.get("jobId")
        title = raw.get("title") or raw.get("job_title") or raw.get("name")
        location, countries = city_hierarchy(raw.get("city_info"))
        if not location:
            location = _location(raw)
        url = raw.get("url") or raw.get("job_url") or raw.get("apply_url")
        if not url and job_id:
            url = self.JOB_URL.format(job_id=job_id)
        hot = raw.get("job_hot_info") if isinstance(raw.get("job_hot_info"), dict) else {}
        description = raw.get("description") or raw.get("job_description") or hot.get("description") or ""
        requirement = raw.get("requirement") or ""
        posted = raw.get("publish_time") or raw.get("create_time") or raw.get("posted_at")
        return Job(
            id=f"{self.COMPANY}:{job_id}",
            company=self.COMPANY,
            title=compact_text(str(title or "")),
            location=location or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(f"{description}\n{requirement}"),
            posted_at=posted_at_value(posted) if isinstance(posted, (int, float)) else (str(posted) if posted else None),
            country_names=countries,
            location_names=(location,) if location else (),
        )


class ByteDanceAdapter(TikTokAdapter):
    API = "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts"
    HEADERS = {
        "Content-Type": "application/json",
        "accept-language": "en",
        "website-path": "en",
        "origin": "https://joinbytedance.com",
        "Referer": "https://joinbytedance.com/",
    }
    COMPANY = "bytedance"
    JOB_URL = "https://jobs.bytedance.com/en/position/{job_id}/detail"


def city_hierarchy(value: Any) -> tuple[str, tuple[str, ...]]:
    """("Jakarta, Jakarta Raya, Indonesia", ("Indonesia",)) from the nested
    city_info (city -> state -> country), using English names."""
    names: list[str] = []
    country = ""
    node = value if isinstance(value, dict) else None
    depth = 0
    while isinstance(node, dict) and depth < 6:
        name = compact_text(str(node.get("en_name") or node.get("i18n_name") or ""))
        if name:
            names.append(name)
            if node.get("location_type") == 1 or not isinstance(node.get("parent"), dict):
                country = name
        node = node.get("parent")
        depth += 1
    return ", ".join(names), ((country,) if country else ())


def _job_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("job_post_list", "jobs", "list", "job_posts"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _location(raw: dict[str, Any]) -> str:
    city_info = raw.get("city_info") or raw.get("location")
    if isinstance(city_info, str):
        return compact_text(city_info)
    if isinstance(city_info, dict):
        parts = [
            city_info.get("name")
            or city_info.get("city_name")
            or city_info.get("city"),
            city_info.get("country") or city_info.get("country_name"),
        ]
        return compact_text(", ".join(str(part) for part in parts if part))
    return compact_text(str(raw.get("city_name") or raw.get("location_name") or ""))
