from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import os
from time import perf_counter
from typing import Iterable

from adapters.amazon import AmazonAdapter
from adapters.apple import AppleAdapter
from adapters.ashby import AshbyAdapter
from adapters.atlassian import AtlassianAdapter
from adapters.base import Adapter, Job
from adapters.eightfold import EightfoldAdapter, EightfoldBoard, infer_host
from adapters.google import GoogleAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.ibm import IBMAdapter
from adapters.lever import LeverAdapter
from adapters.meta import MetaAdapter
from adapters.optiver import OptiverAdapter
from adapters.oracle import OracleAdapter, OracleBoard
from adapters.phenom import PhenomAdapter, PhenomBoard
from adapters.snap import SnapAdapter
from adapters.tesla import TeslaAdapter
from adapters.tiktok import TikTokAdapter
from adapters.workday import WorkdayAdapter
from core.classifier import Classifier, build_classifier_from_env
from core.config import CompanyConfig, Whitelist
from core.discord import DiscordClient, DiscordMessage
from core.filters import apply_decision, evaluate_job, sort_alert_jobs
from core.health import CompanyHealth, anomaly_lines, format_health
from core.kv import CloudflareKV, StateStore


@dataclass
class ScanResult:
    fetched: int = 0
    matched: int = 0
    notified: int = 0
    dismissed: int = 0
    skipped_seen: int = 0
    recaps: int = 0
    issues: int = 0


def run_scan(
    *,
    whitelist_path: str = "config/whitelist.yaml",
    dry_run: bool = False,
    skip_claude: bool = False,
) -> ScanResult:
    whitelist = Whitelist.load(whitelist_path)
    companies = select_companies(whitelist.companies)
    configs = {company.name.lower(): company for company in companies}
    adapters = build_adapters(companies)
    state = StateStore(
        CloudflareKV(
            account_id=os.getenv("CF_ACCOUNT_ID"),
            namespace_id=os.getenv("CF_KV_NAMESPACE_ID"),
            api_token=os.getenv("CF_API_TOKEN"),
        )
    )
    discord = DiscordClient(
        os.getenv("DISCORD_WEBHOOK_URL"),
        forum_webhook_url=os.getenv("DISCORD_FORUM_WEBHOOK_URL"),
        issues_webhook_url=os.getenv("DISCORD_ISSUES_WEBHOOK_URL"),
        bot_token=os.getenv("DISCORD_BOT_TOKEN"),
        channel_id=os.getenv("DISCORD_CHANNEL_ID"),
        dry_run=dry_run,
    )
    classifier = build_classifier_from_env()
    only = os.getenv("SCAN_ONLY_COMPANIES", "").strip()
    return scan(
        adapters=adapters,
        configs=configs,
        state=state,
        discord=discord,
        classifier=classifier,
        dry_run=dry_run,
        skip_claude=skip_claude,
        skip_dismissals=bool(only),
    )


def select_companies(companies: list[CompanyConfig]) -> list[CompanyConfig]:
    only = _company_names(os.getenv("SCAN_ONLY_COMPANIES"))
    skip = _company_names(os.getenv("SCAN_SKIP_COMPANIES"))
    selected = companies
    if only:
        selected = [company for company in selected if company.name.lower() in only]
    if skip:
        selected = [company for company in selected if company.name.lower() not in skip]
    return selected


def _company_names(raw: str | None) -> set[str]:
    return {part.strip().lower() for part in (raw or "").split(",") if part.strip()}


def scan(
    *,
    adapters: Iterable[Adapter],
    configs: dict[str, CompanyConfig],
    state: StateStore,
    discord: DiscordClient,
    classifier: Classifier,
    dry_run: bool = False,
    skip_claude: bool = False,
    skip_dismissals: bool = False,
) -> ScanResult:
    result = ScanResult()
    if not skip_dismissals:
        result.dismissed = mark_reaction_dismissals(state, discord)
    fetched_by_company: dict[str, int] = defaultdict(int)
    matched_jobs: dict[str, list[Job]] = defaultdict(list)
    health_rows: list[CompanyHealth] = []

    for adapter in adapters:
        started = perf_counter()
        try:
            jobs = adapter.fetch()
        except Exception as exc:
            duration_ms = int((perf_counter() - started) * 1000)
            label = adapter.__class__.__name__
            print(f"[scan] adapter {label} failed: {exc}")
            health_rows.append(
                CompanyHealth(company=label, status="error", duration_ms=duration_ms, error=str(exc))
            )
            _report_issue(discord, result, f"{label} fetch failed", str(exc), dry_run=dry_run)
            continue
        duration_ms = int((perf_counter() - started) * 1000)
        result.fetched += len(jobs)
        companies_in_batch: set[str] = set()
        for job in jobs:
            company_key = job.company.lower()
            fetched_by_company[company_key] += 1
            companies_in_batch.add(company_key)
            config = configs.get(company_key)
            if not config:
                continue
            decision = evaluate_job(job, config)
            if not decision.keep:
                continue
            result.matched += 1
            matched_jobs[company_key].append(apply_decision(job, decision))
        for company_key in companies_in_batch:
            health_rows.append(
                CompanyHealth(
                    company=company_key,
                    fetched=fetched_by_company[company_key],
                    matched=len(matched_jobs.get(company_key, [])),
                    status="ok",
                    duration_ms=duration_ms,
                )
            )

    print(format_health(health_rows))
    previous = {
        row.company: int((state.get_health(row.company) or {}).get("fetched") or 0)
        for row in health_rows
        if row.status == "ok"
    }
    for line in anomaly_lines(health_rows, previous):
        print(line)
        _report_issue(discord, result, "Company fetch looks off", line, dry_run=dry_run)

    for company_key, jobs in matched_jobs.items():
        config = configs[company_key]
        jobs = sort_alert_jobs(jobs)
        fetched = fetched_by_company[company_key]
        if not state.is_bootstrapped(company_key):
            unseen = [
                job
                for job in jobs
                if not state.is_seen(job.id, company=company_key)
                and not state.is_dismissed(job.id, company=company_key)
            ]
            for job in jobs:
                if state.is_seen(job.id, company=company_key) or state.is_dismissed(job.id, company=company_key):
                    result.skipped_seen += 1
            if unseen:
                result.recaps += 1
                recap = discord.post_recap(config.name, unseen, color=config.color)
                for job in unseen:
                    _remember(state, job, recap, dry_run=dry_run)
                    result.notified += 1
            if not dry_run:
                state.mark_bootstrapped(company_key)
                state.record_health(company_key, fetched=fetched, matched=len(jobs))
                _finalize_company_seen(state, company_key, fetched=fetched, live_jobs=jobs)
            continue

        fresh: list[tuple[Job, str, int]] = []
        for job in jobs:
            if state.is_seen(job.id, company=company_key) or state.is_dismissed(job.id, company=company_key):
                result.skipped_seen += 1
                continue
            resume_config = _resume_config(classifier, job, dry_run=dry_run, skip_claude=skip_claude)
            fresh.append((job, resume_config, config.color))
        if not fresh:
            if not dry_run:
                state.record_health(company_key, fetched=fetched, matched=len(jobs))
                _finalize_company_seen(state, company_key, fetched=fetched, live_jobs=jobs)
            continue
        thread_id = state.get_forum_thread(company_key)
        if thread_id:
            discord.thread_ids[config.name] = thread_id
        messages = discord.post_jobs_for_company(config.name, fresh)
        for (job, _, _), message in zip(fresh, _job_messages(messages, len(fresh))):
            _remember(state, job, message, dry_run=dry_run)
            result.notified += 1
        if not dry_run:
            stored_thread = discord.thread_ids.get(config.name)
            if stored_thread:
                state.record_forum_thread(company_key, stored_thread)
            state.record_health(company_key, fetched=fetched, matched=len(jobs))
            _finalize_company_seen(state, company_key, fetched=fetched, live_jobs=jobs)

    if not dry_run:
        for company_key, fetched in fetched_by_company.items():
            if company_key in matched_jobs or fetched <= 0 or company_key not in configs:
                continue
            state.record_health(company_key, fetched=fetched, matched=0)
            _finalize_company_seen(state, company_key, fetched=fetched, live_jobs=[])
        state.flush_dirty_seen()
    return result


def _job_messages(messages: list[DiscordMessage], job_count: int) -> list[DiscordMessage]:
    if len(messages) == job_count:
        return messages
    if len(messages) == job_count + 1:
        return messages[1:]
    return messages[-job_count:]


def _finalize_company_seen(
    state: StateStore,
    company_key: str,
    *,
    fetched: int,
    live_jobs: list[Job],
) -> None:
    if fetched > 0:
        state.prune_seen(company_key, {job.id for job in live_jobs})
    state.flush_seen(company_key)


def _remember(state: StateStore, job: Job, message: DiscordMessage, *, dry_run: bool) -> None:
    if dry_run:
        return
    state.record_notification(
        job_id=job.id,
        company=job.company,
        title=job.title,
        url=job.url,
        message_id=message.id,
        channel_id=message.channel_id,
    )


def _report_issue(
    discord: DiscordClient,
    result: ScanResult,
    title: str,
    body: str,
    *,
    dry_run: bool,
) -> None:
    result.issues += 1
    if dry_run:
        print(f"[dry-run] issue {title}: {body}")
        return
    discord.post_issue(title, body)


def mark_reaction_dismissals(state: StateStore, discord: DiscordClient) -> int:
    count = 0
    checked: set[str] = set()
    dismissed_messages: set[str] = set()
    for notification in state.list_undismissed_notifications(days=7):
        message_id = str(notification["message_id"])
        channel_id = notification.get("channel_id")
        if message_id not in checked:
            checked.add(message_id)
            if discord.has_dismiss_reaction(message_id, channel_id=channel_id):
                dismissed_messages.add(message_id)
        if message_id not in dismissed_messages:
            continue
        company = notification.get("company")
        state.mark_dismissed(
            str(notification["job_id"]),
            company=str(company) if company else None,
        )
        count += 1
    return count


def build_adapters(companies: list[CompanyConfig]) -> list[Adapter]:
    adapters: list[Adapter] = []
    greenhouse = [company for company in companies if company.adapter == "greenhouse" and company.slug]
    if greenhouse:
        adapters.append(
            GreenhouseAdapter(
                [str(company.slug) for company in greenhouse],
                company_names={str(company.slug): company.name for company in greenhouse},
            )
        )

    ashby = [company for company in companies if company.adapter == "ashby" and company.org_slug]
    if ashby:
        adapters.append(
            AshbyAdapter(
                [str(company.org_slug) for company in ashby],
                company_names={str(company.org_slug): company.name for company in ashby},
            )
        )

    workday = [
        company
        for company in companies
        if company.adapter == "workday" and company.host and company.tenant and company.site
    ]
    if workday:
        boards = [(str(company.host), str(company.tenant), str(company.site)) for company in workday]
        adapters.append(
            WorkdayAdapter(
                boards,
                company_names={
                    (str(company.host), str(company.tenant), str(company.site)): company.name
                    for company in workday
                },
            )
        )

    if any(company.adapter == "google" for company in companies):
        adapters.append(GoogleAdapter())

    eightfold_boards: list[EightfoldBoard] = []
    for company in companies:
        extra: dict[str, str] = {}
        if company.adapter == "microsoft":
            extra = {"location": "United States", "filter_employment_type": "internship"}
            eightfold_boards.append(
                EightfoldBoard(
                    company=company.name,
                    host=company.host or infer_host(company.name) or "apply.careers.microsoft.com",
                    domain=company.domain or "microsoft.com",
                    api=company.api or "pcsx",
                    extra_params=extra,
                )
            )
            continue
        if company.adapter != "eightfold" or not company.domain:
            continue
        host = company.host or infer_host(company.name)
        if not host:
            continue
        if company.name.lower() == "microsoft":
            extra = {"location": "United States", "filter_employment_type": "internship"}
        eightfold_boards.append(
            EightfoldBoard(
                company=company.name,
                host=host,
                domain=company.domain,
                api=company.api or "pcsx",
                extra_params=extra,
            )
        )
    if eightfold_boards:
        adapters.append(EightfoldAdapter(eightfold_boards))

    amazon = [company for company in companies if company.adapter == "amazon"]
    if amazon:
        adapters.append(
            AmazonAdapter(
                [company.name for company in amazon],
                slugs={company.name: str(company.slug) for company in amazon if company.slug},
            )
        )

    if any(company.adapter == "apple" for company in companies):
        adapters.append(AppleAdapter())

    lever = [company for company in companies if company.adapter == "lever" and company.slug]
    if lever:
        adapters.append(
            LeverAdapter(
                [str(company.slug) for company in lever],
                company_names={str(company.slug): company.name for company in lever},
            )
        )

    phenom = [company for company in companies if company.adapter == "phenom" and company.host]
    if phenom:
        adapters.append(
            PhenomAdapter(
                [
                    PhenomBoard(
                        company=company.name,
                        host=str(company.host),
                        variant=company.variant or "widgets",
                    )
                    for company in phenom
                ]
            )
        )

    oracle = [
        company
        for company in companies
        if company.adapter == "oracle" and company.host and company.site_number
    ]
    if oracle:
        adapters.append(
            OracleAdapter(
                [
                    OracleBoard(
                        company=company.name,
                        host=str(company.host),
                        site_number=str(company.site_number),
                    )
                    for company in oracle
                ]
            )
        )

    simple = {
        "tesla": TeslaAdapter,
        "snap": SnapAdapter,
        "tiktok": TikTokAdapter,
        "ibm": IBMAdapter,
        "optiver": OptiverAdapter,
        "atlassian": AtlassianAdapter,
        "meta": MetaAdapter,
    }
    for adapter_name, adapter_cls in simple.items():
        if any(company.adapter == adapter_name for company in companies):
            adapters.append(adapter_cls())

    return adapters


def _resume_config(classifier: Classifier, job: Job, *, dry_run: bool, skip_claude: bool) -> str:
    if skip_claude or not classifier.api_key:
        return (
            "target_role: intern\n"
            f"company: {job.company}\n"
            f"title: {job.title}\n"
            "resume_angle: Emphasize the closest projects, systems work, and measurable impact from the JD.\n"
            "keywords: [internship, software engineering, relevant technical stack]"
        )
    return classifier.generate_resume_config(job)
