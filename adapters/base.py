from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Protocol
import re


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Job:
    id: str
    company: str
    title: str
    location: str
    url: str
    jd_text: str
    posted_at: str | None = None
    location_unknown: bool = False
    degree_flag: str | None = None
    term_flag: str | None = None


class Adapter(Protocol):
    def fetch(self) -> list[Job]:
        """Return normalized jobs from one or more company career boards."""


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_text(unescape(text))


def posted_at_value(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.replace(".", "", 1).isdigit():
            value = float(stripped)
        else:
            return compact_text(stripped)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return str(value)
    return compact_text(str(value))
