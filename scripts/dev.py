"""Runs the app on your machine without Docker: migrations, then the server on http://localhost:8000.

    python scripts/dev.py                 # demo mode (no browser automation), your .env settings
    python scripts/dev.py --automation    # real browser automation (needs requirements-automation.txt
                                          # and `playwright install chromium`)
    python scripts/dev.py --database-url sqlite:///./data/scratch.db --port 8001
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database-url", help="overrides DATABASE_URL")
    parser.add_argument("--upload-dir", help="overrides UPLOAD_DIR")
    parser.add_argument("--checkpoint-db", help="overrides CHECKPOINT_DB")
    parser.add_argument("--automation", action="store_true", help="enable real browser automation")
    args = parser.parse_args()

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    if args.upload_dir:
        os.environ["UPLOAD_DIR"] = args.upload_dir
    if args.checkpoint_db:
        os.environ["CHECKPOINT_DB"] = args.checkpoint_db
    if args.automation:
        os.environ["ENABLE_BROWSER_AUTOMATION"] = "true"
    # Plain http on localhost; Safari drops Secure cookies there.
    os.environ.setdefault("COOKIE_SECURE", "false")

    import db_migrate

    db_migrate.migrate()

    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
