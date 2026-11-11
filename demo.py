"""Guest accounts: a throwaway user with a filled-in profile, a sample CV and a few logged
applications, so a visitor sees the app working without typing anything.

The demo persona is fictional.
"""

from __future__ import annotations

import io
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from agent import vector_store
from agent.parser import CONTENT_TYPES, DOCX, chunk_text
from auth import get_password_hash
from database import utcnow
from models import Application, User
from storage import get_storage, user_prefix

DEMO_PROFILE = {
    "full_name": "Alex Demo",
    "email": "alex.demo@example.com",
    "phone": "+66 80 000 0000",
    "university": "Chulalongkorn University",
    "degree": "B.Eng. Computer Engineering",
    "gpa": 3.45,
    "graduation_year": 2026,
    "skills": "Python, SQL, pandas, scikit-learn, Power BI, Tableau, FastAPI, Git, English and Thai",
    "work_experience": (
        "Data Analyst Intern, Bangkok retail group (2025): built weekly sales dashboards in Power BI "
        "and automated reporting with Python, saving the team about 6 hours a week.\n"
        "Teaching Assistant, Intro to Programming (2024): ran lab sessions for 60 students."
    ),
    "linkedin_url": "https://www.linkedin.com/in/example",
    "github_url": "https://github.com/example",
    "preferred_job_titles": "Data Analyst, Junior Data Scientist",
    "preferred_locations": "Bangkok, Thailand",
}

DEMO_CV = """Alex Demo
Bangkok, Thailand · alex.demo@example.com · github.com/example

Summary
Computer engineering graduate who turns messy data into decisions. Comfortable with Python, SQL and BI tools, and with explaining results to non-technical teams in English and Thai.

Experience
Data Analyst Intern, Bangkok retail group (Jun 2025 to Dec 2025)
Built weekly sales and inventory dashboards in Power BI used by 12 store managers.
Automated the monthly reporting pipeline with Python and SQL, saving about 6 hours a week.
Found a pricing anomaly in one product category that recovered an estimated 2% margin.

Teaching Assistant, Intro to Programming, Chulalongkorn University (2024)
Ran weekly Python lab sessions for 60 students and wrote auto-graded exercises.

Projects
Customer churn model: scikit-learn gradient boosting on 50k telecom records, 0.86 ROC-AUC, served with FastAPI.
Bangkok air quality tracker: daily PM2.5 scraping, PostgreSQL storage and a Streamlit dashboard.

Education
B.Eng. Computer Engineering, Chulalongkorn University, 2026. GPA 3.45.

Skills
Python, pandas, NumPy, scikit-learn, SQL (PostgreSQL), Power BI, Tableau, FastAPI, Git, Docker basics.
Languages: Thai (native), English (IELTS 7.0).
"""

DEMO_APPLICATIONS = [
    ("Data Analyst", "Sample Retail Co.", "Interviewed", 3),
    ("Junior Data Scientist", "Example Fintech", "Applied", 1),
    ("BI Analyst", "Demo Logistics", "Pending", 0),
]


def _demo_cv_docx() -> bytes:
    import docx

    document = docx.Document()
    for block in DEMO_CV.strip().split("\n\n"):
        document.add_paragraph(block)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def create_guest(db: Session) -> User:
    now = utcnow()
    user = User(
        username=f"guest-{secrets.token_hex(4)}",
        # Guests never sign in with a password; this random one can't be guessed or used.
        hashed_password=get_password_hash(secrets.token_urlsafe(24)),
        is_guest=True,
        created_at=now,
        profile_completed=True,
        **DEMO_PROFILE,
    )
    db.add(user)
    db.flush()

    key = f"{user_prefix(user.id)}/cv.docx"
    get_storage().put(key, _demo_cv_docx(), CONTENT_TYPES[DOCX])
    user.cv_key = key
    user.cv_filename = "Alex Demo - sample CV.docx"
    user.cv_uploaded_at = now

    for title, company, status, days_ago in DEMO_APPLICATIONS:
        db.add(Application(
            user_id=user.id, job_title=title, company=company, status=status,
            assets_preview="Sample application created for the demo account.",
            timestamp=now - timedelta(days=days_ago),
        ))
    db.commit()

    # One embedding call; if it fails the chunks are stored without vectors and still work.
    vector_store.replace_user_documents(user.id, chunk_text(DEMO_CV))
    return user
