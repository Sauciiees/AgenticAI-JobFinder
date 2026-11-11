# Testing it yourself

## 1. Automated checks (2 minutes)

```bash
.venv\Scripts\activate
pip install -r requirements-dev.txt
ruff check .
mypy
pytest                      # SQLite, about 30 s
$env:TEST_POSTGRES = "1"    # PowerShell; Git Bash/macOS/Linux: export TEST_POSTGRES=1
pytest                      # embedded Postgres 16 + pgvector
```

All 88 tests should pass on both. No real API calls are made.

## 2. The public demo, on your machine (10 minutes)

```bash
python scripts/dev.py
```

The first run migrates `data/jobfinder.db` and saves a backup copy next to it first
(`jobfinder.db.backup-<time>`). Open http://localhost:8000 and try:

| Try this | You should see |
|---|---|
| **Try it as a guest** | Signed in as Alex Demo, a blue guest banner, 3 applications, "10 of 10 agent runs left today" |
| Click **Find Data Analyst roles in Bangkok…** | 10 or so live jobs. With a valid Serper key, some come from JobsDB, Indeed and JobBKK, not only LinkedIn |
| Tick 2 jobs → **Continue** | Match scores, notes, and cover letters with resume bullets under "Cover letters and resume bullets" |
| **Run the dry run** | "Dry run finished. Nothing was submitted.", with steps and a form-answer table for each job; 2 new "Dry run" entries in Applications |
| Ask "what should I improve in my CV?" | A career-advice answer that quotes the sample CV |
| Profile → upload a PDF or .docx | "CV uploaded and processed"; a .txt or renamed file is refused |
| Reload the page | Still signed in (the cookie session) |
| Profile → **Delete my account** | Back at sign-in with "Your account and all its data were deleted." |
| Create a real account, then sign out | Sign-out works; the old session can't be reused |
| Send 10 messages as one user | The 11th is refused: "You've used today's 10 agent runs" |

Each search uses about 3 Serper searches and a few Gemini calls on your keys.

## 3. The full build with real auto-apply (needs Docker)

```bash
docker compose up --build
```

Open http://localhost:8000 and sign in (quotas are off in this build). Profile → **Job board
sign-in** opens the board in the browser view at http://localhost:6080: sign in there once.
Approving an application then runs the real auto-apply agent, which you can watch in that view.
This is the run to record for the demo video.

## 4. After deploying

Use the checklist at the end of [DEPLOY.md](DEPLOY.md).
