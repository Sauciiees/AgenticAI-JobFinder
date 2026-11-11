from .job_tools import search_indeed_jobs, search_jobbkk_jobs, search_jobsdb_jobs, search_linkedin_jobs

# Update the centralized tool list
ALL_TOOLS = [
    search_linkedin_jobs,
    search_indeed_jobs,
    search_jobsdb_jobs,
    search_jobbkk_jobs
]