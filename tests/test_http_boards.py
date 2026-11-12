"""Browser-free JobsDB/JobBKK scrapers and the four-board public search, on sample HTML (no network)."""

import pytest

from tools import http_boards, job_tools

JOBSDB_HTML = """
<html><body>
<article>
  <h3>Data Analyst</h3>
  <a href="/th/job/123?type=standard">view</a>
  <a data-automation="jobCompany">Acme Co., Ltd.</a>
  <span data-automation="jobCardLocation">ราชเทวี กรุงเทพมหานคร</span>
  <span data-automation="jobListingDate">3 ชั่วโมงที่ผ่านมา</span>
  <div data-automation="company-logo"><img src="https://logo.example/acme.png"></div>
  <ul><li>SQL and Power BI</li><li>Hybrid</li></ul>
</article>
<article><h3>No link here</h3></article>
</body></html>
"""

JOBBKK_HTML = """
<html><body>
<p class="hidden">ขออภัยไม่พบตำแหน่งงาน</p>
<div>พบ 161 ตำแหน่ง</div>
<a href="/jobs/detailurgent/1/2">
  <img alt="company-image" src="/_next/image?url=https%3A%2F%2Fstore.jobbkk.com%2Flogo.png&w=96">
  <div>Data Analyst</div><div>Teeyu Express</div><div>2 ชั่วโมงที่แล้ว</div>
  <div>รับสมัครด่วน</div><div>กรุงเทพมหานคร (เขตลาดพร้าว)</div><div>ตามตกลง</div>
</a>
<a href="/jobs/detail/9/9">
  <div>Old Job</div><div>Old Co</div><div>2 เดือนที่แล้ว</div><div>กรุงเทพมหานคร</div>
</a>
</body></html>
"""

JOBBKK_EMPTY_HTML = """
<html><body>
<p>ขออภัยไม่พบตำแหน่งงาน</p><div>พบ 0 ตำแหน่ง</div>
<a href="/jobs/detail/5/5"><div>Recommended</div><div>Some Co</div><div>1 วันที่แล้ว</div></a>
</body></html>
"""


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def serve(monkeypatch):
    def _serve(html, status=200):
        monkeypatch.setattr(http_boards.requests, "get", lambda *a, **k: _Resp(html, status))
    return _serve


def test_jobsdb_card_fields(serve):
    serve(JOBSDB_HTML)
    jobs = http_boards.jobsdb_http_search("Data Analyst", "Bangkok", 5, "qdr:w")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Analyst" and j["company"] == "Acme Co., Ltd."
    assert j["link"] == "https://th.jobsdb.com/th/job/123?type=standard"
    assert j["location"] == "ราชเทวี กรุงเทพมหานคร" and j["date"] == "3 ชั่วโมงที่ผ่านมา"
    assert j["logo_url"] == "https://logo.example/acme.png" and "Power BI" in j["snippet"]
    assert j["platform"] == "JobsDB"


def test_jobbkk_card_fields_and_age_filter(serve):
    serve(JOBBKK_HTML)
    jobs = http_boards.jobbkk_http_search("Data Analyst", "Bangkok", 5, "qdr:w")
    assert [j["title"] for j in jobs] == ["Data Analyst"]  # the 2-month-old job is dropped
    j = jobs[0]
    assert j["company"] == "Teeyu Express" and j["date"] == "2 ชั่วโมงที่แล้ว"
    assert j["location"] == "กรุงเทพมหานคร (เขตลาดพร้าว)" and j["snippet"] == "ตามตกลง"
    assert j["link"] == "https://www.jobbkk.com/jobs/detailurgent/1/2"
    assert j["logo_url"] == "https://store.jobbkk.com/logo.png"


def test_jobbkk_no_results_ignores_recommendations(serve):
    serve(JOBBKK_EMPTY_HTML)
    assert http_boards.jobbkk_http_search("zzz", "Bangkok", 5, "qdr:w") == []


def test_http_errors_return_empty(serve):
    serve("blocked", status=403)
    assert http_boards.jobsdb_http_search("x", "Bangkok") == []
    assert http_boards.jobbkk_http_search("x", "Bangkok") == []


def test_public_search_uses_all_four_boards(monkeypatch):
    import tools.http_boards as hb
    import tools.linkedin_search as li

    monkeypatch.setattr(li, "linkedin_direct_search", lambda *a: [{"platform": "LinkedIn"}])
    monkeypatch.setattr(hb, "jobsdb_http_search", lambda *a: [{"platform": "JobsDB"}])
    monkeypatch.setattr(hb, "jobbkk_http_search", lambda *a: [{"platform": "JobBKK"}])
    calls = []
    monkeypatch.setattr(job_tools, "_serper_search_structured",
                        lambda kw, loc, site, name, tf, num: calls.append(site) or [{"platform": name}])
    jobs = job_tools.public_job_search("Data Analyst", "Bangkok", "qdr:w")
    assert sorted(j["platform"] for j in jobs) == ["Indeed", "JobBKK", "JobsDB", "LinkedIn"]
    assert calls == [job_tools.INDEED_SERPER_SITE]  # only Indeed goes through Serper


def test_one_failing_board_does_not_break_search(monkeypatch):
    import tools.http_boards as hb
    import tools.linkedin_search as li

    def boom(*a):
        raise RuntimeError("site down")

    monkeypatch.setattr(li, "linkedin_direct_search", boom)
    monkeypatch.setattr(hb, "jobsdb_http_search", lambda *a: [{"platform": "JobsDB"}])
    monkeypatch.setattr(hb, "jobbkk_http_search", lambda *a: [])
    monkeypatch.setattr(job_tools, "_serper_search_structured", lambda *a: [])
    assert [j["platform"] for j in job_tools.public_job_search("x", "y")] == ["JobsDB"]
