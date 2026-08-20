from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adapters.amazon import AmazonAdapter
from adapters.apple import AppleAdapter
from adapters.ashby import AshbyAdapter
from adapters.google import GoogleAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.microsoft import MicrosoftAdapter
from adapters.workday import WorkdayAdapter


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self.payload)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self.payload)


def load_fixture(name: str) -> dict[str, Any]:
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


def test_microsoft_normalizes_nested_search_results() -> None:
    session = FakeSession(load_fixture("microsoft_search.json"))
    adapter = MicrosoftAdapter(session=session)

    jobs = adapter.fetch()

    assert session.urls == [MicrosoftAdapter.API]
    assert [job.id for job in jobs] == ["microsoft:1827364", "microsoft:1827365"]
    assert jobs[0].title == "Software Engineering Intern"
    assert jobs[0].location == "Redmond, Washington, United States"
    assert jobs[0].url == "https://jobs.careers.microsoft.com/global/en/job/1827364"
    assert "cloud services" in jobs[0].jd_text
    assert jobs[1].location == "Cambridge, Massachusetts, United States"


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


def test_google_normalizes_resilient_payload() -> None:
    session = FakeSession(load_fixture("google.json"))
    adapter = GoogleAdapter(session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "google:123456789"
    assert jobs[0].location == "Mountain View, CA, USA"
    assert jobs[0].url == "https://careers.google.com/jobs/results/123456789/"


def test_amazon_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("amazon.json"))
    adapter = AmazonAdapter(session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "amazon:2828282"
    assert jobs[0].url == "https://www.amazon.jobs/en/jobs/2828282/software-development-engineer-intern"


def test_apple_normalizes_jobs() -> None:
    session = FakeSession(load_fixture("apple.json"))
    adapter = AppleAdapter(session=session)

    jobs = adapter.fetch()

    assert jobs[0].id == "apple:200611111"
    assert jobs[0].title == "Software Engineering Internship"
    assert jobs[0].location == "Cupertino, California, United States"
