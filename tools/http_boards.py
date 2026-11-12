"""Browser-free scrapers for JobsDB and JobBKK.

Both boards send their search results as ordinary HTML, so they can be read with a plain HTTP request
and BeautifulSoup: the same cards and fields the Playwright versions (jobsdb_search.py,
jobbkk_search.py) read, without needing a browser. The public demo server is too small to run Chrome,
so it uses these.
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
