"""Application configuration and environment loading for the raid bot."""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

if importlib.util.find_spec("dotenv") is not None:
    from dotenv import load_dotenv

    load_dotenv()
else:  # pragma: no cover - optional dependency fallback
    def load_dotenv() -> None:
        return None

DB_PATH = os.getenv("RAIDBOT_DB", "raids.db")
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    token_file = Path("token.txt")
    if token_file.exists():
        TOKEN = token_file.read_text(encoding="utf-8").strip()

TIME_FMT = "%H:%M %d.%m.%y"  # e.g. 22:00 30.09.25

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("raidbot")

__all__ = ["DB_PATH", "TOKEN", "TIME_FMT", "log"]
