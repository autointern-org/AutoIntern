from __future__ import annotations

from typing import Any, Callable

import requests

from adapters.base import DEFAULT_USER_AGENT


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }
    )
    return session


def retry_once(request: Callable[[], Any], *, label: str = "http") -> Any:
    """Career-site APIs occasionally stall past the read timeout; a single
    retry recovers nearly all of them without skipping the board."""
    try:
        return request()
    except (requests.Timeout, requests.ConnectionError) as exc:
        print(f"[{label}] retrying after {exc.__class__.__name__}")
        return request()
