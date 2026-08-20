from __future__ import annotations

from dataclasses import dataclass
import re

from adapters.base import Job
from core.config import CompanyConfig


INTERN_TITLE_RE = re.compile(
    r"\b(intern|interns|internship|internships|winternship|"
    r"co-?ops?|summer analyst|campus|student|apprentice)\b",
    re.IGNORECASE,
)
HARD_DROP_TITLE_RE = re.compile(r"\b(recruiter|ambassador)\b", re.IGNORECASE)
FULL_TIME_RE = re.compile(r"\bfull[\s-]?time\b", re.IGNORECASE)
INTERN_FOR_FULLTIME_RE = re.compile(
    r"\b(intern|interns|internship|internships|winternship|co-?ops?)\b",
    re.IGNORECASE,
)
TECH_DROP_RE = re.compile(
    r"\b(electrical|fpga|hardware|firmware|analog|embedded|civil|mechanical|"
    r"security|phishing|detection engineer|"
    r"product manager|product management|associate product manager|\bapm\b|"
    r"pm intern|intern(?:ship)?\s*,?\s*pm\b|"
    r"marketing|accounting|sales)\b",
    re.IGNORECASE,
)
TECH_KEEP_RE = re.compile(
    r"\b(software(?:\s+engineer(?:ing)?|\s+developer|\s+intern)?|"
    r"swe|sde|backend|full[\s-]?stack|systems software|"
    r"research(?:\s+intern|\s+engineer|\s+scientist)?|student researcher|"
    r"machine learning|\bml\b|"
    r"ai(?:\s+engineer)?|applied scientist|"
    r"data scientist|data science|data engineer|site reliability|\bsre\b|"
    r"quant(?:itative)?|step)\b",
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
PART_TIME_RE = re.compile(r"\bpart[\s-]?time\b", re.IGNORECASE)
SUMMER_RE = re.compile(r"\bsummer\b", re.IGNORECASE)
OTHER_SEASON_RE = re.compile(r"\b(winter|spring|fall|autumn)\b", re.IGNORECASE)
YEAR_2027_RE = re.compile(r"\b20'?27\b|\bsummer\s*'?27\b", re.IGNORECASE)
PAST_YEAR_RE = re.compile(r"\b20(23|24|25|26)\b", re.IGNORECASE)

PAST_TERM_RE = re.compile(
    r"\b(summer|spring|fall|winter|autumn)\s*(20)?(23|24|25|26)\b|\b20(23|24|25|26)\b",
    re.IGNORECASE,
)
TARGET_TERM_RE = re.compile(
    r"\b20'?27\b|\bsummer\s*'?27\b|\(2027\s+start\)|\[2027\s+summer\]",
    re.IGNORECASE,
)

US_LOCATION_PHRASES = (
    r"united states of america",
    r"united states",
    r"u\.s\.a\.",
    r"u\.s\.",
    r"iso-country-usa",
    r"remote[\s-]+us",
    r"remote\s*\(us\)",
    r"us remote",
)
US_LOCATION_PHRASE_RE = re.compile(
    r"\b(" + "|".join(US_LOCATION_PHRASES) + r")\b",
    re.IGNORECASE,
)

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
US_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted(US_STATE_NAMES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
US_CITY_STATE_RE = re.compile(
    r"\b[a-z][a-z .'-]+,\s*(" + "|".join(sorted(US_STATE_CODES)) + r")\b(?!-)",
    re.IGNORECASE,
)
COUNTRY_CODE_COLLISIONS = {
    "in": re.compile(r"\bindia\b", re.IGNORECASE),
    "ca": re.compile(r"\bcanada\b", re.IGNORECASE),
}

NON_US_RE = re.compile(
    r"\b(canada|india|ireland|united kingdom|\buk\b|germany|france|singapore|"
    r"japan|china|australia|netherlands|denmark|taiwan|mexico|brazil|poland|"
    r"vietnam|israel|uae|kuwait|chile|colombia|latam|emea|africa|"
    r"dublin|warsaw|shanghai|tokyo|toronto|vancouver|london|paris|berlin|"
    r"munich|amsterdam|milan|rome|madrid|barcelona|sydney|melbourne|"
    r"mumbai|bangalore|bengaluru|hyderabad|tel aviv|sao paulo|são paulo|"
    r"mexico city|krakow|cracow|istanbul|stockholm|cape town|"
    r"hod hasharon)\b",
    re.IGNORECASE,
)


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
    location_blob = f"{job.title}\n{job.location}"

    if not INTERN_TITLE_RE.search(title):
        return FilterDecision(keep=False, stage="intern")
    if HARD_DROP_TITLE_RE.search(title):
        return FilterDecision(keep=False, stage="intern")
    if TECH_DROP_RE.search(title) or not TECH_KEEP_RE.search(title):
        return FilterDecision(keep=False, stage="tech")
    if FULL_TIME_RE.search(title) and not INTERN_FOR_FULLTIME_RE.search(title):
        return FilterDecision(keep=False, stage="intern")

    location_kind = classify_location(location_blob)
    if not config.include_intl and location_kind == "non_us":
        return FilterDecision(keep=False, stage="us")
    location_unknown = location_kind == "unknown"

    lowered_haystack = haystack.lower()
    if config.include_keywords and not any(keyword.lower() in lowered_haystack for keyword in config.include_keywords):
        return FilterDecision(keep=False, stage="keywords", location_unknown=location_unknown)
    if any(keyword.lower() in lowered_haystack for keyword in config.exclude_keywords):
        return FilterDecision(keep=False, stage="keywords", location_unknown=location_unknown)

    degree_flag = classify_degree(title, job.jd_text)
    if _phd_without_undergrad(title, job.jd_text):
        return FilterDecision(keep=False, stage="phd", location_unknown=location_unknown, degree_flag=degree_flag)

    term_flag = classify_term(f"{title}\n{job.jd_text}")
    if not _term_allows(title):
        return FilterDecision(
            keep=False,
            stage="term",
            location_unknown=location_unknown,
            degree_flag=degree_flag,
            term_flag=term_flag,
        )

    return FilterDecision(
        keep=True,
        stage="keep",
        location_unknown=location_unknown,
        degree_flag=degree_flag,
        term_flag=term_flag,
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
    if MULTI_LOCATION_RE.match(value.strip()):
        return "unknown"
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines and all(line.lower() in {"unspecified", "unknown"} or MULTI_LOCATION_RE.match(line) for line in lines):
        return "unknown"
    if _has_us_signal(lowered):
        return "us"
    if NON_US_RE.search(lowered):
        return "non_us"
    if re.search(r"\bremote\b", lowered) and not NON_US_RE.search(lowered):
        return "unknown"
    return "unknown"


def _has_us_signal(lowered: str) -> bool:
    if US_LOCATION_PHRASE_RE.search(lowered):
        return True
    if re.search(r"\b(usa|us)\b", lowered):
        return True
    if US_STATE_NAME_RE.search(lowered):
        return True
    for match in US_CITY_STATE_RE.finditer(lowered):
        code = match.group(1).lower()
        collision = COUNTRY_CODE_COLLISIONS.get(code)
        if collision and collision.search(lowered):
            continue
        return True
    return False


def _phd_without_undergrad(title: str, jd_text: str) -> bool:
    blob = f"{title}\n{jd_text}"
    if not PHD_RE.search(blob):
        return False
    return not (UNDERGRAD_RE.search(blob) or UNDERGRAD_OVERRIDE_RE.search(blob))


def _term_allows(title: str) -> bool:
    if PART_TIME_RE.search(title):
        return True
    if SUMMER_RE.search(title) and YEAR_2027_RE.search(title):
        return True
    if OTHER_SEASON_RE.search(title):
        return False
    if YEAR_2027_RE.search(title):
        return True
    if PAST_YEAR_RE.search(title):
        return False
    return True
