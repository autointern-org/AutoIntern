from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_SEEN_TTL_SECONDS = 60 * 60 * 24 * 30
DEFAULT_DISMISSED_TTL_SECONDS = 60 * 60 * 24 * 90


class CloudflareKV:
    def __init__(
        self,
        *,
        account_id: str | None,
        namespace_id: str | None,
        api_token: str | None,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.account_id = account_id
        self.namespace_id = namespace_id
        self.api_token = api_token
        self.timeout = timeout
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.account_id and self.namespace_id and self.api_token)

    @property
    def base_url(self) -> str:
        if not self.account_id or not self.namespace_id:
            raise RuntimeError("Cloudflare account and namespace IDs are required")
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/storage/kv/namespaces/{self.namespace_id}"
        )

    def get_json(self, key: str) -> dict[str, Any] | None:
        response = self.session.get(
            f"{self.base_url}/values/{quote(key, safe='')}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if not response.text:
            return None
        return response.json()

    def put_json(self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> None:
        params = {"expiration_ttl": str(ttl_seconds)} if ttl_seconds else None
        response = self.session.put(
            f"{self.base_url}/values/{quote(key, safe='')}",
            headers={**self._headers(), "content-type": "application/json"},
            params=params,
            json=value,
            timeout=self.timeout,
        )
        response.raise_for_status()

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        cursor: str | None = None
        while True:
            params = {"prefix": prefix, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            response = self.session.get(
                f"{self.base_url}/keys",
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            keys.extend(str(item["name"]) for item in payload.get("result") or [])
            cursor = ((payload.get("result_info") or {}).get("cursor")) or None
            if not cursor:
                return keys

    def _headers(self) -> dict[str, str]:
        if not self.api_token:
            raise RuntimeError("CF_API_TOKEN is required")
        return {"Authorization": f"Bearer {self.api_token}"}


class StateStore:
    def __init__(self, kv: CloudflareKV | None = None) -> None:
        self.kv = kv
        self.memory: dict[str, dict[str, Any]] = {}

    @property
    def persistent(self) -> bool:
        return bool(self.kv and self.kv.enabled)

    def is_seen(self, job_id: str) -> bool:
        return self.get_job(job_id) is not None

    def is_dismissed(self, job_id: str) -> bool:
        if self._get(self._dismissed_key(job_id)):
            return True
        job = self.get_job(job_id)
        return bool(job and job.get("dismissed"))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._get(self._job_key(job_id))

    def refresh_seen(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        self._put(self._job_key(job_id), job, ttl_seconds=DEFAULT_SEEN_TTL_SECONDS)

    def is_bootstrapped(self, company: str) -> bool:
        return self._get(self._bootstrap_key(company)) is not None

    def mark_bootstrapped(self, company: str) -> None:
        self._put(
            self._bootstrap_key(company),
            {"company": company, "bootstrapped_at": now_iso()},
            ttl_seconds=60 * 60 * 24 * 400,
        )

    def record_forum_thread(self, company: str, thread_id: str) -> None:
        self._put(
            self._thread_key(company),
            {"company": company, "thread_id": thread_id},
            ttl_seconds=60 * 60 * 24 * 400,
        )

    def get_forum_thread(self, company: str) -> str | None:
        value = self._get(self._thread_key(company))
        if not value:
            return None
        thread_id = value.get("thread_id")
        return str(thread_id) if thread_id else None

    def get_health(self, company: str) -> dict[str, Any] | None:
        return self._get(self._health_key(company))

    def record_health(self, company: str, *, fetched: int, matched: int) -> None:
        self._put(
            self._health_key(company),
            {"company": company, "fetched": fetched, "matched": matched, "at": now_iso()},
            ttl_seconds=60 * 60 * 24 * 90,
        )

    def record_notification(
        self,
        *,
        job_id: str,
        company: str,
        title: str,
        url: str,
        message_id: str,
        channel_id: str | None,
    ) -> None:
        self._put(
            self._job_key(job_id),
            {
                "job_id": job_id,
                "company": company,
                "title": title,
                "url": url,
                "message_id": message_id,
                "channel_id": channel_id,
                "dismissed": False,
                "notified_at": now_iso(),
            },
            ttl_seconds=DEFAULT_SEEN_TTL_SECONDS,
        )

    def list_undismissed_notifications(self, *, days: int = 7) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        notifications: list[dict[str, Any]] = []
        for key in self._keys("job:"):
            value = self._get(key)
            if not value or value.get("dismissed"):
                continue
            notified_at = parse_datetime(value.get("notified_at"))
            if notified_at and notified_at < cutoff:
                continue
            if value.get("message_id"):
                notifications.append(value)
        return notifications

    def mark_dismissed(self, job_id: str) -> None:
        dismissed_at = now_iso()
        self._put(
            self._dismissed_key(job_id),
            {"job_id": job_id, "dismissed_at": dismissed_at},
            ttl_seconds=DEFAULT_DISMISSED_TTL_SECONDS,
        )
        job = self.get_job(job_id)
        if job:
            job["dismissed"] = True
            job["dismissed_at"] = dismissed_at
            self._put(self._job_key(job_id), job, ttl_seconds=DEFAULT_SEEN_TTL_SECONDS)

    def _get(self, key: str) -> dict[str, Any] | None:
        if self.persistent:
            return self.kv.get_json(key) if self.kv else None
        return self.memory.get(key)

    def _put(self, key: str, value: dict[str, Any], *, ttl_seconds: int | None = None) -> None:
        if self.persistent and self.kv:
            self.kv.put_json(key, value, ttl_seconds=ttl_seconds)
        else:
            self.memory[key] = value

    def _keys(self, prefix: str) -> list[str]:
        if self.persistent and self.kv:
            return self.kv.list_keys(prefix)
        return [key for key in self.memory if key.startswith(prefix)]

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"job:{job_id}"

    @staticmethod
    def _dismissed_key(job_id: str) -> str:
        return f"dismissed:{job_id}"

    @staticmethod
    def _bootstrap_key(company: str) -> str:
        return f"bootstrapped:{company.lower()}"

    @staticmethod
    def _thread_key(company: str) -> str:
        return f"thread:{company.lower()}"

    @staticmethod
    def _health_key(company: str) -> str:
        return f"health:{company.lower()}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
