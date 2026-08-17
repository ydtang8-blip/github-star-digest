from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .config import DATA_DIR
from .downloader import already_downloaded, download_repo, target_dir
from .github_link import (
    GitHubLinkError,
    ensure_fork,
    link_local_clone,
    sync_hub_catalog,
)

HANDOFF_PATH = DATA_DIR / "opencode_handoff.json"


def write_handoff(job: dict[str, Any], settings: dict[str, Any], requested_ids: list[int]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "kind": job.get("kind") or "",
        "status": job.get("status") or "",
        "message": job.get("message") or "",
        "download_dir": settings.get("download_dir") or "",
        "requested_ids": requested_ids or [],
        "downloads": job.get("downloads") or [],
    }
    HANDOFF_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_handoff() -> dict[str, Any] | None:
    if not HANDOFF_PATH.exists():
        return None
    try:
        return json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _handoff_entries() -> list[dict[str, Any]]:
    handoff = read_handoff() or {}
    return handoff.get("downloads") or []


def check_downloads(settings: dict[str, Any]) -> dict[str, Any]:
    handoff = read_handoff()
    root = settings.get("download_dir") or ""
    entries: list[dict[str, Any]] = []
    for row in _handoff_entries():
        full_name = row.get("full_name") or ""
        dest = target_dir(root, full_name)
        ok = already_downloaded(dest)
        entries.append(
            {
                "full_name": full_name,
                "status": row.get("status") or "",
                "message": row.get("message") or "",
                "local_ok": ok,
                "local_path": str(dest) if ok else "",
                "need_rescue": not ok,
            }
        )
    return {
        "has_handoff": bool(handoff),
        "written_at": (handoff or {}).get("written_at"),
        "job_status": (handoff or {}).get("status"),
        "message": (handoff or {}).get("message") or "",
        "entries": entries,
    }


def _full_name_of(entry: dict[str, Any]) -> str:
    return entry.get("full_name") or ""


def _want_rescue(entry: dict[str, Any]) -> bool:
    status = entry.get("status") or ""
    return status != "done"


def rescue_downloads(settings: dict[str, Any], full_names: list[str] | None = None) -> dict[str, Any]:
    root = settings.get("download_dir") or ""
    if full_names:
        targets = [t for t in full_names if t]
    else:
        targets = [
            fn
            for entry in _handoff_entries()
            if _want_rescue(entry) and (fn := _full_name_of(entry))
        ]
        if not targets:
            targets = [_full_name_of(e) for e in _handoff_entries()]
    targets = list(dict.fromkeys(targets))

    results: list[dict[str, Any]] = []
    for full_name in targets:
        dest = target_dir(root, full_name)
        repo = db.get_repo_by_full_name(full_name)
        was_present = already_downloaded(dest)
        try:
            if not was_present:
                download_repo(full_name, root, settings)
            if settings.get("github_token"):
                link = ensure_fork(full_name, settings)
                link_local_clone(full_name, link["fork_full_name"], settings)
                if repo:
                    db.set_repo_link(
                        repo["id"],
                        link["fork_full_name"],
                        link["fork_url"],
                        local_path=str(dest),
                        status="downloaded",
                    )
            results.append(
                {
                    "full_name": full_name,
                    "ok": True,
                    "already": was_present,
                    "path": str(dest),
                }
            )
        except Exception as exc:
            results.append({"full_name": full_name, "ok": False, "already": was_present, "error": str(exc)})

    hub: dict[str, Any] | None = None
    if settings.get("github_token"):
        try:
            hub = sync_hub_catalog(settings, db.list_catalog_repos())
        except GitHubLinkError as exc:
            hub = {"error": str(exc)}

    return {
        "download_dir": root,
        "results": results,
        "ok": all(r.get("ok") for r in results),
        "rescued": sum(1 for r in results if r.get("ok")),
        "hub": hub,
    }
