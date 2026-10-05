from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from typing import Any, Callable

import requests

from core.discord import DiscordClient
from core.kv import CloudflareKV, StateStore


WORKFLOW_FILE = "internship-monitor.yml"
LAPTOP_JOB = "tesla"
WINDOW_MINUTES = 90
# A laptop job normally finishes within ~5 minutes of its dispatch.
GRACE_MINUTES = 25
MIN_DISPATCHES = 2
ISSUE_TITLE = "Laptop runner looks offline"
REPEAT_SECONDS = 6 * 60 * 60

Getter = Callable[[str], dict[str, Any]]


def runner_down(get: Getter, repo: str, *, now: datetime, current_run_id: str | None = None) -> tuple[bool, int]:
    """(down, dispatched) — down when the laptop timer dispatched runs
    (proof the laptop is awake) but none of their laptop jobs succeeded."""
    since = (now - timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = get(f"/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs?event=workflow_dispatch&created=>={since}&per_page=30")
    old_enough = []
    for run in runs.get("workflow_runs") or []:
        if current_run_id and str(run.get("id")) == str(current_run_id):
            continue
        created = datetime.fromisoformat(str(run.get("created_at")).replace("Z", "+00:00"))
        if now - created >= timedelta(minutes=GRACE_MINUTES):
            old_enough.append(run)
    if len(old_enough) < MIN_DISPATCHES:
        return False, len(old_enough)
    for run in old_enough:
        jobs = get(f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=20")
        for job in jobs.get("jobs") or []:
            if job.get("name") == LAPTOP_JOB and job.get("conclusion") == "success":
                return False, len(old_enough)
    return True, len(old_enough)


def main() -> None:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        print("[runner-check] GITHUB_TOKEN/GITHUB_REPOSITORY missing; skipping")
        return
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def get(path: str) -> dict[str, Any]:
        response = session.get(f"https://api.github.com{path}", timeout=30)
        response.raise_for_status()
        return response.json()

    down, dispatched = runner_down(get, repo, now=datetime.now(UTC), current_run_id=os.getenv("GITHUB_RUN_ID"))
    print(f"[runner-check] dispatched={dispatched} down={down}")
    if not down:
        return
    state = StateStore(
        CloudflareKV(
            account_id=os.getenv("CF_ACCOUNT_ID"),
            namespace_id=os.getenv("CF_KV_NAMESPACE_ID"),
            api_token=os.getenv("CF_API_TOKEN"),
        )
    )
    if state.issue_recently_posted(ISSUE_TITLE, within_seconds=REPEAT_SECONDS):
        print("[runner-check] alert already posted in the last 6 hours")
        return
    companies = os.getenv("LAPTOP_COMPANIES", "tesla, linkedin, citadel, citadel-securities, palo-alto-networks")
    body = (
        f"The laptop timer dispatched {dispatched} runs in the last {WINDOW_MINUTES} minutes, but none of their "
        f"laptop jobs ran, so these companies are not being scanned: {companies}.\n\n"
        "Restart it on the Mac: `cd ~/Desktop/actions-runner && ./run.sh`\n"
        "(or keep it running in the background: `./svc.sh install && ./svc.sh start`)"
    )
    DiscordClient(None, issues_webhook_url=os.getenv("DISCORD_ISSUES_WEBHOOK_URL")).post_issue(ISSUE_TITLE, body)
    state.mark_issue_posted(ISSUE_TITLE)
    state.flush_health()


if __name__ == "__main__":
    main()
