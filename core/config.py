from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


TIER_COLORS = {
    "S": 0xEF4444,
    "A": 0x3B82F6,
    "B": 0x6B7280,
}


@dataclass(frozen=True)
class CompanyConfig:
    name: str
    adapter: str
    tier: str = "B"
    slug: str | None = None
    org_slug: str | None = None
    host: str | None = None
    tenant: str | None = None
    site: str | None = None
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    include_phd: bool = False
    include_intl: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CompanyConfig":
        return cls(
            name=str(raw["name"]),
            adapter=str(raw["adapter"]).lower(),
            tier=str(raw.get("tier", "B")).upper(),
            slug=raw.get("slug"),
            org_slug=raw.get("org_slug") or raw.get("slug"),
            host=raw.get("host"),
            tenant=raw.get("tenant"),
            site=raw.get("site"),
            include_keywords=list(raw.get("include_keywords") or []),
            exclude_keywords=list(raw.get("exclude_keywords") or []),
            include_phd=bool(raw.get("include_phd", False)),
            include_intl=bool(raw.get("include_intl", False)),
        )

    @property
    def color(self) -> int:
        return TIER_COLORS.get(self.tier, TIER_COLORS["B"])


@dataclass(frozen=True)
class Whitelist:
    companies: list[CompanyConfig]

    @classmethod
    def load(cls, path: str | Path) -> "Whitelist":
        with Path(path).open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        return cls([CompanyConfig.from_dict(item) for item in raw.get("companies", [])])

    def by_company(self) -> dict[str, CompanyConfig]:
        return {company.name.lower(): company for company in self.companies}
