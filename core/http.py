from __future__ import annotations

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
