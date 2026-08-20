from __future__ import annotations

from adapters.base import Job
from core.config import CompanyConfig
from core.filters import apply_decision, evaluate_job, passes_filter, sort_alert_jobs


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
    assert not passes_filter(make_job(title="Design Verification Engineer - Internal IP"), config)
    assert passes_filter(make_job(title="Student Researcher, BS/MS"), config)
    assert passes_filter(make_job(title="Women's Winternship"), config)
    assert passes_filter(make_job(title="New Grad Software Engineer"), config)


def test_filter_tags_degree_and_never_drops_phd() -> None:
    config = CompanyConfig(name="google", adapter="google")

    phd = evaluate_job(make_job(title="Research Intern - PhD"), config)
    assert phd.keep
    assert phd.degree_flag == "phd_likely"

    bs_ms = evaluate_job(make_job(title="Student Researcher, BS/MS/PhD, Fall 2026"), config)
    assert bs_ms.keep
    assert bs_ms.degree_flag == "undergrad_ok"

    override = evaluate_job(
        make_job(
            title="Research Scientist Intern, PhD",
            jd_text="Open to exceptional undergraduates with research experience.",
        ),
        config,
    )
    assert override.keep
    assert override.degree_flag == "undergrad_ok"

    plain = evaluate_job(make_job(title="Software Engineer Intern"), config)
    assert plain.keep
    assert plain.degree_flag == "degree_unknown"


def test_filter_tags_term_and_never_drops_past_terms() -> None:
    config = CompanyConfig(name="google", adapter="google")

    past = evaluate_job(make_job(title="Software Engineer Intern, Summer 2026"), config)
    assert past.keep
    assert past.term_flag == "term_past"

    target = evaluate_job(make_job(title="Software Engineer Intern, Summer 2027"), config)
    assert target.keep
    assert target.term_flag == "term_target"

    winter = evaluate_job(make_job(title="Software Engineer Intern, Winter 2027"), config)
    assert winter.keep
    assert winter.term_flag == "term_target"

    both = evaluate_job(make_job(title="Summer 2026/2027 Internship"), config)
    assert both.keep
    assert both.term_flag == "term_target"

    span = evaluate_job(make_job(title="2026-2027 Intern Program"), config)
    assert span.keep
    assert span.term_flag == "term_target"

    unknown = evaluate_job(make_job(title="Software Engineer Intern"), config)
    assert unknown.keep
    assert unknown.term_flag == "term_unknown"


def test_sort_alert_jobs_puts_phd_and_past_terms_last() -> None:
    config = CompanyConfig(name="google", adapter="google")

    def tagged(**overrides: str) -> Job:
        job = make_job(**overrides)
        return apply_decision(job, evaluate_job(job, config))

    ordered = sort_alert_jobs(
        [
            tagged(id="1", title="PhD Intern"),
            tagged(id="2", title="Software Engineer Intern, Summer 2026"),
            tagged(id="3", title="Software Engineer Intern, Summer 2027"),
            tagged(id="4", title="Software Engineer Intern"),
        ]
    )
    assert [job.id for job in ordered] == ["3", "4", "2", "1"]


def test_filter_applies_keywords_and_us_location() -> None:
    config = CompanyConfig(
        name="google",
        adapter="google",
        include_keywords=["machine learning"],
        exclude_keywords=["hardware only"],
    )

    assert passes_filter(make_job(jd_text="Machine learning infrastructure."), config)
    assert not passes_filter(make_job(jd_text="Machine learning hardware only role."), config)
    assert not passes_filter(make_job(location="London, United Kingdom", jd_text="Machine learning."), config)
    assert not passes_filter(make_job(location="Denmark, Roskilde", jd_text="Machine learning."), config)
    assert not passes_filter(make_job(location="Taiwan, Taipei", jd_text="Machine learning."), config)
    assert passes_filter(make_job(location="US, CA, Santa Clara", jd_text="Machine learning."), config)
    assert passes_filter(make_job(location="2 Locations", jd_text="Machine learning."), config)
    assert passes_filter(make_job(location="Unspecified", jd_text="Machine learning."), config)
