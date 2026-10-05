from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, TypeVar

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


B = TypeVar("B")
BOARD_WORKERS = 4


def fetch_boards(
    boards: Iterable[B],
    fetch_one: Callable[[B], Any],
    *,
    workers: int = BOARD_WORKERS,
) -> list[tuple[B, Any]]:
    """Run fetch_one for each board, a few at a time, keeping input order.
    Each outcome is the return value or the exception it raised. For
    adapters whose boards are different companies' hosts; Workday tenants
    share Workday's edge and stay sequential."""

    def run(board: B) -> tuple[B, Any]:
        try:
            return board, fetch_one(board)
        except Exception as exc:  # noqa: BLE001 - returned to the caller
            return board, exc

    items = list(boards)
    if len(items) <= 1 or workers <= 1:
        return [run(board) for board in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(run, items))
