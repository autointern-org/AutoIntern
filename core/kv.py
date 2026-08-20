from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Iterable
from urllib.parse import quote

import requests


DEFAULT_SEEN_TTL_SECONDS = 60 * 60 * 24 * 30
DEFAULT_DISMISSED_TTL_SECONDS = 60 * 60 * 24 * 90
SEEN_LIST_TTL_SECONDS = 60 * 60 * 24 * 400
HEALTH_TTL_SECONDS = 60 * 60 * 24 * 90


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
        self._seen_cache: dict[str, dict[str, Any]] = {}
        self._seen_dirty: set[str] = set()
        self._legacy_cache: dict[str, dict[str, Any] | None] = {}
        self._dismissed_cache: dict[str, dict[str, Any] | None] = {}

    @property
    def persistent(self) -> bool:
        return bool(self.kv and self.kv.enabled)

    def is_seen(self, job_id: str, company: str | None = None) -> bool:
        return self._seen_entry(job_id, company) is not None

    def is_dismissed(self, job_id: str, company: str | None = None) -> bool:
        entry = self._seen_entry(job_id, company)
        if entry and entry.get("dismissed"):
            return True
        if self._dismissed_record(job_id):
            self._flag_seen_dismissed(job_id, company)
            return True
        legacy = self._legacy_job(job_id)
        return bool(legacy and legacy.get("dismissed"))

    def get_job(self, job_id: str, company: str | None = None) -> dict[str, Any] | None:
        entry = self._seen_entry(job_id, company)
        if entry:
            payload = dict(entry)
            payload["job_id"] = job_id
            if company:
                payload.setdefault("company", company.lower())
            return payload
        return self._legacy_job(job_id)

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
        existing = self.get_health(company)
        if existing and _as_int(existing.get("fetched")) == fetched and _as_int(existing.get("matched")) == matched:
            return
        self._put(
            self._health_key(company),
            {"company": company, "fetched": fetched, "matched": matched, "at": now_iso()},
            ttl_seconds=HEALTH_TTL_SECONDS,
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
        company_key = company.lower()
        doc = self._load_seen(company_key)
        entry = {
            "title": title,
            "url": url,
            "message_id": message_id,
            "channel_id": channel_id,
            "dismissed": False,
            "notified_at": now_iso(),
        }
        previous = doc["jobs"].get(job_id)
        if previous and _notification_fields(previous) == _notification_fields(entry):
            return
        doc["jobs"][job_id] = entry
        self._seen_dirty.add(company_key)

    def prune_seen(self, company: str, live_job_ids: Iterable[str]) -> None:
        company_key = company.lower()
        doc = self._load_seen(company_key)
        live = set(live_job_ids)
        stale = [job_id for job_id in list(doc["jobs"]) if job_id not in live]
        if not stale:
            return
        for job_id in stale:
            del doc["jobs"][job_id]
        self._seen_dirty.add(company_key)

    def flush_seen(self, company: str) -> None:
        company_key = company.lower()
        if company_key not in self._seen_dirty:
            return
        doc = self._seen_cache.get(company_key)
        if doc is None:
            self._seen_dirty.discard(company_key)
            return
        self._put(
            self._seen_key(company_key),
            {
                "company": doc.get("company") or company_key,
                "jobs": {job_id: dict(entry) for job_id, entry in doc["jobs"].items()},
            },
            ttl_seconds=SEEN_LIST_TTL_SECONDS,
        )
        self._seen_dirty.discard(company_key)

    def flush_dirty_seen(self) -> None:
        for company_key in list(self._seen_dirty):
            self.flush_seen(company_key)

    def list_undismissed_notifications(self, *, days: int = 7) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        notifications: list[dict[str, Any]] = []
        seen_job_ids: set[str] = set()
        listed_companies: set[str] = set()

        for key in self._keys("seen:"):
            company_key = key.split(":", 1)[1].lower()
            listed_companies.add(company_key)
            self._collect_seen_notifications(
                self._load_seen(company_key),
                cutoff,
                notifications,
                seen_job_ids,
            )

        for company_key, doc in self._seen_cache.items():
            if company_key in listed_companies:
                continue
            self._collect_seen_notifications(doc, cutoff, notifications, seen_job_ids)

        for key in self._keys("job:"):
            job_id = key.split(":", 1)[1]
            if job_id in seen_job_ids:
                continue
            value = self._legacy_job(job_id)
            if not value or value.get("dismissed"):
                continue
            notified_at = parse_datetime(value.get("notified_at"))
            if notified_at and notified_at < cutoff:
                continue
            if not value.get("message_id"):
                continue
            payload = dict(value)
            payload["job_id"] = job_id
            notifications.append(payload)
            seen_job_ids.add(job_id)
        return notifications

    def mark_dismissed(self, job_id: str, company: str | None = None) -> None:
        dismissed_at = now_iso()
        record = {"job_id": job_id, "dismissed_at": dismissed_at}
        self._put(
            self._dismissed_key(job_id),
            record,
            ttl_seconds=DEFAULT_DISMISSED_TTL_SECONDS,
        )
        self._dismissed_cache[job_id] = record
        company_key = (company or self._find_company_for_job(job_id) or "").lower() or None
        if company_key:
            self._flag_seen_dismissed(job_id, company_key, dismissed_at=dismissed_at)
            return
        job = self._legacy_job(job_id)
        if not job:
            return
        updated = dict(job)
        updated["dismissed"] = True
        updated["dismissed_at"] = dismissed_at
        self._put(self._job_key(job_id), updated, ttl_seconds=DEFAULT_SEEN_TTL_SECONDS)
        self._legacy_cache[job_id] = updated

    def _seen_entry(self, job_id: str, company: str | None) -> dict[str, Any] | None:
        if company:
            doc = self._load_seen(company)
            entry = doc["jobs"].get(job_id)
            if entry:
                return entry
            legacy = self._legacy_job(job_id)
            if not legacy:
                return None
            self._adopt_legacy(job_id, company, legacy)
            return doc["jobs"].get(job_id)

        for doc in self._seen_cache.values():
            entry = doc["jobs"].get(job_id)
            if entry:
                return entry
        if not self.persistent:
            for key, value in self.memory.items():
                if not key.startswith("seen:"):
                    continue
                jobs = (value or {}).get("jobs") or {}
                if job_id in jobs and isinstance(jobs[job_id], dict):
                    company_key = key.split(":", 1)[1]
                    self._load_seen(company_key)
                    return self._seen_cache[company_key]["jobs"].get(job_id)
        legacy = self._legacy_job(job_id)
        if not legacy:
            return None
        company_name = legacy.get("company")
        if company_name:
            self._adopt_legacy(job_id, str(company_name), legacy)
            return self._load_seen(str(company_name))["jobs"].get(job_id)
        return dict(legacy)

    def _load_seen(self, company: str) -> dict[str, Any]:
        company_key = company.lower()
        cached = self._seen_cache.get(company_key)
        if cached is not None:
            return cached
        raw = self._get(self._seen_key(company_key))
        jobs: dict[str, dict[str, Any]] = {}
        if raw:
            for job_id, entry in dict(raw.get("jobs") or {}).items():
                if isinstance(entry, dict):
                    jobs[str(job_id)] = dict(entry)
        doc = {"company": str((raw or {}).get("company") or company_key), "jobs": jobs}
        self._seen_cache[company_key] = doc
        return doc

    def _adopt_legacy(self, job_id: str, company: str, legacy: dict[str, Any]) -> None:
        company_key = company.lower()
        doc = self._load_seen(company_key)
        if job_id in doc["jobs"]:
            return
        doc["jobs"][job_id] = _entry_from_legacy(legacy)
        self._seen_dirty.add(company_key)

    def _flag_seen_dismissed(
        self,
        job_id: str,
        company: str | None,
        *,
        dismissed_at: str | None = None,
    ) -> None:
        company_key = (company or self._find_company_for_job(job_id) or "").lower() or None
        if not company_key:
            return
        doc = self._load_seen(company_key)
        entry = doc["jobs"].get(job_id)
        if entry is None:
            legacy = self._legacy_job(job_id)
            if not legacy:
                return
            self._adopt_legacy(job_id, company_key, legacy)
            entry = doc["jobs"].get(job_id)
        if not entry or entry.get("dismissed"):
            return
        entry["dismissed"] = True
        entry["dismissed_at"] = dismissed_at or now_iso()
        self._seen_dirty.add(company_key)

    def _find_company_for_job(self, job_id: str) -> str | None:
        for company_key, doc in self._seen_cache.items():
            if job_id in doc["jobs"]:
                return company_key
        legacy = self._legacy_job(job_id)
        company = (legacy or {}).get("company")
        return str(company).lower() if company else None

    def _collect_seen_notifications(
        self,
        doc: dict[str, Any],
        cutoff: datetime,
        notifications: list[dict[str, Any]],
        seen_job_ids: set[str],
    ) -> None:
        company = str(doc.get("company") or "")
        for job_id, entry in doc["jobs"].items():
            seen_job_ids.add(job_id)
            if not isinstance(entry, dict) or entry.get("dismissed") or not entry.get("message_id"):
                continue
            notified_at = parse_datetime(entry.get("notified_at"))
            if notified_at and notified_at < cutoff:
                continue
            notifications.append(
                {
                    "job_id": job_id,
                    "company": company,
                    "title": entry.get("title"),
                    "url": entry.get("url"),
                    "message_id": entry.get("message_id"),
                    "channel_id": entry.get("channel_id"),
                    "dismissed": False,
                    "notified_at": entry.get("notified_at"),
                }
            )

    def _legacy_job(self, job_id: str) -> dict[str, Any] | None:
        if job_id not in self._legacy_cache:
            value = self._get(self._job_key(job_id))
            self._legacy_cache[job_id] = dict(value) if value else None
        cached = self._legacy_cache[job_id]
        return dict(cached) if cached else None

    def _dismissed_record(self, job_id: str) -> dict[str, Any] | None:
        if job_id not in self._dismissed_cache:
            self._dismissed_cache[job_id] = self._get(self._dismissed_key(job_id))
        return self._dismissed_cache[job_id]

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
    def _seen_key(company: str) -> str:
        return f"seen:{company.lower()}"

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


def _entry_from_legacy(legacy: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(legacy.get("title") or ""),
        "url": str(legacy.get("url") or ""),
        "message_id": legacy.get("message_id") or "",
        "channel_id": legacy.get("channel_id"),
        "dismissed": bool(legacy.get("dismissed")),
        "notified_at": legacy.get("notified_at") or now_iso(),
    }


def _notification_fields(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        entry.get("title"),
        entry.get("url"),
        entry.get("message_id"),
        entry.get("channel_id"),
        bool(entry.get("dismissed")),
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
