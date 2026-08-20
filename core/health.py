from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CompanyHealth:
    company: str
    fetched: int = 0
    matched: int = 0
    status: str = "ok"
    duration_ms: int = 0
    error: str | None = None


def format_health(rows: list[CompanyHealth]) -> str:
    lines = ["[health] company fetched matched status duration_ms"]
    for row in rows:
        extra = f" error={row.error}" if row.error else ""
        lines.append(
            f"[health] {row.company} {row.fetched} {row.matched} {row.status} {row.duration_ms}{extra}"
        )
    return "\n".join(lines)


def anomaly_lines(rows: list[CompanyHealth], previous_fetched: dict[str, int] | None = None) -> list[str]:
    previous_fetched = previous_fetched or {}
    alerts: list[str] = []
    for row in rows:
        if row.status == "error":
            alerts.append(f"{row.company} fetch failed: {row.error}")
            continue
        if row.company.lower() == "google":
            continue
        prior = previous_fetched.get(row.company.lower())
        if prior and prior >= 20 and row.fetched == 0:
            alerts.append(f"{row.company} fetched dropped to 0 (was {prior})")
    return alerts
