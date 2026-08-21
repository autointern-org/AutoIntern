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


def test_filter_requires_internish_and_tech_title() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(title="Software Engineer"), config)
    assert not passes_filter(make_job(title="Internal Communications Manager"), config)
    assert not passes_filter(make_job(title="International Equity Manager"), config)
    assert not passes_filter(make_job(title="Design Verification Engineer - Internal IP"), config)
    assert not passes_filter(make_job(title="New Grad Software Engineer"), config)
    assert not passes_filter(make_job(title="Women's Winternship"), config)
    assert passes_filter(make_job(title="Student Researcher, BS/MS"), config)
    assert passes_filter(make_job(title="Software Engineer (Campus)"), config)
    assert passes_filter(make_job(title="Software Engineering Intern"), config)


def test_filter_hard_drops_recruiter_and_full_time_without_intern() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(title="Campus Recruiter"), config)
    assert not passes_filter(make_job(title="Campus AI Research Engineer (Full-Time)"), config)
    assert passes_filter(make_job(title="Full-Time Software Engineer Intern"), config)
    assert passes_filter(make_job(title="Software Engineer Intern/Full-Time"), config)


def test_filter_keeps_tech_roles_and_drops_ee_pm_security() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert passes_filter(make_job(title="Backend Intern"), config)
    assert passes_filter(make_job(title="SRE Intern"), config)
    assert passes_filter(make_job(title="Data Engineer Intern"), config)
    assert passes_filter(make_job(title="Data Science Intern"), config)
    assert passes_filter(make_job(title="Quantitative Trader Intern"), config)
    assert passes_filter(make_job(title="Quantitative Finance Intern"), config)
    assert passes_filter(make_job(title="ML Intern"), config)
    assert not passes_filter(make_job(title="FPGA Intern"), config)
    assert not passes_filter(make_job(title="Electrical Engineer Intern"), config)
    assert not passes_filter(make_job(title="Hardware Intern"), config)
    assert not passes_filter(make_job(title="Security Intern"), config)
    assert not passes_filter(make_job(title="Product Management Intern"), config)
    assert not passes_filter(make_job(title="PM Intern"), config)
    assert not passes_filter(make_job(title="Accounting Intern"), config)


def test_filter_drops_phd_unless_undergrad_language() -> None:
    config = CompanyConfig(name="google", adapter="google")

    phd = evaluate_job(make_job(title="Research Intern - PhD", jd_text="PhD required."), config)
    assert not phd.keep
    assert phd.stage == "phd"

    override = evaluate_job(
        make_job(
            title="Research Scientist Intern, PhD",
            jd_text="Open to exceptional undergraduates with research experience.",
        ),
        config,
    )
    assert override.keep
    assert override.degree_flag == "undergrad_ok"

    bachelors_or_phd = evaluate_job(
        make_job(
            title="Software Engineer Intern",
            jd_text="Pursuit of a Bachelor's, Master's, or PhD degree in computer science.",
        ),
        config,
    )
    assert bachelors_or_phd.keep
    assert bachelors_or_phd.degree_flag == "undergrad_ok"

    plain = evaluate_job(make_job(title="Software Engineer Intern"), config)
    assert plain.keep
    assert plain.degree_flag == "degree_unknown"


def test_filter_degree_grad_only_vs_undergrad() -> None:
    config = CompanyConfig(name="google", adapter="google")

    warsaw = evaluate_job(
        make_job(
            title="Data Science PhD Intern, 2027",
            location="Unspecified",
            jd_text=(
                "This internship is for students in their penultimate or final year "
                "of a PhD program. Role is based in EMEA / Poland."
            ),
        ),
        config,
    )
    assert not warsaw.keep
    assert warsaw.stage == "phd"

    apple = evaluate_job(
        make_job(
            title="Machine Learning and Artificial Intelligence Masters Internships",
            location="Cupertino, CA",
            jd_text="Candidates must be pursuing a graduate (MS) degree in a related field.",
        ),
        config,
    )
    assert not apple.keep
    assert apple.stage == "phd"

    stripe = evaluate_job(
        make_job(
            title="Software Engineer Intern",
            jd_text="Bachelor's, Master's, or PhD in computer science or a related field.",
        ),
        config,
    )
    assert stripe.keep
    assert stripe.degree_flag == "undergrad_ok"

    exceptional = evaluate_job(
        make_job(
            title="Research Intern, PhD",
            jd_text="Open to exceptional undergraduate students with research experience.",
        ),
        config,
    )
    assert exceptional.keep
    assert exceptional.degree_flag == "undergrad_ok"

    no_degree = evaluate_job(make_job(title="Software Engineer Intern"), config)
    assert no_degree.keep
    assert no_degree.degree_flag == "degree_unknown"

    graduate_intern = evaluate_job(
        make_job(title="Software Development Graduate Intern", jd_text="Join our campus program."),
        config,
    )
    assert graduate_intern.keep
    assert graduate_intern.degree_flag == "degree_unknown"

    graduate_ms_phd = evaluate_job(
        make_job(
            title="Software Development Graduate Intern",
            jd_text="Candidates must be in a Master's or PhD program.",
        ),
        config,
    )
    assert not graduate_ms_phd.keep
    assert graduate_ms_phd.stage == "phd"

    penultimate_only = evaluate_job(
        make_job(
            title="Software Engineer Intern",
            jd_text="This role is open to penultimate year students.",
        ),
        config,
    )
    assert penultimate_only.keep
    assert penultimate_only.degree_flag == "degree_unknown"

    phd_preferred = evaluate_job(
        make_job(title="Software Engineer Intern", jd_text="PhD preferred."),
        config,
    )
    assert phd_preferred.keep

    phd_required = evaluate_job(
        make_job(title="Research Intern - PhD", jd_text="PhD required."),
        config,
    )
    assert not phd_required.keep
    assert phd_required.stage == "phd"

    full_time_intern = evaluate_job(
        make_job(title="Full-Time Software Engineer Intern"),
        config,
    )
    assert full_time_intern.keep


def test_filter_term_keeps_summer_2027_and_part_time() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(title="Software Engineer Intern, Summer 2026"), config)
    assert not passes_filter(make_job(title="Software Engineer Intern, Winter 2027"), config)
    assert not passes_filter(make_job(title="Software Engineer Intern, Fall 2026"), config)
    assert passes_filter(make_job(title="Software Engineer Intern, Summer 2027"), config)
    assert passes_filter(make_job(title="Part-Time Software Engineer Intern, Winter 2027"), config)
    assert passes_filter(make_job(title="Summer 2026/2027 Software Engineering Internship"), config)
    assert passes_filter(make_job(title="2026-2027 Software Engineer Intern Program"), config)
    assert passes_filter(make_job(title="Software Engineer Intern"), config)

    target = evaluate_job(make_job(title="Software Engineer Intern, Summer 2027"), config)
    assert target.term_flag == "term_target"


def test_sort_alert_jobs_puts_phd_last() -> None:
    config = CompanyConfig(name="google", adapter="google")

    def tagged(**overrides: str) -> Job:
        job = make_job(**overrides)
        return apply_decision(job, evaluate_job(job, config))

    ordered = sort_alert_jobs(
        [
            tagged(
                id="1",
                title="Research Intern, PhD",
                jd_text="Open to exceptional undergraduates.",
            ),
            tagged(id="3", title="Software Engineer Intern, Summer 2027"),
            tagged(id="4", title="Software Engineer Intern"),
        ]
    )
    assert [job.id for job in ordered] == ["3", "1", "4"]


def test_filter_us_location_title_and_field() -> None:
    config = CompanyConfig(name="google", adapter="google")

    assert not passes_filter(make_job(location="London, United Kingdom"), config)
    assert not passes_filter(make_job(location="Denmark, Roskilde"), config)
    assert not passes_filter(make_job(location="Taiwan, Taipei"), config)
    assert not passes_filter(make_job(location="Dublin"), config)
    assert not passes_filter(make_job(location="Dublin HQ"), config)
    assert not passes_filter(make_job(location="IE-Dublin"), config)
    assert not passes_filter(make_job(title="Software Engineer Intern (Mexico)", location="Unspecified"), config)
    assert passes_filter(make_job(location="Dublin, OH"), config)
    assert passes_filter(make_job(location="Indiana"), config)
    assert not passes_filter(
        make_job(title="Software Engineer Intern, Co-op", location="Toronto, Canada"),
        config,
    )
    assert not passes_filter(make_job(location="Bangalore, IN, India"), config)
    assert not passes_filter(make_job(location="Ontario, CA, Canada"), config)
    assert passes_filter(make_job(location="US, CA, Santa Clara"), config)
    assert passes_filter(make_job(location="San Francisco, CA; London, UK"), config)
    assert passes_filter(make_job(location="2 Locations"), config)
    assert passes_filter(make_job(location="Unspecified"), config)
    assert passes_filter(
        make_job(location="Unspecified", jd_text="This team also staffs our Dublin HQ."),
        config,
    )


def test_filter_applies_keywords() -> None:
    config = CompanyConfig(
        name="google",
        adapter="google",
        include_keywords=["machine learning"],
        exclude_keywords=["hardware only"],
    )

    assert passes_filter(make_job(jd_text="Machine learning infrastructure."), config)
    assert not passes_filter(make_job(jd_text="Machine learning hardware only role."), config)
