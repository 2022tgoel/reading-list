from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'papers.db'}")

TWITTER_BEARER_TOKEN: str | None = os.getenv("TWITTER_BEARER_TOKEN")

TWITTER_API_KEY: str | None = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET: str | None = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN: str | None = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_TOKEN_SECRET: str | None = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
