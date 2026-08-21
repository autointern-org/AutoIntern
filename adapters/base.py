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
    # Structured location data from the ATS, when it provides any.
    # country_codes: ISO 3166-1 alpha-2 codes, uppercase ("US", "IN").
    # location_names: human-readable location strings ("India, Karnataka, Bangalore").
    # country_names: dedicated country-name fields ("United States of America", "India").
    country_codes: tuple[str, ...] = ()
    country_names: tuple[str, ...] = ()
    location_names: tuple[str, ...] = ()


class Adapter(Protocol):
    def fetch(self) -> list[Job]:
        """Return normalized jobs from one or more company career boards."""


COUNTRY_CODE_ALIASES = {"USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US"}


def normalize_country_code(value: object) -> str | None:
    """Return an uppercase ISO 3166-1 alpha-2 code, or None if value is not one."""
    text = compact_text(str(value or "")).upper()
    text = COUNTRY_CODE_ALIASES.get(text, text)
    if len(text) == 2 and text.isalpha():
        return text
    return None


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
