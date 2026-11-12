"""Browser-free scrapers for JobsDB, JobBKK and JobTopGun.

All three boards send their search results as ordinary HTML, so they can be read with a plain HTTP
request and BeautifulSoup. For JobsDB and JobBKK these read the same cards and fields as the Playwright
versions (jobsdb_search.py, jobbkk_search.py). The public demo server is too small to run Chrome, so
it uses these; JobTopGun is scraped this way in both builds.
"""

from __future__ import annotations

import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from tools.jobbkk_search import _BADGES, _MAX_AGE_H, _logo, age_in_hours, jobbkk_search_url
from tools.jobbkk_search import BASE as JOBBKK_BASE
from tools.jobsdb_search import jobsdb_search_url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
}
TIMEOUT_SECONDS = 20


def _get_soup(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
    res.raise_for_status()
    return BeautifulSoup(res.text, "lxml")


def _text(el) -> str:
    return " ".join(el.get_text(" ").split()) if el else ""


def jobsdb_http_search(keyword: str, location: str, num_jobs: int = 5, time_filter: str = "qdr:w") -> list:
    """JobsDB search results (filtered by location and listing date in the URL)."""
    jobs: list = []
    try:
        soup = _get_soup(jobsdb_search_url(keyword, location, time_filter))
    except Exception as e:
        print(f"JobsDB HTTP scrape error: {e}")
        return jobs

    for card in soup.find_all("article"):
        if len(jobs) >= num_jobs:
            break
        title = _text(card.find(["h1", "h2", "h3"]))
        link_el = card.find("a", href=True)
        if not title or not link_el:
            continue
        logo_wrap = card.find(attrs={"data-automation": "company-logo"})
        logo_img = logo_wrap.find("img") if logo_wrap else None
        jobs.append({
            "title": title,
            "company": _text(card.find(attrs={"data-automation": "jobCompany"})) or "Unknown Company",
            "location": _text(card.find(attrs={"data-automation": "jobCardLocation"})),
            "snippet": _text(card.find("ul")),
            "link": urllib.parse.urljoin("https://th.jobsdb.com", link_el["href"]),
            "platform": "JobsDB",
            "date": _text(card.find(attrs={"data-automation": "jobListingDate"})),  # e.g. "3 ชั่วโมงที่ผ่านมา"
            "logo_url": (logo_img.get("src") or "") if logo_img else "",
        })
    return jobs


_RESULT_COUNT = re.compile(r"พบ\s*([\d,]+)\s*ตำแหน่ง")  # "found N positions"


def jobbkk_http_search(keyword: str, location: str, num_jobs: int = 5, time_filter: str = "qdr:w") -> list:
    """JobBKK search results. JobBKK has no date filter in its URL, so older jobs are dropped here."""
    jobs: list = []
    try:
        soup = _get_soup(jobbkk_search_url(keyword, location))
    except Exception as e:
        print(f"JobBKK HTTP scrape error: {e}")
        return jobs

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    # With no match JobBKK still lists unrelated "recommended" jobs; its result count is then 0.
    count = _RESULT_COUNT.search(soup.get_text(" "))
    if count and int(count.group(1).replace(",", "")) == 0:
        return jobs

    max_age = _MAX_AGE_H.get(time_filter, 24 * 7)
    for card in soup.select("a[href*='/jobs/detail']"):
        if len(jobs) >= num_jobs:
            break
        lines = [s.strip() for s in card.get_text("\n").split("\n") if s.strip()]
        # Anchor on the "… ago" line: title and company come right before it.
        at = next((i for i, s in enumerate(lines) if age_in_hours(s) is not None), None)
        if at is None or at < 2:
            continue
        title, company, posted = lines[at - 2], lines[at - 1], lines[at]
        age = age_in_hours(posted)
        if age is None or age > max_age:
            continue
        rest = [s for s in lines[at + 1:] if s not in _BADGES and age_in_hours(s) is None]
        img = card.find("img", alt="company-image")
        jobs.append({
            "title": title,
            "company": company,
            "location": rest[0].rstrip(".").strip() if rest else "",
            "snippet": " · ".join(rest[1:3]) if len(rest) > 1 else "",
            "link": urllib.parse.urljoin(JOBBKK_BASE, card.get("href", "")),
            "platform": "JobBKK",
            "date": posted,
            "logo_url": _logo(img.get("src", "") if img else ""),
        })
    return jobs


# --- JobTopGun ------------------------------------------------------------------------------------

JOBTOPGUN_BASE = "https://www.jobtopgun.com"
_JTG_ANYWHERE = {"", "thailand", "anywhere", "remote", "ทั่วประเทศ", "ประเทศไทย"}
_JTG_THAI_PROVINCES = {"กรุงเทพมหานคร": "bangkok", "กรุงเทพ": "bangkok", "เชียงใหม่": "chiang-mai",
                       "ชลบุรี": "chonburi", "ภูเก็ต": "phuket", "นนทบุรี": "nonthaburi"}
_JTG_JOB_HREF = re.compile(r"^/th/jobs/j\d+-\d+")
_JTG_UI_LINES = {"Save job", "บันทึกงาน"}


def _slug_part(text: str) -> str:
    return urllib.parse.quote(re.sub(r"[\s/]+", "-", text.strip().lower()))


def jobtopgun_search_url(keyword: str, location: str) -> str:
    """e.g. https://www.jobtopgun.com/th/jobs/data-analyst-jobs/in-bangkok"""
    url = f"{JOBTOPGUN_BASE}/th/jobs/{_slug_part(keyword)}-jobs"
    loc = location.strip()
    if loc.lower() not in _JTG_ANYWHERE:
        loc_slug = _JTG_THAI_PROVINCES.get(loc) or (None if re.search(r"[฀-๿]", loc) else _slug_part(loc))
        if loc_slug:
            url += f"/in-{loc_slug}"
    return url


# Thai words that mean the same as common English job-title words, so Thai-only titles match too
# (e.g. "พนักงานบัญชี" for "Accountant"). Keys are English word beginnings, as in _keyword_prefixes.
_THAI_EQUIVALENTS = {
    "accoun": ["บัญชี"], "analys": ["วิเคราะห์"], "data": ["ข้อมูล"], "market": ["การตลาด"],
    "engine": ["วิศวกร"], "softwa": ["ซอฟต์แวร์"], "develo": ["นักพัฒนา", "โปรแกรมเมอร์"],
    "progra": ["โปรแกรมเมอร์"], "sales": ["ขาย"], "financ": ["การเงิน"], "design": ["ออกแบบ"],
    "intern": ["ฝึกงาน", "นักศึกษาฝึกงาน"], "human": ["บุคคล"], "admin": ["ธุรการ"],
}


def _keyword_prefixes(keyword: str) -> list[str]:
    """Word beginnings to look for in a title: 'Accountant' -> ['accoun'], so 'Accounting Staff' matches."""
    words = re.findall(r"[a-z0-9]+|[฀-๿]+", keyword.lower())
    return [w[:6] for w in words if len(w) >= 3]


def _title_matches(title: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    low = title.lower()
    words = re.findall(r"[a-z0-9]+", low)
    for p in prefixes:
        if any(word.startswith(p) for word in words):
            return True
        # Thai has no spaces between words, so Thai terms are matched anywhere in the title.
        if any(t in low for t in [p, *_THAI_EQUIVALENTS.get(p, [])] if re.search(r"[฀-๿]", t)):
            return True
    return False


_POSTED_MARKERS = ("ที่แล้ว", "ที่ผ่านมา", "เมื่อวาน", " ago")


def _posted_line(lines: list[str]) -> str:
    """The card's "… ago" line. Requires an "ago" word, so "สัญญาจ้าง 1 เดือน" (1-month contract) in a
    title isn't mistaken for the posting age."""
    return next((s for s in lines if any(m in s for m in _POSTED_MARKERS) and age_in_hours(s) is not None), "")


def jobtopgun_http_search(keyword: str, location: str, num_jobs: int = 5, time_filter: str = "qdr:w") -> list:
    """JobTopGun search results. When nothing matches, JobTopGun lists unrelated recent jobs instead,
    so only jobs whose title contains a word of the search are kept. It has no date filter in the
    URL, so older jobs are dropped here."""
    jobs: list = []
    url = jobtopgun_search_url(keyword, location)
    try:
        soup = _get_soup(url)
        if not soup.find("a", href=_JTG_JOB_HREF):  # occasionally a page arrives without its job list
            soup = _get_soup(url)
    except Exception as e:
        print(f"JobTopGun HTTP scrape error: {e}")
        return jobs

    prefixes = _keyword_prefixes(keyword)
    max_age = _MAX_AGE_H.get(time_filter, 24 * 7)
    seen: set[str] = set()
    for link in soup.find_all("a", href=_JTG_JOB_HREF):
        if len(jobs) >= num_jobs:
            break
        href = link["href"].split("?")[0]
        if href in seen:
            continue
        seen.add(href)
        # The card is the largest ancestor that still holds only this one job.
        card = link
        while card.parent is not None and len({a["href"].split("?")[0] for a in
                                               card.parent.find_all("a", href=_JTG_JOB_HREF)}) == 1:
            card = card.parent
        lines = [s.strip() for s in card.get_text("\n").split("\n") if s.strip() and s.strip() not in _JTG_UI_LINES]
        if len(lines) < 3:
            continue
        title, company, place = lines[0], lines[1], lines[2]
        if not _title_matches(title, prefixes):
            continue
        posted = _posted_line(lines)
        age = age_in_hours(posted) if posted else None
        if age is not None and age > max_age:
            continue
        details = [s for s in lines[3:] if s != posted]
        img = card.find("img")
        jobs.append({
            "title": title,
            "company": company,
            "location": place,
            "snippet": " · ".join(details[:5]),  # job type, salary and the card's highlights
            "link": urllib.parse.urljoin(JOBTOPGUN_BASE, href),
            "platform": "JobTopGun",
            "date": posted,
            "logo_url": _logo(img.get("src", "")) if img else "",
        })
    return jobs
