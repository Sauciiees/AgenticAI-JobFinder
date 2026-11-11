# Job Finder: an agentic AI job search assistant

[![CI](https://github.com/Sauciiees/AngenticAI-JobFinder/actions/workflows/ci.yml/badge.svg)](https://github.com/Sauciiees/AngenticAI-JobFinder/actions/workflows/ci.yml)

A multi-agent assistant that searches Thai job boards, scores each role against your CV, drafts a
tailored cover letter and resume bullets, and (on its owner's machine) applies in a real browser.
Built with **LangGraph**, **Gemini**, **FastAPI** and **Postgres + pgvector**.

**Live demo:** _add your Cloudflare Pages URL here_. Click **Try it as a guest** for a ready-made
account with a sample CV. **Auto-apply video:** _add your video link here_.

| Sign in or try as a guest | Live jobs from the boards |
|---|---|
| ![Sign-in page with a "Try it as a guest" button](docs/screenshots/1-sign-in.jpg) | ![Job picker listing live Data Analyst roles](docs/screenshots/3-job-picker.jpg) |
| **Scored against your CV, with tailored materials** | **Auto-apply dry run in the demo** |
| ![Review step with match scores and resume bullets](docs/screenshots/4-review.jpg) | ![Dry-run plan with the form answers the agent would submit](docs/screenshots/5-dry-run.jpg) |

## How it works

The agent is a LangGraph state machine with two human checkpoints: you pick the jobs, and you
approve the drafted application before anything is sent.

```mermaid
flowchart LR
    U([You]) --> R{Router}
    R -- question --> C[Career advisor chat]
    R -- job search --> S[Discovery agent<br/>LinkedIn, JobsDB, Indeed, JobBKK]
    S --> P[[You pick jobs]]
    P --> M[Matching agent<br/>scores each job vs. CV]
    M --> T[Tailoring agent<br/>cover letter + bullets]
    T --> A[[You approve]]
    A --> X{Mode}
    X -- local build --> B[Auto-apply agent<br/>Playwright + Gemini vision]
    X -- public demo --> D[Dry run<br/>plans steps and answers]
    B --> L[(Applications log)]
    D --> L
```

CV text is split into chunks and embedded with Gemini. The matching and tailoring agents retrieve
the chunks most relevant to each job (pgvector cosine search) so they quote your real experience.

## Architecture

```mermaid
flowchart LR
    Browser -->|HTTPS, one site| Pages[Cloudflare Pages<br/>static web app]
    Pages -->|/api/* via Pages Function<br/>+ shared secret| API[FastAPI on Render<br/>slim image, free tier]
    API --> PG[(Neon Postgres<br/>users, chats, pgvector,<br/>LangGraph checkpoints)]
    API --> R2[(Private bucket<br/>CVs, signed links)]
    API --> Gemini[Gemini API]
    API --> Serper[Serper search]
    GH[GitHub Actions] -->|CI on every PR| API
    GH -->|daily cleanup| API
    API -.-> Sentry
```

| Concern | How it's handled |
|---|---|
| Auth | bcrypt passwords, JWT in an `httpOnly`, `Secure`, `SameSite=Strict` cookie, CSRF double-submit, per-user token key (logout signs out everywhere) |
| Abuse | Cloudflare Turnstile on sign-up and guests; rate limits and daily quotas counted in Postgres (10 agent runs per user, 60 overall, Serper budget) |
| Privacy | CVs in a private bucket behind short-lived signed links; guests deleted after 24 h, CVs and idle chats after 30 days; one-click account deletion |
| Hardening | The API only answers requests from the proxy; CSP and security headers; uploads checked by content (PDF/DOCX), size-capped, parsed before storing |
| Reliability | Alembic migrations run before each start; deploys only after CI passes; Sentry on both ends; "waking up" screen for the free tier's cold start |
| Cost | $0: Render, Neon, R2, Pages, Turnstile, Sentry free tiers. The API uses about 150 MB of Render's 512 MB |

**Two builds from one codebase.** `ENABLE_BROWSER_AUTOMATION` switches between:

- **Public demo** (`api.dockerfile`): no browser. LinkedIn's public search page plus Serper, job
  pages read over plain HTTP, auto-apply as a dry run, quotas on.
- **Local full build** (`backend.dockerfile` + `docker compose`): Playwright searches, job-board
  sign-in in a visible browser (viewable through noVNC), and real auto-apply with Gemini vision.
  Your job-board sessions never leave your machine.

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # then fill in GOOGLE_API_KEY, SERPER_API_KEY, JWT_SECRET_KEY
python scripts/dev.py             # http://localhost:8000, demo mode, SQLite
```

Full build with real auto-apply (needs Docker):

```bash
docker compose up --build         # http://localhost:8000, browser view at http://localhost:6080
```

See [docs/TESTING.md](docs/TESTING.md) for a walkthrough and [docs/DEPLOY.md](docs/DEPLOY.md) to
put it online for free.

## Tests

```bash
pytest                            # SQLite
TEST_POSTGRES=1 pytest            # embedded Postgres 16 + pgvector, no Docker needed
ruff check . && mypy
```

88 tests cover auth and CSRF, rate limits and quotas, uploads, data isolation between users,
retention, the proxy checks, and the whole agent pipeline with a fake LLM. CI runs them on SQLite
and on a pgvector Postgres container.

## Project layout

```
server.py            FastAPI app: auth, agent endpoints, guests, quotas, retention
agent/               LangGraph graph, agent nodes, CV parsing, vector store
tools/               job-board search, scraping, Playwright auto-apply (local build)
config.py            every setting, from the environment
models.py            SQLAlchemy models;  migrations/  Alembic
auth.py limits.py retention.py storage.py demo.py
frontend/            the web app (vanilla JS), _headers for Cloudflare Pages
functions/api/       Cloudflare Pages Function that proxies /api to Render
api.dockerfile       slim public image;  backend.dockerfile  full local image
render.yaml          Render blueprint;  .github/workflows  CI and daily cleanup
```

## Notes

- LinkedIn's terms forbid automated applying. Real auto-apply runs only in the local build, on
  the owner's own accounts.
- The demo persona "Alex Demo" and the sample applications are fictional.
