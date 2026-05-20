from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from adapters.base import Job


CHECK_EMOJI = "\u2705"
DISCORD_API_BASE = "https://discord.com/api/v10"


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
        bot_token: str | None = None,
        channel_id: str | None = None,
        dry_run: bool = False,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.dry_run = dry_run
        self.timeout = timeout
        self.session = session or requests.Session()
        self.webhook_id, self.webhook_token = _parse_webhook(webhook_url) if webhook_url else (None, None)

    def post_job(self, job: Job, resume_config: str, *, color: int) -> DiscordMessage:
        payload = {"embeds": [build_job_embed(job, resume_config, color=color)]}
        if self.dry_run:
            print("[dry-run] Discord payload:")
            print(payload)
            return DiscordMessage(id=f"dry-run-{job.id}", channel_id=self.channel_id, payload=payload)

        if not self.webhook_url:
            raise RuntimeError("DISCORD_WEBHOOK_URL is required unless dry_run is enabled")

        separator = "&" if "?" in self.webhook_url else "?"
        response = self.session.post(
            f"{self.webhook_url}{separator}wait=true",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        message = response.json()
        return DiscordMessage(id=str(message["id"]), channel_id=message.get("channel_id"), payload=message)

    def fetch_message(self, message_id: str, channel_id: str | None = None) -> dict[str, Any] | None:
        if self.dry_run:
            return None

        if self.webhook_id and self.webhook_token:
            url = f"{DISCORD_API_BASE}/webhooks/{self.webhook_id}/{self.webhook_token}/messages/{message_id}"
            response = self.session.get(url, timeout=self.timeout)
            if response.status_code == 200:
                return response.json()
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
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def has_dismiss_reaction(self, message_id: str, channel_id: str | None = None) -> bool:
        message = self.fetch_message(message_id, channel_id=channel_id)
        if not message:
            return False
        return has_checkmark_reaction(message)


def build_job_embed(job: Job, resume_config: str, *, color: int) -> dict[str, Any]:
    resume_block = _truncate_for_code_block(resume_config)
    posted_at = job.posted_at or "Unknown"
    return {
        "title": f"\U0001f6a8 {job.company} - {job.title}"[:256],
        "url": job.url,
        "description": f"**Location:** {job.location}\n**Posted:** {posted_at}\n\n```text\n{resume_block}\n```",
        "color": color,
        "footer": {"text": f"React {CHECK_EMOJI} to dismiss"},
    }


def has_checkmark_reaction(message: dict[str, Any]) -> bool:
    for reaction in message.get("reactions") or []:
        emoji = reaction.get("emoji") or {}
        if emoji.get("name") == CHECK_EMOJI and int(reaction.get("count") or 0) > 0:
            return True
    return False


def _truncate_for_code_block(value: str, limit: int = 3600) -> str:
    cleaned = value.replace("```", "'''").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 20].rstrip()}\n... [truncated]"


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
