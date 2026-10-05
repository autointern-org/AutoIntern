from __future__ import annotations

from statistics import median
from typing import Any


MIN_SAMPLES = 3
FAST_CLOSE_DAYS = 7


def summarize(samples: list[float]) -> dict[str, Any] | None:
    if len(samples) < MIN_SAMPLES:
        return None
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "median": median(ordered),
        "shortest": ordered[0],
        "fast_share": sum(1 for s in ordered if s <= FAST_CLOSE_DAYS) / len(ordered),
    }


def note_for(company: str, samples: list[float]) -> str | None:
    """One embed line, e.g. '**Typically open:** ~4 days at cisco (median of 12 closed; fastest 2)'."""
    summary = summarize(samples)
    if summary is None:
        return None
    days = summary["median"]
    text = f"~{days:.0f} days" if days >= 1 else "under a day"
    warn = " \u26a0\ufe0f closes fast, apply now" if days <= FAST_CLOSE_DAYS else ""
    return (
        f"**Typically open:** {text} at {company} "
        f"(median of {summary['n']} closed; fastest {summary['shortest']:.0f}d){warn}"
    )


def company_notes(stats: dict[str, Any]) -> dict[str, str]:
    notes: dict[str, str] = {}
    for company, samples in (stats.get("companies") or {}).items():
        note = note_for(company, list(samples))
        if note:
            notes[company.lower()] = note
    return notes


def fastest_closers(stats: dict[str, Any], limit: int = 15) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for company, samples in (stats.get("companies") or {}).items():
        summary = summarize(list(samples))
        if summary:
            rows.append((company, summary))
    rows.sort(key=lambda row: (row[1]["median"], -row[1]["n"]))
    return rows[:limit]
