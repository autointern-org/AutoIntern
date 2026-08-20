from __future__ import annotations

from dataclasses import dataclass
import re

from adapters.base import Job
from core.config import CompanyConfig


INTERN_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|winternship|"
    r"co-?op|coops?|summer analyst|campus|"
    r"new ?grad|new college grad|"
    r"university (grad|graduate|program)|student|apprentice|"
    r"predoctoral|industrial trainee)\b",
    re.IGNORECASE,
)
PHD_RE = re.compile(r"\b(ph\.?d|doctoral|post-?doc)\b", re.IGNORECASE)
UNDERGRAD_RE = re.compile(
    r"\b(bs|b\.s|ba|bachelor'?s?|undergrad(uate)?|sophomore|junior|rising senior)\b",
    re.IGNORECASE,
)
UNDERGRAD_OVERRIDE_RE = re.compile(
    r"exceptional undergraduate|outstanding undergraduate|open to undergraduates|"
    r"currently enrolled in a bachelor'?s?,?\s*master'?s?,?\s*or\s*ph\.?d|"
    r"equivalent practical experience|rising senior|penultimate year",
    re.IGNORECASE,
)
MULTI_LOCATION_RE = re.compile(r"^\d+\s+locations?$", re.IGNORECASE)

PAST_TERM_RE = re.compile(
    r"\b(summer|spring|fall|winter|autumn)\s*(20)?(23|24|25|26)\b|\b20(23|24|25|26)\b",
    re.IGNORECASE,
)
TARGET_TERM_RE = re.compile(
    r"\b20'?27\b|\bsummer\s*'?27\b|\(2027\s+start\)|\[2027\s+summer\]",
    re.IGNORECASE,
)

US_LOCATION_PHRASES = {
    "united states",
    "united states of america",
    "u.s.",
    "u.s.a.",
    "iso-country-usa",
    "remote - us",
    "remote (us)",
    "remote us",
    "us remote",
}

US_LOCATION_TOKENS = {
    "us",
    "usa",
}

US_STATE_CODES = {
    "al",
    "ak",
    "az",
    "ar",
    "ca",
    "co",
    "ct",
    "dc",
    "de",
    "fl",
    "ga",
    "hi",
    "ia",
    "id",
    "il",
    "in",
    "ks",
    "ky",
    "la",
    "ma",
    "md",
    "me",
    "mi",
    "mn",
    "mo",
    "ms",
    "mt",
    "nc",
    "nd",
    "ne",
    "nh",
    "nj",
    "nm",
    "nv",
    "ny",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "va",
    "vt",
    "wa",
    "wi",
    "wv",
    "wy",
}

US_STATE_NAMES = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "colorado",
    "connecticut",
    "delaware",
    "florida",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nebraska",
    "nevada",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "north carolina",
    "north dakota",
    "ohio",
    "oklahoma",
    "oregon",
    "pennsylvania",
    "rhode island",
    "south carolina",
    "south dakota",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west virginia",
    "wisconsin",
    "wyoming",
}

NON_US_LOCATION_HINTS = {
    "canada",
    "india",
    "london",
    "ireland",
    "united kingdom",
    "germany",
    "france",
    "singapore",
    "japan",
    "china",
    "australia",
    "netherlands",
    "denmark",
    "taiwan",
    "emea",
    "remote - global",
    "remote - emea",
}


@dataclass(frozen=True)
class FilterDecision:
    keep: bool
    stage: str
    location_unknown: bool = False
    degree_flag: str | None = None
    term_flag: str | None = None


def evaluate_job(job: Job, config: CompanyConfig) -> FilterDecision:
    title = job.title
    haystack = f"{job.title}\n{job.location}\n{job.jd_text}"

    if not INTERN_TITLE_RE.search(title):
        return FilterDecision(keep=False, stage="intern")

    location_kind = classify_location(job.location)
    if not config.include_intl and location_kind == "non_us":
        return FilterDecision(keep=False, stage="us")
    location_unknown = location_kind == "unknown"

    lowered_haystack = haystack.lower()
    if config.include_keywords and not any(keyword.lower() in lowered_haystack for keyword in config.include_keywords):
        return FilterDecision(keep=False, stage="keywords", location_unknown=location_unknown)
    if any(keyword.lower() in lowered_haystack for keyword in config.exclude_keywords):
        return FilterDecision(keep=False, stage="keywords", location_unknown=location_unknown)

    return FilterDecision(
        keep=True,
        stage="keep",
        location_unknown=location_unknown,
        degree_flag=classify_degree(title, job.jd_text),
        term_flag=classify_term(f"{title}\n{job.jd_text}"),
    )


def passes_filter(job: Job, config: CompanyConfig) -> bool:
    return evaluate_job(job, config).keep


def apply_decision(job: Job, decision: FilterDecision) -> Job:
    return Job(
        id=job.id,
        company=job.company,
        title=job.title,
        location=job.location,
        url=job.url,
        jd_text=job.jd_text,
        posted_at=job.posted_at,
        location_unknown=decision.location_unknown,
        degree_flag=decision.degree_flag,
        term_flag=decision.term_flag,
    )


def filter_jobs(jobs: list[Job], configs: dict[str, CompanyConfig]) -> list[Job]:
    filtered: list[Job] = []
    for job in jobs:
        config = configs.get(job.company.lower())
        if not config:
            continue
        decision = evaluate_job(job, config)
        if decision.keep:
            filtered.append(apply_decision(job, decision))
    return sort_alert_jobs(filtered)


def classify_degree(title: str, jd_text: str) -> str:
    blob = f"{title}\n{jd_text}"
    undergrad = bool(UNDERGRAD_RE.search(blob) or UNDERGRAD_OVERRIDE_RE.search(blob))
    if undergrad:
        return "undergrad_ok"
    if PHD_RE.search(blob):
        return "phd_likely"
    return "degree_unknown"


def classify_term(text: str) -> str:
    if TARGET_TERM_RE.search(text):
        return "term_target"
    if PAST_TERM_RE.search(text):
        return "term_past"
    return "term_unknown"


def sort_alert_jobs(jobs: list[Job]) -> list[Job]:
    degree_rank = {"undergrad_ok": 0, "degree_unknown": 0, "phd_likely": 1}
    term_rank = {"term_target": 0, "term_unknown": 1, "term_past": 2}
    return sorted(
        jobs,
        key=lambda job: (
            degree_rank.get(job.degree_flag or "degree_unknown", 0),
            term_rank.get(job.term_flag or "term_unknown", 1),
            job.company.lower(),
            job.title.lower(),
        ),
    )


def classify_location(location: str) -> str:
    value = (location or "").strip()
    lowered = value.lower()
    if not value or lowered in {"unspecified", "unknown"}:
        return "unknown"
    if MULTI_LOCATION_RE.match(value):
        return "unknown"
    if any(hint in lowered for hint in NON_US_LOCATION_HINTS):
        return "non_us"
    if any(phrase in lowered for phrase in US_LOCATION_PHRASES):
        return "us"
    if any(state in lowered for state in US_STATE_NAMES):
        return "us"
    if re.search(r"\b[a-z]{2,}\s*,\s*[a-z]{2}\b", lowered):
        tokens = set(re.findall(r"[a-z]+", lowered.replace(".", "")))
        if tokens & US_STATE_CODES:
            return "us"
    tokens = set(re.findall(r"[a-z]+", lowered.replace(".", "")))
    if tokens & US_LOCATION_TOKENS:
        return "us"
    if tokens & US_STATE_CODES and re.search(r"\b[a-z]{2}\b", lowered):
        return "us"
    if "remote" in lowered:
        return "unknown"
    return "unknown"
