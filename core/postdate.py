from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import re
from typing import Any


# Posted-date strings seen across the adapters:
#   ISO timestamps            "2026-09-21T11:36:47-07:00", "2026-09-02T00:00:00.000+0000"
#   ISO dates, unpadded too   "2026-08-20", "2026-6-8"
#   US dates                  "06/08/2026" (Radancy job-date-posted)
#   Workday relative text     "Posted Today", "Posted Yesterday", "Posted 4 Days Ago", "Posted 30+ Days Ago"
#   Long dates                "September 3, 2026", "Sep 3, 2026"
#   Epoch seconds / millis    "1747440000", "1747440000000"
_RELATIVE_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.IGNORECASE)
_YMD_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_LONG_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")


def parse_posted(value: Any, *, now: datetime | None = None) -> date | None:
    """Best-effort date a posting went live, or None when the text is unusable."""
    if value is None or value == "":
        return None
    current = now or datetime.now(UTC)
    text = str(value).strip()
    relative = _RELATIVE_RE.search(text)
    if relative:
        word = relative.group(1).lower()
        if word == "today":
            return current.date()
        if word == "yesterday":
            return (current - timedelta(days=1)).date()
        return (current - timedelta(days=int(relative.group(2)))).date()
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:
            number //= 1000
        if number > 946_684_800:  # after 2000-01-01
            return datetime.fromtimestamp(number, tz=UTC).date()
        return None
    iso = text.replace("Z", "+00:00")
    iso = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", iso)
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed.astimezone(UTC).date() if parsed.tzinfo else parsed.date()
    except ValueError:
        pass
    ymd = _YMD_RE.match(text)
    if ymd:
        try:
            return date(int(ymd.group(1)), int(ymd.group(2)), int(ymd.group(3)))
        except ValueError:
            return None
    mdy = _MDY_RE.match(text)
    if mdy:
        try:
            return date(int(mdy.group(3)), int(mdy.group(1)), int(mdy.group(2)))
        except ValueError:
            return None
    for fmt in _LONG_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def posted_age_days(value: Any, *, now: datetime | None = None) -> int | None:
    posted = parse_posted(value, now=now)
    if posted is None:
        return None
    current = (now or datetime.now(UTC)).date()
    return max(0, (current - posted).days)
