"""Job search across the four boards the app scrapes directly: LinkedIn, JobsDB, JobBKK and JobTopGun.

All four send their search results as ordinary HTML, so no browser and no search API are needed.
The local full build still uses Playwright for JobsDB and JobBKK (agent/nodes.py).
"""

from collections.abc import Callable

from langchain_core.tools import tool

BOARDS = ("LinkedIn", "JobsDB", "JobBKK", "JobTopGun")


def public_job_search(keyword: str, location: str, time_filter: str = "qdr:w") -> list:
  """Job search without a browser, across all four boards, run in parallel. A board that fails or
  finds nothing is skipped; the others still return their jobs."""
  from concurrent.futures import ThreadPoolExecutor

  from tools.http_boards import jobbkk_http_search, jobsdb_http_search, jobtopgun_http_search
  from tools.linkedin_search import linkedin_direct_search

  sources: list[tuple[str, Callable[..., list], tuple]] = [
      ("LinkedIn", linkedin_direct_search, (keyword, location, time_filter, 10)),
      ("JobsDB", jobsdb_http_search, (keyword, location, 5, time_filter)),
      ("JobBKK", jobbkk_http_search, (keyword, location, 5, time_filter)),
      ("JobTopGun", jobtopgun_http_search, (keyword, location, 5, time_filter)),
  ]
  jobs: list = []
  with ThreadPoolExecutor(max_workers=len(sources)) as pool:
    futures = [(name, pool.submit(fn, *args)) for name, fn, args in sources]
    for name, future in futures:
      try:
        found = future.result()
        print(f"  {name}: {len(found)} jobs")
        jobs.extend(found)
      except Exception as e:
        print(f"Job search source {name} failed: {e}")
  return jobs


def _format_results_as_string(jobs: list, platform_name: str, keyword: str, location: str) -> str:
  """Format structured job list into a readable string for LangChain tool output."""
  if not jobs:
    return f"No live {platform_name} job postings found for '{keyword}' in '{location}'."

  formatted_results = [
      f"- **{job['title']}**\n  Company: {job['company']}\n  Snippet: {job['snippet']}\n  Link: {job['link']}"
      for job in jobs
  ]
  return f"Found {len(formatted_results)} live {platform_name} jobs for '{keyword}' in {location}:\n\n" + "\n\n".join(
      formatted_results
  )


@tool
def search_linkedin_jobs(keyword: str, location: str = "Thailand") -> str:
  """Search specifically for open job positions on LinkedIn.

  Use this tool whenever the user wants to find live job listings on LinkedIn.

  Args:
      keyword: The job title, tech stack, or skill (e.g., 'Data Scientist', 'Python Developer').
      location: The target location or country (default is 'Thailand').
  """
  from tools.linkedin_search import linkedin_direct_search
  jobs = linkedin_direct_search(keyword, location, "qdr:w", 10)
  return _format_results_as_string(jobs, "LinkedIn", keyword, location)


@tool
def search_jobsdb_jobs(keyword: str, location: str = "Thailand") -> str:
  """Search specifically for open job positions on JobsDB.

  Use this tool whenever the user wants to find live job listings on JobsDB.

  Args:
      keyword: The job title, tech stack, or skill.
      location: The target location or country (default is 'Thailand').
  """
  from tools.http_boards import jobsdb_http_search
  jobs = jobsdb_http_search(keyword, location, 10)
  return _format_results_as_string(jobs, "JobsDB", keyword, location)


@tool
def search_jobbkk_jobs(keyword: str, location: str = "Thailand") -> str:
  """Search specifically for open job positions on JobBKK.

  Use this tool whenever the user wants to find live job listings on JobBKK.

  Args:
      keyword: The job title, tech stack, or skill.
      location: The target location or country (default is 'Thailand').
  """
  from tools.http_boards import jobbkk_http_search
  jobs = jobbkk_http_search(keyword, location, 10)
  return _format_results_as_string(jobs, "JobBKK", keyword, location)


@tool
def search_jobtopgun_jobs(keyword: str, location: str = "Thailand") -> str:
  """Search specifically for open job positions on JobTopGun.

  Use this tool whenever the user wants to find live job listings on JobTopGun.

  Args:
      keyword: The job title, tech stack, or skill.
      location: The target location or country (default is 'Thailand').
  """
  from tools.http_boards import jobtopgun_http_search
  jobs = jobtopgun_http_search(keyword, location, 10)
  return _format_results_as_string(jobs, "JobTopGun", keyword, location)
