from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from adapters.tesla import (
    API,
    CAREERS_PAGE,
    TeslaAdapter,
    TeslaBlockedError,
    TeslaUnavailable,
    parse_tesla_response,
    tesla_cdp_url,
    tesla_proxy_url,
)


FIXTURES = Path(__file__).parent / "fixtures"
INTERN_LISTING = {
    "id": 505789688,
    "t": "Internship, Software Engineer",
    "l": "Palo Alto, California",
    "y": 3,
}
LISTINGS_PAYLOAD = {
    "listings": [
        INTERN_LISTING,
        {"id": 111, "t": "Staff Software Engineer", "l": "Austin, Texas", "y": 1},
    ]
}
ACCESS_DENIED_HTML = (
    "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD>"
    "<BODY>Access Denied You don't have permission to access this server.</BODY></HTML>"
)
CHALLENGE_HTML = "<html><script>window.location.reload()</script><body>Just a moment</body></html>"


class FakeResponse:
    def __init__(
        self,
        payload: Any,
        *,
        status_code: int = 200,
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
    def __init__(self, payload: Any = None) -> None:
        self.payload = payload
        self.urls: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        self.calls.append({"url": url, **kwargs})
        payload = self.payload
        if isinstance(payload, FakeResponse):
            return payload
        return FakeResponse(payload)


class RecordingSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.urls: list[str] = []
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.urls.append(url)
        self.calls.append({"url": url, **kwargs})
        return self.handler(url, kwargs)


@pytest.fixture(autouse=True)
def clear_tesla_cdp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESLA_CDP_URL", raising=False)


@pytest.fixture
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TESLA_PROXY",
        "TESLA_CDP_URL",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)


def _unavailable(reason: str):
    def _inner(self: TeslaAdapter, *args: Any, **kwargs: Any) -> Any:
        raise TeslaUnavailable(reason)

    return _inner


def test_parse_403_html_is_not_listings() -> None:
    response = FakeResponse(ACCESS_DENIED_HTML, status_code=403)
    with pytest.raises(TeslaBlockedError, match="access denied"):
        parse_tesla_response(response)


def test_parse_429_cpr_chlge_is_bot_challenge() -> None:
    response = FakeResponse({"cpr_chlge": True}, status_code=429)
    with pytest.raises(TeslaBlockedError, match="cpr_chlge"):
        parse_tesla_response(response)


def test_parse_200_html_challenge_is_not_listings() -> None:
    response = FakeResponse(CHALLENGE_HTML, status_code=200)
    with pytest.raises(TeslaBlockedError, match="listings JSON"):
        parse_tesla_response(response)


def test_parse_listings_json_succeeds() -> None:
    payload = parse_tesla_response(FakeResponse(LISTINGS_PAYLOAD))
    assert payload["listings"][0]["id"] == 505789688


def test_injected_session_keeps_simple_get() -> None:
    session = FakeSession(json.loads((FIXTURES / "tesla.json").read_text(encoding="utf-8")))
    jobs = TeslaAdapter(session=session).fetch()
    assert session.urls == [API]
    assert len(jobs) == 1
    assert jobs[0].id == "tesla:505789688"
    assert jobs[0].title == "Internship, Software Engineer"


def test_injected_session_skips_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def mark(name: str):
        def _inner(self: TeslaAdapter) -> Any:
            called.append(name)
            raise AssertionError(f"{name} should not run for injected sessions")

        return _inner

    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", mark("cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_direct_requests", mark("requests"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", mark("curl_cffi"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_proxy", mark("proxy"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", mark("playwright"))
    session = FakeSession(LISTINGS_PAYLOAD)
    jobs = TeslaAdapter(session=session).fetch()
    assert called == []
    assert [job.id for job in jobs] == ["tesla:505789688"]


def test_live_403_is_not_treated_as_listings(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    session = FakeSession(FakeResponse(ACCESS_DENIED_HTML, status_code=403))
    monkeypatch.setattr("adapters.tesla.new_session", lambda: session)
    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", _unavailable("skip cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", _unavailable("skip curl"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", _unavailable("skip playwright"))
    with pytest.raises(TeslaBlockedError, match="access denied"):
        TeslaAdapter().fetch()


def test_requests_success_does_not_start_playwright(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    later: list[str] = []
    monkeypatch.setattr("adapters.tesla.new_session", lambda: FakeSession(LISTINGS_PAYLOAD))

    def mark(name: str):
        def _inner(self: TeslaAdapter) -> Any:
            later.append(name)
            raise AssertionError(f"{name} should not run after requests success")

        return _inner

    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", _unavailable("skip cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", mark("curl_cffi"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_proxy", mark("proxy"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", mark("playwright"))
    jobs = TeslaAdapter().fetch()
    assert later == []
    assert jobs[0].id == "tesla:505789688"


def test_proxy_env_is_passed_to_requests(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    monkeypatch.setenv("TESLA_PROXY", "http://127.0.0.1:9999")

    def handler(url: str, kwargs: dict[str, Any]) -> FakeResponse:
        proxies = kwargs.get("proxies") or {}
        if proxies.get("https") == "http://127.0.0.1:9999":
            return FakeResponse(LISTINGS_PAYLOAD)
        return FakeResponse(ACCESS_DENIED_HTML, status_code=403)

    session = RecordingSession(handler)
    monkeypatch.setattr("adapters.tesla.new_session", lambda: session)
    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", _unavailable("skip cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", _unavailable("skip curl"))
    monkeypatch.setattr(TeslaAdapter, "_curl_cffi_get", _unavailable("skip curl get"))
    monkeypatch.setattr(
        TeslaAdapter,
        "_fetch_playwright",
        lambda self: (_ for _ in ()).throw(AssertionError("playwright should not run")),
    )

    jobs = TeslaAdapter().fetch()
    proxy_calls = [call for call in session.calls if (call.get("proxies") or {}).get("https")]
    assert proxy_calls
    assert proxy_calls[0]["proxies"] == {
        "http": "http://127.0.0.1:9999",
        "https": "http://127.0.0.1:9999",
    }
    assert jobs[0].id == "tesla:505789688"


def test_empty_tesla_proxy_does_not_crash(monkeypatch: pytest.MonkeyPatch, no_proxy_env: None) -> None:
    monkeypatch.setenv("TESLA_PROXY", "  ")
    monkeypatch.setenv("HTTPS_PROXY", "")
    assert tesla_proxy_url() is None
    adapter = TeslaAdapter()
    with pytest.raises(TeslaUnavailable, match="no TESLA_PROXY"):
        adapter._fetch_proxy()


def test_tesla_proxy_wins_over_https_proxy(monkeypatch: pytest.MonkeyPatch, no_proxy_env: None) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:8080")
    monkeypatch.setenv("TESLA_PROXY", "http://tesla-proxy:8080")
    assert tesla_proxy_url() == "http://tesla-proxy:8080"


def test_https_proxy_used_when_tesla_proxy_empty(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    monkeypatch.setenv("TESLA_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:8080")
    assert tesla_proxy_url() == "http://https-proxy:8080"


def test_curl_cffi_fallback_before_playwright(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    order: list[str] = []
    monkeypatch.setattr("adapters.tesla.new_session", lambda: FakeSession(FakeResponse(ACCESS_DENIED_HTML, status_code=403)))

    def curl(self: TeslaAdapter) -> Any:
        order.append("curl_cffi")
        return LISTINGS_PAYLOAD

    def playwright(self: TeslaAdapter) -> Any:
        order.append("playwright")
        raise AssertionError("playwright should not run after curl_cffi success")

    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", _unavailable("skip cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", curl)
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", playwright)
    jobs = TeslaAdapter().fetch()
    assert order == ["curl_cffi"]
    assert jobs[0].title == "Internship, Software Engineer"


def test_playwright_last_resort(monkeypatch: pytest.MonkeyPatch, no_proxy_env: None) -> None:
    monkeypatch.setattr("adapters.tesla.new_session", lambda: FakeSession(FakeResponse(ACCESS_DENIED_HTML, status_code=403)))
    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", _unavailable("no cdp"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", _unavailable("no curl_cffi"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", lambda self: LISTINGS_PAYLOAD)
    jobs = TeslaAdapter().fetch()
    assert len(jobs) == 1
    assert jobs[0].location == "Palo Alto, California"


def test_curl_cffi_impersonates_chrome(monkeypatch: pytest.MonkeyPatch, no_proxy_env: None) -> None:
    created: dict[str, Any] = {}

    class FakeCffiSession:
        def __init__(self, impersonate: str | None = None) -> None:
            created["impersonate"] = impersonate
            self.urls: list[str] = []

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.urls.append(url)
            created.setdefault("urls", []).append(url)
            created.setdefault("kwargs", []).append(kwargs)
            if "cua-api" in url:
                return FakeResponse(LISTINGS_PAYLOAD)
            return FakeResponse("<html>careers</html>")

    fake_curl_cffi = types.ModuleType("curl_cffi")
    fake_cffi_requests = types.ModuleType("curl_cffi.requests")
    fake_cffi_requests.Session = FakeCffiSession
    fake_curl_cffi.requests = fake_cffi_requests
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_curl_cffi)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_cffi_requests)

    payload = TeslaAdapter()._curl_cffi_get(proxy=None)
    assert created["impersonate"] == "chrome"
    assert created["urls"][0] == CAREERS_PAGE
    assert created["urls"][1] == API
    assert payload["listings"][0]["y"] == 3


def test_playwright_evaluate_listings(monkeypatch: pytest.MonkeyPatch, no_proxy_env: None) -> None:
    launches: list[dict[str, Any]] = []

    class FakePage:
        def add_init_script(self, script: str) -> None:
            assert "webdriver" in script

        def set_default_timeout(self, timeout: int) -> None:
            return None

        def on(self, event: str, handler: Any) -> None:
            return None

        def goto(self, url: str, **kwargs: Any) -> None:
            assert url == CAREERS_PAGE

        def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
            return None

        def evaluate(self, script: str, url: str) -> dict[str, Any]:
            assert "cua-api" in url
            assert "fetch" in script
            return {
                "status": 200,
                "contentType": "application/json",
                "text": json.dumps(LISTINGS_PAYLOAD),
            }

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

    class FakeBrowser:
        def new_context(self, **kwargs: Any) -> FakeContext:
            return FakeContext()

        def close(self) -> None:
            return None

    class FakeChromium:
        def launch(self, **kwargs: Any) -> FakeBrowser:
            launches.append(kwargs)
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

    fake_playwright = types.ModuleType("playwright")
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakePlaywright()
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    payload = TeslaAdapter()._fetch_playwright()
    assert launches
    assert launches[0].get("headless") is True
    assert "--disable-blink-features=AutomationControlled" in launches[0].get("args", [])
    jobs = TeslaAdapter(session=FakeSession(payload)).fetch()
    assert jobs[0].id == "tesla:505789688"


def test_intern_type_filter_keeps_y3_only() -> None:
    jobs = TeslaAdapter(session=FakeSession(LISTINGS_PAYLOAD)).fetch()
    assert [job.id for job in jobs] == ["tesla:505789688"]


def test_cdp_url_empty_is_skipped() -> None:
    assert tesla_cdp_url() is None
    with pytest.raises(TeslaUnavailable, match="no TESLA_CDP_URL"):
        TeslaAdapter()._fetch_cdp()


def test_cdp_success_skips_other_methods(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    later: list[str] = []
    monkeypatch.setenv("TESLA_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(TeslaAdapter, "_fetch_cdp", lambda self: LISTINGS_PAYLOAD)

    def mark(name: str):
        def _inner(self: TeslaAdapter) -> Any:
            later.append(name)
            raise AssertionError(f"{name} should not run after cdp success")

        return _inner

    monkeypatch.setattr(TeslaAdapter, "_fetch_direct_requests", mark("requests"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_curl_cffi", mark("curl_cffi"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_proxy", mark("proxy"))
    monkeypatch.setattr(TeslaAdapter, "_fetch_playwright", mark("playwright"))
    jobs = TeslaAdapter().fetch()
    assert later == []
    assert jobs[0].id == "tesla:505789688"


def test_cdp_connects_and_does_not_close_browser(
    monkeypatch: pytest.MonkeyPatch, no_proxy_env: None
) -> None:
    monkeypatch.setenv("TESLA_CDP_URL", "http://127.0.0.1:9222")
    connected: list[str] = []
    closed: list[str] = []

    class FakePage:
        def add_init_script(self, script: str) -> None:
            assert "webdriver" in script

        def set_default_timeout(self, timeout: int) -> None:
            return None

        def on(self, event: str, handler: Any) -> None:
            return None

        def goto(self, url: str, **kwargs: Any) -> None:
            assert url == CAREERS_PAGE

        def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
            return None

        def evaluate(self, script: str, url: str) -> dict[str, Any]:
            assert "cua-api" in url
            return {
                "status": 200,
                "contentType": "application/json",
                "text": json.dumps(LISTINGS_PAYLOAD),
            }

        def close(self) -> None:
            closed.append("page")

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

        def close(self) -> None:
            closed.append("browser")

    class FakeChromium:
        def connect_over_cdp(self, url: str) -> FakeBrowser:
            connected.append(url)
            return FakeBrowser()

        def launch(self, **kwargs: Any) -> FakeBrowser:
            raise AssertionError("launch should not run for cdp")

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self) -> FakePlaywright:
            return self

        def __exit__(self, *args: Any) -> bool:
            return False

    fake_playwright = types.ModuleType("playwright")
    fake_sync_api = types.ModuleType("playwright.sync_api")
    fake_sync_api.sync_playwright = lambda: FakePlaywright()
    fake_playwright.sync_api = fake_sync_api
    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_sync_api)

    payload = TeslaAdapter()._fetch_cdp()
    assert connected == ["http://127.0.0.1:9222"]
    assert "browser" not in closed
    assert "page" in closed
    assert payload["listings"][0]["y"] == 3
