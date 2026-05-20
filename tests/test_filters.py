from __future__ import annotations

from adapters.base import Job
from core.config import CompanyConfig
from core.filters import passes_filter


def make_job(**overrides: str) -> Job:
    values = {
        "id": "test:1",
        "company": "google",
        "title": "Software Engineering Intern",
        "location": "Mountain View, CA",
        "url": "https://example.com/job",
        "jd_text": "Build distributed systems.",
        "posted_at": "2026-05-19",
    }
    values.update(overrides)
    return Job(**values)


def test_filter_requires_intern_in_title() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(title="Software Engineer"), config)
    assert not passes_filter(make_job(title="Internal Communications Manager"), config)
    assert not passes_filter(make_job(title="International Equity Manager"), config)


def test_filter_excludes_phd_by_default() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(title="Research Intern - PhD"), config)
    assert passes_filter(make_job(title="Research Intern - PhD"), CompanyConfig(name="google", adapter="google", include_phd=True))


def test_filter_applies_keywords_and_us_location() -> None:
    config = CompanyConfig(
        name="google",
        adapter="google",
        include_keywords=["machine learning"],
        exclude_keywords=["new grad"],
    )

    assert passes_filter(make_job(jd_text="Machine learning infrastructure."), config)
    assert not passes_filter(make_job(jd_text="Machine learning new grad role."), config)
    assert not passes_filter(make_job(location="London, United Kingdom", jd_text="Machine learning."), config)
    assert not passes_filter(make_job(location="Denmark, Roskilde", jd_text="Machine learning."), config)
    assert not passes_filter(make_job(location="Taiwan, Taipei", jd_text="Machine learning."), config)
    assert passes_filter(make_job(location="US, CA, Santa Clara", jd_text="Machine learning."), config)
