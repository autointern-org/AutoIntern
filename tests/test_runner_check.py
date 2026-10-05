from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from scripts.check_laptop_runner import runner_down

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def make_get(runs: list[tuple[int, int]], laptop_success: set[int]):
    def get(path: str) -> dict[str, Any]:
        if "/jobs" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            conclusion = "success" if run_id in laptop_success else None
            return {"jobs": [{"name": "scan", "conclusion": "success"}, {"name": "tesla", "conclusion": conclusion}]}
        return {
            "workflow_runs": [
                {"id": run_id, "created_at": (NOW - timedelta(minutes=age)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                for run_id, age in runs
            ]
        }

    return get


def test_awake_laptop_with_dead_runner_is_flagged() -> None:
    down, dispatched = runner_down(make_get([(1, 30), (2, 45), (3, 60), (4, 5)], set()), "o/r", now=NOW)
    assert down and dispatched == 3


def test_one_successful_laptop_job_means_healthy() -> None:
    down, _ = runner_down(make_get([(1, 30), (2, 45), (3, 60)], {2}), "o/r", now=NOW)
    assert not down


def test_sleeping_laptop_dispatches_nothing_and_is_not_flagged() -> None:
    down, dispatched = runner_down(make_get([], set()), "o/r", now=NOW)
    assert not down and dispatched == 0
    down, _ = runner_down(make_get([(1, 30)], set()), "o/r", now=NOW)
    assert not down  # one dispatch is not enough evidence


def test_current_run_is_ignored() -> None:
    down, dispatched = runner_down(make_get([(1, 30), (2, 45)], set()), "o/r", now=NOW, current_run_id="2")
    assert not down and dispatched == 1
