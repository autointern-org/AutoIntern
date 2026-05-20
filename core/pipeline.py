from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from adapters.base import Adapter, Job
from adapters.amazon import AmazonAdapter
from adapters.apple import AppleAdapter
from adapters.ashby import AshbyAdapter
from adapters.google import GoogleAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.microsoft import MicrosoftAdapter
from adapters.workday import WorkdayAdapter
from core.classifier import Classifier
from core.config import CompanyConfig, Whitelist
from core.discord import DiscordClient
from core.filters import passes_filter
from core.kv import CloudflareKV, StateStore


@dataclass
class ScanResult:
    fetched: int = 0
    matched: int = 0
    notified: int = 0
    dismissed: int = 0
    skipped_seen: int = 0


def run_scan(
    *,
    whitelist_path: str = "config/whitelist.yaml",
    dry_run: bool = False,
    skip_claude: bool = False,
) -> ScanResult:
    whitelist = Whitelist.load(whitelist_path)
    configs = whitelist.by_company()
    adapters = build_adapters(whitelist.companies)
    state = StateStore(
        CloudflareKV(
            account_id=os.getenv("CF_ACCOUNT_ID"),
            namespace_id=os.getenv("CF_KV_NAMESPACE_ID"),
            api_token=os.getenv("CF_API_TOKEN"),
        )
    )
    discord = DiscordClient(
        os.getenv("DISCORD_WEBHOOK_URL"),
        bot_token=os.getenv("DISCORD_BOT_TOKEN"),
        channel_id=os.getenv("DISCORD_CHANNEL_ID"),
        dry_run=dry_run,
    )
    classifier = Classifier(
        os.getenv("ANTHROPIC_API_KEY"),
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
    )
    return scan(
        adapters=adapters,
        configs=configs,
        state=state,
        discord=discord,
        classifier=classifier,
        dry_run=dry_run,
        skip_claude=skip_claude,
    )


def scan(
    *,
    adapters: Iterable[Adapter],
    configs: dict[str, CompanyConfig],
    state: StateStore,
    discord: DiscordClient,
    classifier: Classifier,
    dry_run: bool = False,
    skip_claude: bool = False,
) -> ScanResult:
    result = ScanResult()
    result.dismissed = mark_reaction_dismissals(state, discord)

    for adapter in adapters:
        try:
            jobs = adapter.fetch()
        except Exception as exc:
            print(f"[scan] adapter {adapter.__class__.__name__} failed: {exc}")
            continue
        result.fetched += len(jobs)
        for job in jobs:
            config = configs.get(job.company.lower())
            if not config or not passes_filter(job, config):
                continue
            result.matched += 1
            if state.is_seen(job.id) or state.is_dismissed(job.id):
                result.skipped_seen += 1
                continue
            resume_config = _resume_config(classifier, job, dry_run=dry_run, skip_claude=skip_claude)
            message = discord.post_job(job, resume_config, color=config.color)
            if not dry_run:
                state.record_notification(
                    job_id=job.id,
                    company=job.company,
                    title=job.title,
                    url=job.url,
                    message_id=message.id,
                    channel_id=message.channel_id,
                )
            result.notified += 1
    return result


def mark_reaction_dismissals(state: StateStore, discord: DiscordClient) -> int:
    count = 0
    for notification in state.list_undismissed_notifications(days=7):
        message_id = str(notification["message_id"])
        channel_id = notification.get("channel_id")
        if discord.has_dismiss_reaction(message_id, channel_id=channel_id):
            state.mark_dismissed(str(notification["job_id"]))
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

    if any(company.adapter == "microsoft" for company in companies):
        adapters.append(MicrosoftAdapter())

    if any(company.adapter == "amazon" for company in companies):
        adapters.append(AmazonAdapter())

    if any(company.adapter == "apple" for company in companies):
        adapters.append(AppleAdapter())

    return adapters


def _resume_config(classifier: Classifier, job: Job, *, dry_run: bool, skip_claude: bool) -> str:
    if skip_claude or (dry_run and not classifier.api_key):
        return (
            "target_role: intern\n"
            f"company: {job.company}\n"
            f"title: {job.title}\n"
            "resume_angle: Emphasize the closest projects, systems work, and measurable impact from the JD.\n"
            "keywords: [internship, software engineering, relevant technical stack]"
        )
    return classifier.generate_resume_config(job)
