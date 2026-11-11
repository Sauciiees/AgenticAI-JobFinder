import hmac
import json
import os
import re
import traceback
import uuid
from typing import Any

import httpx
import sentry_sdk
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.orm import Session

import config  # noqa: I001  (loads .env before anything reads settings)
import demo
import limits
import models
import retention
from agent import vector_store
from agent.graph import app as langgraph_app
from agent.parser import CONTENT_TYPES, chunk_text, detect_kind, extract_text
from auth import clear_login_cookies, get_current_user, get_password_hash, set_login_cookies, verify_password_or_dummy
from database import get_db, utcnow
from storage import get_storage, user_prefix
from tools import job_boards

if config.SENTRY_DSN:
    # send_default_pii=False: no cookies, headers with credentials, or IP addresses go to Sentry.
    sentry_sdk.init(dsn=config.SENTRY_DSN, environment=config.APP_ENV, send_default_pii=False,
                    traces_sample_rate=0.0)

app = FastAPI(title="Agentic AI Job Finder API", version="1.0")

# Uploaded CVs live in storage (a local folder or a private bucket). They are never served as
# static files: a user gets their own CV only through the signed-in /api/user/cv endpoint.
MAX_CV_BYTES = config.MAX_CV_BYTES


def _server_error(context: str) -> HTTPException:
    """Logs the full traceback on the server and returns a generic 500, so internals never reach the browser."""
    print(f"{context} ERROR:\n" + traceback.format_exc().encode("ascii", "ignore").decode("ascii"))
    sentry_sdk.capture_exception()
    return HTTPException(status_code=500, detail="Something went wrong on the server. Please try again.")


def _require_own_session(session_id: str, user: models.User, db: Session) -> models.ChatSession:
    """The agent's state is keyed by session_id, so only the session's owner may start or resume it."""
    session = db.query(models.ChatSession).filter(
        models.ChatSession.session_id == session_id,
        models.ChatSession.user_id == user.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return session


def _from_proxy(request: Request) -> bool:
    """True when the request came through our Cloudflare Pages proxy (it sends the shared secret)."""
    sent = request.headers.get("X-Proxy-Secret", "")
    return bool(config.PROXY_SHARED_SECRET) and hmac.compare_digest(sent, config.PROXY_SHARED_SECRET)


def _client_ip(request: Request) -> str:
    """The visitor's IP. Behind the proxy it's the address Cloudflare saw, forwarded in X-Client-IP;
    the header is only trusted when the proxy's secret came with it."""
    if _from_proxy(request) and request.headers.get("X-Client-IP"):
        return request.headers["X-Client-IP"]
    return request.client.host if request.client else "unknown"


# Paths reachable without going through the proxy: Render's health check and the cleanup job.
_DIRECT_OK = ("/api/health", "/internal/")


@app.middleware("http")
async def require_proxy(request: Request, call_next):
    """When PROXY_SHARED_SECRET is set (production), the API only answers requests that came through
    the Cloudflare Pages proxy, so nobody can skip its checks by calling the Render URL directly."""
    if config.PROXY_SHARED_SECRET and not request.url.path.startswith(_DIRECT_OK) and not _from_proxy(request):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    return await call_next(request)


# Content Security Policy for the web app when this server serves it (local runs). Cloudflare Pages
# sends the same policy from frontend/_headers in production.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net https://challenges.cloudflare.com https://browser.sentry-cdn.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://*.ingest.sentry.io https://*.ingest.us.sentry.io https://*.ingest.de.sentry.io",
    "frame-src https://challenges.cloudflare.com",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Content-Security-Policy", CSP)
    if config.IS_PRODUCTION:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    return response


async def _verify_turnstile(token: str | None, ip: str) -> None:
    """Cloudflare Turnstile check for sign-up and guest accounts. Skipped when no secret is set (local)."""
    if not config.TURNSTILE_SECRET_KEY:
        return
    if not token:
        raise HTTPException(status_code=400, detail="Please complete the 'I'm human' check.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={"secret": config.TURNSTILE_SECRET_KEY, "response": token, "remoteip": ip},
            )
            ok = res.json().get("success") is True
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="The 'I'm human' check failed. Please try again.")


def _quotas_enforced() -> bool:
    # The owner's local build (with browser automation) isn't limited; the public demo is.
    return not config.ENABLE_BROWSER_AUTOMATION


def _runs_left(db: Session, user: models.User) -> int | None:
    if not _quotas_enforced():
        return None
    mine = limits.remaining(db, f"runs:user:{user.id}", config.USER_DAILY_AGENT_RUNS, limits.DAY)
    everyone = limits.remaining(db, "runs:global", config.GLOBAL_DAILY_AGENT_RUNS, limits.DAY)
    return min(mine, everyone)


def _charge_agent_run(db: Session, user: models.User) -> None:
    """Counts one agent run against the user's and the demo's daily quota, or refuses with 429."""
    if not _quotas_enforced():
        return
    if limits.remaining(db, "runs:global", config.GLOBAL_DAILY_AGENT_RUNS, limits.DAY) <= 0:
        raise limits.too_many("The demo has reached today's limit for everyone. Please try again tomorrow.", limits.DAY)
    if limits.remaining(db, f"runs:user:{user.id}", config.USER_DAILY_AGENT_RUNS, limits.DAY) <= 0:
        raise limits.too_many(
            f"You've used today's {config.USER_DAILY_AGENT_RUNS} agent runs. They reset at midnight UTC.", limits.DAY)
    limits.consume(db, f"runs:user:{user.id}", limits.DAY)
    limits.consume(db, "runs:global", limits.DAY)


def _require_automation() -> None:
    """Job-board sign-in and real auto-apply exist only in the local build with a browser."""
    if not config.ENABLE_BROWSER_AUTOMATION:
        raise HTTPException(status_code=404, detail="Browser automation is not available in this demo.")

@app.get("/api/health")
async def health():
    """Simple health check endpoint. (The web frontend is served at "/".)"""
    return {"message": "🤖 Agentic AI Job Finder API is online and running!"}

@app.get("/api/config")
async def frontend_config():
    """Settings the web app needs. browser_viewer_url is set in Docker, where the visible browser
    windows (job-board sign-in, auto-apply) run on a virtual screen shown through noVNC."""
    return {
        "browser_viewer_url": config.BROWSER_VIEWER_URL if config.ENABLE_BROWSER_AUTOMATION else None,
        "automation": config.ENABLE_BROWSER_AUTOMATION,
        "turnstile_site_key": config.TURNSTILE_SITE_KEY or None,
        "sentry_dsn": config.SENTRY_FRONTEND_DSN or None,
        "environment": config.APP_ENV,
        "demo_video_url": config.DEMO_VIDEO_URL or None,
        "max_cv_mb": config.MAX_CV_BYTES // (1024 * 1024),
    }

# Updated Pydantic Models to require user_id
class SearchRequest(BaseModel):
    session_id: str
    query: str = "ช่วยหาตำแหน่ง Data Scientist หรือ AI Engineer ในไทยจาก LinkedIn ให้หน่อยครับ"
    time_filter: str = "qdr:w"  # Default: past week. Options: qdr:d (24h), qdr:w (week), qdr:m (month)

class JobSelectionRequest(BaseModel):
    session_id: str
    selected_indices: list  # List of integer indices the user selected

class ResumeSessionRequest(BaseModel):
    session_id: str
    feedback: str = "approve"
    job_title: str | None = None
    company: str | None = None
    jobs: list | None = None

class StatusUpdateRequest(BaseModel):
    status: str

class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    university: str | None = None
    degree: str | None = None
    gpa: float | None = None
    graduation_year: int | None = None
    skills: str | None = None
    work_experience: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    preferred_job_titles: str | None = None
    preferred_locations: str | None = None

class ChatSessionResponse(BaseModel):
    session_id: str
    title: str
    created_at: str

class NewSessionRequest(BaseModel):
    title: str = "New Chat"


# 1. Update upload route to accept user_id and pass it to the parser
# Add db: Session = Depends(get_db) to the parameters
@app.post("/api/upload-resume")
async def upload_resume(
    session_id: str | None = None,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Stores the user's CV (PDF or DOCX, checked by content) and indexes its text for the agents."""
    # Keep only a safe base name for display: no folders, no "..", only ordinary characters.
    original = os.path.basename((file.filename or "").replace("\\", "/"))
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.splitext(original)[0]).strip(" .")[:100] or "cv"

    data = await file.read(MAX_CV_BYTES + 1)
    if len(data) > MAX_CV_BYTES:
        limit_mb = MAX_CV_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"That file is too large. The limit is {limit_mb} MB.")
    kind = detect_kind(data)
    if kind is None:
        raise HTTPException(status_code=400, detail="Upload your CV as a PDF or Word (.docx) file.")

    # Read the text first: a file that can't be parsed is the user's problem (400), not a server error.
    try:
        chunks = chunk_text(extract_text(data, kind))
    except Exception:
        raise HTTPException(status_code=400, detail="We couldn't read that file. Try exporting your CV again as a PDF.") from None
    if not chunks:
        raise HTTPException(status_code=400, detail="That file has no readable text (is it a scanned image?).")

    storage = get_storage()
    key = f"{user_prefix(current_user.id)}/cv.{kind}"
    try:
        storage.put(key, data, CONTENT_TYPES[kind])
        if current_user.cv_key and current_user.cv_key != key:
            storage.delete(current_user.cv_key)

        current_user.cv_filename = f"{stem}.{kind}"
        current_user.cv_key = key
        current_user.cv_uploaded_at = utcnow()
        db.commit()

        vector_store.replace_user_documents(current_user.id, chunks, doc_type="resume")
        return {"status": "success", "message": f"Successfully stored {current_user.cv_filename}"}
    except HTTPException:
        raise
    except Exception:
        raise _server_error("UPLOAD") from None


# 2. Update start route to inject user_id into the LangGraph state
@app.post("/api/job-finder/start")
async def start_job_search(
    req: SearchRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Protected: Only logged-in users can trigger the AI agents, and only in their own chat sessions."""
    session = _require_own_session(req.session_id, current_user, db)
    _charge_agent_run(db, current_user)
    session.updated_at = utcnow()
    db.commit()
    try:
        # Auto-rename if this is a New Chat
        if session.title == "New Chat":
            from langchain_core.messages import HumanMessage

            from agent.nodes import extract_text_from_response, llm
            try:
                # Ask LLM for a very short title
                prompt = f"Generate a short title (max 4 words) for this chat based on the user's first message. Message: '{req.query}'. Output ONLY the title, nothing else."
                title_res = llm.invoke([HumanMessage(content=prompt)])
                new_title = extract_text_from_response(title_res.content).strip().replace('"', '')
                session.title = new_title[:80] or "New Chat"
                db.commit()
            except Exception:
                # Fallback to truncating
                session.title = req.query[:30] + ("..." if len(req.query) > 30 else "")
                db.commit()

        run_config: RunnableConfig = {"configurable": {"thread_id": req.session_id}}
        
        # Securely inject the verified user ID into the LangGraph state
        user_id_str = f"user_{current_user.id}"
        
        structured_profile = {
            "full_name": current_user.full_name,
            "email": current_user.email,
            "university": current_user.university,
            "degree": current_user.degree,
            "gpa": current_user.gpa,
            "graduation_year": current_user.graduation_year,
            "skills": current_user.skills,
            "work_experience": current_user.work_experience,
            "preferred_job_titles": current_user.preferred_job_titles,
            "preferred_locations": current_user.preferred_locations
        }

        initial_state: Any = {
            "user_id": user_id_str,  
            "messages": [("user", req.query)],
            "user_profile": {},
            "structured_profile": structured_profile,
            "raw_jobs": [],
            "scored_jobs": [],
            "tailored_assets": {},
            "route_intent": "",
            "chat_response": "",
            "time_filter": req.time_filter,
            "selected_jobs": [],
            "application_results": [],
        }

        # Run until the first interrupt
        for _event in langgraph_app.stream(initial_state, run_config, stream_mode="values"):
            pass

        # Check graph state
        state_snapshot = langgraph_app.get_state(run_config)
        values = state_snapshot.values
        
        # If the router decided this was a chat, return the chat response
        if values.get("route_intent") == "chat":
            return {
                "status": "chat_response",
                "chat_response": values.get("chat_response", ""),
            }

        # If paused at job_selection_node — return all jobs for the card UI
        if state_snapshot.next and "job_selection_node" in state_snapshot.next:
            return {
                "status": "paused_for_selection",
                "raw_jobs": values.get("raw_jobs", []),
            }
        
        # If paused at human_approval_node — return assets for review
        if state_snapshot.next and "human_approval_node" in state_snapshot.next:
            raw_assets = values.get("tailored_assets", {}).get("top_job_assets", "No assets generated.")
            
            if isinstance(raw_assets, list):
                if len(raw_assets) > 0 and isinstance(raw_assets[0], dict) and "text" in raw_assets[0]:
                    assets = raw_assets[0]["text"]
                else:
                    assets = json.dumps(raw_assets)
            elif isinstance(raw_assets, dict):
                assets = raw_assets.get("text", json.dumps(raw_assets))
            else:
                assets = str(raw_assets)
            
            scored_jobs = values.get("scored_jobs", [])
            
            return {
                "status": "paused_for_review",
                "next_node": state_snapshot.next,
                "tailored_assets": assets,
                "scored_jobs": scored_jobs
            }
        
        return {"status": "completed", "values": state_snapshot.values}

    except Exception:
        raise _server_error("START") from None

@app.post("/api/job-finder/select-jobs")
async def select_jobs(
    req: JobSelectionRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Resumes the pipeline after user selects jobs from the card UI."""
    _require_own_session(req.session_id, current_user, db).updated_at = utcnow()
    db.commit()
    try:
        run_config: RunnableConfig = {"configurable": {"thread_id": req.session_id}}
        
        # Resume with the selected job indices
        for _event in langgraph_app.stream(
            Command(resume=req.selected_indices), run_config, stream_mode="values"
        ):
            pass

        # Check if we hit the next interrupt (human_approval_node)
        state_snapshot = langgraph_app.get_state(run_config)
        values = state_snapshot.values
        
        if state_snapshot.next and "human_approval_node" in state_snapshot.next:
            raw_assets = values.get("tailored_assets", {}).get("top_job_assets", "No assets generated.")
            
            if isinstance(raw_assets, list):
                if len(raw_assets) > 0 and isinstance(raw_assets[0], dict) and "text" in raw_assets[0]:
                    assets = raw_assets[0]["text"]
                else:
                    assets = json.dumps(raw_assets)
            elif isinstance(raw_assets, dict):
                assets = raw_assets.get("text", json.dumps(raw_assets))
            else:
                assets = str(raw_assets)
            
            scored_jobs = values.get("scored_jobs", [])
            
            return {
                "status": "paused_for_review",
                "next_node": state_snapshot.next,
                "tailored_assets": assets,
                "scored_jobs": scored_jobs
            }
        
        return {"status": "completed", "values": state_snapshot.values}

    except Exception:
        raise _server_error("SELECT-JOBS") from None

@app.post("/api/job-finder/resume")
async def resume_job_search(
    req: ResumeSessionRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)  # <-- INJECT DATABASE SESSION
):
    """Resumes the paused agent (real auto-apply locally, the dry run in the demo) and logs the applications."""
    _require_own_session(req.session_id, current_user, db).updated_at = utcnow()
    db.commit()
    if req.feedback == "approve":
        _charge_agent_run(db, current_user)
    run_config: RunnableConfig = {"configurable": {"thread_id": req.session_id}}

    # 0. The agent never logs in: the user must have signed in to each job board themselves first.
    if req.feedback == "approve" and config.ENABLE_BROWSER_AUTOMATION:
        pending_jobs = langgraph_app.get_state(run_config).values.get("scored_jobs", []) or req.jobs or []
        missing = job_boards.missing_sign_ins(f"user_{current_user.id}", [j.get("link", "") for j in pending_jobs])
        if missing:
            names = ", ".join(str(job_boards.BOARDS[b]["name"]) for b in missing)  # type: ignore[index]
            raise HTTPException(status_code=409, detail={
                "message": f"Sign in to {names} first, so the agent can apply with your own account.",
                "boards": missing,
            })

    try:
        # 1. Resume the agent with the user's approval
        for _event in langgraph_app.stream(Command(resume=req.feedback), run_config, stream_mode="values"):
            pass
            
        # 2. Get the final assets the AI generated
        final_state = langgraph_app.get_state(run_config).values
        raw_assets = final_state.get("tailored_assets", {}).get("top_job_assets", "Assets generated but empty.")
        
        # Safely convert to a string if the LLM returned a list or dictionary
        if isinstance(raw_assets, list):
            # Try to extract the text block if it's Anthropic format, otherwise dump to JSON string
            if len(raw_assets) > 0 and isinstance(raw_assets[0], dict) and "text" in raw_assets[0]:
                assets_str = raw_assets[0]["text"]
            else:
                assets_str = json.dumps(raw_assets)
        elif isinstance(raw_assets, dict):
            assets_str = json.dumps(raw_assets)
        else:
            assets_str = str(raw_assets)
            
        assets_preview = assets_str[:500] + "..." if len(assets_str) > 500 else assets_str
        
        application_results = final_state.get("application_results", [])
        result_map = {f"{r.get('title')}_{r.get('company')}": r for r in application_results}
        
        if req.jobs:
            for j in req.jobs:
                key = f"{j.get('title')}_{j.get('company')}"
                r = result_map.get(key, {})
                
                final_status = r.get("final_status", "Applied")
                if final_status == "submitted":
                    status = "Applied"
                elif final_status == "dry_run":
                    status = "Dry run (not submitted)"
                elif final_status == "captcha_timeout":
                    status = "Requires Manual Intervention"
                elif final_status == "login_required":
                    status = "Sign-in needed"
                elif final_status in ["stuck", "error", "max_steps_reached", "no_url"]:
                    status = f"Failed ({final_status})"
                else:
                    status = "Applied"
                    
                new_app = models.Application(
                    user_id=current_user.id,
                    job_title=j.get("title", "Unknown"),
                    company=j.get("company", "Unknown"),
                    logo_url=j.get("logo_url", ""),
                    status=status,
                    assets_preview=assets_preview
                )
                db.add(new_app)
        else:
            new_app = models.Application(
                user_id=current_user.id,
                job_title=req.job_title,
                company=req.company,
                logo_url="",
                status="Applied",
                assets_preview=assets_preview
            )
            db.add(new_app)
            
        db.commit()
        
        return {"status": "completed", "message": "Applications logged successfully!"}

    except Exception:
        db.rollback()
        raise _server_error("RESUME") from None

# (The old unauthenticated GET /api/applications/{user_id} was removed: it let anyone read any
#  user's applications. Use the signed-in GET /api/applications below.)

# Pydantic model for incoming auth requests
class UserAuthRequest(BaseModel):
    username: str
    password: str
    turnstile_token: str | None = None


class GuestRequest(BaseModel):
    turnstile_token: str | None = None


class DeleteAccountRequest(BaseModel):
    password: str | None = None

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
MIN_PASSWORD_LEN = 8

@app.post("/api/signup")
async def signup(user: UserAuthRequest, request: Request, db: Session = Depends(get_db)):
    """Registers a new user in the database with a hashed password."""
    limits.rate_limit(db, f"signup:ip:{_client_ip(request)}", config.SIGNUP_PER_IP_PER_HOUR, limits.HOUR)
    await _verify_turnstile(user.turnstile_token, _client_ip(request))

    username = user.username.strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Usernames are 3-64 characters: letters, numbers and . _ @ -")
    if len(user.password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Use at least {MIN_PASSWORD_LEN} characters for the password.")
    if len(user.password.encode("utf-8")) > 72:  # bcrypt only uses the first 72 bytes
        raise HTTPException(status_code=400, detail="That password is too long (72 bytes at most).")
    if user.password.lower() == username.lower():
        raise HTTPException(status_code=400, detail="The password can't be the same as the username.")
    user.username = username

    # 1. Check if the username already exists
    existing_user = db.query(models.User).filter(models.User.username == user.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # 2. Hash the password and save the user
    hashed_password = get_password_hash(user.password)
    db_user = models.User(username=user.username, hashed_password=hashed_password)
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return {"message": "User created successfully. You can now log in."}


def _user_payload(u: models.User) -> dict:
    """What the browser may know about the signed-in user (never the password hash or the token)."""
    return {
        "user_id": u.id,
        "username": u.username,
        "is_guest": bool(u.is_guest),
        "cv_filename": u.cv_filename,
        "full_name": u.full_name,
        "email": u.email,
        "phone": u.phone,
        "university": u.university,
        "degree": u.degree,
        "gpa": u.gpa,
        "graduation_year": u.graduation_year,
        "skills": u.skills,
        "work_experience": u.work_experience,
        "linkedin_url": u.linkedin_url,
        "github_url": u.github_url,
        "preferred_job_titles": u.preferred_job_titles,
        "preferred_locations": u.preferred_locations,
        "profile_completed": bool(u.profile_completed),
    }


@app.post("/api/login")
async def login(user: UserAuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Checks the password and starts a session: an httpOnly cookie plus a CSRF cookie."""
    limits.rate_limit(db, f"login:ip:{_client_ip(request)}", config.LOGIN_PER_IP_PER_MINUTE, limits.MINUTE)
    limits.rate_limit(db, f"login:user:{user.username.strip().lower()}", config.LOGIN_PER_USER_PER_5_MIN, 5 * limits.MINUTE)
    username = user.username.strip()

    db_user = db.query(models.User).filter(models.User.username == username).first()
    if not verify_password_or_dummy(user.password, db_user.hashed_password if db_user else None) or not db_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if db_user.is_guest:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")

    set_login_cookies(response, db_user.id)
    return _user_payload(db_user)


@app.post("/api/logout")
async def logout(response: Response):
    clear_login_cookies(response)
    return {"status": "signed_out"}


@app.get("/api/me")
async def me(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Who is signed in (the page calls this on load to restore the session from the cookie)."""
    return {**_user_payload(current_user), "runs_left": _runs_left(db, current_user),
            "runs_per_day": config.USER_DAILY_AGENT_RUNS if _quotas_enforced() else None}


@app.post("/api/guest")
async def start_guest(req: GuestRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """One click into a ready-made demo account (deleted after GUEST_TTL_HOURS)."""
    ip = _client_ip(request)
    limits.rate_limit(db, f"guest:ip:{ip}", config.GUEST_PER_IP_PER_HOUR, limits.HOUR)
    await _verify_turnstile(req.turnstile_token, ip)
    try:
        guest = demo.create_guest(db)
    except Exception:
        raise _server_error("GUEST") from None
    set_login_cookies(response, guest.id)
    return {**_user_payload(guest), "runs_left": _runs_left(db, guest),
            "runs_per_day": config.USER_DAILY_AGENT_RUNS if _quotas_enforced() else None}


@app.delete("/api/account")
async def delete_account(
    req: DeleteAccountRequest,
    response: Response,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deletes the account and everything stored for it: CV, chats, agent state, applications."""
    if not current_user.is_guest and not verify_password_or_dummy(req.password or "", current_user.hashed_password):
        raise HTTPException(status_code=403, detail="That password isn't right.")
    try:
        retention.delete_user_everything(db, current_user)
    except Exception:
        raise _server_error("DELETE-ACCOUNT") from None
    clear_login_cookies(response)
    return {"status": "deleted"}


@app.post("/internal/cleanup")
async def internal_cleanup(request: Request, db: Session = Depends(get_db)):
    """Daily retention job, called by a scheduled GitHub Action with the shared secret."""
    sent = request.headers.get("X-Cleanup-Secret", "")
    if not config.CLEANUP_SECRET or not hmac.compare_digest(sent, config.CLEANUP_SECRET):
        raise HTTPException(status_code=404, detail="Not Found")
    return {"status": "ok", "deleted": retention.run_cleanup(db)}


@app.get("/api/user/cv")
async def get_user_cv(current_user: models.User = Depends(get_current_user)):
    """The signed-in user's own CV: a short-lived signed link for bucket storage, else the file itself."""
    if not current_user.cv_key:
        raise HTTPException(status_code=404, detail="No CV found for this user.")
    storage = get_storage()
    filename = current_user.cv_filename or os.path.basename(current_user.cv_key)
    url = storage.signed_url(current_user.cv_key, filename)
    if url:
        return RedirectResponse(url, status_code=307)
    data = storage.get(current_user.cv_key)
    if data is None:
        raise HTTPException(status_code=404, detail="No CV found for this user.")
    kind = current_user.cv_key.rsplit(".", 1)[-1]
    return Response(
        content=data,
        media_type=CONTENT_TYPES.get(kind, "application/octet-stream"),
        headers={"Content-Disposition": f'inline; filename="{filename}"', "Cache-Control": "no-store"},
    )

@app.delete("/api/applications/{app_id}")
async def delete_application(
    app_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a logged job application."""
    app = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == current_user.id
    ).first()
    
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
        
    db.delete(app)
    db.commit()
    return {"message": "Application deleted successfully"}

@app.get("/api/applications")
async def get_user_applications(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch all logged job applications for the authenticated user."""
    try:
        apps = db.query(models.Application).filter(
            models.Application.user_id == current_user.id
        ).order_by(models.Application.timestamp.desc()).all()
        
        results = []
        for app in apps:
            results.append({
                "id": app.id,
                "job_title": app.job_title or "Untitled Position",
                "company": app.company or "Unknown Company",
                "logo_url": getattr(app, "logo_url", ""),
                "timestamp": app.timestamp.strftime("%Y-%m-%d %H:%M:%S") if app.timestamp else "",
                "status": app.status,
                "assets_preview": app.assets_preview
            })
        return results
    except Exception:
        raise _server_error("APPLICATIONS") from None

@app.put("/api/applications/{app_id}/status")
async def update_application_status(
    app_id: int,
    req: StatusUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the status of an application."""
    app = db.query(models.Application).filter(
        models.Application.id == app_id,
        models.Application.user_id == current_user.id
    ).first()
    
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    
    app.status = req.status
    db.commit()
    return {"status": "success", "message": f"Status updated to '{req.status}'"}

@app.get("/api/profile")
async def get_profile(current_user: models.User = Depends(get_current_user)):
    return {
        "full_name": current_user.full_name,
        "email": current_user.email,
        "phone": current_user.phone,
        "university": current_user.university,
        "degree": current_user.degree,
        "gpa": current_user.gpa,
        "graduation_year": current_user.graduation_year,
        "skills": current_user.skills,
        "work_experience": current_user.work_experience,
        "linkedin_url": current_user.linkedin_url,
        "github_url": current_user.github_url,
        "preferred_job_titles": current_user.preferred_job_titles,
        "preferred_locations": current_user.preferred_locations,
        "profile_completed": current_user.profile_completed,
        "cv_filename": current_user.cv_filename,
    }

@app.put("/api/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.full_name = req.full_name
    current_user.email = req.email
    current_user.phone = req.phone
    current_user.university = req.university
    current_user.degree = req.degree
    current_user.gpa = req.gpa
    current_user.graduation_year = req.graduation_year
    current_user.skills = req.skills
    current_user.work_experience = req.work_experience
    current_user.linkedin_url = req.linkedin_url
    current_user.github_url = req.github_url
    current_user.preferred_job_titles = req.preferred_job_titles
    current_user.preferred_locations = req.preferred_locations
    
    has_full_name = bool(current_user.full_name)
    has_other = bool(current_user.university or current_user.skills or current_user.cv_filename)
    current_user.profile_completed = has_full_name and has_other
    
    db.commit()
    return {"status": "success", "message": "Profile updated"}

@app.delete("/api/profile/cv")
async def delete_cv(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    retention.delete_cv(db, current_user)
    return {"status": "success", "message": "CV deleted"}


@app.get("/api/chat-sessions", response_model=list[ChatSessionResponse])
def get_sessions(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = db.query(models.ChatSession).filter(models.ChatSession.user_id == current_user.id).order_by(models.ChatSession.created_at.desc()).all()
    return [{"session_id": s.session_id, "title": s.title, "created_at": str(s.created_at)} for s in sessions]

@app.post("/api/chat-sessions")
def create_session(req: NewSessionRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    session_id = str(uuid.uuid4())
    new_session = models.ChatSession(user_id=current_user.id, session_id=session_id, title=req.title)
    db.add(new_session)
    db.commit()
    return {"session_id": session_id, "title": req.title}

@app.put("/api/chat-sessions/{session_id}")
def rename_session(session_id: str, req: NewSessionRequest, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id, models.ChatSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = req.title
    db.commit()
    return {"status": "success"}

@app.delete("/api/chat-sessions/{session_id}")
def delete_session(session_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id, models.ChatSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return {"status": "success"}

@app.get("/api/chat-sessions/{session_id}/history")
def get_session_history(session_id: str, current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    # verify ownership
    session = db.query(models.ChatSession).filter(models.ChatSession.session_id == session_id, models.ChatSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    run_config: RunnableConfig = {"configurable": {"thread_id": session_id}}
    state_snapshot = langgraph_app.get_state(run_config)
    values = state_snapshot.values if state_snapshot else {}
    messages = values.get("messages", [])
    
    # Format messages for frontend
    history = []
    for msg in messages:
        # Check type
        msg_type = getattr(msg, "type", "")
        if msg_type in ["human", "ai"]:
            # If there are tool calls in AIMessage, skip returning tool_calls in content? 
            # We just return the text content.
            if msg.content:
                text_content = ""
                if isinstance(msg.content, list):
                    for item in msg.content:
                        if isinstance(item, dict) and "text" in item:
                            text_content += item["text"] + "\n"
                        elif isinstance(item, str):
                            text_content += item + "\n"
                else:
                    text_content = str(msg.content)
                if text_content.strip():
                    history.append({"role": "user" if msg_type == "human" else "assistant", "content": text_content.strip()})
        elif isinstance(msg, tuple):
             if len(msg) >= 2 and msg[0] in ["human", "ai", "user", "assistant"]:
                 # Map user->human, assistant->ai if needed
                 role = "user" if msg[0] in ["human", "user"] else "assistant"
                 if msg[1]:
                     history.append({"role": role, "content": str(msg[1])})
             
    # Return current pipeline status if paused
    status = "idle"
    if state_snapshot and state_snapshot.next:
        if "job_selection_node" in state_snapshot.next:
            status = "paused_for_selection"
        elif "human_approval_node" in state_snapshot.next:
            status = "paused_for_review"
            
    # Format raw assets
    raw_assets = values.get("tailored_assets", {}).get("top_job_assets", "No assets generated.")
    if isinstance(raw_assets, list):
        if len(raw_assets) > 0 and isinstance(raw_assets[0], dict) and "text" in raw_assets[0]:
            assets = raw_assets[0]["text"]
        else:
            assets = json.dumps(raw_assets)
    elif isinstance(raw_assets, dict):
        assets = raw_assets.get("text", json.dumps(raw_assets))
    else:
        assets = str(raw_assets)
            
    return {
        "history": history,
        "status": status,
        "raw_jobs": values.get("raw_jobs", []),
        "scored_jobs": values.get("scored_jobs", []),
        "tailored_assets": assets
    }

class BoardConfirmRequest(BaseModel):
    signed_in: bool

def _board_or_404(board: str) -> str:
    if board not in job_boards.BOARDS:
        raise HTTPException(status_code=404, detail="Unknown job board")
    return board

@app.get("/api/job-boards")
def get_job_boards(current_user: models.User = Depends(get_current_user)):
    """Which job boards this user has signed in to for auto-apply."""
    _require_automation()
    return job_boards.status(f"user_{current_user.id}")

@app.post("/api/job-boards/{board}/sign-in")
def sign_in_job_board(board: str, current_user: models.User = Depends(get_current_user)):
    """Opens a browser window on this computer at the board's sign-in page, in the user's own
    profile, and waits until the user closes it. Sync def: FastAPI runs it in a worker thread."""
    _require_automation()
    _board_or_404(board)
    try:
        result = job_boards.open_sign_in_window(f"user_{current_user.id}", board)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception:
        err = _server_error("JOB-BOARD SIGN-IN")
        err.detail = "Couldn't open the browser window. Please try again."
        raise err from None
    return result

@app.post("/api/job-boards/{board}/confirm")
def confirm_job_board(board: str, req: BoardConfirmRequest, current_user: models.User = Depends(get_current_user)):
    """For boards whose sign-in can't be checked automatically, the user says whether it worked."""
    _require_automation()
    _board_or_404(board)
    return job_boards.set_signed_in(f"user_{current_user.id}", board, req.signed_in, verified=False)


# 3. Serve the web frontend (frontend/) at "/". Mounted last so every /api route above takes priority.
class RevalidatedStaticFiles(StaticFiles):
    """Static files that browsers re-check on every load (a cheap 304 when unchanged), so UI updates show up at once."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
app.mount("/", RevalidatedStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
