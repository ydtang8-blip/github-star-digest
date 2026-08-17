from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, DATA_DIR, load_settings

_lock = threading.Lock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    html_url TEXT NOT NULL,
    description TEXT,
    language TEXT,
    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    topics TEXT NOT NULL DEFAULT '[]',
    license TEXT,
    homepage TEXT,
    default_branch TEXT,
    created_at TEXT,
    updated_at TEXT,
    pushed_at TEXT,
    readme_excerpt TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    local_path TEXT,
    notes TEXT,
    last_error TEXT,
    fork_full_name TEXT,
    fork_url TEXT,
    linked_at TEXT
);

CREATE TABLE IF NOT EXISTS digest_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date TEXT NOT NULL,
    repo_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    stars_today INTEGER NOT NULL DEFAULT 0,
    stars_delta INTEGER NOT NULL DEFAULT 0,
    summary_zh TEXT,
    why_useful TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    score INTEGER,
    UNIQUE(digest_date, repo_id),
    FOREIGN KEY(repo_id) REFERENCES repos(id)
);

CREATE TABLE IF NOT EXISTS star_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL,
    snap_date TEXT NOT NULL,
    stars INTEGER NOT NULL,
    UNIQUE(repo_id, snap_date),
    FOREIGN KEY(repo_id) REFERENCES repos(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_digest_date ON digest_items(digest_date);
CREATE INDEX IF NOT EXISTS idx_repos_status ON repos(status);
CREATE INDEX IF NOT EXISTS idx_repos_lang ON repos(language);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(repos)").fetchall()}
    if "fork_full_name" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN fork_full_name TEXT")
    if "fork_url" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN fork_url TEXT")
    if "linked_at" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN linked_at TEXT")


def settings_overlay() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def get_settings() -> dict:
    return load_settings(settings_overlay())


def save_settings(payload: dict[str, Any]) -> dict:
    allowed = {
        "download_dir",
        "github_token",
        "github_login",
        "hub_repo",
        "app_repo",
        "xai_api_key",
        "xai_model",
        "deepseek_api_key",
        "deepseek_model",
        "spoken_codes",
        "prog_langs",
        "rising_days",
        "rising_min_stars",
        "min_stars",
        "max_repos_per_day",
        "port",
    }
    with db() as conn:
        for key, value in payload.items():
            if key not in allowed:
                continue
            if isinstance(value, (list, dict)):
                stored = json.dumps(value, ensure_ascii=False)
            else:
                stored = "" if value is None else str(value)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, stored),
            )
    from .config import write_env

    merged = get_settings()
    write_env(
        merged.get("github_token", ""),
        merged.get("xai_api_key", ""),
        merged.get("deepseek_api_key", ""),
    )
    Path(merged["download_dir"]).mkdir(parents=True, exist_ok=True)
    return public_settings(merged)


def public_settings(settings: dict | None = None) -> dict:
    s = dict(settings or get_settings())
    s["has_github_token"] = bool(s.get("github_token"))
    s["has_xai_key"] = bool(s.get("xai_api_key"))
    s["has_deepseek_key"] = bool(s.get("deepseek_api_key"))
    s["github_connected"] = bool(s.get("github_token") and s.get("github_login"))
    s["github_url"] = f"https://github.com/{s['github_login']}" if s.get("github_login") else ""
    s["hub_url"] = (
        f"https://github.com/{s['github_login']}/{s.get('hub_repo') or 'github-star-picks'}"
        if s.get("github_login")
        else ""
    )
    s["app_url"] = (
        f"https://github.com/{s['github_login']}/{s.get('app_repo') or 'github-star-digest'}"
        if s.get("github_login")
        else ""
    )
    s["github_token"] = "********" if s.get("github_token") else ""
    s["xai_api_key"] = "********" if s.get("xai_api_key") else ""
    s["deepseek_api_key"] = "********" if s.get("deepseek_api_key") else ""
    return s


def upsert_repo(conn: sqlite3.Connection, repo: dict, today: str) -> int:
    existing = conn.execute(
        "SELECT id, stars, first_seen FROM repos WHERE full_name=?",
        (repo["full_name"],),
    ).fetchone()
    topics = repo.get("topics") or []
    if isinstance(topics, list):
        topics_json = json.dumps(topics, ensure_ascii=False)
    else:
        topics_json = str(topics)
    fields = {
        "owner": repo["owner"],
        "name": repo["name"],
        "html_url": repo.get("html_url") or f"https://github.com/{repo['full_name']}",
        "description": repo.get("description"),
        "language": repo.get("language"),
        "stars": int(repo.get("stars") or 0),
        "forks": int(repo.get("forks") or 0),
        "topics": topics_json,
        "license": repo.get("license"),
        "homepage": repo.get("homepage"),
        "default_branch": repo.get("default_branch"),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "last_seen": today,
    }
    if repo.get("readme_excerpt"):
        fields["readme_excerpt"] = repo["readme_excerpt"]
    if existing:
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(
            f"UPDATE repos SET {sets} WHERE id=?",
            [*fields.values(), existing["id"]],
        )
        repo_id = int(existing["id"])
    else:
        fields["full_name"] = repo["full_name"]
        fields["first_seen"] = today
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        cur = conn.execute(
            f"INSERT INTO repos({cols}) VALUES({qs})",
            list(fields.values()),
        )
        repo_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO star_snapshots(repo_id, snap_date, stars) VALUES(?,?,?) "
        "ON CONFLICT(repo_id, snap_date) DO UPDATE SET stars=excluded.stars",
        (repo_id, today, int(repo.get("stars") or 0)),
    )
    return repo_id


def previous_stars(conn: sqlite3.Connection, repo_id: int, today: str) -> int | None:
    row = conn.execute(
        "SELECT stars FROM star_snapshots WHERE repo_id=? AND snap_date<? "
        "ORDER BY snap_date DESC LIMIT 1",
        (repo_id, today),
    ).fetchone()
    return None if row is None else int(row["stars"])


def upsert_digest_item(conn: sqlite3.Connection, item: dict) -> int:
    conn.execute(
        """
        INSERT INTO digest_items(
            digest_date, repo_id, source, rank, stars_today, stars_delta,
            summary_zh, why_useful, tags, score
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(digest_date, repo_id) DO UPDATE SET
            source=excluded.source,
            rank=excluded.rank,
            stars_today=excluded.stars_today,
            stars_delta=excluded.stars_delta,
            summary_zh=COALESCE(excluded.summary_zh, digest_items.summary_zh),
            why_useful=COALESCE(excluded.why_useful, digest_items.why_useful),
            tags=excluded.tags,
            score=COALESCE(excluded.score, digest_items.score)
        """,
        (
            item["digest_date"],
            item["repo_id"],
            item["source"],
            item.get("rank") or 0,
            item.get("stars_today") or 0,
            item.get("stars_delta") or 0,
            item.get("summary_zh"),
            item.get("why_useful"),
            json.dumps(item.get("tags") or [], ensure_ascii=False),
            item.get("score"),
        ),
    )
    row = conn.execute(
        "SELECT id FROM digest_items WHERE digest_date=? AND repo_id=?",
        (item["digest_date"], item["repo_id"]),
    ).fetchone()
    return int(row["id"])


def update_item_summary(conn: sqlite3.Connection, item_id: int, summary: dict) -> None:
    conn.execute(
        "UPDATE digest_items SET summary_zh=?, why_useful=?, tags=?, score=? WHERE id=?",
        (
            summary.get("summary_zh"),
            summary.get("why_useful"),
            json.dumps(summary.get("tags") or [], ensure_ascii=False),
            summary.get("score"),
            item_id,
        ),
    )


def update_repo_readme(conn: sqlite3.Connection, repo_id: int, excerpt: str) -> None:
    conn.execute("UPDATE repos SET readme_excerpt=? WHERE id=?", (excerpt, repo_id))


def set_repo_status(
    repo_id: int,
    status: str,
    notes: str | None = None,
    local_path: str | None = None,
    last_error: str | None = None,
) -> dict | None:
    with db() as conn:
        fields = ["status=?"]
        values: list[Any] = [status]
        if notes is not None:
            fields.append("notes=?")
            values.append(notes)
        if local_path is not None:
            fields.append("local_path=?")
            values.append(local_path)
        if last_error is not None:
            fields.append("last_error=?")
            values.append(last_error)
        values.append(repo_id)
        conn.execute(f"UPDATE repos SET {', '.join(fields)} WHERE id=?", values)
    return get_repo(repo_id)


def set_repo_link(
    repo_id: int,
    fork_full_name: str,
    fork_url: str,
    local_path: str | None = None,
    status: str | None = None,
) -> dict | None:
    today = today_iso()
    with db() as conn:
        fields = ["fork_full_name=?", "fork_url=?", "linked_at=?", "last_error=?"]
        values: list[Any] = [fork_full_name, fork_url, today, ""]
        if local_path is not None:
            fields.append("local_path=?")
            values.append(local_path)
        if status is not None:
            fields.append("status=?")
            values.append(status)
        values.append(repo_id)
        conn.execute(f"UPDATE repos SET {', '.join(fields)} WHERE id=?", values)
    return get_repo(repo_id)


def list_catalog_repos() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM repos
            WHERE status IN ('useful', 'downloaded')
               OR IFNULL(fork_full_name,'') != ''
            ORDER BY linked_at DESC, last_seen DESC, stars DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_repo(repo_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
    return dict(row) if row else None


def get_repo_by_full_name(full_name: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM repos WHERE full_name=?", (full_name,)).fetchone()
    return dict(row) if row else None


def list_dates() -> list[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT digest_date FROM digest_items ORDER BY digest_date DESC"
        ).fetchall()
    return [r["digest_date"] for r in rows]


def digest_exists(day: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM digest_items WHERE digest_date=? LIMIT 1", (day,)
        ).fetchone()
    return row is not None


def query_digest(
    day: str,
    q: str = "",
    language: str = "",
    source: str = "",
    status: str = "",
    min_stars: int = 0,
) -> list[dict]:
    sql = """
        SELECT
            d.id AS item_id,
            d.digest_date,
            d.source,
            d.rank,
            d.stars_today,
            d.stars_delta,
            d.summary_zh,
            d.why_useful,
            d.tags,
            d.score,
            r.id AS repo_id,
            r.full_name,
            r.owner,
            r.name,
            r.html_url,
            r.description,
            r.language,
            r.stars,
            r.forks,
            r.topics,
            r.license,
            r.homepage,
            r.created_at,
            r.updated_at,
            r.readme_excerpt,
            r.status,
            r.local_path,
            r.notes,
            r.last_error,
            r.first_seen,
            r.fork_full_name,
            r.fork_url,
            r.linked_at
        FROM digest_items d
        JOIN repos r ON r.id = d.repo_id
        WHERE d.digest_date = ?
    """
    params: list[Any] = [day]
    if q:
        sql += (
            " AND (r.full_name LIKE ? OR IFNULL(r.description,'') LIKE ? "
            "OR IFNULL(d.summary_zh,'') LIKE ? OR IFNULL(d.why_useful,'') LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if language:
        sql += " AND IFNULL(r.language,'') = ?"
        params.append(language)
    if source:
        sql += " AND d.source = ?"
        params.append(source)
    if status:
        sql += " AND r.status = ?"
        params.append(status)
    if min_stars:
        sql += " AND r.stars >= ?"
        params.append(int(min_stars))
    sql += " ORDER BY r.stars DESC, d.stars_today DESC, d.rank ASC"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["tags"] = _json_list(item.get("tags"))
        item["topics"] = _json_list(item.get("topics"))
        items.append(item)
    return items


def list_picked(status: str = "useful") -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM repos WHERE status=? ORDER BY last_seen DESC, stars DESC",
            (status,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats(day: str) -> dict:
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM digest_items WHERE digest_date=?", (day,)
        ).fetchone()["n"]
        useful = conn.execute(
            """
            SELECT COUNT(*) AS n FROM digest_items d
            JOIN repos r ON r.id=d.repo_id
            WHERE d.digest_date=? AND r.status='useful'
            """,
            (day,),
        ).fetchone()["n"]
        downloaded = conn.execute(
            """
            SELECT COUNT(*) AS n FROM digest_items d
            JOIN repos r ON r.id=d.repo_id
            WHERE d.digest_date=? AND r.status='downloaded'
            """,
            (day,),
        ).fetchone()["n"]
        skipped = conn.execute(
            """
            SELECT COUNT(*) AS n FROM digest_items d
            JOIN repos r ON r.id=d.repo_id
            WHERE d.digest_date=? AND r.status='skipped'
            """,
            (day,),
        ).fetchone()["n"]
        all_downloaded = conn.execute(
            "SELECT COUNT(*) AS n FROM repos WHERE status='downloaded'"
        ).fetchone()["n"]
        linked = conn.execute(
            "SELECT COUNT(*) AS n FROM repos WHERE IFNULL(fork_full_name,'') != ''"
        ).fetchone()["n"]
        langs = conn.execute(
            """
            SELECT IFNULL(r.language,'未知') AS language, COUNT(*) AS n
            FROM digest_items d JOIN repos r ON r.id=d.repo_id
            WHERE d.digest_date=?
            GROUP BY IFNULL(r.language,'未知')
            ORDER BY n DESC
            """,
            (day,),
        ).fetchall()
    return {
        "date": day,
        "total": total,
        "useful": useful,
        "downloaded": downloaded,
        "skipped": skipped,
        "all_downloaded": all_downloaded,
        "linked": linked,
        "languages": [dict(r) for r in langs],
    }


def today_iso() -> str:
    return date.today().isoformat()


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
