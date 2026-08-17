from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx

from .collector import github_headers


def safe_dirname(full_name: str) -> str:
    return full_name.replace("/", "__")


def target_dir(download_root: str | Path, full_name: str) -> Path:
    return Path(download_root) / safe_dirname(full_name)


def already_downloaded(path: Path) -> bool:
    if not path.exists():
        return False
    if (path / ".git").exists():
        return True
    return any(path.iterdir())


def download_repo(full_name: str, download_root: str | Path, settings: dict) -> Path:
    dest = target_dir(download_root, full_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if already_downloaded(dest):
        return dest
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    git = shutil.which("git")
    url = f"https://github.com/{full_name}.git"
    if git:
        proc = subprocess.run(
            [git, "clone", "--depth", "1", "--single-branch", url, str(dest)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0 and dest.exists():
            return dest
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
    return download_zipball(full_name, dest, settings)


def download_zipball(full_name: str, dest: Path, settings: dict) -> Path:
    headers = github_headers(
        settings.get("github_token") or "",
        settings.get("user_agent") or "",
    )
    tmp = dest.with_suffix(".zip")
    last_error = None
    with httpx.Client(timeout=60.0, follow_redirects=True, headers=headers) as client:
        for url in (
            f"https://api.github.com/repos/{full_name}/zipball",
            f"https://github.com/{full_name}/archive/refs/heads/main.zip",
            f"https://github.com/{full_name}/archive/refs/heads/master.zip",
        ):
            try:
                with client.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        last_error = f"{url} -> {resp.status_code}"
                        continue
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                _extract_zip(tmp, dest)
                tmp.unlink(missing_ok=True)
                return dest
            except Exception as exc:
                last_error = str(exc)
                tmp.unlink(missing_ok=True)
    raise RuntimeError(last_error or f"下载失败 {full_name}")


def _extract_zip(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("空压缩包")
        root = names[0].split("/")[0]
        dest.mkdir(parents=True, exist_ok=True)
        zf.extractall(dest.parent)
        extracted = dest.parent / root
        if extracted.exists() and extracted != dest:
            if dest.exists():
                shutil.rmtree(dest)
            extracted.rename(dest)
