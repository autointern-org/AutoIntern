from __future__ import annotations

import json
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


CAREERS_URL = "https://www.deshaw.com/careers"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class DEShawAdapter:
    """deshaw.com/careers is a Next.js page whose __NEXT_DATA__ blob already
    contains every posting with its description, so one request covers all."""

    def __init__(self, *, timeout: int = 30, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self.session = session or new_session()

    def fetch(self) -> list[Job]:
        response = self.session.get(CAREERS_URL, timeout=self.timeout)
        response.raise_for_status()
        match = NEXT_DATA_RE.search(response.text or "")
        if not match:
            raise RuntimeError("deshaw careers page has no __NEXT_DATA__ payload")
        props = (json.loads(match.group(1)).get("props") or {}).get("pageProps") or {}
        jobs: list[Job] = []
        seen: set[str] = set()
        for key in ("internships", "regularJobs"):
            for item in props.get(key) or []:
                job = self._normalize(item)
                if job and job.id not in seen:
                    seen.add(job.id)
                    jobs.append(job)
        if not jobs:
            raise RuntimeError("deshaw careers payload listed no jobs")
        return jobs

    def _normalize(self, item: Any) -> Job | None:
        if not isinstance(item, dict):
            return None
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        if str(data.get("activeOnJobsListing", "True")).lower() == "false":
            return None
        job_id = str(data.get("id") or item.get("id") or "")
        title = compact_text(str(data.get("displayName") or item.get("displayName") or ""))
        if not job_id or not title:
            return None
        offices = item.get("office") if isinstance(item.get("office"), list) else data.get("office") or []
        location = "; ".join(
            compact_text(str(o.get("name") or o.get("abbreviation") or "")) for o in offices if isinstance(o, dict)
        )
        description = data.get("jobDescription") if isinstance(data.get("jobDescription"), dict) else {}
        jd = "\n".join(str(v) for v in description.values() if isinstance(v, str))
        slug = str(data.get("jobUrl") or "")
        return Job(
            id=f"deshaw:{job_id}",
            company="de-shaw",
            title=title,
            location=location or "Unspecified",
            url=f"https://www.deshaw.com/careers/{slug}" if slug else CAREERS_URL,
            jd_text=html_to_text(jd),
            posted_at=None,
        )
