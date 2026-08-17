from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

Progress = Callable[[str, int, str], None]

README_NAMES = (
    "README.md",
    "README.zh-CN.md",
    "README.rst",
    "README",
)


class CollectError(RuntimeError):
    pass


def github_headers(token: str = "", user_agent: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": user_agent
        or "GitHubStarDigest/1.0 (+local personal digest)",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_int(text: str | None) -> int:
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else 0


def parse_trending_html(html: str, source: str = "trending") -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    articles = soup.select("article.Box-row")
    if not articles:
        articles = soup.select("article")
    for art in articles:
        link = art.select_one("h2 a[href], h1 a[href], a.Link[href]")
        href = ""
        if link and link.get("href"):
            href = str(link.get("href"))
        if not href:
            continue
        href = href.split("?")[0].strip("/")
        if href.count("/") != 1:
            continue
        owner, name = href.split("/", 1)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            continue
        full_name = f"{owner}/{name}"
        if full_name.lower() in seen:
            continue
        seen.add(full_name.lower())
        desc_el = art.select_one("p")
        lang_el = art.select_one('[itemprop="programmingLanguage"]')
        star_el = art.select_one('a[href$="/stargazers"]')
        fork_el = art.select_one('a[href$="/forks"]')
        blob = art.get_text(" ", strip=True)
        stars_today = 0
        m = re.search(r"([\d,]+)\s+stars?\s+today", blob, re.I)
        if not m:
            m = re.search(r"today\s+star[s]?\s+([\d,]+)", blob, re.I)
        if m:
            stars_today = parse_int(m.group(1))
        items.append(
            {
                "full_name": full_name,
                "owner": owner,
                "name": name,
                "html_url": f"https://github.com/{full_name}",
                "description": desc_el.get_text(" ", strip=True) if desc_el else "",
                "language": lang_el.get_text(strip=True) if lang_el else "",
                "stars": parse_int(star_el.get_text() if star_el else ""),
                "forks": parse_int(fork_el.get_text() if fork_el else ""),
                "stars_today": stars_today,
                "source": source,
            }
        )
    if items:
        return items
    return parse_trending_fallback(html, source)


def parse_trending_fallback(text: str, source: str = "trending") -> list[dict]:
    """Works on GitHub HTML or markdown-converted trending pages."""
    items: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"\[(?P<owner>[A-Za-z0-9_.-]+)\s*/\s*(?P<name>[A-Za-z0-9_.-]+)\]"
        r"\(/[\w.-]+/[\w.-]+\)\s*(?P<body>.*?)(?=\n\[(?:Star|Sponsor)\]|\n## |\Z)",
        re.S,
    )
    for match in pattern.finditer(text):
        owner = match.group("owner")
        name = match.group("name")
        full_name = f"{owner}/{name}"
        if full_name.lower() in seen:
            continue
        seen.add(full_name.lower())
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        stars_today = 0
        tm = re.search(r"([\d,]+)\s+stars?\s+today", body, re.I)
        if tm:
            stars_today = parse_int(tm.group(1))
        stars = 0
        sm = re.search(r"\[([\d,]+)\]\([^)]+/stargazers\)", body)
        if sm:
            stars = parse_int(sm.group(1))
        forks = 0
        fm = re.search(r"\[([\d,]+)\]\([^)]+/forks\)", body)
        if fm:
            forks = parse_int(fm.group(1))
        lang = ""
        lm = re.search(
            r"\b(TypeScript|JavaScript|Python|Go|Rust|C\+\+|C#|Java|Shell|HTML|CSS|"
            r"Vue|Swift|Kotlin|Ruby|PHP|C|Dart|Lua|R|Scala|Zig|Elixir)\b",
            body,
        )
        if lm:
            lang = lm.group(1)
        desc = body
        desc = re.sub(r"\[.*?\]\(.*?\)", " ", desc)
        desc = re.sub(r"\b[\d,]+\s+stars? today\b", " ", desc, flags=re.I)
        desc = re.sub(r"\s+", " ", desc).strip()
        if lang and desc.startswith(lang):
            desc = desc[len(lang) :].strip()
        items.append(
            {
                "full_name": full_name,
                "owner": owner,
                "name": name,
                "html_url": f"https://github.com/{full_name}",
                "description": desc[:300],
                "language": lang,
                "stars": stars,
                "forks": forks,
                "stars_today": stars_today,
                "source": source,
            }
        )
    return items


def fetch_trending_pages(settings: dict, progress: Progress | None = None) -> list[dict]:
    spoken = settings.get("spoken_codes") or [""]
    langs = settings.get("prog_langs") or []
    headers = {
        "User-Agent": settings.get("user_agent") or github_headers()["User-Agent"],
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    collected: list[dict] = []
    seen: set[str] = set()
    jobs: list[tuple[str, str]] = []
    for code in spoken:
        jobs.append(("spoken", code))
    for lang in langs:
        jobs.append(("lang", lang))
    if not jobs:
        jobs = [("spoken", "")]

    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for i, (kind, value) in enumerate(jobs, start=1):
            if kind == "spoken":
                params = {"since": "daily"}
                if value:
                    params["spoken_language_code"] = value
                url = "https://github.com/trending?" + urlencode(params)
                source = "trending-zh" if value == "zh" else "trending"
            else:
                url = f"https://github.com/trending/{value}?since=daily"
                source = f"trending-{value}"
            if progress:
                progress("trending", int(i / max(len(jobs), 1) * 30), f"抓取趋势 {url}")
            resp = client.get(url)
            if resp.status_code >= 400:
                continue
            for item in parse_trending_html(resp.text, source=source):
                key = item["full_name"].lower()
                if key in seen:
                    if item.get("stars_today"):
                        for old in collected:
                            if old["full_name"].lower() == key:
                                old["stars_today"] = max(
                                    old.get("stars_today") or 0,
                                    item.get("stars_today") or 0,
                                )
                                if old.get("source") == "trending" and item["source"] != "trending":
                                    old["source"] = item["source"]
                                break
                    continue
                seen.add(key)
                collected.append(item)
    if not collected:
        raise CollectError("无法解析 GitHub Trending，请稍后重试或填写 GitHub Token。")
    return collected


def fetch_rising(settings: dict, progress: Progress | None = None) -> list[dict]:
    days = int(settings.get("rising_days") or 7)
    min_stars = int(settings.get("rising_min_stars") or 50)
    since = (date.today() - timedelta(days=days)).isoformat()
    query = f"created:>={since} stars:>={min_stars}"
    url = "https://api.github.com/search/repositories"
    headers = github_headers(
        settings.get("github_token") or "",
        settings.get("user_agent") or "",
    )
    if progress:
        progress("rising", 35, f"搜索近{days}天新晋高星")
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            resp = client.get(
                url,
                params={
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 20,
                },
            )
            if resp.status_code == 403:
                return []
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError:
        return []
    items = []
    for repo in payload.get("items") or []:
        license_info = repo.get("license") or {}
        items.append(
            {
                "full_name": repo.get("full_name"),
                "owner": (repo.get("owner") or {}).get("login") or repo["full_name"].split("/")[0],
                "name": repo.get("name"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description") or "",
                "language": repo.get("language") or "",
                "stars": repo.get("stargazers_count") or 0,
                "forks": repo.get("forks_count") or 0,
                "topics": repo.get("topics") or [],
                "license": license_info.get("spdx_id") if isinstance(license_info, dict) else "",
                "homepage": repo.get("homepage") or "",
                "default_branch": repo.get("default_branch") or "main",
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "pushed_at": repo.get("pushed_at"),
                "stars_today": 0,
                "source": "rising",
            }
        )
    return items


def fetch_readme(full_name: str, branch: str | None, settings: dict) -> str:
    branches = [b for b in [branch, "main", "master"] if b]
    names = README_NAMES
    headers = {
        "User-Agent": settings.get("user_agent") or github_headers()["User-Agent"],
        "Accept": "text/plain",
    }
    with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
        tried_missing_branch = set()
        for br in dict.fromkeys(branches):
            for name in names:
                url = f"https://raw.githubusercontent.com/{full_name}/{br}/{name}"
                try:
                    resp = client.get(url)
                except httpx.HTTPError:
                    continue
                if resp.status_code == 404:
                    if name == "README.md":
                        tried_missing_branch.add(br)
                    continue
                if resp.status_code == 200 and resp.text.strip():
                    return clip_readme(resp.text)
            if br in tried_missing_branch:
                continue
    token = settings.get("github_token") or ""
    if not token:
        return ""
    api_headers = github_headers(token, settings.get("user_agent") or "")
    api_headers["Accept"] = "application/vnd.github.raw+json"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True, headers=api_headers) as client:
            resp = client.get(f"https://api.github.com/repos/{full_name}/readme")
            if resp.status_code == 200 and resp.text.strip():
                return clip_readme(resp.text)
    except httpx.HTTPError:
        return ""
    return ""


PROMO_HEADING = re.compile(
    r"apilayer|sponsor|now live|sign up|best[- ]seller|discord server|"
    r"postman collection|one api key|covered under",
    re.I,
)
PROMO_LINE = re.compile(
    r"utm_source=|apilayer\.com|app\.apilayer|god\.gw\.postman|"
    r"postman\.com/.*/collection|entityType%3D|workspaceId%3D|"
    r"discord\.com/invite|sign up and start|one account, one dashboard",
    re.I,
)


def strip_promo(text: str) -> str:
    """Drop sponsor/ad blocks so summaries see the real project intro."""
    blocks = re.split(r"(?=^#{1,3}\s)", text.replace("\r\n", "\n"), flags=re.M)
    kept: list[str] = []
    for block in blocks:
        raw = block.strip()
        if not raw:
            continue
        first = raw.splitlines()[0]
        heading = first.lstrip("#").strip()
        if PROMO_HEADING.search(heading) and not re.search(
            r"public api|repository is|manually curated", raw, re.I
        ):
            continue
        lines = []
        for line in raw.splitlines():
            if re.search(r"<div>|<p>|</p>|</div>", line, re.I) and "http" in line.lower():
                continue
            if PROMO_LINE.search(line):
                prose = re.sub(r"\[.*?\]\(.*?\)", "", line)
                prose = re.sub(r"https?://\S+", "", prose).strip(" -*|")
                if len(prose) < 40:
                    continue
                line = prose
            lines.append(line)
        cleaned = "\n".join(lines).strip()
        cleaned = re.sub(r"<[^>]+>", "", cleaned)
        cleaned = re.sub(
            r"^(APILayer is|Join our|Explore\s+here).*$",
            "",
            cleaned,
            flags=re.I | re.M,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned:
            kept.append(cleaned)
    return "\n\n".join(kept).strip() or text


def clip_readme(text: str, limit: int = 4000) -> str:
    text = text.replace("\r\n", "\n")
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
    text = strip_promo(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        return text[:limit].rsplit("\n", 1)[0] + "\n…"
    return text


def merge_candidates(*groups: list[dict], limit: int) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        for item in group:
            key = (item.get("full_name") or "").lower()
            if not key:
                continue
            if key not in merged:
                merged[key] = dict(item)
                continue
            old = merged[key]
            for field in (
                "description",
                "language",
                "license",
                "homepage",
                "default_branch",
                "created_at",
                "updated_at",
                "pushed_at",
            ):
                if not old.get(field) and item.get(field):
                    old[field] = item[field]
            old["stars"] = max(int(old.get("stars") or 0), int(item.get("stars") or 0))
            old["forks"] = max(int(old.get("forks") or 0), int(item.get("forks") or 0))
            old["stars_today"] = max(
                int(old.get("stars_today") or 0), int(item.get("stars_today") or 0)
            )
            if old.get("source") == "rising" and item.get("source", "").startswith("trending"):
                old["source"] = item["source"]
            if item.get("topics"):
                topics = list(old.get("topics") or [])
                for t in item["topics"]:
                    if t not in topics:
                        topics.append(t)
                old["topics"] = topics
    ranked = sorted(
        merged.values(),
        key=lambda r: (int(r.get("stars_today") or 0), int(r.get("stars") or 0)),
        reverse=True,
    )
    return ranked[: max(1, int(limit))]
