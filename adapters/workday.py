from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


WorkdayBoard = tuple[str, str, str]


class WorkdayAdapter:
    def __init__(
        self,
        boards: Iterable[WorkdayBoard],
        *,
        company_names: dict[WorkdayBoard, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or requests.Session()

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        for board in self.boards:
            host, tenant, site = board
            url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
            try:
                response = self.session.post(
                    url,
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "intern"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.RequestException as exc:
                print(f"[workday] failed to fetch {host}/{tenant}/{site}: {exc}")
                continue
            for raw in _find_workday_jobs(response.json()):
                jobs.append(self._normalize(board, raw))
        return jobs

    def _normalize(self, board: WorkdayBoard, raw: dict[str, Any]) -> Job:
        host, tenant, site = board
        job_id = raw.get("bulletFields", [None])[0] if isinstance(raw.get("bulletFields"), list) else None
        job_id = raw.get("jobReqId") or raw.get("requisitionId") or raw.get("id") or job_id or raw.get("externalPath")
        external_path = raw.get("externalPath") or raw.get("url")
        if external_path and str(external_path).startswith("/"):
            url = f"https://{host}{external_path}"
        else:
            url = str(external_path or f"https://{host}/wday/cxs/{tenant}/{site}/job/{job_id}")

        jd_text = " ".join(
            str(value)
            for value in (
                raw.get("description"),
                raw.get("summary"),
                raw.get("timeType"),
                raw.get("workerSubType"),
            )
            if value
        )
        return Job(
            id=f"workday:{tenant}:{site}:{job_id}",
            company=self.company_names.get(board, tenant),
            title=compact_text(raw.get("title")),
            location=compact_text(raw.get("locationsText") or raw.get("location") or "Unspecified"),
            url=compact_text(url),
            jd_text=html_to_text(jd_text),
            posted_at=raw.get("postedOn") or raw.get("startDate") or raw.get("postedDate"),
        )


def _find_workday_jobs(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("jobPostings", "jobs", "postings"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []
