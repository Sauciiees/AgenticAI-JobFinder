from .job_tools import search_jobbkk_jobs, search_jobsdb_jobs, search_jobtopgun_jobs, search_linkedin_jobs

# The agent's search tools: one per job board.
ALL_TOOLS = [
    search_linkedin_jobs,
    search_jobsdb_jobs,
    search_jobbkk_jobs,
    search_jobtopgun_jobs,
]
