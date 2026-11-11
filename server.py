import json
import os
import re
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
from auth import create_access_token, get_current_user, get_password_hash, verify_password
from database import engine, get_db

load_dotenv()
models.Base.metadata.create_all(bind=engine)

from agent.graph import app as langgraph_app  # noqa: E402  (needs .env loaded first)
from agent.parser import parse_and_store_document  # noqa: E402  (needs .env loaded first)
from agent.vector_store import vector_store  # noqa: E402  (needs .env loaded first)
from tools import job_boards  # noqa: E402  (needs .env loaded first)

app = FastAPI(title="Agentic AI Job Finder API", version="1.0")

# Uploaded CVs live here. They are NOT served as static files: a user gets their own CV only
# through the signed-in /api/user/cv endpoint.
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_CV_BYTES = 10 * 1024 * 1024  # 10 MB


def _server_error(context: str) -> HTTPException:
    """Logs the full traceback on the server and returns a generic 500, so internals never reach the browser."""
    print(f"{context} ERROR:\n" + traceback.format_exc().encode("ascii", "ignore").decode("ascii"))
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


class _RateLimiter:
    """In-memory sliding-window limiter (per process). Behind a reverse proxy, configure uvicorn's
    --proxy-headers / --forwarded-allow-ips so request.client.host is the real client address."""

    def __init__(self, max_hits: int, window_seconds: int):
        self.max_hits, self.window = max_hits, window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_hits:
                retry = int(self.window - (now - q[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many attempts. Try again in {retry} seconds.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)


login_ip_limiter = _RateLimiter(max_hits=20, window_seconds=300)       # 20 tries / 5 min per IP
login_user_limiter = _RateLimiter(max_hits=8, window_seconds=300)      # 8 tries / 5 min per username
signup_ip_limiter = _RateLimiter(max_hits=5, window_seconds=3600)      # 5 accounts / hour per IP


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

@app.get("/api/health")
async def health():
    """Simple health check endpoint. (The web frontend is served at "/".)"""
    return {"message": "🤖 Agentic AI Job Finder API is online and running!"}

@app.get("/api/config")
async def frontend_config():
    """Settings the web app needs. browser_viewer_url is set in Docker, where the visible browser
    windows (job-board sign-in, auto-apply) run on a virtual screen shown through noVNC."""
    return {"browser_viewer_url": os.getenv("BROWSER_VIEWER_URL") or None}

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
    session_id: str, 
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Keep only a safe base name: no folders, no "..", only ordinary characters.
    original = os.path.basename((file.filename or "").replace("\\", "/"))
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", os.path.splitext(original)[0]).strip(" .")[:100] or "cv"
    safe_name = f"{stem}.pdf"
    if not original.lower().endswith(".pdf") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Upload your CV as a PDF file.")

    # Read with a size cap, and check it really is a PDF (they start with "%PDF-").
    data = await file.read(MAX_CV_BYTES + 1)
    if len(data) > MAX_CV_BYTES:
        raise HTTPException(status_code=413, detail="That file is too large. The limit is 10 MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="That file isn't a valid PDF.")

    try:
        # Create a user-specific folder: uploads/user_1/
        user_folder = os.path.abspath(os.path.join(UPLOAD_DIR, f"user_{current_user.id}"))
        os.makedirs(user_folder, exist_ok=True)
        destination_path = os.path.join(user_folder, safe_name)
        if os.path.dirname(destination_path) != user_folder:  # belt and braces against path tricks
            raise HTTPException(status_code=400, detail="Invalid file name.")

        with open(destination_path, "wb") as buffer:
            buffer.write(data)

        # Update user record in database
        current_user.cv_filename = safe_name
        current_user.cv_filepath = destination_path
        db.commit()

        # Vectorize document into ChromaDB using saved path
        user_id_str = f"user_{current_user.id}"
        parse_and_store_document(destination_path, user_id=user_id_str, doc_type="resume")

        return {"status": "success", "message": f"Successfully stored {safe_name}"}

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
    try:
        # Auto-rename if this is a New Chat
        if session.title == "New Chat":
            from langchain_core.messages import HumanMessage

            from agent.nodes import llm
            try:
                # Ask LLM for a very short title
                prompt = f"Generate a short title (max 4 words) for this chat based on the user's first message. Message: '{req.query}'. Output ONLY the title, nothing else."
                title_res = llm.invoke([HumanMessage(content=prompt)])
                new_title = title_res.content.strip().replace('"', '')
                session.title = new_title
                db.commit()
            except Exception:
                # Fallback to truncating
                session.title = req.query[:30] + ("..." if len(req.query) > 30 else "")
                db.commit()

        config = {"configurable": {"thread_id": req.session_id}}
        
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

        initial_state = {
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
        for _event in langgraph_app.stream(initial_state, config, stream_mode="values"):
            pass

        # Check graph state
        state_snapshot = langgraph_app.get_state(config)
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
    _require_own_session(req.session_id, current_user, db)
    try:
        config = {"configurable": {"thread_id": req.session_id}}
        
        # Resume with the selected job indices
        for _event in langgraph_app.stream(
            Command(resume=req.selected_indices), config, stream_mode="values"
        ):
            pass

        # Check if we hit the next interrupt (human_approval_node)
        state_snapshot = langgraph_app.get_state(config)
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
    """Resumes the paused LangGraph agent and SAVES the result to SQLite."""
    _require_own_session(req.session_id, current_user, db)
    config = {"configurable": {"thread_id": req.session_id}}

    # 0. The agent never logs in: the user must have signed in to each job board themselves first.
    if req.feedback == "approve":
        pending_jobs = langgraph_app.get_state(config).values.get("scored_jobs", []) or req.jobs or []
        missing = job_boards.missing_sign_ins(f"user_{current_user.id}", [j.get("link", "") for j in pending_jobs])
        if missing:
            names = ", ".join(job_boards.BOARDS[b]["name"] for b in missing)
            raise HTTPException(status_code=409, detail={
                "message": f"Sign in to {names} first, so the agent can apply with your own account.",
                "boards": missing,
            })

    try:
        # 1. Resume the agent with the user's approval
        for _event in langgraph_app.stream(Command(resume=req.feedback), config, stream_mode="values"):
            pass
            
        # 2. Get the final assets the AI generated
        final_state = langgraph_app.get_state(config).values
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

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")
MIN_PASSWORD_LEN = 8

@app.post("/api/signup")
async def signup(user: UserAuthRequest, request: Request, db: Session = Depends(get_db)):
    """Registers a new user in the database with a hashed password."""
    signup_ip_limiter.check(_client_ip(request))

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


@app.post("/api/login")
async def login(user: UserAuthRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticates a user and returns a JWT access token."""
    login_ip_limiter.check(_client_ip(request))
    login_user_limiter.check(user.username.strip().lower())
    user.username = user.username.strip()

    # 1. Find the user in the database
    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    
    # 2. Verify the user exists and the password matches
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    # 3. Generate the JWT (embedding the user's database ID)
    access_token = create_access_token(data={"sub": str(db_user.id)})
    
    # Look at the bottom of your login function and update the return dictionary:
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "user_id": db_user.id,
        "username": db_user.username,
        "cv_filename": db_user.cv_filename,
        "full_name": db_user.full_name,
        "email": db_user.email,
        "phone": db_user.phone,
        "university": db_user.university,
        "degree": db_user.degree,
        "gpa": db_user.gpa,
        "graduation_year": db_user.graduation_year,
        "skills": db_user.skills,
        "work_experience": db_user.work_experience,
        "linkedin_url": db_user.linkedin_url,
        "github_url": db_user.github_url,
        "preferred_job_titles": db_user.preferred_job_titles,
        "preferred_locations": db_user.preferred_locations,
        "profile_completed": db_user.profile_completed
    }

@app.get("/api/user/cv")
async def get_user_cv(current_user: models.User = Depends(get_current_user)):
    """Returns the user's stored CV file directly."""
    if not current_user.cv_filepath or not os.path.exists(current_user.cv_filepath):
        raise HTTPException(status_code=404, detail="No CV found for this user.")
        
    return FileResponse(
        path=current_user.cv_filepath, 
        filename=current_user.cv_filename,
        media_type="application/pdf"
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
    if current_user.cv_filepath and os.path.exists(current_user.cv_filepath):
        try:
            os.remove(current_user.cv_filepath)
        except OSError:
            pass
    
    current_user.cv_filename = None
    current_user.cv_filepath = None
    db.commit()

    user_id_str = f"user_{current_user.id}"
    results = vector_store.collection.get(where={"user_id": user_id_str})
    if results and results.get('ids'):
        vector_store.collection.delete(ids=results['ids'])

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
        
    config = {"configurable": {"thread_id": session_id}}
    state_snapshot = langgraph_app.get_state(config)
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
    return job_boards.status(f"user_{current_user.id}")

@app.post("/api/job-boards/{board}/sign-in")
def sign_in_job_board(board: str, current_user: models.User = Depends(get_current_user)):
    """Opens a browser window on this computer at the board's sign-in page, in the user's own
    profile, and waits until the user closes it. Sync def: FastAPI runs it in a worker thread."""
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
