from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from star_digest import db
from star_digest.config import WEB_DIR, ensure_dirs
from star_digest.github_link import GitHubLinkError, fetch_identity, inspect_token
from star_digest.jobs import (
    job_status,
    mark_started,
    run_connect,
    run_daily_collect,
    run_downloads,
    run_summarize,
)

ensure_dirs()
db.init_db()

app = FastAPI(title="GitHub 高星日报", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


class StatusIn(BaseModel):
    status: str
    notes: str | None = None


class DownloadIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


class SettingsIn(BaseModel):
    download_dir: str | None = None
    github_token: str | None = None
    github_login: str | None = None
    hub_repo: str | None = None
    app_repo: str | None = None
    xai_api_key: str | None = None
    xai_model: str | None = None
    deepseek_api_key: str | None = None
    deepseek_model: str | None = None
    spoken_codes: list[str] | None = None
    prog_langs: list[str] | None = None
    rising_days: int | None = None
    rising_min_stars: int | None = None
    min_stars: int | None = None
    max_repos_per_day: int | None = None


class ConnectIn(BaseModel):
    github_token: str | None = None


class CollectIn(BaseModel):
    force: bool = False


class SummarizeIn(BaseModel):
    date: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "templates" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "today": db.today_iso()}


@app.get("/api/digest")
def api_digest(
    date: str = "",
    q: str = "",
    language: str = "",
    source: str = "",
    status: str = "",
    min_stars: int = -1,
) -> dict:
    day = date or db.today_iso()
    if min_stars < 0:
        min_stars = int(db.get_settings().get("min_stars") or 0)
    items = db.query_digest(
        day, q=q, language=language, source=source, status=status, min_stars=min_stars
    )
    return {
        "date": day,
        "dates": db.list_dates(),
        "stats": db.stats(day),
        "items": items,
        "job": job_status(),
        "settings": db.public_settings(),
    }


@app.get("/api/stats")
def api_stats(date: str = "") -> dict:
    return db.stats(date or db.today_iso())


@app.post("/api/repo/{repo_id}/status")
def api_status(repo_id: int, body: StatusIn) -> dict:
    allowed = {"new", "useful", "skipped", "downloaded"}
    if body.status not in allowed:
        raise HTTPException(400, "非法状态")
    row = db.set_repo_status(repo_id, body.status, notes=body.notes)
    if not row:
        raise HTTPException(404, "仓库不存在")
    return row


@app.post("/api/summarize")
def api_summarize(body: SummarizeIn) -> dict:
    settings = db.get_settings()
    if not settings.get("deepseek_api_key"):
        raise HTTPException(400, "请先在设置里填写 DeepSeek API Key")
    if not mark_started("summarize", "正在用 DeepSeek 写中文摘要"):
        raise HTTPException(409, "已有任务在跑，请稍后再试")
    threading.Thread(target=run_summarize, kwargs={"day": body.date or ""}, daemon=True).start()
    return job_status()


@app.post("/api/collect")
def api_collect(body: CollectIn) -> dict:
    if not mark_started("collect", "已开始采集"):
        return job_status()
    threading.Thread(
        target=run_daily_collect, kwargs={"force": body.force}, daemon=True
    ).start()
    return job_status()


@app.get("/api/job")
def api_job() -> dict:
    return job_status()


@app.post("/api/download")
def api_download(body: DownloadIn) -> dict:
    if not body.ids:
        raise HTTPException(400, "请先勾选要下载的项目")
    if not db.get_settings().get("github_token"):
        raise HTTPException(400, "请先连接你的 GitHub，才能把项目接到你的仓库")
    if not mark_started("download", "已开始下载"):
        raise HTTPException(409, "已有任务在跑，请稍后再试")
    threading.Thread(target=run_downloads, args=(body.ids,), daemon=True).start()
    return job_status()


@app.get("/api/settings")
def api_get_settings() -> dict:
    return db.public_settings()


@app.post("/api/settings")
def api_save_settings(body: SettingsIn) -> dict:
    payload = body.model_dump(exclude_none=True)
    if payload.get("github_token") == "********":
        payload.pop("github_token")
    if payload.get("xai_api_key") == "********":
        payload.pop("xai_api_key")
    if payload.get("deepseek_api_key") == "********":
        payload.pop("deepseek_api_key")
    if payload.get("download_dir"):
        path = Path(payload["download_dir"]).expanduser()
        payload["download_dir"] = str(path)
    saved = db.save_settings(payload)
    token = db.get_settings().get("github_token")
    if token and payload.get("github_token"):
        try:
            identity = fetch_identity(db.get_settings())
            saved = db.save_settings({"github_login": identity["login"]})
        except GitHubLinkError as exc:
            raise HTTPException(400, str(exc)) from exc
    return saved


@app.get("/api/github")
def api_github_status() -> dict:
    return db.public_settings()


@app.get("/api/github/token-info")
def api_github_token_info() -> dict:
    settings = db.get_settings()
    if not settings.get("github_token"):
        return {"has_token": False}
    try:
        info = inspect_token(settings)
        info["has_token"] = True
        return info
    except GitHubLinkError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/github/connect")
def api_github_connect(body: ConnectIn) -> dict:
    if body.github_token and body.github_token != "********":
        db.save_settings({"github_token": body.github_token})
    settings = db.get_settings()
    if not settings.get("github_token"):
        raise HTTPException(400, "请先填写 GitHub Token")
    try:
        identity = fetch_identity(settings)
    except GitHubLinkError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.save_settings({"github_login": identity["login"]})
    if not mark_started("connect", f"正在把项目接到 @{identity['login']}"):
        raise HTTPException(409, "已有任务在跑，请稍后再试")
    threading.Thread(target=run_connect, daemon=True).start()
    return {"status": "running", "login": identity["login"], "job": job_status()}


@app.post("/api/open-folder")
def api_open_folder(path: str = "") -> dict:
    settings = db.get_settings()
    root = Path(settings["download_dir"]).resolve()
    target = Path(path).expanduser().resolve() if path else root
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(400, "只能打开下载目录里的文件夹") from exc
    if not target.exists():
        root.mkdir(parents=True, exist_ok=True)
        target = root
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"ok": True, "path": str(target)}


def main() -> None:
    import uvicorn

    settings = db.get_settings()
    uvicorn.run(
        "app:app",
        host=settings.get("host") or "127.0.0.1",
        port=int(settings.get("port") or 8787),
        reload=False,
    )


if __name__ == "__main__":
    sys.exit(main() or 0)
