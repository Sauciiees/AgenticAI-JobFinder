# Deploying the public demo (free)

About 45 minutes the first time. You create five free accounts; nothing needs a credit card except
where noted. Keep a text file open to collect the values marked **(save)**.

Order matters: database and bucket first, then the API on Render, then the web app on Cloudflare
Pages (it needs the Render URL), then the scheduled cleanup on GitHub.

## 0. Keys you already have

- **Gemini**: https://aistudio.google.com/apikey → `GOOGLE_API_KEY` **(save)**
- **Serper**: https://serper.dev → API key → `SERPER_API_KEY` **(save)**.
  The key currently in your `.env` is rejected by Serper (`403 Unauthorized`), so make a new one.

## 1. Database: Neon Postgres

1. Sign up at https://neon.tech and create a project (region: Singapore is closest to Thailand).
2. Dashboard → **Connection string**, with **Pooled connection** on. It looks like
   `postgresql://user:pass@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
   → `DATABASE_URL` **(save)**.
3. Nothing else: the app creates the tables and turns on pgvector the first time it starts.

## 2. CV storage: Cloudflare R2

R2's free tier is 10 GB, but enabling R2 asks for a payment method. If you'd rather not add one,
use Supabase Storage instead (below).

1. Cloudflare dashboard → **R2** → **Create bucket**, name `jobfinder-cvs`. Leave public access **off**.
2. R2 → **Manage API tokens** → **Create API token**, permission *Object Read & Write*, scoped to that bucket.
3. Save: `S3_BUCKET=jobfinder-cvs`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and
   `S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com` **(save)**.

*Supabase alternative:* create a project → Storage → new **private** bucket → Project Settings →
Storage → S3 connection → create access keys. Use the endpoint shown there, and set
`S3_REGION` to the region it shows.

## 3. "I'm human" check: Cloudflare Turnstile

Cloudflare dashboard → **Turnstile** → **Add widget**. Add the hostname `<your-project>.pages.dev`
(you choose the project name in step 5, so decide it now), mode *Managed*.
Save the **site key** → `TURNSTILE_SITE_KEY` and **secret key** → `TURNSTILE_SECRET_KEY` **(save)**.

## 4. API: Render

1. Push this repository to GitHub. Render deploys the branch named in `render.yaml`
   (`branch: chore/phase-1-cleanup`); change it there if you deploy from another branch.
2. https://render.com → **New** → **Blueprint** → pick the repository. Render reads `render.yaml`.
3. Fill in the values it asks for: `DATABASE_URL`, `GOOGLE_API_KEY`, `SERPER_API_KEY`, the four
   `S3_*` values, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`. Leave `SENTRY_*` and
   `DEMO_VIDEO_URL` empty for now if you don't have them.
4. After the first deploy: service → **Environment**, copy the generated `PROXY_SHARED_SECRET` and
   `CLEANUP_SECRET` **(save)**, and copy the service URL, e.g. `https://jobfinder-api.onrender.com` **(save)**.
5. Check it: open `<render-url>/api/health`. It should answer. Any other path answers 404,
   which is correct: the API only accepts requests sent through the Pages proxy.

The blueprint sets **Auto-Deploy: after CI checks pass**, so a commit with failing tests never ships.
If Render reports `autoDeployTrigger` as unknown, delete that line from `render.yaml` and pick
*Settings → Build & Deploy → Auto-Deploy → After CI Checks Pass* in the dashboard instead.

## 5. Web app: Cloudflare Pages

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** → connect the GitHub repository.
2. Build settings: **production branch** `chore/phase-1-cleanup`, framework *None*, build command *(empty)*,
   build output directory `frontend`.
   Root directory stays the repository root, so `functions/` (the API proxy) is picked up.
3. **Environment variables** (Production): `API_ORIGIN` = your Render URL (no trailing slash),
   `PROXY_SHARED_SECRET` = the value from Render.
4. Deploy, then open `https://<project>.pages.dev`. The first load may show "Waking up the server"
   for up to a minute.

## 6. Daily cleanup: GitHub Actions

Repository → **Settings** → **Secrets and variables** → **Actions**:
- **Secrets** → `CLEANUP_SECRET` = the value from Render
- **Variables** → `API_URL` = your Render URL

Then **Actions** → *Daily data cleanup* → **Run workflow** once to check it. It should end with
`{"status":"ok",...}`.

GitHub only runs *scheduled* workflows from the repository's **default branch**. If you deploy
from `chore/phase-1-cleanup` without merging, either make it the default branch (Settings → General → Default
branch), or skip the schedule: the API also runs the cleanup every time it starts, and the free
instance restarts after each idle sleep, so old data is still removed.

## 7. Optional: Sentry error tracking

https://sentry.io → create two projects: *Python (FastAPI)* and *Browser JavaScript*. Put their
DSNs in Render as `SENTRY_DSN` and `SENTRY_FRONTEND_DSN`. Render redeploys automatically.

## 8. Finish the README

Add your Pages URL and the auto-apply video link at the top of `README.md`, and set
`DEMO_VIDEO_URL` on Render so the dry run links to the video.

## Checklist after deploying

- [ ] `https://<project>.pages.dev` loads, "Try it as a guest" works
- [ ] A search returns jobs from LinkedIn **and** at least one Serper board (JobsDB, Indeed, JobBKK)
- [ ] Sign-up shows the Turnstile check
- [ ] `<render-url>/api/config` returns 404 (direct calls blocked)
- [ ] The cleanup workflow run succeeds
- [ ] The README badge shows CI passing
