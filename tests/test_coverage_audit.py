from __future__ import annotations

from core.config import CompanyConfig
from core.kv import StateStore
from scripts.coverage_audit import audit, board_key, norm_name
from tests.test_pipeline import FakeKV


def listing(company: str, title: str, url: str, *, term: str = "Summer 2027", category: str = "Software", locations=("New York, NY",), active: bool = True) -> dict:
    return {"company_name": company, "title": title, "url": url, "terms": [term], "category": category, "locations": list(locations), "active": active, "is_visible": True, "id": title}


def test_board_key_recognizes_ats_urls() -> None:
    assert board_key("https://job-boards.greenhouse.io/figma/jobs/123") == ("greenhouse", "figma")
    assert board_key("https://jobs.ashbyhq.com/notion/abc") == ("ashby", "notion")
    assert board_key("https://jobs.lever.co/zoox/abc") == ("lever", "zoox")
    assert board_key("https://bah.wd1.myworkdayjobs.com/bah_jobs/job/x") == ("workday", "bah")
    assert board_key("https://careers-sig.icims.com/jobs/1/job") == ("icims", "careers-sig.icims.com")
    assert board_key("https://www.akunacapital.com/careers/job/8018847/") == ("site", "www.akunacapital.com")
    assert norm_name("The Home Depot, Inc.") == "homedepot"


def test_audit_splits_uncovered_and_finds_misses() -> None:
    companies = [
        CompanyConfig(name="figma", adapter="greenhouse", slug="figma"),
        CompanyConfig(name="sig", adapter="phenom", host="careers.sig.com", aliases=["Susquehanna International Group (SIG)"]),
    ]
    listings = [
        listing("Figma", "Software Engineer Intern (2027)", "https://job-boards.greenhouse.io/figma/jobs/1"),
        listing("Figma", "Data Engineer Intern (2027)", "https://job-boards.greenhouse.io/figma/jobs/2"),
        listing("Susquehanna International Group (SIG)", "Software Developer Intern", "https://careers-sig.icims.com/jobs/1/job"),
        listing("Booz Allen", "Data Scientist Intern", "https://bah.wd1.myworkdayjobs.com/bah_jobs/job/x"),
        listing("Booz Allen", "Software Engineer Intern", "https://bah.wd1.myworkdayjobs.com/bah_jobs/job/y"),
        listing("Toronto Co", "Software Engineer Intern", "https://x.com/1", locations=("Toronto, ON, Canada",)),
        listing("Hardware Co", "FPGA Intern", "https://y.com/1", category="Hardware"),
        listing("Old Co", "Software Engineer Intern", "https://z.com/1", term="Summer 2026"),
    ]
    kv = FakeKV()
    state = StateStore(kv)
    state.record_notification(job_id="g:1", company="figma", title="Software Engineer Intern (2027)", url="u", message_id="m", channel_id="c")
    report = audit(listings, companies, state)
    assert [row["company"] for row in report["uncovered"]] == ["Booz Allen"]
    assert report["uncovered"][0]["count"] == 2 and report["uncovered"][0]["boards"] == {"workday:bah"}
    assert report["covered_companies"] == 2
    assert [miss["title"] for miss in report["misses"]] == ["Data Engineer Intern (2027)", "Software Developer Intern"]
    assert audit(listings, companies, None)["misses"] == []
