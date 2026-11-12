# Production plan

Agreed 2026-11-11. The aim is a professional-looking portfolio project that runs for free, for fewer than 100 users. The top risk is leaking user data.

## Product

- **Public demo.** Anyone can sign up and use search, job matching, CV analysis and the agent chat.
- **Try as guest.** One click opens a demo account with seeded data. Guest accounts are wiped after 24 hours.
- **Auto-apply dry run for guests.** The agent writes a tailored cover letter and the form answers, and shows the steps it would take, ending with "Submit skipped (demo)". There is no browser in the cloud. A dry run counts as one agent run.
- **Real auto-apply and the live browser are admin-only.** They run only on the owner's machine (`ENABLE_BROWSER_AUTOMATION=true`, local Docker Compose, its own local database). A demo video shows them.

## Architecture

| Piece | Choice |
|---|---|
| Frontend | Cloudflare Pages, plus a Pages Function that proxies `/api/*`, so the browser only ever sees one site |
| API | Render free tier, slim image (no Xvfb, VNC or Playwright), `ENABLE_BROWSER_AUTOMATION=false` |
| Database | Free Neon or Supabase Postgres, with Alembic migrations |
| Vectors | pgvector, replacing ChromaDB |
| Agent state | LangGraph's Postgres checkpointer |
| CVs | Private bucket (Supabase Storage or R2), served only through short-lived signed links. PDF or DOCX, 5 MB maximum |
| Errors | Sentry free tier, on both the frontend and the API |

A cold start of 30–60 seconds is accepted. The frontend shows a "waking up" state while the API starts.

## Security and abuse

- The JWT lives in an `httpOnly`, `Secure`, `SameSite=Strict` cookie, with CSRF protection. No more localStorage.
- Cloudflare Turnstile on sign-up, plus security headers.
- Limits are stored in Postgres, not in memory:
  - 10 agent runs per user per day
  - 60 agent runs per day across everyone; when hit, the app shows "Demo quota reached"
  - Login: 5 per minute per IP address
  - Sign-up: 3 per hour per IP address
- Every user runs on the owner's Gemini key.
- **Retention:**
  - CVs and chats are deleted after 30 days.
  - A "Delete my account" button removes everything.
  - A privacy note sits on the sign-up page.
  - A daily GitHub Actions job calls the protected `/internal/cleanup` endpoint, authenticated with a shared secret.

## Process

- Work happens on branches and goes in through pull requests. CI runs ruff, mypy and pytest.
- Deploys happen only once CI is green, with Alembic migrations run before the new version starts.

## Phases

1. **Clean up.** Delete the Streamlit app, move `scratch/` into `tests/`, add CI. *(done)*
2. **Data.** Postgres, Alembic, pgvector, the Postgres checkpointer, object storage, SQLAlchemy `Mapped[]` models. *(done)*
3. **Split and deploy.** The automation flag, the slim public image, Render, the Pages proxy, cookie login. *(done)*
4. **Public safety.** Turnstile, guest accounts and the dry run, quotas, retention, headers, Sentry. *(done)*
5. **Polish.** Seeded data, the waking-up screen, the README with an architecture diagram, the demo video. *(done, except recording the video)*

## Known follow-ups from Phase 1

- mypy checks only the files listed in `pyproject.toml`. `server.py`, `main.py` and the Playwright tools join that list after Phases 2 and 3.
- ~~`models.py` still uses `datetime.utcnow` defaults.~~ Fixed in Phase 2.
- `scratch/` is untracked but still on disk locally. Delete it whenever you like.

## Changes made during implementation

- **Job boards.** The app searches LinkedIn, JobsDB, JobBKK and JobTopGun. All four send their search results as plain HTML, so the demo scrapes them without a browser; the local build still drives JobsDB and JobBKK with Playwright. Indeed was dropped in November 2026: it now blocks automated access (Cloudflare bot check, 403 on plain requests and RSS) and its Thai job pages aren't in Google or Bing, so no legitimate route was left. Serper was dropped with it.
- **Token key.** Every login token carries a per-user key. SQLite reuses the ids of deleted users, so without it an old token could open a newer account. Logout rotates the key, which signs the user out everywhere.
