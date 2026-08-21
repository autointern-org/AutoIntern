from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import requests

from adapters.base import Job, compact_text, html_to_text, posted_at_value
from core.http import new_session


KNOWN_HOSTS = {
    "microsoft": "apply.careers.microsoft.com",
    "nvidia": "jobs.nvidia.com",
    "qualcomm": "careers.qualcomm.com",
    "netflix": "explore.jobs.netflix.net",
    "micron": "micron.eightfold.ai",
    "lamresearch": "careers.lamresearch.com",
}

MAX_PAGES = 20


@dataclass
class EightfoldBoard:
    company: str
    host: str
    domain: str
    api: str = "pcsx"
    extra_params: dict[str, str] = field(default_factory=dict)


def infer_host(company_name: str) -> str | None:
    key = company_name.lower().replace(" ", "").replace("-", "").replace("_", "")
    return KNOWN_HOSTS.get(key)


class EightfoldAdapter:
    def __init__(
        self,
        boards: Iterable[EightfoldBoard],
        *,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.boards = list(boards)
        self.timeout = timeout
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []

    def fetch(self) -> list[Job]:
        self.board_errors = []
        jobs: list[Job] = []
        seen: set[str] = set()
        for board in self.boards:
            try:
                for extra in _param_sets(board):
                    for job in self._paginate(board, extra):
                        if job.id in seen:
                            continue
                        seen.add(job.id)
                        jobs.append(job)
            except Exception as exc:
                self.board_errors.append((board.company, str(exc)))
        return jobs

    def _paginate(self, board: EightfoldBoard, extra: dict[str, str]) -> list[Job]:
        jobs: list[Job] = []
        start = 0
        page_size = 10 if board.api == "apply" else 50
        for _ in range(MAX_PAGES):
            payload = self._get_json(board, start, page_size, extra)
            positions = _positions(board.api, payload)
            if not positions:
                break
            for raw in positions:
                if isinstance(raw, dict):
                    jobs.append(self._normalize(board, raw))
            if len(positions) < page_size:
                break
            start += page_size
        return jobs

    def _get_json(
        self,
        board: EightfoldBoard,
        start: int,
        num: int,
        extra: dict[str, str],
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("domain", board.domain),
            ("query", "intern"),
            ("start", str(start)),
            ("num", str(num)),
        ]
        if board.api != "apply":
            params.append(("sort_by", "timestamp"))
        params.extend((key, value) for key, value in extra.items())
        path = "/api/apply/v2/jobs" if board.api == "apply" else "/api/pcsx/search"
        url = f"https://{board.host}{path}?{urlencode(params)}"
        for attempt in range(3):
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 429 and attempt < 2:
                time.sleep(_retry_after_seconds(response))
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    f"eightfold {board.host} status {response.status_code}: {(getattr(response, 'text', '') or '')[:200]}"
                )
            try:
                data = response.json()
            except Exception as exc:
                text = (getattr(response, "text", "") or "")[:200]
                raise RuntimeError(f"eightfold {board.host} non-JSON: {text}") from exc
            if not isinstance(data, dict):
                raise RuntimeError(
                    f"eightfold {board.host} expected object, got {type(data).__name__}"
                )
            return data
        raise RuntimeError(f"eightfold {board.host} status 429: retries exhausted")

    def _normalize(self, board: EightfoldBoard, raw: dict[str, Any]) -> Job:
        job_id = raw.get("id")
        title = raw.get("name") or raw.get("title")
        url = _job_url(board.host, raw)
        description = (
            raw.get("jobDescription")
            or raw.get("htmlJobDescription")
            or raw.get("description")
            or raw.get("atsJobDescription")
            or ""
        )
        posted = raw.get("postedTs") if board.api != "apply" else raw.get("t_create")
        return Job(
            id=f"eightfold:{board.company}:{job_id}",
            company=board.company,
            title=compact_text(str(title or "")),
            location=_location(raw),
            url=compact_text(url),
            jd_text=html_to_text(str(description)),
            posted_at=posted_at_value(posted),
        )


def _param_sets(board: EightfoldBoard) -> list[dict[str, str]]:
    if board.extra_params:
        return [dict(board.extra_params)]
    return [{}]


def _retry_after_seconds(response: requests.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is not None:
        try:
            return min(float(raw), 10.0)
        except (TypeError, ValueError):
            pass
    return 5.0


def _positions(api: str, payload: dict[str, Any]) -> list[Any]:
    if api == "apply":
        value = payload.get("positions")
    else:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        value = data.get("positions") if isinstance(data, dict) else None
    if not isinstance(value, list):
        return []
    return value


def _location(raw: dict[str, Any]) -> str:
    std = raw.get("standardizedLocations")
    text = _location_list(std)
    if text:
        return text
    text = _location_list(raw.get("locations"))
    if text:
        return text
    location = raw.get("location")
    if isinstance(location, str) and location.strip():
        return compact_text(location)
    if isinstance(location, dict):
        item = _location_item(location)
        if item:
            return item
    return "Unspecified"


def _location_list(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return compact_text(value)
    if not isinstance(value, list) or not value:
        return ""
    parts = [_location_item(item) for item in value]
    return "; ".join(part for part in parts if part)


def _location_item(value: Any) -> str:
    if isinstance(value, str):
        return compact_text(value)
    if not isinstance(value, dict):
        return ""
    name = value.get("name") or value.get("displayName") or value.get("label")
    if name:
        return compact_text(str(name))
    parts = [
        compact_text(str(value[key]))
        for key in ("city", "state", "country", "countryName")
        if value.get(key)
    ]
    return ", ".join(part for part in parts if part)


def _job_url(host: str, raw: dict[str, Any]) -> str:
    url = raw.get("canonicalPositionUrl") or raw.get("positionUrl") or raw.get("url") or ""
    text = str(url)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return f"https://{host}{text}"
    if text:
        return f"https://{host}/{text}"
    job_id = raw.get("id")
    if job_id:
        return f"https://{host}/careers/job/{job_id}"
    return ""
