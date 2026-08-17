from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "digest.db"
ENV_PATH = ROOT / ".env"
WEB_DIR = ROOT / "web"

DEFAULT_DOWNLOAD_DIR = Path.home() / "Documents" / "github-stars"

DEFAULTS: dict = {
    "host": "127.0.0.1",
    "port": 8787,
    "download_dir": str(DEFAULT_DOWNLOAD_DIR),
    "github_token": "",
    "github_login": "",
    "hub_repo": "github-star-picks",
    "app_repo": "github-star-digest",
    "xai_api_key": "",
    "xai_model": "grok-4.5",
    "spoken_codes": ["", "zh"],
    "prog_langs": [],
    "rising_days": 7,
    "rising_min_stars": 50,
    "max_repos_per_day": 60,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36 GitHubStarDigest/1.0"
    ),
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(load_settings()["download_dir"]).mkdir(parents=True, exist_ok=True)


def _load_env_file() -> dict[str, str]:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
    return {
        "github_token": os.environ.get("GITHUB_TOKEN", "").strip(),
        "xai_api_key": os.environ.get("XAI_API_KEY", "").strip(),
    }


def load_settings(db_overlay: dict | None = None) -> dict:
    settings = dict(DEFAULTS)
    env = _load_env_file()
    if env["github_token"]:
        settings["github_token"] = env["github_token"]
    if env["xai_api_key"]:
        settings["xai_api_key"] = env["xai_api_key"]
    if db_overlay:
        for key, value in db_overlay.items():
            if value is None or value == "":
                continue
            if key in {"spoken_codes", "prog_langs"} and isinstance(value, str):
                try:
                    settings[key] = json.loads(value)
                except json.JSONDecodeError:
                    settings[key] = [v.strip() for v in value.split(",") if v.strip()]
            elif key == "port":
                settings[key] = int(value)
            elif key in {"rising_days", "rising_min_stars", "max_repos_per_day"}:
                settings[key] = int(value)
            else:
                settings[key] = value
    return settings


def write_env(github_token: str, xai_api_key: str) -> None:
    lines = [
        f"GITHUB_TOKEN={github_token or ''}",
        f"XAI_API_KEY={xai_api_key or ''}",
        "",
    ]
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    if github_token:
        os.environ["GITHUB_TOKEN"] = github_token
    if xai_api_key:
        os.environ["XAI_API_KEY"] = xai_api_key
