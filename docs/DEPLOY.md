# Deploying the public demo (free)

Three free accounts: **Neon** (database), **Render** (the API) and **Cloudflare** (the web page).
About 30 minutes. Keep a Notepad file open to collect the values marked **(save)**, and never
commit it or paste it into chats.

## 1. Database: Neon

1. Sign up at https://neon.tech and create a project named `jobfinder`, region **Singapore**.
2. **Connect** → turn **Pooled connection** on → copy the line starting `postgresql://` and ending
   `?sslmode=require` → `DATABASE_URL` **(save)**.

The app creates its tables, turns on pgvector, and stores uploaded CVs in this database by itself.

## 2. API: Render

1. Sign up at https://render.com with your GitHub account, and allow it to see the
   `AgenticAI-JobFinder` repository.
2. **New +** → **Blueprint** → choose the repository → **Connect**.
3. Render reads `render.yaml` and asks for two values:
   - `DATABASE_URL`: the Neon line from step 1
   - `GOOGLE_API_KEY`: your Gemini key
4. **Apply**. The first build takes 5–10 minutes. It's done when the service shows **Live**.
5. Open the service and **(save)**:
   - the URL at the top, e.g. `https://jobfinder-api.onrender.com`
   - **Environment** → the value of `PROXY_SHARED_SECRET` (click the eye icon)
6. Check it: open `<your Render URL>/api/health` in a browser. It should say the API is online.
   Other addresses answer *Not found*. That's on purpose: the API only accepts requests from
   your web page.

Render deploys the `chore/phase-1-cleanup` branch, and only after CI passes. If Render says
`autoDeployTrigger` is unknown, delete that line from `render.yaml`, then pick *Settings → Build &
Deploy → Auto-Deploy → After CI Checks Pass* in the dashboard.

## 3. Web page: Cloudflare (Worker)

Cloudflare now creates a **Worker** when you connect a repository (the page shows *Version ID*,
*Traffic %* and *Edit code*). The repository is set up for that: `wrangler.jsonc` serves
`frontend/` and `worker/index.js` forwards `/api/*` to Render.

1. Sign up at https://dash.cloudflare.com.
2. **Workers & Pages** → **Create** → **Import a repository** → choose the repository.
   - **Project name:** `agenticai-jobfinder`. It must match `"name"` in `wrangler.jsonc`;
     change that line if you pick another name.
   - **Build command:** leave empty. **Deploy command:** `npx wrangler deploy` (the default).
3. Worker → **Settings** → **Build** → **Branch control**: set the production branch to
   `chore/phase-1-cleanup`.
4. Worker → **Settings** → **Variables and Secrets** → **Add**, type **Secret**, for both:
   - `API_ORIGIN` = your Render URL (no `/` at the end)
   - `PROXY_SHARED_SECRET` = the value from Render
5. Push any commit (or retry the latest build) so it deploys. Your address is under **Settings →
   Domains & Routes**, ending in `.workers.dev`. The first visit may show "Waking up the server"
   for up to a minute.

*Classic Cloudflare Pages also works* (the repository keeps `functions/api/` for it): production
branch `chore/phase-1-cleanup`, no build command, output directory `frontend`, and the same two
variables.

**That's the demo online.** Share the `workers.dev` (or `pages.dev`) link.

## Optional extras (add any time on Render → Environment; it redeploys by itself)

| Add | What it gives you | Where to get it |
|---|---|---|
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | "I'm human" check on sign-up and guest accounts (recommended once you share the link widely) | Cloudflare → Turnstile → Add widget, hostname `<name>.pages.dev` |
| `SENTRY_DSN`, `SENTRY_FRONTEND_DSN` | Error alerts | https://sentry.io, one Python and one Browser project |
| `DEMO_VIDEO_URL` | A "watch the real thing" link next to the dry run | Your video link |

**Retention.** Old data (guests after 24 hours, CVs and idle chats after 30 days) is deleted every
time the API starts, and the free instance restarts after each idle period. For a fixed daily run
as well, add GitHub repository secret `CLEANUP_SECRET` (from Render) and variable `API_URL` (your
Render URL). GitHub only runs that schedule from the repository's default branch.

**Storing CVs in a bucket instead.** On Render set `STORAGE_BACKEND=s3`, plus `S3_BUCKET`,
`S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` from Cloudflare R2 or Supabase
Storage.

## Checklist

- [ ] `https://<name>.pages.dev` loads, and "Try it as a guest" works
- [ ] A search returns LinkedIn jobs
- [ ] `<Render URL>/api/config` says *Not found* (direct calls are blocked)
- [ ] Add the Pages link at the top of `README.md`
