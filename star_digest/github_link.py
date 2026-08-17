from __future__ import annotations

import base64
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .collector import github_headers
from .config import ROOT
from .downloader import already_downloaded, target_dir


class GitHubLinkError(RuntimeError):
    pass


def _api_message(resp: httpx.Response) -> str:
    text = (resp.text or "").strip()
    try:
        data = resp.json()
    except Exception:
        return f"{resp.status_code} {text[:240]}"
    if isinstance(data, dict):
        msg = data.get("message") or ""
        extra = data.get("documentation_url") or ""
        errors = data.get("errors") or []
        detail = ""
        if isinstance(errors, list) and errors:
            detail = "；".join(
                e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in errors[:3]
            )
        bits = [p for p in (msg, detail) if p]
        hint = ""
        blob = " ".join(bits).lower()
        if resp.status_code == 403:
            if "personal access token" in blob or "resource not accessible" in blob:
                hint = (
                    "当前 Token 没有「新建仓库」权限。"
                    "请用 Classic token，勾选 repo；"
                    "或先在网页上手动建好空仓库 github-star-digest / github-star-picks。"
                )
            elif "sso" in blob:
                hint = "这个 Token 还要去 GitHub 点 Authorize SSO。"
        elif resp.status_code == 422 and "already exists" in blob:
            hint = "仓库名已存在，将改用现有仓库。"
        body = " ".join(bits) or text[:200]
        return " ".join(p for p in (f"{resp.status_code}", body, hint, extra) if p)
    return f"{resp.status_code} {text[:240]}"


def inspect_token(settings: dict) -> dict:
    with _client(settings) as client:
        resp = client.get("/user")
        if resp.status_code >= 400:
            raise GitHubLinkError(_api_message(resp))
        scopes = (resp.headers.get("x-oauth-scopes") or "").replace(" ", "")
        scope_list = [s for s in scopes.split(",") if s]
        login = (resp.json() or {}).get("login") or ""
    classic = bool(scope_list)
    can_create = (not classic) or any(s in {"repo", "public_repo"} for s in scope_list)
    return {
        "login": login,
        "classic": classic,
        "scopes": scope_list,
        "can_create_repo": can_create,
        "fine_grained": not classic,
    }


def _client(settings: dict) -> httpx.Client:
    token = (settings.get("github_token") or "").strip()
    if not token:
        raise GitHubLinkError("还没有连接你的 GitHub。请在设置里粘贴 Token。")
    return httpx.Client(
        timeout=40.0,
        follow_redirects=True,
        headers=github_headers(token, settings.get("user_agent") or ""),
        base_url="https://api.github.com",
    )


def fetch_identity(settings: dict) -> dict:
    with _client(settings) as client:
        resp = client.get("/user")
        if resp.status_code == 401:
            raise GitHubLinkError("GitHub Token 无效，请重新创建 Classic token，并勾选 repo。")
        if resp.status_code >= 400:
            raise GitHubLinkError(f"读取 GitHub 账号失败：{_api_message(resp)}")
        data = resp.json()
    login = data.get("login")
    if not login:
        raise GitHubLinkError("Token 有效，但读不到用户名。")
    return {
        "login": login,
        "name": data.get("name") or login,
        "html_url": data.get("html_url") or f"https://github.com/{login}",
    }


def require_login(settings: dict) -> str:
    login = (settings.get("github_login") or "").strip()
    if login:
        return login
    return fetch_identity(settings)["login"]


def ensure_fork(full_name: str, settings: dict) -> dict:
    login = require_login(settings)
    owner, name = full_name.split("/", 1)
    if owner.lower() == login.lower():
        return {
            "fork_full_name": full_name,
            "fork_url": f"https://github.com/{full_name}",
            "own": True,
        }
    with _client(settings) as client:
        existing = client.get(f"/repos/{login}/{name}")
        if existing.status_code == 200:
            data = existing.json()
            return {
                "fork_full_name": data.get("full_name") or f"{login}/{name}",
                "fork_url": data.get("html_url") or f"https://github.com/{login}/{name}",
                "own": bool(data.get("fork") is False),
            }
        resp = client.post(f"/repos/{owner}/{name}/forks")
        if resp.status_code not in {200, 201, 202}:
            raise GitHubLinkError(f"Fork {full_name} 失败：{_api_message(resp)}")
        forked = _wait_fork(client, login, name)
    return {
        "fork_full_name": forked.get("full_name") or f"{login}/{name}",
        "fork_url": forked.get("html_url") or f"https://github.com/{login}/{name}",
        "own": False,
    }


def _wait_fork(client: httpx.Client, login: str, name: str) -> dict:
    deadline = time.time() + 45
    last = {}
    while time.time() < deadline:
        resp = client.get(f"/repos/{login}/{name}")
        if resp.status_code == 200:
            return resp.json()
        last = {"status": resp.status_code, "text": resp.text[:160]}
        time.sleep(1.5)
    raise GitHubLinkError(f"Fork {login}/{name} 已提交，但 GitHub 还没准备好：{last}")


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise GitHubLinkError((proc.stderr or proc.stdout or "git 失败").strip())
    return proc


def public_git_url(full_name: str) -> str:
    return f"https://github.com/{full_name}.git"


def authed_git_url(token: str, full_name: str) -> str:
    return f"https://x-access-token:{token}@github.com/{full_name}.git"


def set_local_remotes(path: Path, origin_full_name: str, upstream_full_name: str | None = None) -> None:
    if not (path / ".git").exists():
        return
    remotes = git("remote", cwd=path, check=False).stdout.split()
    origin = public_git_url(origin_full_name)
    if "origin" in remotes:
        git("remote", "set-url", "origin", origin, cwd=path)
    else:
        git("remote", "add", "origin", origin, cwd=path)
    if upstream_full_name and upstream_full_name.lower() != origin_full_name.lower():
        upstream = public_git_url(upstream_full_name)
        if "upstream" in remotes:
            git("remote", "set-url", "upstream", upstream, cwd=path)
        else:
            git("remote", "add", "upstream", upstream, cwd=path)


def ensure_repo(settings: dict, name: str, description: str) -> dict:
    login = require_login(settings)
    with _client(settings) as client:
        resp = client.get(f"/repos/{login}/{name}")
        if resp.status_code == 200:
            data = resp.json()
            return {
                "full_name": data["full_name"],
                "html_url": data["html_url"],
                "clone_url": data.get("clone_url") or public_git_url(data["full_name"]),
                "created": False,
            }
        payload = {
            "name": name,
            "description": description,
            "private": False,
            "auto_init": True,
        }
        created = client.post("/user/repos", json=payload)
        if created.status_code == 422:
            again = client.get(f"/repos/{login}/{name}")
            if again.status_code == 200:
                data = again.json()
                return {
                    "full_name": data["full_name"],
                    "html_url": data["html_url"],
                    "clone_url": data.get("clone_url") or public_git_url(data["full_name"]),
                    "created": False,
                }
        if created.status_code not in {200, 201}:
            raise GitHubLinkError(f"创建仓库 {name} 失败：{_api_message(created)}")
        data = created.json()
    return {
        "full_name": data["full_name"],
        "html_url": data["html_url"],
        "clone_url": data.get("clone_url") or public_git_url(data["full_name"]),
        "created": True,
    }


def put_file(settings: dict, repo_full_name: str, path: str, content: str, message: str) -> None:
    raw = base64.b64encode(content.encode("utf-8")).decode("ascii")
    body = {"message": message, "content": raw}
    with _client(settings) as client:
        current = client.get(f"/repos/{repo_full_name}/contents/{path}")
        if current.status_code == 200:
            body["sha"] = current.json()["sha"]
            if current.json().get("content"):
                old = base64.b64decode(current.json()["content"].encode("ascii")).decode("utf-8")
                if old.replace("\r\n", "\n") == content.replace("\r\n", "\n"):
                    return
        resp = client.put(f"/repos/{repo_full_name}/contents/{path}", json=body)
        if resp.status_code not in {200, 201}:
            raise GitHubLinkError(f"写入 {repo_full_name}/{path} 失败：{_api_message(resp)}")


def build_catalog_markdown(login: str, items: list[dict], hub_repo: str, app_url: str = "") -> str:
    lines = [
        f"# {login} 的高星精选",
        "",
        f"由 [GitHub 高星日报]({app_url or 'http://127.0.0.1:8787'}) 自动维护。",
        f"总仓库：`{login}/{hub_repo}`。每个有用的项目都会 fork 到你的账号，本地 `origin` 指向你的 fork。",
        "",
        f"更新时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "| 原仓库 | 我的 fork | 语言 | Star | 状态 | 摘要 |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for item in items:
        src = item.get("full_name") or ""
        fork = item.get("fork_full_name") or f"{login}/{src.split('/')[-1] if src else ''}"
        summary = (item.get("summary_zh") or item.get("description") or "").replace("|", "\\|")
        if len(summary) > 80:
            summary = summary[:79] + "…"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"[{src}](https://github.com/{src})" if src else "",
                    f"[{fork}](https://github.com/{fork})" if fork else "",
                    item.get("language") or "",
                    str(item.get("stars") or 0),
                    item.get("status") or "",
                    summary,
                ]
            )
            + " |"
        )
    if not items:
        lines.append("")
        lines.append("还没有精选项目。打开日报勾选后点「下载并接到我的 GitHub」。")
    lines.append("")
    return "\n".join(lines)


def sync_hub_catalog(settings: dict, items: list[dict]) -> dict:
    login = require_login(settings)
    hub_name = settings.get("hub_repo") or "github-star-picks"
    hub = ensure_repo(settings, hub_name, "我从 GitHub 高星日报挑出来的项目总册")
    app_name = settings.get("app_repo") or "github-star-digest"
    app_url = f"https://github.com/{login}/{app_name}"
    markdown = build_catalog_markdown(login, items, hub_name, app_url)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "login": login,
        "items": [
            {
                "full_name": i.get("full_name"),
                "fork_full_name": i.get("fork_full_name"),
                "html_url": i.get("html_url"),
                "fork_url": i.get("fork_url"),
                "language": i.get("language"),
                "stars": i.get("stars"),
                "status": i.get("status"),
                "summary_zh": i.get("summary_zh") or i.get("description") or "",
                "local_path": i.get("local_path") or "",
            }
            for i in items
        ],
    }
    put_file(settings, hub["full_name"], "CATALOG.md", markdown, "chore: 同步高星精选目录")
    put_file(
        settings,
        hub["full_name"],
        "picks.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "chore: 同步高星精选数据",
    )
    return hub


def publish_app_repo(settings: dict) -> dict:
    login = require_login(settings)
    name = settings.get("app_repo") or "github-star-digest"
    remote = ensure_repo(settings, name, "本地 GitHub 高星日报：每日采集、挑选、下载并接到我的仓库")
    _ensure_app_git()
    token = settings["github_token"]
    git("remote", "remove", "origin", cwd=ROOT, check=False)
    git("remote", "add", "origin", authed_git_url(token, remote["full_name"]), cwd=ROOT)
    git("add", "-A", cwd=ROOT)
    staged = git("status", "--porcelain", cwd=ROOT, check=False)
    if staged.stdout.strip():
        git("commit", "-m", "chore: 同步 GitHub 高星日报", cwd=ROOT, check=False)
    pull = git("pull", "--rebase", "origin", "main", check=False)
    if pull.returncode != 0 and "couldn't find remote ref" not in (pull.stderr or ""):
        git("pull", "--rebase", "--autostash", "origin", "main", cwd=ROOT, check=False)
    push = git("push", "-u", "origin", "HEAD:main", cwd=ROOT, check=False)
    git("remote", "set-url", "origin", public_git_url(remote["full_name"]), cwd=ROOT)
    if push.returncode != 0:
        raise GitHubLinkError((push.stderr or push.stdout or "推送日报程序失败").strip())
    return remote


def _ensure_app_git() -> None:
    if not (ROOT / ".git").exists():
        git("init", "-b", "main", cwd=ROOT, check=False)
        if not (ROOT / ".git").exists():
            git("init", cwd=ROOT)
            git("checkout", "-B", "main", cwd=ROOT, check=False)
    name = git("config", "user.name", cwd=ROOT, check=False).stdout.strip()
    email = git("config", "user.email", cwd=ROOT, check=False).stdout.strip()
    if not name:
        git("config", "user.name", "GitHub Star Digest", cwd=ROOT)
    if not email:
        git("config", "user.email", "digest@users.noreply.github.com", cwd=ROOT)


def link_local_clone(full_name: str, fork_full_name: str, settings: dict) -> Path | None:
    dest = target_dir(settings["download_dir"], full_name)
    if not already_downloaded(dest):
        return None
    set_local_remotes(dest, fork_full_name, full_name)
    return dest


def connection_view(settings: dict) -> dict:
    login = (settings.get("github_login") or "").strip()
    token = bool(settings.get("github_token"))
    hub = settings.get("hub_repo") or "github-star-picks"
    app = settings.get("app_repo") or "github-star-digest"
    return {
        "connected": bool(token and login),
        "github_login": login,
        "github_url": f"https://github.com/{login}" if login else "",
        "hub_repo": hub,
        "hub_url": f"https://github.com/{login}/{hub}" if login else "",
        "app_repo": app,
        "app_url": f"https://github.com/{login}/{app}" if login else "",
        "has_github_token": token,
    }
