"""Browser-free JobsDB, JobBKK and JobTopGun scrapers and the four-board search, on sample HTML (no network)."""

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


JOBTOPGUN_HTML = """
<html><body>
<div class="card">
  <a href="/th/jobs/j179959-24"><h3>นักวิเคราะห์ข้อมูล (Data Analyst)</h3></a>
  <img src="/_next/image?url=https%3A%2F%2Fstatic.jobtopgun.com%2Fcompany_image%2F9.png&w=64">
  <div>บริษัท ดี ไฟน์ ไนซ์ จำกัด</div><div>สมุทรสาคร, กรุงเทพมหานคร</div><div>งานประจำ</div>
  <div>20,000 - 25,000 บาท/เดือน</div><div>ได้ใช้ SQL, Power BI</div><div>1 สัปดาห์ที่แล้ว</div>
  <button>Save job</button>
</div>
<div class="card">
  <a href="/th/jobs/j200000-1"><h3>Accounting Staff (สัญญาจ้าง 1 เดือน)</h3></a>
  <div>Some Co</div><div>กรุงเทพมหานคร</div><div>งานพาร์ทไทม์</div><div>2 วันที่แล้ว</div>
</div>
<div class="card">
  <a href="/th/jobs/j300000-1"><h3>Project Manager</h3></a>
  <div>Other Co</div><div>กรุงเทพมหานคร</div><div>งานประจำ</div><div>เมื่อวาน</div>
</div>
<div class="card">
  <a href="/th/jobs/j400000-1"><h3>Data Engineer</h3></a>
  <div>Old Co</div><div>กรุงเทพมหานคร</div><div>งานประจำ</div><div>2 เดือนที่แล้ว</div>
</div>
</body></html>
"""


def test_jobtopgun_url():
    assert http_boards.jobtopgun_search_url("Data Analyst", "Bangkok") ==         "https://www.jobtopgun.com/th/jobs/data-analyst-jobs/in-bangkok"
    assert http_boards.jobtopgun_search_url("Accountant", "Thailand") == "https://www.jobtopgun.com/th/jobs/accountant-jobs"
    assert http_boards.jobtopgun_search_url("x", "กรุงเทพมหานคร").endswith("/in-bangkok")


def test_jobtopgun_cards_relevance_and_age(serve):
    serve(JOBTOPGUN_HTML)
    jobs = http_boards.jobtopgun_http_search("Data Analyst", "Bangkok", 5, "qdr:w")
    # "Project Manager" doesn't match; the 2-month-old Data Engineer is too old for a 1-week search.
    assert [j["title"] for j in jobs] == ["นักวิเคราะห์ข้อมูล (Data Analyst)"]
    j = jobs[0]
    assert j["company"] == "บริษัท ดี ไฟน์ ไนซ์ จำกัด" and j["location"] == "สมุทรสาคร, กรุงเทพมหานคร"
    assert j["date"] == "1 สัปดาห์ที่แล้ว" and "20,000" in j["snippet"]
    assert j["link"] == "https://www.jobtopgun.com/th/jobs/j179959-24"
    assert j["logo_url"] == "https://static.jobtopgun.com/company_image/9.png"
    assert j["platform"] == "JobTopGun"


def test_jobtopgun_contract_length_is_not_the_posting_age(serve):
    serve(JOBTOPGUN_HTML)
    jobs = http_boards.jobtopgun_http_search("Accountant", "Bangkok", 5, "qdr:w")
    assert [j["date"] for j in jobs] == ["2 วันที่แล้ว"]  # not "1 เดือน" from the title


def test_jobtopgun_unrelated_fallback_jobs_are_dropped(serve):
    serve(JOBTOPGUN_HTML)
    assert http_boards.jobtopgun_http_search("zzqxwvnonsense", "Bangkok", 5, "qdr:m") == []


@pytest.mark.parametrize("title,keyword,ok", [
    ("Accounting Staff", "Accountant", True),
    ("พนักงานบัญชี", "Accountant", True),
    ("วิศวกรซอฟต์แวร์", "Software Engineer", True),
    ("Digital Marketing Executive", "Digital Marketing", True),
    ("Project Manager", "Data Analyst", False),
])
def test_title_matching(title, keyword, ok):
    assert http_boards._title_matches(title, http_boards._keyword_prefixes(keyword)) is ok


def test_public_search_uses_all_four_boards(monkeypatch):
    import tools.http_boards as hb
    import tools.linkedin_search as li

    monkeypatch.setattr(li, "linkedin_direct_search", lambda *a: [{"platform": "LinkedIn"}])
    monkeypatch.setattr(hb, "jobsdb_http_search", lambda *a: [{"platform": "JobsDB"}])
    monkeypatch.setattr(hb, "jobbkk_http_search", lambda *a: [{"platform": "JobBKK"}])
    monkeypatch.setattr(hb, "jobtopgun_http_search", lambda *a: [{"platform": "JobTopGun"}])
    jobs = job_tools.public_job_search("Data Analyst", "Bangkok", "qdr:w")
    assert sorted(j["platform"] for j in jobs) == ["JobBKK", "JobTopGun", "JobsDB", "LinkedIn"]


def test_one_failing_board_does_not_break_search(monkeypatch):
    import tools.http_boards as hb
    import tools.linkedin_search as li

    def boom(*a):
        raise RuntimeError("site down")

    monkeypatch.setattr(li, "linkedin_direct_search", boom)
    monkeypatch.setattr(hb, "jobsdb_http_search", lambda *a: [{"platform": "JobsDB"}])
    monkeypatch.setattr(hb, "jobbkk_http_search", lambda *a: [])
    monkeypatch.setattr(hb, "jobtopgun_http_search", lambda *a: [])
    assert [j["platform"] for j in job_tools.public_job_search("x", "y")] == ["JobsDB"]
