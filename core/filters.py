from __future__ import annotations

import re

from adapters.base import Job
from core.config import CompanyConfig


INTERN_TITLE_RE = re.compile(r"\bintern(?:ship|ships|s)?\b", re.IGNORECASE)
PHD_RE = re.compile(r"\bph\.?\s*d\b", re.IGNORECASE)

US_LOCATION_PHRASES = {
    "united states",
    "u.s.",
    "u.s.a.",
    "remote - us",
    "remote us",
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
}


def passes_filter(job: Job, config: CompanyConfig) -> bool:
    title = job.title.lower()
    haystack = f"{job.title}\n{job.location}\n{job.jd_text}".lower()

    if not INTERN_TITLE_RE.search(job.title):
        return False
    if not config.include_phd and PHD_RE.search(job.title):
        return False
    if not config.include_intl and not _looks_us(job.location):
        return False
    if config.include_keywords and not any(keyword.lower() in haystack for keyword in config.include_keywords):
        return False
    if any(keyword.lower() in haystack for keyword in config.exclude_keywords):
        return False
    return True


def filter_jobs(jobs: list[Job], configs: dict[str, CompanyConfig]) -> list[Job]:
    filtered: list[Job] = []
    for job in jobs:
        config = configs.get(job.company.lower())
        if config and passes_filter(job, config):
            filtered.append(job)
    return filtered


def _looks_us(location: str) -> bool:
    value = location.lower()
    if "unspecified" in value or "remote" in value and "non-us" not in value:
        return True
    if any(hint in value for hint in NON_US_LOCATION_HINTS):
        return False
    if any(phrase in value for phrase in US_LOCATION_PHRASES):
        return True
    if any(state in value for state in US_STATE_NAMES):
        return True
    tokens = set(re.findall(r"[a-z]+", value.replace(".", "")))
    return bool(tokens & (US_LOCATION_TOKENS | US_STATE_CODES))
