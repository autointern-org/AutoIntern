from __future__ import annotations

from collections.abc import Iterable
import re
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


WorkdayBoard = tuple[str, str, str]
# Workday's edge started serving its HTML app shell (instead of JSON) to
# GitHub's runners about 90 minutes after boards were fetched four at a time.
# One board at a time with a short pause keeps the traffic shaped like a
# browser session; the adapters still run concurrently with each other.
BOARD_WORKERS = 1
BOARD_PAUSE_SECONDS = 0.5
PAGE_LIMIT = 20
# The "intern" text search ranks by relevance, not title, and intern postings
# can sit past result #500 (Capital One had 12 of 13 there). Each tenant's
# own intern facet (workerSubType / jobFamilyGroup ...) is queried exhaustively
# on top; tenants without such a facet get the text search read to the end.
TEXT_PAGES_WITH_FACET = 25
MAX_PAGES = 80
FACET_PREFERENCE = ("workerSubType", "jobFamilyGroup", "jobFamily", "Job_Family", "jobFamilies", "timeType")
FACET_VALUE_RE = re.compile(r"\b(intern|interns|internship|internships|co-?ops?|student|trainee|apprentice)\b", re.IGNORECASE)


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
        self.session = session or new_session()
        self.board_errors: list[tuple[str, str]] = []
        self.source_totals: dict[str, tuple[int, int]] = {}

    def fetch(self) -> list[Job]:
        self.board_errors = []
        self.source_totals = {}
        jobs: list[Job] = []

        def run(board: WorkdayBoard) -> tuple[WorkdayBoard, list[Job] | Exception]:
            try:
                return board, self._fetch_board(board)
            except Exception as exc:  # noqa: BLE001 - recorded per board below
                return board, exc

        # Boards are independent and each takes seconds; a few threads keep
        # the 30+ Workday tenants from dominating the scan's wall clock.
        if BOARD_WORKERS <= 1:
            outcomes = []
            for index, board in enumerate(self.boards):
                if index:
                    sleep(BOARD_PAUSE_SECONDS)
                outcomes.append(run(board))
        else:
            with ThreadPoolExecutor(max_workers=min(BOARD_WORKERS, max(1, len(self.boards)))) as pool:
                outcomes = list(pool.map(run, self.boards))
        for board, outcome in outcomes:
            name = self.company_names.get(board, board[1])
            if isinstance(outcome, Exception):
                print(f"[workday] {name} fetch failed: {outcome}")
                self.board_errors.append((name, str(outcome)))
            else:
                jobs.extend(outcome)
        return jobs

    def _fetch_board(self, board: WorkdayBoard) -> list[Job]:
        host, tenant, site = board
        name = self.company_names.get(board, tenant)
        text_rows, text_total, first_payload = self._paginate(board, {}, "intern", None)
        facet = intern_facet(first_payload)
        rows = list(text_rows)
        if facet is None:
            reported, parsed = text_total, len(text_rows)
        else:
            facet_rows, facet_total, _ = self._paginate(board, {facet[0]: facet[1]}, "", MAX_PAGES)
            seen = {_row_key(row) for row in rows}
            rows.extend(row for row in facet_rows if _row_key(row) not in seen)
            reported, parsed = facet_total, len(facet_rows)
        if reported:
            self.source_totals[name] = (reported, parsed)
        return [self._normalize(board, raw) for raw in rows]

    def _paginate(
        self,
        board: WorkdayBoard,
        applied: dict[str, list[str]],
        search: str,
        max_pages: int | None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        """All rows for one query. max_pages=None means: TEXT_PAGES_WITH_FACET
        when the first page shows an intern facet, otherwise MAX_PAGES."""
        host, tenant, site = board
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        rows: list[dict[str, Any]] = []
        first_payload: dict[str, Any] = {}
        total = 0
        offset = 0
        warmed = False
        limit_pages = max_pages or MAX_PAGES
        page = 0
        while page < limit_pages:
            try:
                payload = self._post_jobs(url, host=host, site=site, offset=offset, limit=PAGE_LIMIT, applied=applied, search=search)
            except Exception as exc:
                if not rows and not warmed:
                    warmed = True
                    print(f"[workday] {host}/{tenant}/{site} retry after: {exc}")
                    self._warmup(host, site)
                elif rows:
                    print(f"[workday] {host}/{tenant}/{site} retry offset={offset}: {exc}")
                    sleep(1)
                else:
                    raise RuntimeError(f"workday {host}/{tenant}/{site}: {exc}") from exc
                try:
                    payload = self._post_jobs(url, host=host, site=site, offset=offset, limit=PAGE_LIMIT, applied=applied, search=search)
                except Exception as retry_exc:
                    if rows:
                        print(f"[workday] {host}/{tenant}/{site} pagination stopped: {retry_exc}")
                        break
                    raise RuntimeError(f"workday {host}/{tenant}/{site}: {retry_exc}") from retry_exc
            if page == 0:
                first_payload = payload if isinstance(payload, dict) else {}
                if max_pages is None and intern_facet(first_payload) is not None:
                    limit_pages = TEXT_PAGES_WITH_FACET
            page_rows = _find_workday_jobs(payload)
            # Workday returns the total on the first page only.
            page_total = _as_int(payload.get("total")) if isinstance(payload, dict) else 0
            if page_total:
                total = max(total, page_total)
            rows.extend(page_rows)
            offset += PAGE_LIMIT
            page += 1
            if not page_rows or (total and offset >= total) or len(page_rows) < PAGE_LIMIT:
                break
        return rows, total, first_payload

    def _post_jobs(
        self,
        url: str,
        *,
        host: str,
        site: str,
        offset: int,
        limit: int,
        applied: dict[str, list[str]] | None = None,
        search: str = "intern",
    ) -> Any:
        response = self.session.post(
            url,
            json={"appliedFacets": applied or {}, "limit": limit, "offset": offset, "searchText": search},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": f"https://{host}",
                "Referer": f"https://{host}/{site}",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return _response_json(response, host=host)

    def _warmup(self, host: str, site: str) -> None:
        try:
            self.session.get(
                f"https://{host}/{site}",
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Referer": f"https://{host}/",
                },
                timeout=self.timeout,
            )
        except Exception as exc:
            print(f"[workday] warmup {host}/{site} failed: {exc}")

    def _normalize(self, board: WorkdayBoard, raw: dict[str, Any]) -> Job:
        host, tenant, site = board
        job_id = raw.get("bulletFields", [None])[0] if isinstance(raw.get("bulletFields"), list) else None
        job_id = raw.get("jobReqId") or raw.get("requisitionId") or raw.get("id") or job_id or raw.get("externalPath")
        external_path = raw.get("externalPath") or raw.get("url")
        if external_path and str(external_path).startswith("/"):
            # externalPath is relative to the career site ("/job/..."); the
            # public link Workday itself uses is https://{host}/{site}/job/...
            path = str(external_path)
            if not path.startswith(f"/{site}/"):
                path = f"/{site}{path}"
            url = f"https://{host}{path}"
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


def _response_json(response: Any, *, host: str) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        status = getattr(response, "status_code", "?")
        text = str(getattr(response, "text", "") or "").strip()[:120]
        raise RuntimeError(
            f"workday {host} not JSON (HTTP {status}): {text or 'empty body'}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"workday {host} expected object, got {type(payload).__name__}")
    return payload


def intern_facet(payload: Any) -> tuple[str, list[str]] | None:
    """The tenant's own intern filter, e.g. ("workerSubType", [<id of "Intern (Fixed Term)">])."""
    facets = payload.get("facets") if isinstance(payload, dict) else None
    if not isinstance(facets, list):
        return None
    by_param = {f.get("facetParameter"): f for f in facets if isinstance(f, dict)}
    for param in FACET_PREFERENCE:
        facet = by_param.get(param)
        if not facet:
            continue
        ids = [
            str(value["id"])
            for value in facet.get("values") or []
            if isinstance(value, dict)
            and value.get("id")
            and FACET_VALUE_RE.search(str(value.get("descriptor") or ""))
            and "internal" not in str(value.get("descriptor") or "").lower()
        ]
        if ids:
            return param, ids
    return None


def _row_key(row: dict[str, Any]) -> str:
    return str(row.get("externalPath") or row.get("bulletFields") or row.get("title"))


def _find_workday_jobs(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("jobPostings", "jobs", "postings"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
