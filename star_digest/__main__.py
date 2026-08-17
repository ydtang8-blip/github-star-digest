from __future__ import annotations

import argparse
import json
import sys

from . import db
from .jobs import run_connect, run_daily_collect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub 高星日报")
    parser.add_argument(
        "command",
        choices=["collect", "status", "connect"],
        help="collect 采集今日简报；connect 把项目接到你的 GitHub",
    )
    parser.add_argument("--force", action="store_true", help="即使今日已有简报也重新采集")
    args = parser.parse_args(argv)
    db.init_db()
    if args.command == "status":
        settings = db.public_settings()
        print(
            json.dumps(
                {
                    "dates": db.list_dates(),
                    "today": db.today_iso(),
                    "github_connected": settings.get("github_connected"),
                    "github_login": settings.get("github_login"),
                    "hub_url": settings.get("hub_url"),
                    "app_url": settings.get("app_url"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "connect":
        result = run_connect()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") != "error" else 1
    result = run_daily_collect(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
