from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urlparse

import requests

from adapters.base import Job


CHECK_EMOJI = "\u2705"
DISCORD_API_BASE = "https://discord.com/api/v10"
PREVIEW_MAX = 5
SUMMARY_TITLE_LIMIT = 10
THREAD_NAME_MAX = 100


@dataclass(frozen=True)
class DiscordMessage:
    id: str
    channel_id: str | None
    payload: dict[str, Any]


class DiscordClient:
    def __init__(
        self,
        webhook_url: str | None,
        *,
        forum_webhook_url: str | None = None,
        issues_webhook_url: str | None = None,
        bot_token: str | None = None,
        channel_id: str | None = None,
        dry_run: bool = False,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.forum_webhook_url = forum_webhook_url
        self.issues_webhook_url = issues_webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = session or requests.Session()
        self.webhook_id, self.webhook_token = _parse_webhook(webhook_url) if webhook_url else (None, None)
        self.thread_ids: dict[str, str] = {}

    def post_job(self, job: Job, resume_config: str, *, color: int) -> DiscordMessage:
        payload = {"embeds": [build_job_embed(job, resume_config, color=color)]}
        return self._post_webhook(
            self.webhook_url,
            payload,
            dry_run_id=f"dry-run-{job.id}",
            missing_url_error="DISCORD_WEBHOOK_URL is required unless dry_run is enabled",
        )

    def post_jobs_for_company(
        self,
        company: str,
        jobs_with_resume: list[tuple[Job, str, int]],
    ) -> list[DiscordMessage]:
        if not jobs_with_resume:
            return []

        if len(jobs_with_resume) <= PREVIEW_MAX:
            return [
                self.post_job(job, resume_config, color=color)
                for job, resume_config, color in jobs_with_resume
            ]

        messages = [
            self._post_webhook(
                self.webhook_url,
                {"embeds": [build_summary_embed(company, jobs_with_resume)]},
                dry_run_id=f"dry-run-summary-{company}",
                missing_url_error="DISCORD_WEBHOOK_URL is required unless dry_run is enabled",
            )
        ]

        if self.forum_webhook_url:
            messages.extend(self.post_forum_jobs(company, jobs_with_resume))
            return messages

        print(
            "[discord] warning: DISCORD_FORUM_WEBHOOK_URL is not set; "
            "posting overflow jobs to the main channel"
        )
        for job, resume_config, color in jobs_with_resume:
            messages.append(self.post_job(job, resume_config, color=color))
        return messages

    def post_forum_jobs(
        self,
        company: str,
        jobs_with_resume: list[tuple[Job, str, int]],
    ) -> list[DiscordMessage]:
        if not jobs_with_resume or not self.forum_webhook_url:
            return []
        return [
            self._post_forum_job(company, job, resume_config, color=color)
            for job, resume_config, color in jobs_with_resume
        ]

    def post_recap(self, company: str, jobs: list[Job], *, color: int) -> DiscordMessage:
        payload = {
            "embeds": [
                build_summary_embed(
                    company,
                    [(job, "", color) for job in jobs],
                    title=f"{company} — {len(jobs)} intern roles currently open",
                    footer="First look — later new roles ping as they appear",
                )
            ]
        }
        return self._post_webhook(
            self.webhook_url,
            payload,
            dry_run_id=f"dry-run-recap-{company}",
            missing_url_error="DISCORD_WEBHOOK_URL is required unless dry_run is enabled",
        )

    def post_issue(self, title: str, body: str) -> DiscordMessage | None:
        print(f"[issues] {title}: {body}")
        if self.dry_run and not self.issues_webhook_url:
            return DiscordMessage(id=f"dry-run-issue-{title}", channel_id=None, payload={"title": title, "body": body})
        if not self.issues_webhook_url:
            return None
        payload = {
            "embeds": [
                {
                    "title": title[:256],
                    "description": body[:4000],
                    "color": 0xF59E0B,
                }
            ]
        }
        try:
            return self._post_webhook(
                self.issues_webhook_url,
                payload,
                dry_run_id=f"dry-run-issue-{title}",
                missing_url_error="DISCORD_ISSUES_WEBHOOK_URL is required unless dry_run is enabled",
            )
        except requests.RequestException as exc:
            print(f"[issues] warning: failed to post to Discord: {exc}")
            return None

    def fetch_message(self, message_id: str, channel_id: str | None = None) -> dict[str, Any] | None:
        if self.dry_run:
            return None

        try:
            return self._fetch_message(message_id, channel_id=channel_id)
        except requests.RequestException as exc:
            print(f"[discord] warning: failed to read message; skipping dismiss check ({exc})")
            return None

    def _fetch_message(self, message_id: str, channel_id: str | None = None) -> dict[str, Any] | None:
        if self.dry_run:
            return None

        if self.webhook_id and self.webhook_token:
            url = f"{DISCORD_API_BASE}/webhooks/{self.webhook_id}/{self.webhook_token}/messages/{message_id}"
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                print("[discord] warning: rate limited while reading reactions; skipping dismiss check")
                return None
            if response.status_code not in {401, 403, 404}:
                response.raise_for_status()

        token = self.bot_token
        target_channel_id = channel_id or self.channel_id
        if not token or not target_channel_id:
            return None
        response = self.session.get(
            f"{DISCORD_API_BASE}/channels/{target_channel_id}/messages/{message_id}",
            headers={"Authorization": f"Bot {token}"},
            timeout=self.timeout,
        )
        if response.status_code in {404, 429}:
            if response.status_code == 429:
                print("[discord] warning: rate limited while reading reactions; skipping dismiss check")
            return None
        response.raise_for_status()
        return response.json()

    def has_dismiss_reaction(self, message_id: str, channel_id: str | None = None) -> bool:
        message = self.fetch_message(message_id, channel_id=channel_id)
        if not message:
            return False
        return has_checkmark_reaction(message)

    def _post_forum_job(self, company: str, job: Job, resume_config: str, *, color: int) -> DiscordMessage:
        payload: dict[str, Any] = {"embeds": [build_job_embed(job, resume_config, color=color)]}
        thread_id = self.thread_ids.get(company)
        if not thread_id:
            payload["thread_name"] = company[:THREAD_NAME_MAX]
        message = self._post_webhook(
            self.forum_webhook_url,
            payload,
            dry_run_id=f"dry-run-forum-{job.id}",
            thread_id=thread_id,
            missing_url_error="DISCORD_FORUM_WEBHOOK_URL is required unless dry_run is enabled",
        )
        if company not in self.thread_ids:
            stored = message.channel_id or f"dry-run-thread-{company}"
            self.thread_ids[company] = stored
        return message

    def _post_webhook(
        self,
        webhook_url: str | None,
        payload: dict[str, Any],
        *,
        dry_run_id: str,
        missing_url_error: str,
        thread_id: str | None = None,
    ) -> DiscordMessage:
        if self.dry_run:
            print("[dry-run] Discord payload:")
            print(payload)
            return DiscordMessage(id=dry_run_id, channel_id=thread_id or self.channel_id, payload=payload)

        if not webhook_url:
            raise RuntimeError(missing_url_error)

        url = _webhook_execute_url(webhook_url, thread_id=thread_id)
        response = None
        for attempt in range(3):
            response = self.session.post(url, json=payload, timeout=self.timeout)
            if response.status_code == 429 and attempt < 2:
                time.sleep(_retry_after_seconds(response))
                continue
            response.raise_for_status()
            message = response.json()
            return DiscordMessage(id=str(message["id"]), channel_id=message.get("channel_id"), payload=message)
        assert response is not None
        response.raise_for_status()
        message = response.json()
        return DiscordMessage(id=str(message["id"]), channel_id=message.get("channel_id"), payload=message)


def build_job_embed(job: Job, resume_config: str, *, color: int) -> dict[str, Any]:
    resume_block = _truncate_for_code_block(resume_config)
    lines = [f"**Location:** {job.location}"]
    if job.posted_at:
        lines.append(f"**Posted:** {job.posted_at}")
    flags = _job_flags(job)
    if flags:
        lines.append(f"**Flags:** {', '.join(flags)}")
    return {
        "title": f"\U0001f6a8 {job.company} - {job.title}"[:256],
        "url": job.url,
        "description": "\n".join(lines) + f"\n\n```text\n{resume_block}\n```",
        "color": color,
        "footer": {"text": f"React {CHECK_EMOJI} to dismiss"},
    }


def build_summary_embed(
    company: str,
    jobs_with_resume: list[tuple[Job, str, int]],
    *,
    title: str | None = None,
    footer: str = "Details in forum thread",
) -> dict[str, Any]:
    count = len(jobs_with_resume)
    links = [f"- [{job.title}]({job.url})" for job, _, _ in jobs_with_resume[:SUMMARY_TITLE_LIMIT]]
    extra = f"\n- …and {count - SUMMARY_TITLE_LIMIT} more" if count > SUMMARY_TITLE_LIMIT else ""
    return {
        "title": (title or f"{company} — {count} new intern postings")[:256],
        "description": "\n".join(links) + extra,
        "color": jobs_with_resume[0][2] if jobs_with_resume else 0x6B7280,
        "footer": {"text": footer},
    }


def has_checkmark_reaction(message: dict[str, Any]) -> bool:
    for reaction in message.get("reactions") or []:
        emoji = reaction.get("emoji") or {}
        if emoji.get("name") == CHECK_EMOJI and int(reaction.get("count") or 0) > 0:
            return True
    return False


def _job_flags(job: Job) -> list[str]:
    flags: list[str] = []
    if job.location_unknown:
        flags.append("location_unknown")
    if job.degree_flag:
        flags.append(job.degree_flag if job.degree_flag != "unknown" else "degree_unknown")
    if job.term_flag:
        flags.append(job.term_flag)
    return flags


def _truncate_for_code_block(value: str, limit: int = 3600) -> str:
    cleaned = value.replace("```", "'''").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 20].rstrip()}\n... [truncated]"


def _webhook_execute_url(webhook_url: str, *, thread_id: str | None = None) -> str:
    params = []
    if thread_id:
        params.append(f"thread_id={thread_id}")
    params.append("wait=true")
    separator = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{separator}{'&'.join(params)}"


def _parse_webhook(webhook_url: str | None) -> tuple[str | None, str | None]:
    if not webhook_url:
        return None, None
    path = urlparse(webhook_url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 3 and parts[-3] == "webhooks":
        return parts[-2], parts[-1]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return None, None


def _retry_after_seconds(response: requests.Response) -> float:
    raw = response.headers.get("Retry-After") if getattr(response, "headers", None) else None
    if raw is not None:
        try:
            return min(float(raw), 10.0)
        except (TypeError, ValueError):
            pass
    return 5.0
