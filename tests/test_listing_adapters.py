from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from adapters.listing import Card, ListingAdapter, ListingBoard, job_posting_ld


class Response:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self._body = body

    def json(self) -> Any:
        return self._body if not isinstance(self._body, str) else json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} Client Error")


class Session:
    """Routes by URL substring; the first matching key wins."""

    def __init__(self, routes: dict[str, Any], posts: dict[str, Any] | None = None) -> None:
        self.routes = routes
        self.posts = posts or {}
        self.urls: list[str] = []
        self.bodies: list[Any] = []

    def _match(self, table: dict[str, Any], url: str) -> Response:
        for key, value in table.items():
            if key in url:
                value = value(url) if callable(value) else value
                return value if isinstance(value, Response) else Response(value)
        return Response("not found", 404)

    def get(self, url: str, **kwargs: Any) -> Response:
        params = kwargs.get("params")
        self.urls.append(url if not params else f"{url}?{params}")
        return self._match(self.routes, url)

    def post(self, url: str, **kwargs: Any) -> Response:
        self.urls.append(url)
        self.bodies.append(kwargs.get("json") or kwargs.get("data"))
        return self._match(self.posts, url)


LD = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
"title":"Software Engineer Intern","datePosted":"2026-09-15","description":"&lt;p&gt;Build things. Pursuing a BS.&lt;/p&gt;",
"jobLocation":{"@type":"Place","address":{"addressLocality":"Austin","addressRegion":"TX","addressCountry":"US"}}}</script>"""


# --- shared flow -----------------------------------------------------------


class FakeBoard(ListingAdapter):
    NAME = "fake"

    def __init__(self, cards: list[Card], **kwargs: Any) -> None:
        super().__init__([ListingBoard("acme", "acme.example")], session=Session({}), **kwargs)
        self.cards = cards
        self.details: list[str] = []

    def list_cards(self, board: ListingBoard) -> list[Card]:
        return self.cards

    def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
        self.details.append(card.id)
        return {"description": f"JD {card.id}", "location": "Austin, TX, US", "country_codes": ("US",)}


def test_listing_flow_budgets_job_pages_and_remembers_checked_ids() -> None:
    cards = [
        Card("1", "Software Engineer Intern", "u1"),
        Card("2", "Senior Engineer", "u2"),
        Card("3", "Data Intern", "u3", country_codes=("IN",)),
        Card("4", "Co-op, Firmware", "u4"),
        Card("5", "Summer Analyst", "u5"),
        Card("1", "Software Engineer Intern", "u1"),  # duplicate row
    ]
    adapter = FakeBoard(cards, max_details=2)
    jobs = adapter.fetch()
    # Non-intern titles are never read; the non-US row skips its page; the
    # budget of 2 stops "Summer Analyst" until a later run.
    assert [j.id for j in jobs] == ["fake:acme:1", "fake:acme:3", "fake:acme:4"]
    assert adapter.details == ["1", "4"]
    assert jobs[0].jd_text == "JD 1" and jobs[0].country_codes == ("US",)
    assert jobs[1].jd_text == "" and jobs[1].country_codes == ("IN",)
    assert adapter.listing_counts == {"acme": 5}
    assert adapter.checked_by_company["acme"] == ({"1", "2", "3", "4"}, {"1", "3", "4"})

    # Next run: known non-intern ids are skipped; known interns are re-read
    # without spending budget, so "5" now fits.
    again = FakeBoard(cards, max_details=1, known={"acme": adapter.checked_by_company["acme"]})
    assert [j.id.rsplit(":", 1)[1] for j in again.fetch()] == ["1", "3", "4", "5"]
    assert again.details == ["1", "4", "5"]


def test_listing_retries_a_throttled_job_page_once() -> None:
    class Throttled(FakeBoard):
        calls = 0

        def detail(self, board: ListingBoard, card: Card) -> dict[str, Any]:
            Throttled.calls += 1
            if Throttled.calls == 1:
                raise RuntimeError("429 Client Error: Too Many Requests")
            return {"description": "ok"}

    import adapters.listing as listing

    original = listing.sleep
    listing.sleep = lambda seconds: None
    try:
        jobs = Throttled([Card("1", "Intern", "u1")]).fetch()
    finally:
        listing.sleep = original
    assert jobs[0].jd_text == "ok" and Throttled.calls == 2


def test_job_posting_ld_reads_description_location_and_country() -> None:
    info = job_posting_ld(f"<html>{LD}</html>")
    assert info["description"] == "Build things. Pursuing a BS."
    assert info["location"] == "Austin, TX, US" and info["country_codes"] == ("US",)
    assert info["posted_at"] == "2026-09-15"


# --- platforms -------------------------------------------------------------


def test_paylocity_reads_page_data_jobs() -> None:
    from adapters.paylocity import PaylocityAdapter

    jobs_json = {
        "Jobs": [
            {"JobId": 101, "JobTitle": "Software Engineer Intern", "PublishedDate": "2026-09-12T09:27:19-05:00",
             "Description": "Short", "IsInternal": False,
             "JobLocation": {"City": "Johnston", "State": "IA", "Country": "USA"}},
            {"JobId": 102, "JobTitle": "Internal Audit Intern", "IsInternal": True},
            {"JobId": 103, "JobTitle": "Accountant", "IsInternal": False, "JobLocation": {}},
        ]
    }
    board_html = f"<script>window.pageData = {json.dumps(jobs_json)};\n window.other = 1;</script>"
    session = Session({"/Jobs/All/guid-1": board_html, "/Jobs/Details/101": f"<html>{LD}</html>"})
    adapter = PaylocityAdapter([ListingBoard("delta", "", site="guid-1")], session=session)
    adapter.DETAIL_PAUSE_SECONDS = 0
    jobs = adapter.fetch()
    assert [j.id for j in jobs] == ["paylocity:delta:101"]
    assert jobs[0].url == "https://recruiting.paylocity.com/Recruiting/Jobs/Details/101"
    assert jobs[0].location == "Austin, TX, US"  # the job page's structured location wins
    assert jobs[0].country_codes == ("US",)
    assert jobs[0].jd_text == "Build things. Pursuing a BS."
    assert adapter.listing_counts == {"delta": 2}


def test_jazzhr_parses_the_apply_list() -> None:
    from adapters.jazzhr import JazzHRAdapter

    html = """<ul class="list-group">
    <li class="list-group-item">
      <h3 class='list-group-item-heading'>
        <a href="http://ragleinc.applytojob.com/apply/bOx8uiciOs/Data-Engineering-Intern-Summer-2027">
          Data Engineering Intern- Summer 2027 </a></h3>
      <ul class='list-inline list-group-item-text'>
        <li><i class='fa fa-map-marker'></i>North Richland Hills, TX</li>
        <li><i class='fa fa-sitemap'></i>DFW</li></ul>
    </li>
    <li class="list-group-item">
      <h3 class='list-group-item-heading'><a href="https://ragleinc.applytojob.com/apply/z97/Crane-Operator">Crane Operator</a></h3>
    </li></ul>"""
    session = Session({"applytojob.com/apply/bOx8uiciOs": f"<html>{LD}</html>", "applytojob.com/apply": html})
    jobs = JazzHRAdapter([ListingBoard("ragle", "ragleinc.applytojob.com")], session=session).fetch()
    assert session.urls[0] == "https://ragleinc.applytojob.com/apply"
    assert [(j.id, j.title) for j in jobs] == [("jazzhr:ragle:bOx8uiciOs", "Data Engineering Intern- Summer 2027")]
    assert jobs[0].url.startswith("https://ragleinc.applytojob.com/apply/bOx8uiciOs/")
    assert jobs[0].posted_at == "2026-09-15"


def test_bamboohr_reads_list_and_detail_json() -> None:
    from adapters.bamboohr import BambooHRAdapter

    listing = {"result": [
        {"id": "398", "jobOpeningName": "Ground Software Engineering Intern - Summer 2027",
         "location": {"city": "Golden", "state": "Colorado"}, "atsLocation": {"country": None}},
        {"id": "297", "jobOpeningName": "Business Development Engineer", "location": {"city": "Golden"}},
    ]}
    detail = {"result": {"jobOpening": {"description": "<p>Write flight software.</p>", "datePosted": "2026-09-21",
                                        "location": {"city": "Golden", "state": "Colorado", "addressCountry": "United States"}}}}
    session = Session({"/careers/list": listing, "/careers/398/detail": detail})
    jobs = BambooHRAdapter([ListingBoard("lunar-outpost", "lunaroutpost.bamboohr.com")], session=session).fetch()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://lunaroutpost.bamboohr.com/careers/398"
    assert job.location == "Golden, Colorado, United States" and job.country_codes == ("US",)
    assert job.jd_text == "Write flight software." and job.posted_at == "2026-09-21"


def test_pinpoint_uses_listing_descriptions_without_job_pages() -> None:
    from adapters.pinpoint import PinpointAdapter

    payload = {"data": [
        {"id": "1", "title": "Engineering Intern", "url": "https://x.pinpointhq.com/en/postings/abc",
         "description": "<p>Design pumps.</p>", "key_responsibilities": "<ul><li>CAD</li></ul>",
         "location": {"city": "Fort Wayne", "province": "Indiana", "name": "Fort Wayne"}},
        {"id": "2", "title": "Area Sales Manager", "url": "u2", "location": {"name": "India"}},
    ]}
    session = Session({"postings.json": payload})
    jobs = PinpointAdapter([ListingBoard("franklin", "x.pinpointhq.com")], session=session).fetch()
    assert session.urls == ["https://x.pinpointhq.com/postings.json"]
    assert [j.title for j in jobs] == ["Engineering Intern"]
    assert jobs[0].jd_text == "Design pumps. CAD" and jobs[0].location == "Fort Wayne, Indiana, Fort Wayne"


def test_applicantpro_finds_the_domain_id_then_reads_json() -> None:
    from adapters.applicantpro import ApplicantProAdapter

    jobs_json = {"success": True, "data": {"jobs": [
        {"id": 4115690, "title": "Software Development Summer Internship", "city": "Alexandria", "abbreviation": "VA",
         "iso3": "USA", "startDateRef": "Jun 11, 2026", "jobUrl": "https://simon.applicantpro.com/jobs/4115690"},
    ]}}
    detail = {"success": True, "data": {"description": "<p>Java and React.</p>"}}
    session = Session({
        "/core/jobs/9728/4115690/job-details": detail,
        "/core/jobs/9728?getParams=": jobs_json,
        "/jobs/": '<script>var x = {"domain_id":"4"}; domainId : 9728,</script>',
    })
    jobs = ApplicantProAdapter([ListingBoard("simon", "simon.applicantpro.com")], session=session).fetch()
    assert session.urls[1] == f"https://simon.applicantpro.com/core/jobs/9728?getParams={quote('{}')}"
    assert jobs[0].country_codes == ("US",) and jobs[0].location == "Alexandria, VA"
    assert jobs[0].jd_text == "Java and React."


def test_jobvite_searches_each_keyword_and_reads_job_pages() -> None:
    from adapters.jobvite import JobviteAdapter

    def search(url: str) -> str:
        if "p=0" not in url:
            return "<table></table>"
        job = "oMAPAfwv" if "q=intern" in url else "oCOOPfwv"
        title = "Software Development Intern, Summer 2027" if "q=intern" in url else "Software Co-op"
        return f"""<tr>
            <td class="jv-job-list-name">
                <a href="/tylertech/job/{job}">{title}</a>
            </td>
            <td class="jv-job-list-location">

            Plano,
            Texas
            </td>
        </tr>"""

    detail = """<p class="jv-job-detail-meta">
        Software Engineering<span class='jv-inline-separator'></span>
            Lakewood,
            Colorado
            <br>Salary: USD 25.00 - 28.00 Annually<br>
        </p>
        <div class="jv-job-detail-description" ng-non-bindable><p>Build &amp; ship.</p></div>
        <div class="jv-job-detail-bottom"></div>"""
    session = Session({"/search?": search, "/job/": detail})
    jobs = JobviteAdapter([ListingBoard("tyler", "", site="tylertech", search="intern, co-op")], session=session).fetch()
    assert [j.id for j in jobs] == ["jobvite:tyler:oMAPAfwv", "jobvite:tyler:oCOOPfwv"]
    assert jobs[0].url == "https://jobs.jobvite.com/tylertech/job/oMAPAfwv"
    assert jobs[0].location == "Lakewood, Colorado" and jobs[0].jd_text == "Build & ship."
    assert jobs[0].location_names == ("Plano, Texas", "Lakewood, Colorado")


def test_taleo_finds_the_portal_pages_the_search_and_decodes_descriptions() -> None:
    from adapters.taleo import TaleoAdapter

    def page(n: int) -> dict[str, Any]:
        rows = [] if n > 1 else [
            {"jobId": "1", "contestNo": "342750", "column": ["2027 Intern - Software Engineer", '["US-Maryland-Hunt Valley"]', "10/01/2026"],
             "linkedColumn": 0, "locationsColumns": [1]},
            {"jobId": "2", "contestNo": "342751", "column": ["2027 Intern - Software", '["CA-Ontario-Toronto"]', "09/01/2026"],
             "linkedColumn": 0, "locationsColumns": [1]},
            {"jobId": "3", "contestNo": "342752", "column": [], "linkedColumn": 0, "locationsColumns": []},
        ]
        return {"requisitionList": rows, "pagingData": {"currentPageNo": n, "pageSize": 25, "totalCount": 2}}

    pages = iter([page(1), page(2)])
    description = quote("<p>Write <b>C++</b> for sensors.</p>")
    detail = f"api.fillList('requisitionDescriptionInterface', 'descRequisition', ['x','true','!*!{description}','2027']);"
    session = Session(
        {"jobsearch.ftl": "<script>var portal=8140753014&amp;x</script>", "jobdetail.ftl?job=342750": detail},
        posts={"searchjobs": lambda url: Response(next(pages))},
    )
    adapter = TaleoAdapter([ListingBoard("textron", "textron.taleo.net", site="textron")], session=session)
    jobs = adapter.fetch()
    assert session.urls[1] == "https://textron.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=8140753014"
    assert session.bodies[0]["fieldData"]["fields"]["KEYWORD"] == "intern" and session.bodies[1]["pageNo"] == 2
    assert [j.id for j in jobs] == ["taleo:textron:342750", "taleo:textron:342751"]
    assert jobs[0].url == "https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=342750&lang=en"
    assert jobs[0].country_codes == ("US",) and jobs[0].posted_at == "10/01/2026"
    assert jobs[0].jd_text == "Write C++ for sensors."
    assert jobs[1].country_codes == ("CA",) and jobs[1].jd_text == ""  # non-US: no job page


def test_selectminds_creates_a_search_with_the_page_token() -> None:
    from adapters.selectminds import SelectMindsAdapter

    rows = """<div id="job_list_19302" class="job_list_row jlr_Odd ">
      <div class="jlr_title"><p><a href="https://et.selectminds.com/ETP/jobs/intern-credit-risk-19302" class="job_link font_bold">Intern &ndash; Software Engineering</a></p>
      <p class="jlr_cat_loc"><span class="location"> HOUSTON, Texas, United States </span></p></div>
      <div class="jlr_content"><p class="jlr_description">Build tools...</p></div> <!-- jlr_content -->
    </div>"""
    session = Session(
        {
            "/ETP/jobs/search/77/page1": rows,
            "/ETP/jobs/search/77/page2": "<html></html>",
            "/ETP/jobs/intern-credit-risk-19302": '<div class="job_description"><p>Python and SQL.</p></div> <div class="job_meta">',
            "/ETP": '<input type = "hidden" name="tsstoken" id = "tsstoken" value ="tok=="/>',
        },
        posts={"/ajax/jobs/search/create": {"Status": "OK", "Result": {"JobSearch.id": 77}}},
    )
    jobs = SelectMindsAdapter([ListingBoard("energy-transfer", "et.selectminds.com", site="ETP")], session=session).fetch()
    assert session.bodies[0] == {"keywords": "intern"}
    assert [(j.id, j.title) for j in jobs] == [("selectminds:energy-transfer:19302", "Intern – Software Engineering")]
    assert jobs[0].country_codes == ("US",) and jobs[0].jd_text == "Python and SQL."


def test_yello_applies_the_us_country_filter_and_reads_job_pages() -> None:
    from adapters.yello import YelloAdapter

    board = '<div :filters="[{&quot;id&quot;:30091,&quot;label&quot;:&quot;United Kingdom&quot;},{&quot;id&quot;:30092,&quot;label&quot;:&quot;United States&quot;}]"></div>'
    row = ('<li class="search-results__item"><a class="search-results__req_title" lang="en" '
           'href="/jobs/gqy0pJfA?job_board_id=B1">USA - Tax - Data &amp; Technology - Intern - Summer 2027</a><div><span>1740872</span></div></li>')
    detail = ('<div class="details-top__title pull-left"><h1 lang="en">USA - Tax - Intern</h1><span>1740872</span>'
              '<span>FL-Miami, IL-Chicago</span></div><section class="job-details__description pull-left"><p>Use Python.</p></section>')
    session = Session({
        "/search?": {"html": row, "more_requisitions": False},
        "/jobs/gqy0pJfA": detail,
        "/job_boards/B1": board,
    })
    jobs = YelloAdapter([ListingBoard("ey", "eyglobal.yello.co", site="B1")], session=session).fetch()
    assert session.urls[1] == "https://eyglobal.yello.co/job_boards/B1/search?query=intern&filters=30092&page_number=1"
    assert jobs[0].id == "yello:ey:gqy0pJfA" and jobs[0].country_codes == ("US",)
    assert jobs[0].location == "FL-Miami, IL-Chicago" and jobs[0].jd_text == "Use Python."


def test_deutsche_bank_reads_programmes_and_recsolu_pages() -> None:
    from adapters.deutschebank import DeutscheBankAdapter

    api = {"SearchResult": {"SearchResultCountAll": 2, "SearchResultItems": [
        {"MatchedObjectDescriptor": {"PositionID": "75067", "PositionTitle": "Deutsche Bank Internship Programme - Technology – Cary 2027",
                                     "PositionURI": "https://db.recsolu.com/external/requisitions/ETGZ",
                                     "PositionLocation": [{"CityName": "Cary", "CountryCode": "US"}], "PublicationStartDate": "2026-08-17"}},
        {"MatchedObjectDescriptor": {"PositionID": "74989", "PositionTitle": "2027 Quantitative Internship Programme - Singapore",
                                     "PositionURI": "https://db.recsolu.com/external/requisitions/4Xja",
                                     "PositionLocation": [{"CityName": "Singapore", "CountryCode": "SG"}]}},
    ]}}
    recsolu = ('<ul class="unstyled external_requisition_fields" data-id="requisition-fields">'
               '<li><strong lang="en">Country:</strong><ul class="unstyled"><li>United States</li></ul></li>'
               '<li><strong lang="en">City:</strong><ul class="unstyled"><li>Cary</li></ul></li>'
               '<li><strong lang="en">Job Description:</strong><ul class="unstyled"><li><p>Engineering rotations.</p></li></ul></li></ul></div>')
    session = Session({"graduatesearch": api, "requisitions/ETGZ": recsolu})
    jobs = DeutscheBankAdapter(session=session).fetch()
    assert [j.id for j in jobs] == ["deutschebank:deutsche-bank:75067", "deutschebank:deutsche-bank:74989"]
    assert jobs[0].location == "Cary, United States" and jobs[0].country_codes == ("US",)
    assert "Engineering rotations." in jobs[0].jd_text and jobs[0].posted_at == "2026-08-17"
    assert jobs[1].jd_text == ""  # Singapore: no job page read


def test_ashby_falls_back_to_the_hosted_board_graphql() -> None:
    from adapters.ashby import AshbyAdapter

    board = {"data": {"jobBoard": {"jobPostings": [
        {"id": "a1", "title": "Software Engineer Intern", "locationName": "New York, NY", "secondaryLocations": [{"locationName": "Remote - US"}]},
        {"id": "a2", "title": "Account Executive", "locationName": "London, UK", "secondaryLocations": []},
    ]}}}
    posting = {"data": {"jobPosting": {"id": "a1", "descriptionHtml": "<p>Go and React.</p>", "publishedDate": "2026-10-01"}}}
    session = Session(
        {"posting-api/job-board/whatnot": Response("Not Found", 404)},
        posts={"ApiJobBoardWithTeams": board, "ApiJobPosting": posting},
    )
    adapter = AshbyAdapter(["whatnot"], company_names={"whatnot": "whatnot"}, session=session)
    jobs = adapter.fetch()
    assert adapter.board_errors == [] and adapter.listing_counts == {"whatnot": 2}
    assert jobs[0].location == "New York, NY; Remote - US" and jobs[0].jd_text == "Go and React."
    assert jobs[0].url == "https://jobs.ashbyhq.com/whatnot/a1" and jobs[0].posted_at == "2026-10-01"
    assert jobs[1].jd_text == ""  # descriptions are read only for intern-looking titles
    assert sum("ApiJobPosting" in u for u in session.urls) == 1
