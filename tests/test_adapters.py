from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from adapters.amazon import AmazonAdapter
from adapters.apple import AppleAdapter
from adapters.ashby import AshbyAdapter
from adapters.atlassian import AtlassianAdapter
from adapters.eightfold import EightfoldAdapter, EightfoldBoard
from adapters.google import GoogleAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.ibm import IBMAdapter
from adapters.lever import LeverAdapter
from adapters.optiver import OptiverAdapter
from adapters.oracle import OracleAdapter, OracleBoard
from adapters.phenom import PhenomAdapter, PhenomBoard
from adapters.snap import SnapAdapter
from adapters.tesla import TeslaAdapter
from adapters.tiktok import TikTokAdapter
from adapters.workday import WorkdayAdapter
from core.config import CompanyConfig
from core.pipeline import build_adapters


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        if content_type is None:
            content_type = (
                "application/json"
                if isinstance(payload, (dict, list))
                else "text/html; charset=utf-8"
            )
        self.headers = {"Content-Type": content_type}
        if headers:
            self.headers.update(headers)

    def json(self) -> Any:
        if isinstance(self.payload, (dict, list)):
            return self.payload
        raise ValueError("No JSON object could be decoded")

    @property
    def text(self) -> str:
        if isinstance(self.payload, (dict, list)):
            return json.dumps(self.payload)
        return str(self.payload or "")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, payload: Any = None, by_url: dict[str, Any] | None = None) -> None:
        self.payload = payload
        self.by_url = by_url or {}
        self.urls: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._respond("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._respond("POST", url, **kwargs)

    def _respond(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        self.calls.append({"method": method, "url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        payload = self._payload_for(url)
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(payload)

    def _payload_for(self, url: str) -> Any:
        if url in self.by_url:
            return self.by_url[url]
        matches = [key for key in self.by_url if key.startswith("http") and key in url]
        if matches:
            return self.by_url[max(matches, key=len)]
        return self.payload


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_greenhouse_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("greenhouse_anthropic.json"))
    adapter = GreenhouseAdapter(["anthropic"], company_names={"anthropic": "anthropic"}, session=session)

    jobs = adapter.fetch()

    assert session.urls == [
        "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true"
    ]
    assert len(jobs) == 1
    assert jobs[0].id == "greenhouse:anthropic:111111"
    assert jobs[0].company == "anthropic"
    assert jobs[0].title == "Software Engineer Intern, Product"
    assert jobs[0].location == "San Francisco, CA"
    assert jobs[0].url == "https://job-boards.greenhouse.io/anthropic/jobs/654321"
    assert "Build useful AI systems" in jobs[0].jd_text
    assert jobs[0].posted_at == "2026-05-18T09:00:00-07:00"


def test_greenhouse_includes_office_country() -> None:
    session = FakeSession(
        {
            "jobs": [
                {
                    "id": 8097801,
                    "internal_job_id": 8097801,
                    "title": "Software Engineer Intern",
                    "absolute_url": "https://job-boards.greenhouse.io/stripe/jobs/8097801",
                    "location": {"name": "Dublin"},
                    "content": "<p>Build payments infrastructure.</p>",
                    "offices": [{"name": "IE-Dublin", "location": "Dublin, Dublin, Ireland"}],
                }
            ]
        }
    )
    adapter = GreenhouseAdapter(["stripe"], company_names={"stripe": "stripe"}, session=session)

    jobs = adapter.fetch()

    assert jobs[0].location == "Dublin; IE-Dublin; Dublin, Dublin, Ireland"
    assert "ireland" in jobs[0].location.lower() or "IE-Dublin" in jobs[0].location


def test_eightfold_pcsx_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("eightfold_pcsx.json"))
    adapter = EightfoldAdapter(
        [
            EightfoldBoard(
                company="microsoft",
                host="apply.careers.microsoft.com",
                domain="microsoft.com",
                extra_params={"location": "United States", "filter_employment_type": "internship"},
            )
        ],
        session=session,
    )

    jobs = adapter.fetch()

    assert len(session.urls) == 2
    assert all("/api/pcsx/search" in url for url in session.urls)
    queries = [parse_qs(urlparse(url).query) for url in session.urls]
    assert any("filter_employment_type" not in query for query in queries)
    assert any(query.get("filter_employment_type") == ["internship"] for query in queries)
    assert [job.id for job in jobs] == ["eightfold:microsoft:1827364", "eightfold:microsoft:1827365"]
    assert jobs[0].title == "Software Engineering Intern"
    assert jobs[0].location == "Redmond, Washington, United States"
    assert jobs[0].url == "https://apply.careers.microsoft.com/careers/job/1827364"
    assert "cloud services" in jobs[0].jd_text
    assert jobs[1].location == "Cambridge, Massachusetts, United States"


def test_eightfold_apply_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("eightfold_apply.json"))
    adapter = EightfoldAdapter(
        [
            EightfoldBoard(
                company="netflix",
                host="explore.jobs.netflix.net",
                domain="netflix.com",
                api="apply",
            )
        ],
        session=session,
    )

    jobs = adapter.fetch()

    assert "/api/apply/v2/jobs" in session.urls[0]
    assert jobs[0].id == "eightfold:netflix:nf-1"
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].location == "Los Gatos, California"
    assert jobs[0].url == "https://explore.jobs.netflix.net/careers/job/nf-1"


def test_ashby_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("ashby.json"))
    adapter = AshbyAdapter(["perplexity"], company_names={"perplexity": "perplexity"}, session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "ashby:perplexity:ashby-job-1"
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].location == "San Francisco, CA"
    assert "search assistant" in jobs[0].jd_text


def test_workday_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("workday.json"))
    board = ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    adapter = WorkdayAdapter([board], company_names={board: "nvidia"}, session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "workday:nvidia:NVIDIAExternalCareerSite:JR1987654"
    assert jobs[0].company == "nvidia"
    assert jobs[0].url == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Santa-Clara/Software-Intern_JR1987654"
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["searchText"] == "intern"
    assert session.calls[0]["json"]["offset"] == 0


def test_google_parses_ds1_blob() -> None:
    record = [
        "123456789",
        "Student Researcher, BS/MS",
        "/about/careers/applications/jobs/results/123456789-student-researcher",
        [None, "Responsibilities"],
        [None, "Min quals"],
        None,
        None,
        "Google",
        None,
        [["Mountain View, CA, USA", [], "Mountain View", "94043", "CA", "USA"]],
        [None, "About the job"],
        [4],
        [1710000000, 0],
        [1710000001, 0],
        1710000002,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    html = (
        "<html>AF_initDataCallback({key:'ds:1', data:"
        + json.dumps([[record], None, 1, 20])
        + ", sideChannel:{}});</html>"
    )
    session = FakeSession(html)
    jobs = GoogleAdapter(session=session).fetch()

    assert jobs[0].id == "google:123456789"
    assert jobs[0].title == "Student Researcher, BS/MS"
    assert "Mountain View, CA, USA" in jobs[0].location
    assert jobs[0].url.endswith("123456789-student-researcher")
    assert "About the job" in jobs[0].jd_text


def test_google_parses_ds1_blob_after_registry_and_company_list() -> None:
    record = [
        "123456789",
        "Student Researcher, BS/MS",
        "/about/careers/applications/jobs/results/123456789-student-researcher",
        [None, "Responsibilities"],
        [None, "Min quals"],
        None,
        None,
        "Google",
        None,
        [["Mountain View, CA, USA", [], "Mountain View", "94043", "CA", "USA"]],
        [None, "About the job"],
        [4],
        [1710000000, 0],
        [1710000001, 0],
        1710000002,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    registry = (
        'var AF_initDataKeys = ["ds:0","ds:1"]; '
        "var AF_dataServiceRequests = {'ds:0' : {id:'x',request:[]},'ds:1' : {id:'y',request:[[\"intern\"]]}};"
    )
    company_list = (
        "AF_initDataCallback({key:'ds:0', data:"
        + json.dumps(
            [
                [
                    ["uuid-dm", "DeepMind", "deepmind"],
                    ["uuid-gf", "GFiber", "gfiber"],
                    ["uuid-g", "Google", "google"],
                ]
            ]
        )
        + "});"
    )
    jobs_blob = (
        "AF_initDataCallback({key:'ds:1', data:"
        + json.dumps([[record], None, 1, 20])
        + ", sideChannel:{}});"
    )
    html = f"<html>{registry}{company_list}{jobs_blob}</html>"
    session = FakeSession(html)
    jobs = GoogleAdapter(session=session).fetch()

    assert jobs[0].id == "google:123456789"
    assert jobs[0].title == "Student Researcher, BS/MS"


def test_google_falls_back_to_html_cards() -> None:
    html = '<a href="/about/careers/applications/jobs/results/555-software-intern">Intern</a>'
    session = FakeSession(html)
    jobs = GoogleAdapter(session=session).fetch()
    assert jobs[0].id == "google:555"
    assert jobs[0].url.endswith("555-software-intern")


def test_google_fails_loudly_when_page_has_no_jobs() -> None:
    session = FakeSession("<html><body>no jobs here</body></html>")
    try:
        GoogleAdapter(session=session).fetch()
    except RuntimeError as exc:
        assert "no job records were parsed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_amazon_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("amazon.json"))
    adapter = AmazonAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == [
        "https://www.amazon.jobs/en/search.json?normalized_country_code[]=USA&base_query=intern&result_limit=100&sort=recent"
    ]
    assert jobs[0].id == "amazon:amazon:2828282"
    assert jobs[0].url == "https://www.amazon.jobs/en/jobs/2828282/software-development-engineer-intern"


def test_amazon_fetches_aws_category_when_configured() -> None:
    session = FakeSession(load_fixture("amazon.json"))
    adapter = AmazonAdapter(["amazon", "aws"], session=session)

    jobs = adapter.fetch()

    assert len(session.urls) == 2
    assert "business_category[]=aws" not in session.urls[0]
    assert session.urls[1].endswith("&business_category[]=aws")
    assert [job.company for job in jobs] == ["amazon", "aws"]
    assert jobs[1].id == "amazon:aws:2828282"


def test_apple_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("apple.json"))
    adapter = AppleAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls[0] == "https://jobs.apple.com/api/v1/csrfToken"
    assert session.urls.count("https://jobs.apple.com/api/v1/search") == 2
    bodies = [call["json"] for call in session.calls if call["method"] == "POST"]
    assert bodies[0]["filters"] == {"locations": ["postLocation-USA"]}
    assert bodies[1]["filters"]["teams"] == [{"team": "teamsAndSubTeams-STDNT", "subTeam": "subTeam-INTRN"}]
    assert bodies[0]["query"] == "intern"
    post_headers = [call["headers"] for call in session.calls if call["method"] == "POST"]
    assert post_headers[0]["Origin"] == "https://jobs.apple.com"
    assert post_headers[0]["Referer"] == "https://jobs.apple.com/en-us/search"
    assert jobs[0].id == "apple:200611111"
    assert jobs[0].title == "Software Engineering Internship"
    assert jobs[0].location == "Cupertino, California, United States"
    assert jobs[0].url == (
        "https://jobs.apple.com/en-us/details/200611111/software-engineering-internship?team=STDNT"
    )


def test_apple_raises_on_akamai_html() -> None:
    session = FakeSession("<html>challenge</html>")
    adapter = AppleAdapter(session=session)

    try:
        adapter.fetch()
    except RuntimeError as exc:
        assert "challenge" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_lever_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("lever.json"))
    adapter = LeverAdapter(["stripe"], company_names={"stripe": "stripe"}, session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://api.lever.co/v0/postings/stripe?mode=json"]
    assert jobs[0].id == "lever:stripe:lever-job-1"
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].location == "San Francisco"
    assert jobs[0].url == "https://jobs.lever.co/example/lever-job-1"


def test_phenom_widgets_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("phenom_widgets.json"))
    adapter = PhenomAdapter(
        [PhenomBoard(company="cisco", host="jobs.cisco.com", variant="widgets")],
        session=session,
    )

    jobs = adapter.fetch()

    assert session.urls == ["https://jobs.cisco.com/widgets"]
    assert session.calls[0]["json"]["ddoKey"] == "refineSearch"
    assert session.calls[0]["json"]["keywords"] == "intern"
    assert jobs[0].id == "phenom:cisco:cisco-1"
    assert jobs[0].location == "San Jose, California, United States"


def test_phenom_get_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("phenom_get.json"))
    adapter = PhenomAdapter(
        [PhenomBoard(company="amd", host="careers.amd.com", variant="get")],
        session=session,
    )

    jobs = adapter.fetch()

    assert "keywords=intern" in session.urls[0]
    assert jobs[0].id == "phenom:amd:amd-intern-1"
    assert jobs[0].url == "https://careers.amd.com/jobs/amd-intern-1"


def test_oracle_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("oracle.json"))
    adapter = OracleAdapter(
        [OracleBoard(company="oracle", host="eeho.fa.us2.oraclecloud.com", site_number="CX_1")],
        session=session,
    )

    jobs = adapter.fetch()

    assert "expand=requisitionList.secondaryLocations" in session.urls[0]
    assert "finder=findReqs;siteNumber=CX_1,keyword=intern,limit=25,sortBy=POSTING_DATES_DESC" in session.urls[0]
    assert jobs[0].id == "oracle:oracle:ORA123"
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].url == "https://careers.oracle.com/en/sites/jobsearch/job/ORA123"


def test_tesla_normalizes_intern_listings() -> None:
    session = FakeSession(load_fixture("tesla.json"))
    adapter = TeslaAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://www.tesla.com/cua-api/apps/careers/state?region=5"]
    assert len(jobs) == 1
    assert jobs[0].id == "tesla:505789688"
    assert jobs[0].title == "Internship, Software Engineer"
    assert jobs[0].location == "Palo Alto, California"
    assert jobs[0].url == "https://www.tesla.com/careers/search/job/505789688"


def test_snap_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("snap.json"))
    adapter = SnapAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://careers.snap.com/api/jobs"]
    assert len(jobs) == 2
    assert jobs[0].id == "snap:R0045795"
    assert jobs[0].title == "Software Engineer Intern"


def test_tiktok_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("tiktok.json"))
    adapter = TikTokAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls[0].startswith("https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts")
    assert session.calls[0]["json"] == {
        "keyword": "",
        "limit": 100,
        "offset": 0,
        "recruitment_id_list": ["202"],
    }
    headers = session.calls[0]["headers"] or {}
    assert headers.get("website-path") == "tiktok"
    assert headers.get("origin") == "https://lifeattiktok.com"
    assert jobs[0].id == "tiktok:747123"
    assert jobs[0].location == "San Jose, United States"


def test_ibm_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("ibm.json"))
    adapter = IBMAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://www-api.ibm.com/search/api/v2"]
    assert session.calls[0]["json"]["appId"] == "careers"
    assert jobs[0].id == "ibm:ibm-intern-1"
    assert jobs[0].url == "https://www.ibm.com/careers/us-en/job/ibm-intern-1"
    assert jobs[0].location == "Austin, United States"


def test_optiver_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("optiver.json"))
    adapter = OptiverAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://www.optiver.com/en/api/v1/jobs?level=internship"]
    assert jobs[0].id == "optiver:opt-1"
    assert jobs[0].location == "Chicago"
    assert jobs[0].url == "https://www.optiver.com/join-us/jobs/technology/chicago/software-engineer-internship-2027-start/"


def test_atlassian_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("atlassian.json"))
    adapter = AtlassianAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://www.atlassian.com/endpoint/careers/listings"]
    assert jobs[0].id == "atlassian:atl-1"
    assert jobs[0].title == "Software Engineer Intern"


def test_atlassian_empty_body_is_clear_fetch_error() -> None:
    session = FakeSession("")
    with pytest.raises(RuntimeError, match="not JSON"):
        AtlassianAdapter(session=session).fetch()


def test_build_adapters_maps_microsoft_and_new_platforms() -> None:
    adapters = build_adapters(
        [
            CompanyConfig(name="microsoft", adapter="microsoft"),
            CompanyConfig(name="stripe", adapter="lever", slug="stripe"),
            CompanyConfig(name="cisco", adapter="phenom", host="jobs.cisco.com", variant="widgets"),
            CompanyConfig(name="oracle", adapter="oracle", host="eeho.fa.us2.oraclecloud.com", site_number="CX_1"),
            CompanyConfig(name="tesla", adapter="tesla"),
            CompanyConfig(name="optiver", adapter="optiver"),
            CompanyConfig(name="snap", adapter="snap"),
        ]
    )
    names = [type(adapter).__name__ for adapter in adapters]
    assert "EightfoldAdapter" in names
    assert "MicrosoftAdapter" not in names
    assert "LeverAdapter" in names
    assert "PhenomAdapter" in names
    assert "OracleAdapter" in names
    assert "TeslaAdapter" in names
    assert "OptiverAdapter" in names
    assert "SnapAdapter" in names
