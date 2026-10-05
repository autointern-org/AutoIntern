"""Weekly coverage audit against the community SimplifyJobs Summer 2027 list.

A. Companies with relevant US Summer 2027 listings that the whitelist does
   not scan, with the job board each one is hosted on.
B. Listings from scanned companies that pass the filters but were never
   pinged (needs Cloudflare KV; skipped without credentials).
C. Companies whose postings close fastest (posting-lifetime stats).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import os
import re
from typing import Any
from urllib.parse import urlparse

import requests

from adapters.base import Job
from core.config import CompanyConfig, Whitelist
from core.discord import DiscordClient
from core.filters import classify_location, evaluate_job
from core.kv import CloudflareKV, StateStore
from core.lifetimes import fastest_closers


LISTINGS_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
TERM = "Summer 2027"
RELEVANT_CATEGORIES = {
    "software",
    "software engineering",
    "ai/ml/data",
    "data science, ai & machine learning",
    "quant",
}
_SUFFIX_RE = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|group|holdings|the)\b")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def norm_name(name: str) -> str:
    text = _SUFFIX_RE.sub(" ", (name or "").lower().replace("&", " and "))
    return _NON_ALNUM_RE.sub("", text)


def board_key(url: str) -> tuple[str, str] | None:
    """(ats, identifier) recognized from a posting URL."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("greenhouse.io") and parts and parts[0] not in {"embed", "v1"}:
        return "greenhouse", parts[0].lower()
    if "greenhouse.io" in host and "for=" in (parsed.query or ""):
        match = re.search(r"for=([^&]+)", parsed.query)
        if match:
            return "greenhouse", match.group(1).lower()
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0].lower()
    if host == "jobs.lever.co" and parts:
        return "lever", parts[0].lower()
    if host.endswith("myworkdayjobs.com"):
        return "workday", host.split(".")[0]
    if host.endswith("icims.com"):
        return "icims", host
    if host.endswith("smartrecruiters.com") and parts:
        return "smartrecruiters", parts[0].lower()
    if host.endswith("eightfold.ai"):
        return "eightfold", host
    if "oraclecloud.com" in host:
        return "oracle", host
    if host.endswith("avature.net"):
        return "avature", host
    if host == "ats.rippling.com" and parts:
        return "rippling", parts[0].lower()
    if host.endswith("workable.com") and parts:
        return "workable", parts[-1].lower() if parts[0] == "j" else parts[0].lower()
    return ("site", host) if host else None


@dataclass
class Index:
    by_board: dict[tuple[str, str], CompanyConfig]
    by_name: dict[str, CompanyConfig]


def build_index(companies: list[CompanyConfig]) -> Index:
    by_board: dict[tuple[str, str], CompanyConfig] = {}
    by_name: dict[str, CompanyConfig] = {}
    for company in companies:
        by_name.setdefault(norm_name(company.name), company)
        for alias in company.aliases:
            by_name.setdefault(norm_name(alias), company)
        for slug in {company.slug, company.org_slug}:
            if slug:
                by_board.setdefault((company.adapter, str(slug).lower()), company)
                by_name.setdefault(norm_name(str(slug)), company)
        if company.adapter == "workday" and company.tenant:
            by_board.setdefault(("workday", str(company.tenant).lower()), company)
        if company.host:
            by_board.setdefault((company.adapter, str(company.host).lower()), company)
            by_board.setdefault(("site", str(company.host).lower()), company)
    return Index(by_board, by_name)


def match(index: Index, listing: dict[str, Any]) -> CompanyConfig | None:
    key = board_key(str(listing.get("url") or ""))
    if key and key in index.by_board:
        return index.by_board[key]
    return index.by_name.get(norm_name(str(listing.get("company_name") or "")))


def relevant(listing: dict[str, Any]) -> bool:
    if not listing.get("active") or not listing.get("is_visible", True):
        return False
    if TERM not in (listing.get("terms") or []):
        return False
    if str(listing.get("category") or "").lower() not in RELEVANT_CATEGORIES:
        return False
    locations = "\n".join(str(loc) for loc in listing.get("locations") or [])
    return classify_location(locations) != "non_us"


def listing_job(listing: dict[str, Any], company: str) -> Job:
    return Job(
        id=f"simplify:{listing.get('id')}",
        company=company,
        title=str(listing.get("title") or ""),
        location="; ".join(str(loc) for loc in listing.get("locations") or []) or "Unspecified",
        url=str(listing.get("url") or ""),
        jd_text="",
    )


_TITLE_RE = re.compile(r"[^a-z0-9]+")


def _title(text: str) -> str:
    return _TITLE_RE.sub(" ", (text or "").lower()).strip()


def audit(listings: list[dict[str, Any]], companies: list[CompanyConfig], state: StateStore | None) -> dict[str, Any]:
    index = build_index(companies)
    uncovered: dict[str, dict[str, Any]] = {}
    covered_listings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for listing in listings:
        if not relevant(listing):
            continue
        config = match(index, listing)
        if config is None:
            name = str(listing.get("company_name") or "?")
            row = uncovered.setdefault(norm_name(name), {"company": name, "count": 0, "boards": set(), "sample": listing.get("url")})
            row["count"] += 1
            key = board_key(str(listing.get("url") or ""))
            if key:
                row["boards"].add(f"{key[0]}:{key[1]}")
        else:
            covered_listings[config.name.lower()].append(listing)
    misses: list[dict[str, Any]] = []
    if state is not None:
        configs = {c.name.lower(): c for c in companies}
        for company_key, items in covered_listings.items():
            config = configs[company_key]
            try:
                seen_titles = {_title(str(e.get("title") or "")) for e in state.seen_entries(company_key).values() if isinstance(e, dict)}
            except Exception as exc:
                print(f"[audit] seen read failed for {company_key}: {exc}")
                continue
            for listing in items:
                job = listing_job(listing, company_key)
                if not evaluate_job(job, config).keep:
                    continue
                if _title(job.title) not in seen_titles:
                    misses.append({"company": company_key, "title": job.title, "url": job.url, "locations": job.location})
    return {
        "uncovered": sorted(uncovered.values(), key=lambda r: (-r["count"], r["company"].lower())),
        "covered_companies": len(covered_listings),
        "covered_listings": sum(len(v) for v in covered_listings.values()),
        "misses": misses,
    }


def render_markdown(report: dict[str, Any], closers: list[tuple[str, dict[str, Any]]], *, kv: bool) -> str:
    lines = ["# AutoIntern coverage audit", ""]
    lines.append(f"Scanned companies with relevant Summer 2027 listings on SimplifyJobs: **{report['covered_companies']}** ({report['covered_listings']} listings).")
    lines += ["", f"## A. Companies not scanned ({len(report['uncovered'])})", "", "| Company | Listings | Job board | Example |", "|---|---|---|---|"]
    for row in report["uncovered"]:
        lines.append(f"| {row['company']} | {row['count']} | {', '.join(sorted(row['boards'])) or '?'} | {row['sample']} |")
    lines += ["", "## B. Possible misses (pass filters, never pinged)", ""]
    if not kv:
        lines.append("_Skipped: no Cloudflare credentials in this run._")
    elif not report["misses"]:
        lines.append("None.")
    else:
        lines += ["| Company | Title | Locations | Link |", "|---|---|---|---|"]
        for miss in report["misses"]:
            lines.append(f"| {miss['company']} | {miss['title']} | {miss['locations']} | {miss['url']} |")
    lines += ["", "## C. Fastest-closing companies (median days open)", ""]
    if not closers:
        lines.append("Not enough closed postings yet (needs 3 per company).")
    else:
        lines += ["| Company | Median days | Closed postings | Fastest |", "|---|---|---|---|"]
        for company, summary in closers:
            lines.append(f"| {company} | {summary['median']:.1f} | {summary['n']} | {summary['shortest']:.1f} |")
    return "\n".join(lines) + "\n"


def discord_summary(report: dict[str, Any], closers: list[tuple[str, dict[str, Any]]], *, kv: bool, run_url: str | None) -> str:
    top = report["uncovered"][:12]
    parts = [f"**Not scanned** ({len(report['uncovered'])} companies with Summer 2027 listings):"]
    parts += [f"• {row['company']} ({row['count']}) {', '.join(sorted(row['boards']))}" for row in top]
    if kv:
        parts.append(f"\n**Possible misses:** {len(report['misses'])}")
        parts += [f"• {m['company']}: {m['title']}" for m in report["misses"][:10]]
    if closers:
        parts.append("\n**Fastest closers (median days):** " + ", ".join(f"{c} {s['median']:.0f}d" for c, s in closers[:8]))
    if run_url:
        parts.append(f"\nFull report: {run_url}")
    return "\n".join(parts)[:3900]


def main() -> None:
    listings = requests.get(LISTINGS_URL, timeout=60).json()
    companies = Whitelist.load("config/whitelist.yaml").companies
    kv = CloudflareKV(
        account_id=os.getenv("CF_ACCOUNT_ID"),
        namespace_id=os.getenv("CF_KV_NAMESPACE_ID"),
        api_token=os.getenv("CF_API_TOKEN"),
    )
    state = StateStore(kv) if kv.enabled else None
    report = audit(listings, companies, state)
    closers = fastest_closers(state.lifetime_stats()) if state is not None else []
    markdown = render_markdown(report, closers, kv=state is not None)
    out = os.getenv("AUDIT_OUTPUT") or os.getenv("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    else:
        print(markdown)
    print(f"[audit] uncovered={len(report['uncovered'])} misses={len(report['misses'])} covered={report['covered_companies']}")
    webhook = os.getenv("DISCORD_ISSUES_WEBHOOK_URL")
    if webhook:
        run_url = None
        if os.getenv("GITHUB_RUN_ID") and os.getenv("GITHUB_REPOSITORY"):
            run_url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        DiscordClient(None, issues_webhook_url=webhook).post_issue(
            "Weekly coverage audit", discord_summary(report, closers, kv=state is not None, run_url=run_url)
        )


if __name__ == "__main__":
    main()
