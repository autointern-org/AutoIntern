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
from adapters.avature import AvatureAdapter, AvatureBoard
from adapters.citadel import CitadelAdapter, CitadelBoard
from adapters.deshaw import DEShawAdapter
from adapters.eightfold import EightfoldAdapter, EightfoldBoard
from adapters.google import GoogleAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.ibm import IBMAdapter
from adapters.lever import LeverAdapter
from adapters.linkedin import LinkedInAdapter
from adapters.meta import MetaAdapter
from adapters.optiver import OptiverAdapter
from adapters.oracle import OracleAdapter, OracleBoard
from adapters.phenom import PhenomAdapter, PhenomBoard
from adapters.radancy import RadancyAdapter, RadancyBoard
from adapters.rippling import RipplingAdapter
from adapters.smartrecruiters import SmartRecruitersAdapter
from adapters.workable import WorkableAdapter
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
            return self._next_payload(self.by_url[url])
        matches = [key for key in self.by_url if key.startswith("http") and key in url]
        if matches:
            return self._next_payload(self.by_url[max(matches, key=len)])
        return self.payload

    @staticmethod
    def _next_payload(payload: Any) -> Any:
        if isinstance(payload, list):
            if not payload:
                return None
            if len(payload) == 1:
                return payload[0]
            return payload.pop(0)
        return payload


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

    assert len(session.urls) == 1
    assert "/api/pcsx/search" in session.urls[0]
    query = parse_qs(urlparse(session.urls[0]).query)
    assert query.get("query") == ["intern"]
    assert query.get("filter_employment_type") == ["internship"]
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


def test_workday_isolates_empty_json_board() -> None:
    good = load_fixture("workday.json")
    nvidia = ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    etsy = ("etsy.wd5.myworkdayjobs.com", "etsy", "Etsy_Careers")
    session = FakeSession(
        by_url={
            "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs": good,
            "https://etsy.wd5.myworkdayjobs.com/wday/cxs/etsy/Etsy_Careers/jobs": "",
        }
    )
    adapter = WorkdayAdapter(
        [etsy, nvidia],
        company_names={etsy: "etsy", nvidia: "nvidia"},
        session=session,
    )

    jobs = adapter.fetch()

    assert [job.company for job in jobs] == ["nvidia"]
    assert adapter.board_errors[0][0] == "etsy"
    assert "empty body" in adapter.board_errors[0][1]
    etsy_posts = [call for call in session.calls if call["url"].endswith("/Etsy_Careers/jobs")]
    assert len(etsy_posts) == 2


def test_workday_retries_empty_json_after_cookie_warmup() -> None:
    good = load_fixture("workday.json")
    nvidia = ("nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite")
    jobs_url = "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
    session = FakeSession(
        by_url={
            jobs_url: ["", good],
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite": "<html></html>",
        }
    )
    adapter = WorkdayAdapter([nvidia], company_names={nvidia: "nvidia"}, session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "workday:nvidia:NVIDIAExternalCareerSite:JR1987654"
    assert adapter.board_errors == []
    assert [call["method"] for call in session.calls[:3]] == ["POST", "GET", "POST"]
    assert session.calls[0]["headers"]["Referer"] == "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"


def _workday_page(start: int, count: int, total: int) -> dict[str, Any]:
    return {
        "total": total,
        "jobPostings": [
            {
                "title": f"Intern {index}",
                "externalPath": f"/Search/job/Intern_{index}",
                "jobReqId": str(index),
                "locationsText": "Milpitas, CA",
            }
            for index in range(start, start + count)
        ],
    }


def test_workday_retries_pagination_502(monkeypatch: Any) -> None:
    monkeypatch.setattr("adapters.workday.sleep", lambda *_args, **_kwargs: None)
    board = ("kla.wd1.myworkdayjobs.com", "kla", "Search")
    url = "https://kla.wd1.myworkdayjobs.com/wday/cxs/kla/Search/jobs"
    session = FakeSession(
        by_url={
            url: [
                _workday_page(0, 20, 40),
                FakeResponse("", status_code=502),
                _workday_page(20, 20, 40),
            ]
        }
    )
    adapter = WorkdayAdapter([board], company_names={board: "kla"}, session=session)

    jobs = adapter.fetch()

    assert len(jobs) == 40
    assert adapter.board_errors == []
    assert [call["json"]["offset"] for call in session.calls if call["method"] == "POST"] == [0, 20, 20]


def test_workday_keeps_partial_jobs_if_pagination_502_persists(monkeypatch: Any) -> None:
    monkeypatch.setattr("adapters.workday.sleep", lambda *_args, **_kwargs: None)
    board = ("kla.wd1.myworkdayjobs.com", "kla", "Search")
    url = "https://kla.wd1.myworkdayjobs.com/wday/cxs/kla/Search/jobs"
    session = FakeSession(
        by_url={
            url: [
                _workday_page(0, 20, 40),
                FakeResponse("", status_code=502),
                FakeResponse("", status_code=502),
            ]
        }
    )
    adapter = WorkdayAdapter([board], company_names={board: "kla"}, session=session)

    jobs = adapter.fetch()

    assert len(jobs) == 20
    assert adapter.board_errors == []


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


def test_snap_normalizes_elasticsearch_body() -> None:
    session = FakeSession(
        {
            "body": [
                {
                    "_id": "R0046464",
                    "_source": {
                        "id": "R0046464",
                        "title": "Research Intern, User Modeling and Personalization",
                        "employment_type": "Intern",
                        "absolute_url": "https://careers.snap.com/job?id=R0046464",
                        "primary_location": "Bellevue",
                        "offices": [{"location": "Bellevue, Washington", "name": "Bellevue"}],
                    },
                }
            ]
        }
    )
    jobs = SnapAdapter(session=session).fetch()
    assert len(jobs) == 1
    assert jobs[0].id == "snap:R0046464"
    assert jobs[0].title == "Research Intern, User Modeling and Personalization"
    assert "Bellevue" in jobs[0].location
    assert jobs[0].url == "https://careers.snap.com/job?id=R0046464"


def test_phenom_get_without_search_keywords_omits_intern() -> None:
    session = FakeSession(load_fixture("phenom_get.json"))
    adapter = PhenomAdapter(
        [
            PhenomBoard(
                company="github",
                host="www.github.careers",
                variant="get",
                search_keywords="",
            )
        ],
        session=session,
    )
    jobs = adapter.fetch()
    assert "keywords=" not in session.urls[0]
    assert "limit=100" in session.urls[0]
    assert jobs[0].id == "phenom:github:amd-intern-1"


def test_build_adapters_github_empty_search_keywords() -> None:
    adapters = build_adapters(
        [
            CompanyConfig(
                name="github",
                adapter="phenom",
                host="www.github.careers",
                variant="get",
                search_keywords="",
            )
        ]
    )
    phenom = next(adapter for adapter in adapters if isinstance(adapter, PhenomAdapter))
    assert phenom.boards[0].search_keywords == ""


def test_eightfold_intern_query_only_without_extra_params() -> None:
    session = FakeSession(load_fixture("eightfold_pcsx.json"))
    adapter = EightfoldAdapter(
        [
            EightfoldBoard(
                company="microsoft",
                host="apply.careers.microsoft.com",
                domain="microsoft.com",
            )
        ],
        session=session,
    )
    jobs = adapter.fetch()
    assert len(session.urls) == 1
    query = parse_qs(urlparse(session.urls[0]).query)
    assert query.get("query") == ["intern"]
    assert "filter_employment_type" not in query
    assert "location" not in query
    assert len(jobs) == 2


def test_eightfold_retries_429_then_succeeds(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("adapters.eightfold.time.sleep", lambda seconds: sleeps.append(seconds))
    payload = load_fixture("eightfold_pcsx.json")

    class FlakySession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.remaining_failures = 1

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            self.calls.append({"method": "GET", "url": url})
            if self.remaining_failures:
                self.remaining_failures -= 1
                return FakeResponse("Please try again later", status_code=429)
            return FakeResponse(payload)

    adapter = EightfoldAdapter(
        [
            EightfoldBoard(
                company="microsoft",
                host="apply.careers.microsoft.com",
                domain="microsoft.com",
            )
        ],
        session=FlakySession(),
    )
    jobs = adapter.fetch()
    assert len(jobs) == 2
    assert adapter.board_errors == []
    assert sleeps == [5]


def test_eightfold_isolates_board_429(monkeypatch: Any) -> None:
    monkeypatch.setattr("adapters.eightfold.time.sleep", lambda seconds: None)
    payload = load_fixture("eightfold_pcsx.json")

    class MixedSession(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "apply.careers.microsoft.com" in url:
                return FakeResponse("Please try again later", status_code=429)
            return FakeResponse(payload)

    adapter = EightfoldAdapter(
        [
            EightfoldBoard(
                company="microsoft",
                host="apply.careers.microsoft.com",
                domain="microsoft.com",
            ),
            EightfoldBoard(
                company="nvidia",
                host="jobs.nvidia.com",
                domain="nvidia.com",
            ),
        ],
        session=MixedSession(),
    )
    jobs = adapter.fetch()
    assert all(job.company == "nvidia" for job in jobs)
    assert len(jobs) == 2
    assert adapter.board_errors
    assert adapter.board_errors[0][0] == "microsoft"
    assert "429" in adapter.board_errors[0][1]


def test_ibm_uses_jobid_from_url() -> None:
    session = FakeSession(
        {
            "hits": {
                "total": {"value": 2, "relation": "eq"},
                "hits": [
                    {
                        "_id": "hash-1",
                        "_source": {
                            "title": "Data Engineer Intern 2027",
                            "url": "https://careers.ibm.com/careers/JobDetail?jobId=128645",
                            "field_keyword_05": ["United States"],
                            "field_keyword_08": ["Internship"],
                            "field_keyword_18": ["United States"],
                        },
                    },
                    {
                        "_id": "hash-2",
                        "_source": {
                            "title": "Data Engineer Intern 2027",
                            "url": "https://careers.ibm.com/careers/JobDetail?jobId=128526",
                            "field_keyword_05": ["United States"],
                            "field_keyword_08": ["Internship"],
                            "field_keyword_18": ["United States"],
                        },
                    },
                ],
            }
        }
    )
    jobs = IBMAdapter(session=session).fetch()
    assert [job.id for job in jobs] == ["ibm:128645", "ibm:128526"]
    assert jobs[0].url.endswith("jobId=128645")
    assert session.calls[0]["json"]["size"] == 100
    assert session.calls[0]["json"]["from"] == 0


def test_eightfold_pcsx_paginates_by_server_count() -> None:
    def page(ids: list[str], count: int) -> dict[str, Any]:
        return {
            "status": 200,
            "data": {
                "count": count,
                "positions": [
                    {"id": job_id, "name": f"Intern {job_id}", "location": "Redmond, WA", "jobDescription": "x"}
                    for job_id in ids
                ],
            },
        }

    base = "https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query=intern"
    session = FakeSession(
        by_url={
            f"{base}&start=0&num=50&sort_by=timestamp": page(["a", "b"], 3),
            f"{base}&start=2&num=50&sort_by=timestamp": page(["c"], 3),
            f"{base}&start=3&num=50&sort_by=timestamp": page([], 3),
        }
    )
    adapter = EightfoldAdapter(
        [EightfoldBoard(company="microsoft", host="apply.careers.microsoft.com", domain="microsoft.com")],
        session=session,
    )
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == [
        "eightfold:microsoft:a",
        "eightfold:microsoft:b",
        "eightfold:microsoft:c",
    ]
    assert len(session.urls) == 2
    assert adapter.board_errors == []


def test_eightfold_stops_when_server_repeats_a_page() -> None:
    payload = {"data": {"count": 500, "positions": [{"id": "same", "name": "Intern", "location": "x"}]}}
    session = FakeSession(payload)
    adapter = EightfoldAdapter(
        [EightfoldBoard(company="micron", host="micron.eightfold.ai", domain="micron.com")],
        session=session,
    )
    jobs = adapter.fetch()
    assert len(jobs) == 1
    assert len(session.urls) == 2


def test_eightfold_structured_locations_shapes() -> None:
    from adapters.eightfold import structured_locations

    cases = [
        ({"standardizedLocations": ["Bengaluru, KA, IN"], "locations": ["India, Karnataka, Bangalore"]}, ("IN",)),
        ({"standardizedLocations": ["Redmond, WA, US", "Mountain View, CA, US"]}, ("US",)),
        ({"standardizedLocations": ["Redmond, WA, US; Bengaluru, KA, IN"]}, ("US", "IN")),
        ({"standardizedLocations": ["BG, RS"]}, ("RS",)),
        ({"standardizedLocations": ["Taipei City,TW"]}, ("TW",)),
        ({"standardizedLocations": ["SG"]}, ("SG",)),
        ({"standardizedLocations": ["Remote"]}, ()),
        ({"standardizedLocations": ["kr-yongin-03 (3259)"]}, ()),
        ({"standardizedLocations": [{"city": "Redmond", "state": "Washington", "country": "United States"}]}, ()),
        ({"standardizedLocations": [{"city": "Redmond", "countryCode": "US"}]}, ("US",)),
        ({"location": "Los Gatos,California,United States of America"}, ()),
        ({}, ()),
    ]
    for raw, expected_codes in cases:
        codes, names = structured_locations(raw)
        assert codes == expected_codes, raw
    codes, names = structured_locations(
        {"standardizedLocations": ["kr-yongin-03 (3259)"], "locations": ["Korea, Yongin"]}
    )
    assert names == ("Korea, Yongin",)
    codes, names = structured_locations(
        {"standardizedLocations": [{"city": "Redmond", "state": "Washington", "country": "United States"}]}
    )
    assert names == ("Redmond, Washington, United States",)


def test_eightfold_jobs_carry_country_codes() -> None:
    session = FakeSession(
        {"data": {"count": 1, "positions": [
            {"id": "7", "name": "Research Intern", "standardizedLocations": ["Bengaluru, KA, IN"],
             "locations": ["India, Karnataka, Bangalore"]}
        ]}}
    )
    adapter = EightfoldAdapter(
        [EightfoldBoard(company="microsoft", host="apply.careers.microsoft.com", domain="microsoft.com")],
        session=session,
    )
    job = adapter.fetch()[0]
    assert job.country_codes == ("IN",)
    assert job.location_names == ("India, Karnataka, Bangalore",)


def test_oracle_structured_locations() -> None:
    from adapters.oracle import structured_locations

    raw = {
        "PrimaryLocationCountry": "GB",
        "PrimaryLocation": "London, United Kingdom",
        "secondaryLocations": [
            {"CountryCode": "US", "Name": "United States"},
            {"CountryCode": "BR", "Name": "Brazil"},
            "garbage",
        ],
    }
    assert structured_locations(raw) == (("GB", "US", "BR"), ("London, United Kingdom", "United States", "Brazil"))
    assert structured_locations({"PrimaryLocationCountry": "usa", "PrimaryLocation": "Austin, TX, United States"}) == (
        ("US",),
        ("Austin, TX, United States",),
    )
    assert structured_locations({"PrimaryLocation": "Somewhere"}) == ((), ("Somewhere",))


def test_oracle_jobs_carry_country_codes() -> None:
    session = FakeSession(load_fixture("oracle.json"))
    adapter = OracleAdapter([OracleBoard(company="uber", host="example.com", site_number="CX_1")], session=session)
    job = adapter.fetch()[0]
    assert job.location_names == ("Austin, TX, United States",)
    assert job.country_codes == ()


def test_phenom_locations() -> None:
    from adapters.phenom import get_locations, widget_locations

    assert widget_locations(
        {
            "country": "United States of America",
            "cityStateCountry": "San Jose, California, United States of America",
            "multi_location": ["San Jose, California, United States of America", {"location": "Austin, Texas, United States of America"}],
        }
    ) == (
        ("United States of America",),
        (
            "San Jose, California, United States of America",
            "Austin, Texas, United States of America",
        ),
    )
    assert widget_locations({"country": "India", "cityStateCountry": "Bangalore, India"}) == (("India",), ("Bangalore, India",))
    assert widget_locations({}) == ((), ())
    assert get_locations({"country_code": "SG", "country": "Singapore", "full_location": "Singapore", "location_name": "SG,Singapore"}) == (
        ("SG",),
        ("Singapore",),
        ("Singapore", "SG,Singapore"),
    )
    assert get_locations({"country": "Canada"}) == ((), ("Canada",), ())


def test_phenom_jobs_carry_structured_locations() -> None:
    session = FakeSession(load_fixture("phenom_widgets.json"))
    adapter = PhenomAdapter([PhenomBoard(company="cisco", host="careers.cisco.com")], session=session)
    job = adapter.fetch()[0]
    assert job.location_names == ("San Jose, California, United States",)
    session = FakeSession(load_fixture("phenom_get.json"))
    adapter = PhenomAdapter([PhenomBoard(company="amd", host="careers.amd.com", variant="get")], session=session)
    job = adapter.fetch()[0]
    assert job.location_names == ("Austin, TX, United States",)


def test_workable_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("workable.json"))
    adapter = WorkableAdapter(["huggingface"], company_names={"huggingface": "hugging-face"}, session=session)

    jobs = adapter.fetch()

    assert session.urls == ["https://www.workable.com/api/accounts/huggingface?details=true"]
    assert adapter.board_errors == []
    assert [job.id for job in jobs] == ["workable:huggingface:ABC123", "workable:huggingface:DEF456"]
    intern = jobs[0]
    assert intern.company == "hugging-face"
    assert intern.title == "Machine Learning Intern"
    assert intern.location == "New York, New York, United States"
    assert intern.url == "https://apply.workable.com/j/ABC123"
    assert "Train transformers with us." in intern.jd_text and "Python" in intern.jd_text
    assert intern.posted_at == "2026-08-01"
    assert intern.country_codes == ("US", "FR")
    assert intern.country_names == ("United States", "France")
    assert intern.location_names == (
        "New York, New York, United States",
        "Paris, Île-de-France, France",
    )
    assert jobs[1].country_codes == ("FR",)


def test_workable_isolates_failed_account() -> None:
    class MixedSession(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "bad" in url:
                return FakeResponse("Not Found", status_code=404)
            return FakeResponse(load_fixture("workable.json"))

    adapter = WorkableAdapter(["bad", "huggingface"], session=MixedSession())
    jobs = adapter.fetch()
    assert len(jobs) == 2
    assert adapter.board_errors and adapter.board_errors[0][0] == "bad"


def test_smartrecruiters_lists_then_fetches_intern_details() -> None:
    base = "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings"
    session = FakeSession(
        by_url={
            f"{base}?limit=100&offset=0": load_fixture("smartrecruiters_list.json"),
            f"{base}/744000001": load_fixture("smartrecruiters_detail.json"),
            f"{base}/744000002": {"id": "744000002", "postingUrl": "https://jobs.smartrecruiters.com/ServiceNow/744000002-intern", "jobAd": {"sections": {}}},
        }
    )
    adapter = SmartRecruitersAdapter(["ServiceNow"], company_names={"ServiceNow": "servicenow"}, session=session)

    jobs = adapter.fetch()

    # One list page (totalFound=3 reached) + one detail per intern-titled posting; the staff role is skipped.
    assert session.urls == [f"{base}?limit=100&offset=0", f"{base}/744000001", f"{base}/744000002"]
    assert [job.id for job in jobs] == ["smartrecruiters:ServiceNow:744000001", "smartrecruiters:ServiceNow:744000002"]
    intern = jobs[0]
    assert intern.company == "servicenow"
    assert intern.title == "Software Engineer Intern"
    assert intern.location == "San Diego, California, United States"
    assert intern.url == "https://jobs.smartrecruiters.com/ServiceNow/744000001-software-engineer-intern"
    assert "Build platform features." in intern.jd_text
    assert "Pursuing a Bachelor's degree" in intern.jd_text
    assert "About ServiceNow" not in intern.jd_text
    assert intern.posted_at == "2026-08-20T18:45:00.129Z"
    assert intern.country_codes == ("US",)
    assert intern.location_names == ("San Diego, California, United States", "Remote")
    assert jobs[1].country_codes == ("AU",)


def test_smartrecruiters_survives_detail_failure() -> None:
    base = "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings"

    class FlakySession(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if url.endswith("offset=0"):
                return FakeResponse(load_fixture("smartrecruiters_list.json"))
            return FakeResponse("boom", status_code=500)

    adapter = SmartRecruitersAdapter(["ServiceNow"], session=FlakySession())
    jobs = adapter.fetch()
    assert len(jobs) == 2
    assert jobs[0].jd_text == ""
    assert jobs[0].url == "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings/744000001"
    assert adapter.board_errors == []


def test_rippling_lists_then_fetches_intern_details() -> None:
    base = "https://ats.rippling.com/api/v2/board/rippling/jobs"
    session = FakeSession(
        by_url={
            f"{base}?page=0&pageSize=100": load_fixture("rippling_list.json"),
            f"{base}/aaa-111": load_fixture("rippling_detail.json"),
            f"{base}/bbb-222": {"uuid": "bbb-222", "description": "<p>Plain string description.</p>", "createdOn": "2026-08-10T00:00:00Z"},
        }
    )
    adapter = RipplingAdapter(["rippling"], company_names={"rippling": "rippling"}, session=session)

    jobs = adapter.fetch()

    assert session.urls == [f"{base}?page=0&pageSize=100", f"{base}/aaa-111", f"{base}/bbb-222"]
    assert [job.id for job in jobs] == ["rippling:rippling:aaa-111", "rippling:rippling:bbb-222"]
    intern = jobs[0]
    assert intern.title == "Machine Learning Software Engineer Intern - Winter 2027"
    assert intern.location == "San Francisco, CA"
    assert intern.url == "https://ats.rippling.com/rippling/jobs/aaa-111"
    assert "Build ML systems." in intern.jd_text and "pursuing a Bachelor's degree" in intern.jd_text
    assert intern.posted_at == "2026-08-15T10:00:00Z"
    assert intern.country_codes == ("US",)
    assert intern.country_names == ("United States",)
    assert intern.location_names == ("San Francisco, CA",)
    india = jobs[1]
    assert india.country_codes == ("IN",)
    assert india.location_names == ("Bangalore, India", "Remote (India)")
    assert india.jd_text == "Plain string description."


def test_rippling_paginates_until_total_pages() -> None:
    base = "https://ats.rippling.com/api/v2/board/rippling/jobs"
    page0 = {"items": [{"id": "x1", "name": "Intern A", "url": "u", "locations": []}], "page": 0, "pageSize": 100, "totalItems": 2, "totalPages": 2}
    page1 = {"items": [{"id": "x2", "name": "Intern B", "url": "u", "locations": []}], "page": 1, "pageSize": 100, "totalItems": 2, "totalPages": 2}
    session = FakeSession(by_url={f"{base}?page=0&pageSize=100": page0, f"{base}?page=1&pageSize=100": page1, f"{base}/x1": {}, f"{base}/x2": {}})
    adapter = RipplingAdapter(["rippling"], session=session)
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == ["rippling:rippling:x1", "rippling:rippling:x2"]
    assert session.urls[:2] == [f"{base}?page=0&pageSize=100", f"{base}?page=1&pageSize=100"]
    assert jobs[0].location == "Unspecified"


def _meta_detail(title: str, employment: str) -> str:
    payload = {
        "@type": "JobPosting",
        "title": title,
        "employmentType": employment,
        "datePosted": "2026-08-20",
        "description": "<p>Build things.</p>",
        "jobLocation": [{"address": {"addressLocality": "Menlo Park", "addressRegion": "CA", "addressCountry": "US"}}],
    }
    return f'<html><script type="application/ld+json">{json.dumps(payload)}</script></html>'


def test_meta_fetches_only_new_ids_plus_known_interns() -> None:
    sitemap = "".join(f"<url><loc>https://www.metacareers.com/profile/job_details/{i}/</loc></url>" for i in ("1", "2", "3", "4"))
    by_url = {
        "https://www.metacareers.com/jobsearch/sitemap.xml": FakeResponse(sitemap, status_code=200),
        "https://www.metacareers.com/profile/job_details/1/": FakeResponse(_meta_detail("Software Engineer", "Full-time"), status_code=200),
        "https://www.metacareers.com/profile/job_details/2/": FakeResponse(_meta_detail("Software Engineer Intern", "Internship"), status_code=200),
        "https://www.metacareers.com/profile/job_details/3/": FakeResponse(_meta_detail("Research Intern", "Internship"), status_code=200),
        "https://www.metacareers.com/profile/job_details/4/": FakeResponse("", status_code=500),
    }
    session = FakeSession(by_url=by_url)
    # Run 1: nothing known, cap of 2 -> checks ids 1 and 2 only.
    adapter = MetaAdapter(session=session, max_details=2)
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == ["meta:2"]
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].location == "Menlo Park, CA, US"
    assert adapter.checked_ids == {"1", "2"}
    assert adapter.intern_ids == {"2"}
    assert adapter.backlog == 2

    # Run 2: known ids skipped, known intern re-fetched to stay live, next new ids checked.
    session = FakeSession(by_url=by_url)
    adapter = MetaAdapter(session=session, max_details=2, known_ids={"1", "2"}, intern_ids={"2"})
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == ["meta:2", "meta:3"]
    detail_urls = [u for u in session.urls if "job_details" in u]
    assert detail_urls == [
        "https://www.metacareers.com/profile/job_details/2/",
        "https://www.metacareers.com/profile/job_details/3/",
        "https://www.metacareers.com/profile/job_details/4/",
    ]
    assert adapter.checked_ids == {"1", "2", "3"}  # 4 failed, so it will be retried
    assert adapter.intern_ids == {"2", "3"}
    assert adapter.backlog == 0

    # Ids that left the sitemap are forgotten.
    adapter = MetaAdapter(session=FakeSession(by_url=by_url), max_details=0, known_ids={"1", "gone"}, intern_ids=set())
    adapter.fetch()
    assert adapter.checked_ids == {"1"}


def test_meta_raises_when_sitemap_is_empty() -> None:
    session = FakeSession(by_url={"https://www.metacareers.com/jobsearch/sitemap.xml": FakeResponse("<html>Error</html>", status_code=200)})
    import pytest

    with pytest.raises(RuntimeError):
        MetaAdapter(session=session).fetch()


def _linkedin_session(list_html: str, detail_html: str, *, rate_limit_once: bool = False) -> FakeSession:
    class S(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.limited = rate_limit_once

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "seeMoreJobPostings" in url:
                if self.limited:
                    self.limited = False
                    resp = FakeResponse("slow down", status_code=429)
                    resp.headers = {"Retry-After": "7"}
                    return resp
                return FakeResponse(list_html if "start=0" in url else "<ul></ul>", status_code=200)
            return FakeResponse(detail_html, status_code=200)

    return S()


def test_linkedin_parses_cards_and_fetches_intern_details_only() -> None:
    list_html = (FIXTURES / "linkedin_list.html").read_text()
    detail_html = (FIXTURES / "linkedin_detail.html").read_text()
    sleeps: list[float] = []
    session = _linkedin_session(list_html, detail_html)
    adapter = LinkedInAdapter("1337", session=session, sleep=sleeps.append, max_details=5)

    jobs = adapter.fetch()

    assert [job.id for job in jobs] == ["linkedin:linkedin:111", "linkedin:linkedin:333"]
    assert jobs[0].title == "Software Engineer Intern"
    assert jobs[0].location == "Mountain View, CA"
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/software-engineer-intern-at-linkedin-111"
    assert jobs[0].posted_at == "2026-08-20"
    assert "pursuing a Bachelor's degree" in jobs[0].jd_text
    detail_urls = [u for u in session.urls if "jobPosting/" in u]
    assert detail_urls == [
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/111",
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/333",
    ]
    assert adapter.checked_ids == {"111", "222", "333"}
    assert adapter.intern_ids == {"111", "333"}
    assert all(gap == 6.0 for gap in sleeps)


def test_linkedin_skips_known_non_interns_and_refreshes_known_interns() -> None:
    list_html = (FIXTURES / "linkedin_list.html").read_text()
    detail_html = (FIXTURES / "linkedin_detail.html").read_text()
    session = _linkedin_session(list_html, detail_html)
    adapter = LinkedInAdapter("1337", session=session, sleep=lambda s: None, known_ids={"111", "222"}, intern_ids={"111"}, max_details=1)
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == ["linkedin:linkedin:111", "linkedin:linkedin:333"]
    assert adapter.backlog == 0


def test_linkedin_backs_off_on_429() -> None:
    list_html = (FIXTURES / "linkedin_list.html").read_text()
    detail_html = (FIXTURES / "linkedin_detail.html").read_text()
    sleeps: list[float] = []
    session = _linkedin_session(list_html, detail_html, rate_limit_once=True)
    adapter = LinkedInAdapter("1337", session=session, sleep=sleeps.append, max_details=5)
    jobs = adapter.fetch()
    assert len(jobs) == 2
    assert 7.0 in sleeps


def test_linkedin_raises_when_no_cards() -> None:
    import pytest

    session = _linkedin_session("<ul></ul>", "")
    with pytest.raises(RuntimeError):
        LinkedInAdapter("1337", session=session, sleep=lambda s: None).fetch()


def _html(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_avature_lists_pages_and_fetches_intern_details() -> None:
    class S(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "jobOffset=0" in url:
                return FakeResponse(_html("avature_list.html"), status_code=200)
            if "jobOffset=" in url:
                return FakeResponse("<div></div>", status_code=200)
            return FakeResponse(_html("avature_detail.html"), status_code=200)

    session = S()
    adapter = AvatureAdapter([AvatureBoard("two-sigma", "careers.twosigma.com", "careers/OpenRoles")], session=session)
    jobs = adapter.fetch()
    assert [job.id for job in jobs] == ["avature:two-sigma:14096", "avature:two-sigma:19932"]
    assert session.urls[0] == "https://careers.twosigma.com/careers/OpenRoles/?jobOffset=0"
    assert session.urls[1] == "https://careers.twosigma.com/careers/OpenRoles/?jobOffset=3"
    intern = jobs[0]
    assert intern.title == "AI Research Scientist - Intern [2027 Summer]"
    assert intern.location == "United States - NY New York"
    assert intern.url.endswith("/JobDetail/New-York-AI-Research-Scientist-Intern-2027-Summer/14096")
    assert "ML research" in intern.jd_text and "Bachelor's" in intern.jd_text
    assert jobs[1].location == "Sao Paulo, São Paulo, Brazil"
    # Senior Engineer (13000) is checked without a detail fetch; the filler article is ignored.
    assert adapter.checked_by_company["two-sigma"] == ({"14096", "13000", "19932"}, {"14096", "19932"})
    assert adapter.board_errors == []


def test_avature_isolates_board_failure() -> None:
    class S(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "bloomberg" in url:
                return FakeResponse("nope", status_code=500)
            if "jobOffset=0" in url:
                return FakeResponse(_html("avature_list.html"), status_code=200)
            return FakeResponse("<div></div>" if "jobOffset" in url else _html("avature_detail.html"), status_code=200)

    adapter = AvatureAdapter(
        [AvatureBoard("bloomberg", "bloomberg.avature.net", "careers/SearchJobs"), AvatureBoard("two-sigma", "careers.twosigma.com", "careers/OpenRoles")],
        session=S(),
    )
    jobs = adapter.fetch()
    assert len(jobs) == 2 and adapter.board_errors[0][0] == "bloomberg"


def test_citadel_lists_via_admin_ajax_and_reads_ldjson() -> None:
    class S(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.challenged = True

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            self.calls.append({"url": url, "data": kwargs.get("data"), "headers": kwargs.get("headers")})
            if self.challenged:
                self.challenged = False
                return FakeResponse("Just a moment...", status_code=403)
            return FakeResponse(load_fixture("citadel_list.json"), status_code=200)

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            return FakeResponse(_html("citadel_detail.html"), status_code=200)

    session = S()
    sleeps: list[float] = []
    adapter = CitadelAdapter([CitadelBoard("citadel", "www.citadel.com")], session=session, sleep=sleeps.append)
    jobs = adapter.fetch()
    assert session.calls[0]["data"]["action"] == "careers_listing_filter"
    assert session.calls[0]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert sleeps == [3.0]  # one Cloudflare challenge, one retry
    assert [job.id for job in jobs] == ["citadel:citadel:software-engineer-intern-us", "citadel:citadel:software-engineer-intern-asia"]
    intern = jobs[0]
    assert intern.title == "Software Engineer – Intern (US)"
    assert intern.location == "New York, NY, US; Miami, FL, US"
    assert intern.posted_at == "2026-08-22"
    assert "trading systems" in intern.jd_text
    assert intern.country_codes == ("US",)
    assert intern.location_names == ("Miami, New York", "New York, NY, US; Miami, FL, US")
    assert adapter.checked_by_company["citadel"][0] == {"software-engineer-intern-us", "quant-researcher", "software-engineer-intern-asia"}


def test_deshaw_reads_next_data() -> None:
    session = FakeSession(FakeResponse(_html("deshaw.html"), status_code=200))
    jobs = DEShawAdapter(session=session).fetch()
    assert session.urls == ["https://www.deshaw.com/careers"]
    assert [job.id for job in jobs] == ["deshaw:5709", "deshaw:5800", "deshaw:100"]
    intern = jobs[0]
    assert intern.company == "de-shaw"
    assert intern.title == "Software Developer Intern (New York) – Summer 2027"
    assert intern.location == "New York"
    assert intern.url == "https://www.deshaw.com/careers/Software-Developer-Intern-New-York-Summer-2027-5709"
    assert "software developer interns" in intern.jd_text and "Build systems" in intern.jd_text
    assert jobs[1].location == "London"


def test_radancy_parses_results_html_and_ldjson() -> None:
    class S(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "/search-jobs/results" in url:
                return FakeResponse(load_fixture("radancy_results.json"), status_code=200)
            return FakeResponse(_html("radancy_detail.html"), status_code=200)

    session = S()
    adapter = RadancyAdapter([RadancyBoard("intuit", "jobs.intuit.com")], session=session)
    jobs = adapter.fetch()
    assert "RecordsPerPage=40" in session.urls[0] and "CurrentPage=1" in session.urls[0]
    assert [job.id for job in jobs] == ["radancy:intuit:87369448720", "radancy:intuit:96174872720"]
    intern = jobs[0]
    assert intern.title == "Software Engineer Intern"
    assert intern.url == "https://jobs.intuit.com/job/mountain-view/software-engineer-intern/27595/87369448720"
    assert intern.location == "Mountain View, California"
    assert intern.posted_at == "2026-8-14"
    assert intern.country_codes == ("US",)
    assert "Join Intuit" in intern.jd_text
    assert jobs[1].posted_at == "2026-8-14"  # ld+json wins over the card date
    # The principal architect was checked without a detail fetch.
    assert adapter.checked_by_company["intuit"][0] == {"87369448720", "96174872720", "79031267008"}
    assert adapter.checked_by_company["intuit"][1] == {"87369448720", "96174872720"}


def test_radancy_prefixed_routes_and_known_ids() -> None:
    class S(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "/search-jobs/results" in url:
                return FakeResponse(load_fixture("radancy_results.json"), status_code=200)
            return FakeResponse(_html("radancy_detail.html"), status_code=200)

    session = S()
    adapter = RadancyAdapter(
        [RadancyBoard("palo-alto-networks", "jobs.paloaltonetworks.com", "/en")],
        session=session,
        known={"palo-alto-networks": ({"87369448720", "96174872720", "79031267008"}, {"96174872720"})},
    )
    jobs = adapter.fetch()
    assert session.urls[0].startswith("https://jobs.paloaltonetworks.com/en/search-jobs/results?")
    # Only the known intern is refreshed; the known non-intern is skipped.
    assert [job.id for job in jobs] == ["radancy:palo-alto-networks:96174872720"]


def test_apple_retry_once_recovers_from_a_timeout() -> None:
    import requests as _requests

    from core.http import retry_once

    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _requests.Timeout("read timed out")
        return "ok"

    assert retry_once(flaky) == "ok"
    assert len(calls) == 2

    def always() -> str:
        raise _requests.Timeout("still down")

    import pytest

    with pytest.raises(_requests.Timeout):
        retry_once(always)


def test_oracle_isolates_a_failing_tenant() -> None:
    class S(FakeSession):
        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            if "jpmc" in url:
                return FakeResponse("Service Temporarily Unavailable", status_code=503)
            return FakeResponse(load_fixture("oracle.json"), status_code=200)

    adapter = OracleAdapter(
        [OracleBoard(company="jpmorgan", host="jpmc.fa.oraclecloud.com", site_number="CX_1001"), OracleBoard(company="uber", host="iaziqy.fa.ocs.oraclecloud.com", site_number="CX_1")],
        session=S(),
    )
    jobs = adapter.fetch()
    assert [job.company for job in jobs] == ["uber"]
    assert adapter.board_errors == [("jpmorgan", adapter.board_errors[0][1])]
    assert "503" in adapter.board_errors[0][1]
