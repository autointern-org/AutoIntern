from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text


GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op={op}"
BOARD_QUERY = (
    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) { "
    "jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) { "
    "jobPostings { id title locationName workplaceType secondaryLocations { locationName } } } }"
)
POSTING_QUERY = (
    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
    "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
    "id descriptionHtml publishedDate } }"
)
INTERN_HINT_RE = re.compile(r"\b(intern|interns|internship|co-?op|student|new grad)\b", re.IGNORECASE)


class AshbyAdapter:
    """Ashby's public posting API by board slug. Some boards (Whatnot) turn
    that API off and 404; their hosted job page's GraphQL API still lists
    every posting, and descriptions are read only for intern-looking titles."""

    API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

    def __init__(
        self,
        org_slugs: Iterable[str],
        *,
        company_names: dict[str, str] | None = None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.org_slugs = list(org_slugs)
        self.company_names = company_names or {}
        self.timeout = timeout
        self.session = session or requests.Session()
        self.board_errors: list[tuple[str, str]] = []
        self.listing_counts: dict[str, int] = {}

    def fetch(self) -> list[Job]:
        self.board_errors = []
        self.listing_counts = {}
        jobs: list[Job] = []
        for slug in self.org_slugs:
            name = self.company_names.get(slug, slug)
            try:
                response = self.session.get(self.API.format(slug=slug), timeout=self.timeout)
                if response.status_code == 404:
                    rows = self._graphql_rows(slug)
                else:
                    response.raise_for_status()
                    rows = response.json().get("jobs", [])
            except Exception as exc:
                print(f"[ashby] failed to fetch {slug}: {exc}")
                self.board_errors.append((name, str(exc)))
                continue
            self.listing_counts[name] = len(rows)
            for raw in rows:
                jobs.append(self._normalize(slug, raw))
        return jobs

    def _graphql(self, op: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            GRAPHQL.format(op=op),
            json={"operationName": op, "variables": variables, "query": query},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors") or not payload.get("data"):
            raise RuntimeError(f"ashby graphql {op}: {str(payload.get('errors'))[:200]}")
        return payload["data"]

    def _graphql_rows(self, slug: str) -> list[dict[str, Any]]:
        board = self._graphql("ApiJobBoardWithTeams", BOARD_QUERY, {"organizationHostedJobsPageName": slug}).get("jobBoard")
        if not board:
            raise RuntimeError(f"ashby {slug}: posting API 404 and no hosted job board")
        rows: list[dict[str, Any]] = []
        for posting in board.get("jobPostings") or []:
            places = [posting.get("locationName")] + [
                p.get("locationName") for p in posting.get("secondaryLocations") or [] if isinstance(p, dict)
            ]
            row = {
                "id": posting.get("id"),
                "title": posting.get("title"),
                "location": "; ".join(p for p in places if p) or posting.get("workplaceType"),
                "jobUrl": f"https://jobs.ashbyhq.com/{slug}/{posting.get('id')}",
            }
            if INTERN_HINT_RE.search(str(posting.get("title") or "")):
                try:
                    detail = self._graphql(
                        "ApiJobPosting",
                        POSTING_QUERY,
                        {"organizationHostedJobsPageName": slug, "jobPostingId": posting.get("id")},
                    ).get("jobPosting") or {}
                    row["descriptionHtml"] = detail.get("descriptionHtml")
                    row["publishedAt"] = detail.get("publishedDate")
                except Exception as exc:  # noqa: BLE001 - keep the row without a description
                    print(f"[ashby] {slug} posting {posting.get('id')} detail failed: {exc}")
            rows.append(row)
        return rows

    def _normalize(self, slug: str, raw: dict[str, Any]) -> Job:
        location = raw.get("location")
        if isinstance(location, dict):
            location_text = location.get("name") or location.get("displayName") or location.get("location")
        else:
            location_text = location

        job_id = raw.get("id") or raw.get("jobId") or raw.get("externalLinkId")
        return Job(
            id=f"ashby:{slug}:{job_id}",
            company=self.company_names.get(slug, slug),
            title=compact_text(raw.get("title")),
            location=compact_text(str(location_text or "Unspecified")),
            url=compact_text(
                raw.get("jobUrl")
                or raw.get("externalLink")
                or raw.get("applyUrl")
                or f"https://jobs.ashbyhq.com/{slug}/{job_id}"
            ),
            jd_text=html_to_text(raw.get("descriptionHtml") or raw.get("descriptionPlain") or raw.get("description")),
            posted_at=raw.get("publishedAt") or raw.get("postedAt") or raw.get("createdAt"),
        )
