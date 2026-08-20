from __future__ import annotations

from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text, posted_at_value
from core.http import new_session


API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
LIMIT = 20
MAX_PAGES = 20
BODY = {"recruitment_id_list": ["202"]}


class TikTokAdapter:
    API = API

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        for _ in range(MAX_PAGES):
            url = f"{API}?keyword=intern&limit={LIMIT}&offset={offset}"
            response = self.session.post(url, json=BODY, timeout=self.timeout)
            response.raise_for_status()
            rows = _job_rows(response.json())
            if not rows:
                break
            for raw in rows:
                jobs.append(self._normalize(raw))
            if len(rows) < LIMIT:
                break
            offset += LIMIT
        return jobs

    def _normalize(self, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id") or raw.get("job_id") or raw.get("jobId")
        title = raw.get("title") or raw.get("job_title") or raw.get("name")
        location = _location(raw)
        url = raw.get("url") or raw.get("job_url") or raw.get("apply_url")
        if not url and job_id:
            url = f"https://lifeattiktok.com/search/{job_id}"
        hot = raw.get("job_hot_info") if isinstance(raw.get("job_hot_info"), dict) else {}
        description = raw.get("description") or raw.get("job_description") or hot.get("description") or ""
        posted = raw.get("publish_time") or raw.get("create_time") or raw.get("posted_at")
        return Job(
            id=f"tiktok:{job_id}",
            company="tiktok",
            title=compact_text(str(title or "")),
            location=location or "Unspecified",
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description or "")),
            posted_at=posted_at_value(posted) if isinstance(posted, (int, float)) else (str(posted) if posted else None),
        )


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
