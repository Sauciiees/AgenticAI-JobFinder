"""Job-title parsing and Serper result filtering. Replaces scratch/test_extractor.py and the
scratch/test_serper*.py scripts, with the network call mocked out."""

import pytest

from agent.nodes import extract_text_from_response
from tools import job_tools


@pytest.mark.parametrize(
    "raw,title,company",
    [
        ("Krungthai Bank hiring AI Engineer in Bangkok ...", "AI Engineer", "Krungthai Bank"),
        ("Beryl8 hiring Solution Architect - AI (Lead AI)", "Solution Architect - AI (Lead AI)", "Beryl8"),
        ("Software Engineer (Senior/Lead Squad) at Tech Co", "Software Engineer (Senior/Lead Squad)", "Tech Co"),
        ("Data Analyst - Acme Corp", "Data Analyst", "Acme Corp"),
        ("Backend Developer - Acme Corp - Indeed.com", "Backend Developer", "Acme Corp"),
        ("Plain Title", "Plain Title", "Unknown Company"),
    ],
)
def test_clean_title_and_company(raw, title, company):
    assert job_tools._clean_title_and_company(raw, "") == (title, company)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_serper_filters_directory_pages(monkeypatch):
    organic = [
        {"title": "Acme hiring Data Analyst in Bangkok", "link": "https://th.linkedin.com/jobs/view/123", "snippet": "s"},
        {"title": "1000+ Data Analyst jobs", "link": "https://th.linkedin.com/jobs/data-analyst", "snippet": "s"},
        {"title": "Data Analyst (75 Open Roles)", "link": "https://example.com/x", "snippet": "s"},
        {"title": "Beta hiring Analyst", "link": "https://th.linkedin.com/jobs/data-analyst-jobs", "snippet": "s"},
    ]
    monkeypatch.setattr(job_tools.requests, "post", lambda *a, **k: _FakeResponse({"organic": organic}))

    jobs = job_tools._serper_search_structured("data analyst", "Bangkok", "linkedin.com/jobs", "LinkedIn")

    assert [j["company"] for j in jobs] == ["Acme"]
    assert jobs[0]["title"] == "Data Analyst"
    assert jobs[0]["platform"] == "LinkedIn"


def test_serper_without_key_returns_empty(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert job_tools._serper_search_structured("x", "y", "z", "P") == []


def test_serper_network_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("offline")

    monkeypatch.setattr(job_tools.requests, "post", boom)
    assert job_tools._serper_search_structured("x", "y", "z", "P") == []


@pytest.mark.parametrize(
    "content,expected",
    [
        ("plain text", "plain text"),
        ([{"type": "text", "text": "from parts"}], "from parts"),
        (123, "123"),
    ],
)
def test_extract_text_from_response(content, expected):
    assert extract_text_from_response(content) == expected
