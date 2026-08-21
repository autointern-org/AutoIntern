from __future__ import annotations

import json
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


SITEMAP_URL = "https://www.metacareers.com/jobsearch/sitemap.xml"
DETAIL_URL = "https://www.metacareers.com/profile/job_details/{id}/"
LOC_RE = re.compile(r"<loc>https://www\.metacareers\.com/profile/job_details/(\d+)/</loc>")
JSONLD_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>([\s\S]*?)</script>', re.I)
MAX_DETAILS_PER_RUN = 100


class MetaAdapter:
    """Meta has no public job-search API. The sitemap lists every job ID
    (~900) but not titles, so each ID needs one detail-page fetch to learn
    whether it is an internship. The pipeline persists `checked_ids` and
    `intern_ids` between runs so only new IDs (and known interns, to keep
    them live) are fetched each run."""

    def __init__(
        self,
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
        max_details: int = MAX_DETAILS_PER_RUN,
        known_ids: set[str] | None = None,
        intern_ids: set[str] | None = None,
    ) -> None:
        self.timeout = timeout
        # Meta answers 400 to browser-like user agents on the sitemap; the
        # default python-requests agent is the one that works.
        self.session = session or requests.Session()
        self.max_details = max_details
        self.known_ids = set(known_ids or set())
        self.intern_ids = set(intern_ids or set())
        self.checked_ids: set[str] = set()
        self.backlog = 0

    def fetch(self) -> list[Job]:
        response = self.session.get(SITEMAP_URL, timeout=self.timeout)
        response.raise_for_status()
        ids = LOC_RE.findall(response.text or "")
        if not ids:
            raise RuntimeError("Meta sitemap returned no job IDs")
        live = set(ids)
        known_live = self.known_ids & live
        interns_live = [job_id for job_id in ids if job_id in self.intern_ids]
        new_ids = [job_id for job_id in ids if job_id not in self.known_ids]
        self.backlog = max(0, len(new_ids) - self.max_details)
        if self.backlog:
            print(f"[meta] {len(new_ids)} unchecked ids; checking {self.max_details} this run")
        to_fetch = interns_live + new_ids[: self.max_details]
        jobs: list[Job] = []
        checked = set(known_live)
        for job_id in to_fetch:
            detail = self.session.get(DETAIL_URL.format(id=job_id), timeout=self.timeout)
            if detail.status_code != 200:
                continue
            checked.add(job_id)
            job = _job_from_detail(job_id, detail.text or "")
            if job:
                jobs.append(job)
        self.checked_ids = checked
        self.intern_ids = {job.id.split(":", 1)[1] for job in jobs}
        return jobs


def _job_from_detail(job_id: str, html: str) -> Job | None:
    match = JSONLD_RE.search(html)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if payload.get("employmentType") != "Internship":
        return None
    locations = payload.get("jobLocation") or []
    return Job(
        id=f"meta:{job_id}",
        company="meta",
        title=compact_text(payload.get("title")),
        location=_location_text(locations) or "Unspecified",
        url=DETAIL_URL.format(id=job_id),
        jd_text=html_to_text(
            " ".join(
                str(payload.get(key) or "")
                for key in ("description", "responsibilities", "qualifications")
            )
        ),
        posted_at=payload.get("datePosted"),
    )


def _location_text(value: Any) -> str:
    if isinstance(value, dict):
        address = value.get("address") or value
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                address.get("addressCountry"),
            ]
            return compact_text(", ".join(str(part) for part in parts if part))
        return compact_text(str(address))
    if isinstance(value, list):
        return "; ".join(part for item in value if (part := _location_text(item)))
    return compact_text(str(value or ""))
