"""The agent's search tools and helpers (network calls are mocked)."""

import pytest

from agent.nodes import extract_text_from_response
from tools import ALL_TOOLS, job_tools


def test_agent_has_one_search_tool_per_board():
    names = sorted(t.name for t in ALL_TOOLS)
    assert names == ["search_jobbkk_jobs", "search_jobsdb_jobs", "search_jobtopgun_jobs", "search_linkedin_jobs"]
    assert sorted(job_tools.BOARDS) == ["JobBKK", "JobTopGun", "JobsDB", "LinkedIn"]


def test_tool_output_lists_jobs(monkeypatch):
    import tools.http_boards as hb

    monkeypatch.setattr(hb, "jobtopgun_http_search", lambda *a: [
        {"title": "Data Analyst", "company": "Acme", "snippet": "SQL", "link": "https://x/1"}])
    out = job_tools.search_jobtopgun_jobs.invoke({"keyword": "Data Analyst", "location": "Bangkok"})
    assert "Found 1 live JobTopGun jobs" in out and "Acme" in out


def test_tool_output_when_nothing_found(monkeypatch):
    import tools.http_boards as hb

    monkeypatch.setattr(hb, "jobbkk_http_search", lambda *a: [])
    out = job_tools.search_jobbkk_jobs.invoke({"keyword": "zzz", "location": "Bangkok"})
    assert out.startswith("No live JobBKK job postings found")


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
