from __future__ import annotations

import traceback
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from . import db
from .collector import fetch_readme, fetch_rising, fetch_trending_pages, merge_candidates
from .config import ensure_dirs
from .downloader import download_repo
from .github_link import (
    GitHubLinkError,
    ensure_fork,
    fetch_identity,
    inspect_token,
    link_local_clone,
    publish_app_repo,
    sync_hub_catalog,
)
from .summarizer import summarize_items

_job_lock = Lock()
_job: dict[str, Any] = {
    "kind": "",
    "status": "idle",
    "stage": "",
    "progress": 0,
    "message": "",
    "error": "",
    "started_at": "",
    "finished_at": "",
    "downloads": [],
}


def job_status() -> dict[str, Any]:
    return dict(_job)


def mark_started(kind: str, message: str) -> bool:
    if _job.get("status") == "running":
        return False
    _set(
        kind=kind,
        status="running",
        stage="queued",
        progress=1,
        message=message,
        error="",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at="",
        downloads=[],
    )
    return True


def _set(**kwargs: Any) -> None:
    _job.update(kwargs)


def run_daily_collect(force: bool = False) -> dict[str, Any]:
    if not _job_lock.acquire(blocking=False):
        return job_status()
    try:
        ensure_dirs()
        db.init_db()
        settings = db.get_settings()
        today = db.today_iso()
        if db.digest_exists(today) and not force:
            _set(
                kind="collect",
                status="done",
                stage="skip",
                progress=100,
                message="今日简报已存在，打开即可挑选。",
                error="",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            return job_status()
        started = datetime.now(timezone.utc).isoformat()
        _set(
            kind="collect",
            status="running",
            stage="trending",
            progress=5,
            message="开始采集今日高星项目…",
            error="",
            started_at=started,
            finished_at="",
            downloads=[],
        )

        def progress(stage: str, pct: int, message: str) -> None:
            _set(stage=stage, progress=pct, message=message)

        trending = fetch_trending_pages(settings, progress)
        rising = fetch_rising(settings, progress)
        candidates = merge_candidates(
            trending, rising, limit=int(settings.get("max_repos_per_day") or 60)
        )
        _set(stage="readme", progress=45, message=f"已找到 {len(candidates)} 个项目，正在读 README…")

        with db.db() as conn:
            prepared = []
            for idx, repo in enumerate(candidates, start=1):
                _set(
                    stage="readme",
                    progress=45 + int(idx / max(len(candidates), 1) * 25),
                    message=f"读取 {repo['full_name']} 的 README",
                )
                excerpt = fetch_readme(repo["full_name"], repo.get("default_branch"), settings)
                if excerpt:
                    repo["readme_excerpt"] = excerpt
                repo_id = db.upsert_repo(conn, repo, today)
                prev = db.previous_stars(conn, repo_id, today)
                stars_now = int(repo.get("stars") or 0)
                stars_delta = 0 if prev is None else stars_now - prev
                item_id = db.upsert_digest_item(
                    conn,
                    {
                        "digest_date": today,
                        "repo_id": repo_id,
                        "source": repo.get("source") or "trending",
                        "rank": idx,
                        "stars_today": int(repo.get("stars_today") or 0),
                        "stars_delta": stars_delta,
                    },
                )
                row = dict(repo)
                row["item_id"] = item_id
                row["repo_id"] = repo_id
                prepared.append(row)

            engine = "DeepSeek" if settings.get("deepseek_api_key") else "简要规则"
            _set(stage="summary", progress=75, message=f"正在用 {engine} 写中文摘要…")

            def on_collect_batch(done, total, _part):
                _set(
                    stage="summary",
                    progress=75 + int(done / max(total, 1) * 20),
                    message=f"正在用 {engine} 写中文摘要（{done}/{total} 批）…",
                )

            summaries = summarize_items(prepared, settings, on_batch=on_collect_batch)
            for row, summary in zip(prepared, summaries):
                db.update_item_summary(conn, row["item_id"], summary)
                if row.get("readme_excerpt"):
                    db.update_repo_readme(conn, row["repo_id"], row["readme_excerpt"])

        _set(
            status="done",
            stage="done",
            progress=100,
            message=f"今日简报已生成，共 {len(prepared)} 个项目。",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    except Exception as exc:
        _set(
            status="error",
            stage="error",
            progress=100,
            message="采集失败",
            error=f"{exc}\n{traceback.format_exc()}",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    finally:
        _job_lock.release()


def run_downloads(repo_ids: list[int]) -> dict[str, Any]:
    if not _job_lock.acquire(blocking=False):
        return job_status()
    try:
        settings = db.get_settings()
        root = settings["download_dir"]
        started = datetime.now(timezone.utc).isoformat()
        downloads = []
        _set(
            kind="download",
            status="running",
            stage="download",
            progress=5,
            message="开始下载选中的仓库…",
            error="",
            started_at=started,
            finished_at="",
            downloads=downloads,
        )
        total = max(len(repo_ids), 1)
        for i, repo_id in enumerate(repo_ids, start=1):
            repo = db.get_repo(repo_id)
            if not repo:
                downloads.append(
                    {"repo_id": repo_id, "full_name": "?", "status": "error", "message": "仓库不存在"}
                )
                continue
            entry = {
                "repo_id": repo_id,
                "full_name": repo["full_name"],
                "status": "running",
                "message": "下载中",
                "path": repo.get("local_path") or "",
            }
            downloads.append(entry)
            _set(
                progress=int(i / total * 90),
                message=f"正在下载 {repo['full_name']}（{i}/{len(repo_ids)}）",
                downloads=list(downloads),
            )
            try:
                if not settings.get("github_token"):
                    raise GitHubLinkError("还没有连接你的 GitHub，不能把项目接到你的仓库。")
                if not settings.get("github_login"):
                    identity = fetch_identity(settings)
                    db.save_settings({"github_login": identity["login"]})
                    settings = db.get_settings()
                link = ensure_fork(repo["full_name"], settings)
                path = download_repo(repo["full_name"], root, settings)
                link_local_clone(repo["full_name"], link["fork_full_name"], settings)
                db.set_repo_link(
                    repo_id,
                    link["fork_full_name"],
                    link["fork_url"],
                    local_path=str(path),
                    status="downloaded",
                )
                entry["status"] = "done"
                entry["message"] = f"已下载并接到 {link['fork_full_name']}"
                entry["path"] = str(path)
                entry["fork_url"] = link["fork_url"]
            except Exception as exc:
                db.set_repo_status(repo_id, repo.get("status") or "useful", last_error=str(exc))
                entry["status"] = "error"
                entry["message"] = str(exc)
            _set(downloads=list(downloads))
        try:
            if settings.get("github_token"):
                hub = sync_hub_catalog(settings, db.list_catalog_repos())
                _set(message=f"下载结束，总册已更新：{hub['full_name']}")
        except Exception as exc:
            _set(message=f"本地下载完成，但同步总册失败：{exc}")
        _set(
            status="done",
            stage="done",
            progress=100,
            finished_at=datetime.now(timezone.utc).isoformat(),
            downloads=list(downloads),
        )
        return job_status()
    except Exception as exc:
        _set(
            status="error",
            error=str(exc),
            message="下载失败",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    finally:
        _job_lock.release()


def link_one_repo(repo_id: int) -> dict:
    settings = db.get_settings()
    if not settings.get("github_token"):
        raise GitHubLinkError("还没有连接你的 GitHub。")
    if not settings.get("github_login"):
        identity = fetch_identity(settings)
        db.save_settings({"github_login": identity["login"]})
        settings = db.get_settings()
    repo = db.get_repo(repo_id)
    if not repo:
        raise GitHubLinkError("仓库不存在")
    link = ensure_fork(repo["full_name"], settings)
    local = None
    if repo.get("local_path") or repo.get("status") == "downloaded":
        dest = link_local_clone(repo["full_name"], link["fork_full_name"], settings)
        local = str(dest) if dest else repo.get("local_path")
    row = db.set_repo_link(
        repo_id,
        link["fork_full_name"],
        link["fork_url"],
        local_path=local,
        status=repo.get("status") if repo.get("status") != "new" else "useful",
    )
    try:
        sync_hub_catalog(settings, db.list_catalog_repos())
    except Exception:
        pass
    return row or {}


def run_connect() -> dict[str, Any]:
    if not _job_lock.acquire(blocking=False):
        return job_status()
    try:
        settings = db.get_settings()
        _set(
            kind="connect",
            status="running",
            stage="auth",
            progress=10,
            message="正在验证你的 GitHub 账号…",
            error="",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at="",
            downloads=[],
        )
        identity = fetch_identity(settings)
        db.save_settings({"github_login": identity["login"]})
        settings = db.get_settings()
        token_info = inspect_token(settings)
        notes: list[str] = []
        if token_info.get("fine_grained"):
            notes.append(
                "当前是 Fine-grained Token，通常不能新建仓库。"
                "建议改用 Classic token 并勾选 repo。"
            )
        _set(progress=35, stage="app", message=f"已识别 @{identity['login']}，正在接入仓库…")
        app_repo = None
        try:
            app_repo = publish_app_repo(settings)
        except Exception as exc:
            notes.append(f"日报程序仓库先跳过：{exc}")
        _set(progress=55, stage="fork", message="正在把已选项目 fork 到你的账号…")
        linked = 0
        for repo in db.list_catalog_repos():
            try:
                link = ensure_fork(repo["full_name"], settings)
                local = None
                if repo.get("local_path") or repo.get("status") == "downloaded":
                    dest = link_local_clone(repo["full_name"], link["fork_full_name"], settings)
                    local = str(dest) if dest else repo.get("local_path")
                db.set_repo_link(
                    repo["id"],
                    link["fork_full_name"],
                    link["fork_url"],
                    local_path=local,
                    status=repo.get("status") if repo.get("status") != "new" else "useful",
                )
                linked += 1
            except Exception as exc:
                notes.append(f"{repo.get('full_name')}: {exc}")
                continue
        hub = None
        _set(progress=80, stage="hub", message="正在更新精选总册…")
        try:
            hub = sync_hub_catalog(settings, db.list_catalog_repos())
        except Exception as exc:
            notes.append(f"精选总册先跳过：{exc}")
        parts = [f"已连接 @{identity['login']}。已同步 {linked} 个项目。"]
        if app_repo:
            parts.append(f"日报程序 {app_repo['html_url']}")
        if hub:
            parts.append(f"精选总册 {hub['html_url']}")
        if notes:
            parts.append(" ".join(notes[:4]))
        _set(
            status="done",
            stage="done",
            progress=100,
            message=" ".join(parts),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    except Exception as exc:
        _set(
            status="error",
            stage="error",
            progress=100,
            message="连接 GitHub 失败",
            error=f"{exc}\n{traceback.format_exc()}",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    finally:
        _job_lock.release()


def run_summarize(day: str | None = None) -> dict[str, Any]:
    if not _job_lock.acquire(blocking=False):
        return job_status()
    try:
        settings = db.get_settings()
        if not settings.get("deepseek_api_key"):
            raise RuntimeError("还没有填写 DeepSeek API Key。")
        target = day or db.today_iso()
        min_stars = int(settings.get("min_stars") or 0)
        items = db.query_digest(target, min_stars=min_stars)
        if not items:
            raise RuntimeError("这一天还没有简报，先采集今日项目。")
        _set(
            kind="summarize",
            status="running",
            stage="summary",
            progress=15,
            message=f"正在用 DeepSeek 写 {len(items)} 条中文摘要…",
            error="",
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at="",
            downloads=[],
        )
        by_name = {(row.get("full_name") or "").lower(): row for row in items}

        def on_batch(done, total, part):
            with db.db() as conn:
                for name, extra in part.items():
                    row = by_name.get(name)
                    if not row:
                        continue
                    db.update_item_summary(conn, row["item_id"], extra)
            _set(
                stage="summary",
                progress=15 + int(done / max(total, 1) * 80),
                message=f"DeepSeek 摘要进行中（{done}/{total} 批）…",
            )

        summaries = summarize_items(items, settings, on_batch=on_batch)
        with db.db() as conn:
            for row, summary in zip(items, summaries):
                db.update_item_summary(conn, row["item_id"], summary)
        _set(
            status="done",
            stage="done",
            progress=100,
            message=f"中文摘要已更新，共 {len(items)} 条。",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    except Exception as exc:
        _set(
            status="error",
            stage="error",
            progress=100,
            message="DeepSeek 摘要失败",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return job_status()
    finally:
        _job_lock.release()
