from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

import requests

from adapters.base import Job, compact_text, html_to_text
from core.http import new_session


INTERN_TYPE = 3
API = "https://www.tesla.com/cua-api/apps/careers/state?region=5"
CAREERS_PAGE = "https://www.tesla.com/careers/search/"
PLAYWRIGHT_TIMEOUT_S = 45

_COOKIE_MARKERS = ("set-cookie:", "ak_bmsc=", "_abck=", "bm_sz=", "bm_sv=")


class TeslaBlockedError(RuntimeError):
    """Tesla returned a bot challenge, WAF page, or non-listings body."""


class TeslaUnavailable(RuntimeError):
    """This fetch method cannot run (missing package or empty proxy)."""


class _HttpPayload:
    def __init__(self, status_code: int, text: str, content_type: str = "") -> None:
        self.status_code = int(status_code or 0)
        self.text = text or ""
        self.headers = {"Content-Type": content_type or ""}

    def json(self) -> Any:
        return json.loads(self.text)


def tesla_proxy_url() -> str | None:
    for key in ("TESLA_PROXY", "HTTPS_PROXY", "https_proxy"):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = str(raw).strip()
        if value:
            return value
    return None


def tesla_cdp_url() -> str | None:
    value = str(os.environ.get("TESLA_CDP_URL") or "").strip()
    return value or None


def tesla_chrome_js_enabled() -> bool:
    return os.environ.get("TESLA_CHROME_JS", "").strip().lower() in {"1", "true", "yes"}


def parse_tesla_response(response: Any) -> Any:
    status = int(getattr(response, "status_code", 0) or 0)
    text = _response_text(response)
    payload = _try_json(response, text)

    if _is_listings_payload(payload) and status < 400:
        return payload
    if isinstance(payload, dict) and payload.get("cpr_chlge"):
        raise TeslaBlockedError(f"tesla bot challenge HTTP {status} cpr_chlge")
    if status == 403 or "access denied" in text[:800].lower():
        raise TeslaBlockedError(f"tesla blocked HTTP {status} access denied")
    if status == 429:
        raise TeslaBlockedError("tesla bot challenge HTTP 429")
    if status >= 400:
        raise TeslaBlockedError(f"tesla HTTP {status}: {_safe_error_text(text)}")
    raise TeslaBlockedError(
        f"tesla expected listings JSON, got HTTP {status}: {_safe_error_text(text)}"
    )


class TeslaAdapter:
    API = API

    def __init__(self, *, timeout: int = PLAYWRIGHT_TIMEOUT_S, session: requests.Session | None = None) -> None:
        self.timeout = timeout
        self._injected_session = session is not None
        self.session = session or new_session()
        if not self._injected_session:
            self.session.trust_env = False

    def fetch(self) -> list[Job]:
        if self._injected_session:
            response = self.session.get(self.API, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            return self._jobs_from_payload(payload)
        return self._jobs_from_payload(self._live_fetch())

    def _live_fetch(self) -> Any:
        errors: list[str] = []
        steps = self._fetch_steps()
        for name, method in steps:
            try:
                payload = method()
                print(f"[tesla] fetch ok via {name}")
                return payload
            except TeslaUnavailable as exc:
                print(f"[tesla] {name}: skipped ({_safe_error_text(str(exc))})")
                errors.append(f"{name}: skipped ({_safe_error_text(str(exc))})")
            except TeslaBlockedError as exc:
                print(f"[tesla] {name}: {_safe_error_text(str(exc))}")
                errors.append(f"{name}: {_safe_error_text(str(exc))}")
            except Exception as exc:
                print(f"[tesla] {name}: {type(exc).__name__}: {_safe_error_text(str(exc))}")
                errors.append(f"{name}: {type(exc).__name__}: {_safe_error_text(str(exc))}")
        raise TeslaBlockedError("tesla fetch failed: " + " | ".join(errors))

    def _fetch_steps(self) -> tuple[tuple[str, Any], ...]:
        laptop: list[tuple[str, Any]] = []
        if tesla_chrome_js_enabled():
            laptop.append(("chrome_js", self._fetch_chrome_js))
        if tesla_cdp_url():
            laptop.append(("cdp", self._fetch_cdp))
        if laptop:
            # Do not fall through to requests/curl/playwright. Those extra
            # hits make Akamai ban the laptop IP after a browser success.
            return tuple(laptop)
        return (
            ("requests", self._fetch_direct_requests),
            ("curl_cffi", self._fetch_curl_cffi),
            ("proxy", self._fetch_proxy),
            ("playwright", self._fetch_playwright),
        )

    def _fetch_chrome_js(self) -> Any:
        if not tesla_chrome_js_enabled():
            raise TeslaUnavailable("TESLA_CHROME_JS is off")
        if sys.platform != "darwin":
            raise TeslaUnavailable("Chrome AppleScript fetch is macOS-only")
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = _osascript_chrome_js(_CHROME_LISTINGS_JS, timeout=max(self.timeout, 90))
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise TeslaBlockedError(
                        f"tesla chrome js returned non-JSON: {_safe_error_text(raw)}"
                    ) from exc
                status = int(payload.get("status") or 0) if isinstance(payload, dict) else 0
                text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
                if isinstance(payload, dict) and _is_listings_payload(payload) and status < 400:
                    return payload
                parsed_error = TeslaBlockedError(
                    f"tesla chrome js HTTP {status}: {_safe_error_text(text or raw)}"
                )
                try:
                    return parse_tesla_response(_HttpPayload(status or 403, text or raw))
                except TeslaBlockedError as exc:
                    parsed_error = exc
                last_error = parsed_error
            except TeslaBlockedError as exc:
                last_error = exc
            except TeslaUnavailable:
                raise
            if attempt < 2 and _retryable_tesla_error(last_error):
                print(f"[tesla] chrome_js retry {attempt + 1}/2 after {_safe_error_text(str(last_error))}")
                time.sleep(5)
                continue
            break
        assert last_error is not None
        raise last_error

    def _fetch_direct_requests(self) -> Any:
        response = self.session.get(self.API, timeout=self.timeout)
        return parse_tesla_response(response)

    def _fetch_curl_cffi(self) -> Any:
        return self._curl_cffi_get(proxy=None)

    def _fetch_proxy(self) -> Any:
        proxy = tesla_proxy_url()
        if not proxy:
            raise TeslaUnavailable("no TESLA_PROXY or HTTPS_PROXY")
        try:
            return self._curl_cffi_get(proxy=proxy)
        except TeslaUnavailable:
            return self._requests_via_proxy(proxy)

    def _requests_via_proxy(self, proxy: str) -> Any:
        response = self.session.get(
            self.API,
            timeout=self.timeout,
            proxies={"http": proxy, "https": proxy},
        )
        return parse_tesla_response(response)

    def _curl_cffi_get(self, *, proxy: str | None) -> Any:
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError as exc:
            raise TeslaUnavailable("curl_cffi is not installed") from exc

        session = None
        last_error: Exception | None = None
        for impersonate in ("chrome", "chrome131", "chrome124"):
            try:
                session = cffi_requests.Session(impersonate=impersonate)
                break
            except Exception as exc:
                last_error = exc
        if session is None:
            raise TeslaUnavailable(f"curl_cffi impersonate failed: {last_error}")

        get_kwargs: dict[str, Any] = {"timeout": self.timeout, "allow_redirects": True}
        if proxy:
            get_kwargs.update(_curl_cffi_proxy_kwargs(session.get, proxy))
        try:
            session.get(CAREERS_PAGE, **get_kwargs)
        except Exception:
            pass
        response = session.get(
            self.API,
            headers={"Accept": "application/json, text/plain, */*", "Referer": CAREERS_PAGE},
            **get_kwargs,
        )
        return parse_tesla_response(response)

    def _fetch_cdp(self) -> Any:
        cdp_url = tesla_cdp_url()
        if not cdp_url:
            raise TeslaUnavailable("no TESLA_CDP_URL")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TeslaUnavailable("playwright is not installed") from exc

        remaining_ms = _timeout_remaining_ms(self.timeout, label="cdp")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context(
                locale="en-US",
                viewport={"width": 1366, "height": 768},
                timezone_id="America/Los_Angeles",
            )
            page = context.new_page()
            try:
                return self._scrape_listings_from_page(page, remaining_ms, extra_wait=False)
            finally:
                page.close()

    def _fetch_playwright(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TeslaUnavailable("playwright is not installed") from exc

        remaining_ms = _timeout_remaining_ms(self.timeout, label="playwright")
        with sync_playwright() as playwright:
            browser = _launch_playwright_browser(playwright)
            try:
                context = browser.new_context(
                    locale="en-US",
                    viewport={"width": 1366, "height": 768},
                    timezone_id="America/Los_Angeles",
                )
                page = context.new_page()
                return self._scrape_listings_from_page(
                    page, remaining_ms, extra_wait=_playwright_headed()
                )
            finally:
                browser.close()

    def _scrape_listings_from_page(
        self,
        page: Any,
        remaining_ms: Any,
        *,
        extra_wait: bool,
    ) -> Any:
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page.set_default_timeout(remaining_ms())
        captured: dict[str, Any] = {}

        def on_response(response: Any) -> None:
            try:
                url = str(getattr(response, "url", "") or "")
                if "cua-api/apps/careers/state" not in url or "text" in captured:
                    return
                headers = getattr(response, "headers", None) or {}
                captured["status"] = int(getattr(response, "status", 0) or 0)
                captured["content_type"] = (
                    headers.get("content-type") or headers.get("Content-Type") or ""
                )
                captured["text"] = response.text()
            except Exception:
                return

        page.on("response", on_response)
        page.goto(CAREERS_PAGE, wait_until="domcontentloaded", timeout=remaining_ms())
        try:
            page.wait_for_load_state("networkidle", timeout=min(15_000, remaining_ms()))
        except Exception:
            pass
        if extra_wait:
            try:
                page.wait_for_timeout(min(8_000, remaining_ms()))
            except Exception:
                pass

        if captured.get("text"):
            try:
                return parse_tesla_response(
                    _HttpPayload(
                        int(captured.get("status") or 0),
                        str(captured.get("text") or ""),
                        str(captured.get("content_type") or ""),
                    )
                )
            except TeslaBlockedError:
                pass

        page.set_default_timeout(remaining_ms())
        result = page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: { Accept: 'application/json, text/plain, */*' },
                });
                const text = await resp.text();
                return {
                    status: resp.status,
                    contentType: resp.headers.get('content-type') || '',
                    text,
                };
            }""",
            self.API,
        )
        if not isinstance(result, dict):
            raise TeslaBlockedError("tesla playwright fetch returned no payload")
        return parse_tesla_response(
            _HttpPayload(
                int(result.get("status") or 0),
                str(result.get("text") or ""),
                str(result.get("contentType") or ""),
            )
        )

    def _jobs_from_payload(self, payload: Any) -> list[Job]:
        listings = payload.get("listings") if isinstance(payload, dict) else payload
        locations = _location_lookup(payload)
        jobs: list[Job] = []
        if not isinstance(listings, list):
            return jobs
        for raw in listings:
            if not isinstance(raw, dict):
                continue
            try:
                listing_type = int(raw.get("y"))
            except (TypeError, ValueError):
                listing_type = None
            if listing_type != INTERN_TYPE:
                continue
            jobs.append(self._normalize(raw, locations))
        return jobs

    def _normalize(self, raw: dict[str, Any], locations: dict[str, str] | None = None) -> Job:
        job_id = raw.get("id") or raw.get("jobId")
        title = raw.get("t") or raw.get("title") or raw.get("name")
        location = raw.get("l") or raw.get("location") or "Unspecified"
        loc_key = str(location)
        if locations and loc_key in locations:
            location = locations[loc_key]
        description = raw.get("d") if isinstance(raw.get("d"), str) else raw.get("description") or ""
        url = raw.get("url") or raw.get("applyUrl")
        if not url and job_id:
            url = f"https://www.tesla.com/careers/search/job/{job_id}"
        return Job(
            id=f"tesla:{job_id}",
            company="tesla",
            title=compact_text(str(title or "")),
            location=compact_text(str(location)),
            url=compact_text(str(url or "")),
            jd_text=html_to_text(str(description or "")),
            posted_at=raw.get("posted") or raw.get("posted_at"),
        )


_CHROME_LISTINGS_JS = """(function(){
  var xhr = new XMLHttpRequest();
  xhr.open('GET', 'https://www.tesla.com/cua-api/apps/careers/state?region=5', false);
  xhr.setRequestHeader('Accept', 'application/json, text/plain, */*');
  xhr.send(null);
  var text = xhr.responseText || '';
  if (xhr.status !== 200) {
    return JSON.stringify({status: xhr.status, text: text.slice(0, 800)});
  }
  var data = JSON.parse(text);
  var listings = (data.listings || []).filter(function(x){ return x && x.y === 3; });
  var lookup = data.lookup || {};
  return JSON.stringify({
    status: 200,
    listings: listings,
    lookup: {locations: lookup.locations || {}}
  });
})()"""

_CHROME_JS_RUNNER = """
on run argv
  set js to item 1 of argv
  tell application "Google Chrome"
    repeat with w in windows
      repeat with t in tabs of w
        if (URL of t as string) contains "tesla.com/careers" then
          execute t javascript "location.reload()"
          delay 8
          return execute t javascript js
        end if
      end repeat
    end repeat
    if (count of windows) is 0 then
      make new window
      delay 1
    end if
    set newTab to make new tab at end of tabs of front window with properties {URL:"https://www.tesla.com/careers/search/?site=US"}
    delay 8
    return execute newTab javascript js
  end tell
end run
"""


def _osascript_chrome_js(javascript: str, *, timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-", javascript],
            input=_CHROME_JS_RUNNER,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise TeslaUnavailable("osascript is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise TeslaBlockedError("tesla chrome js timed out") from exc
    combined = f"{proc.stderr or ''} {proc.stdout or ''}"
    if "turned off" in combined.lower():
        raise TeslaUnavailable(
            "Chrome AppleScript JavaScript is off; enable View > Developer > Allow JavaScript from Apple Events"
        )
    if proc.returncode != 0:
        raise TeslaBlockedError(
            f"tesla chrome js failed: {_safe_error_text(proc.stderr or proc.stdout or 'osascript error')}"
        )
    raw = (proc.stdout or "").strip()
    if not raw:
        raise TeslaBlockedError("tesla chrome js returned empty output")
    return raw


def _retryable_tesla_error(exc: Exception | None) -> bool:
    text = str(exc or "").lower()
    return "429" in text or "cpr_chlge" in text or "bot challenge" in text


def _location_lookup(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    lookup = payload.get("lookup")
    if not isinstance(lookup, dict):
        return {}
    locations = lookup.get("locations")
    if not isinstance(locations, dict):
        return {}
    mapped: dict[str, str] = {}
    for key, value in locations.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            mapped[str(key)] = text
    return mapped


def _timeout_remaining_ms(timeout: float, *, label: str):
    started = time.monotonic()
    budget = max(float(timeout), float(PLAYWRIGHT_TIMEOUT_S))

    def remaining_ms() -> int:
        left = budget - (time.monotonic() - started)
        if left <= 0.5:
            raise TeslaBlockedError(f"tesla {label} timed out")
        return int(left * 1000)

    return remaining_ms


def _playwright_headed() -> bool:
    return os.environ.get("TESLA_PLAYWRIGHT_HEADED", "").strip().lower() in {"1", "true", "yes"}


def _launch_playwright_browser(playwright: Any) -> Any:
    headed = _playwright_headed()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    if os.environ.get("GITHUB_ACTIONS") and not headed:
        args.append("--no-sandbox")
    launch_kwargs: dict[str, Any] = {
        "headless": not headed,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
    }
    try:
        return playwright.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception:
        return playwright.chromium.launch(**launch_kwargs)


def _curl_cffi_proxy_kwargs(get_fn: Any, proxy: str) -> dict[str, Any]:
    try:
        import inspect

        params = inspect.signature(get_fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "proxy" in params:
        return {"proxy": proxy}
    return {"proxies": {"http": proxy, "https": proxy}}


def _is_listings_payload(payload: Any) -> bool:
    if isinstance(payload, list):
        return True
    return isinstance(payload, dict) and isinstance(payload.get("listings"), list)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "") or ""
    if isinstance(text, bytes):
        return text.decode("utf-8", "replace")
    return str(text)


def _try_json(response: Any, text: str) -> Any:
    try:
        payload = response.json()
        if isinstance(payload, (dict, list)):
            return payload
    except Exception:
        pass
    stripped = (text or "").lstrip()
    if stripped[:1] not in "{[":
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _safe_error_text(value: str) -> str:
    text = " ".join((value or "").split())
    lower = text.lower()
    if any(marker in lower for marker in _COOKIE_MARKERS):
        return "redacted client-state material"
    return text[:180]
