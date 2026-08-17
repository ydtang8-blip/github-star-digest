from __future__ import annotations

import json
import re
from typing import Iterable

KIND_RULES = (
    ("模型", ("llm", "model", "transformer", "diffusion", "checkpoint", "inference")),
    ("框架", ("framework", "sdk", "library", "toolkit", "engine", "runtime")),
    ("工具", ("cli", "tool", "utility", "desktop", "app", "extension", "plugin")),
    ("教程", ("awesome", "guide", "tutorial", "course", "learn", "example")),
    ("数据", ("dataset", "benchmark", "corpus")),
    ("运维", ("devops", "k8s", "kubernetes", "docker", "observability")),
)


def first_paragraph(text: str) -> str:
    if not text:
        return ""
    cleaned = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if cleaned:
                break
            continue
        if s.startswith("#") or s.startswith("![") or s.startswith("<"):
            continue
        if s.startswith("[!") or s.startswith("<!--"):
            continue
        cleaned.append(re.sub(r"[#*_`]+", "", s))
        if sum(len(x) for x in cleaned) > 160:
            break
    return re.sub(r"\s+", " ", " ".join(cleaned)).strip()


def classify(text: str) -> str:
    blob = text.lower()
    for kind, keys in KIND_RULES:
        if any(k in blob for k in keys):
            return kind
    return "项目"


def heuristic_summary(repo: dict, item: dict) -> dict:
    desc = (repo.get("description") or "").strip()
    para = first_paragraph(repo.get("readme_excerpt") or "")
    kind = classify(" ".join([desc, para, repo.get("full_name") or ""]))
    lang = repo.get("language") or "多语言"
    stars_today = int(item.get("stars_today") or 0)
    body = desc or para or "暂无简介，打开仓库主页看 README。"
    if not _mostly_cjk(body):
        summary = f"{kind}（{lang}）。{body}"
    else:
        summary = body
    why_parts = []
    if stars_today:
        why_parts.append(f"今日热度 +{stars_today}")
    if item.get("source", "").startswith("trending"):
        why_parts.append("出现在 GitHub 趋势榜")
    elif item.get("source") == "rising":
        why_parts.append("近几天新仓库里星标靠前")
    if repo.get("stars"):
        why_parts.append(f"累计 {repo['stars']:,} star")
    why_parts.append(f"适合先看 README 再决定是否本地下载")
    tags = [kind, lang]
    if item.get("source") == "trending-zh":
        tags.append("中文社区")
    return {
        "summary_zh": summary[:220],
        "why_useful": "；".join(why_parts),
        "tags": tags[:4],
        "score": _score(stars_today, int(repo.get("stars") or 0), item.get("source") or ""),
    }


def _score(stars_today: int, stars: int, source: str) -> int:
    score = 2
    if stars_today >= 200:
        score += 2
    elif stars_today >= 50:
        score += 1
    if stars >= 10000:
        score += 1
    if source.startswith("trending"):
        score += 1
    return max(1, min(5, score))


def _mostly_cjk(text: str) -> bool:
    if not text:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk >= max(4, len(text) * 0.2)


def summarize_with_grok(batch: list[dict], settings: dict) -> dict[str, dict]:
    api_key = (settings.get("xai_api_key") or "").strip()
    if not api_key:
        return {}
    from openai import OpenAI

    model = settings.get("xai_model") or "grok-4.5"
    payload = []
    for row in batch:
        payload.append(
            {
                "full_name": row["full_name"],
                "description": row.get("description") or "",
                "language": row.get("language") or "",
                "stars": row.get("stars") or 0,
                "stars_today": row.get("stars_today") or 0,
                "source": row.get("source") or "",
                "readme": (row.get("readme_excerpt") or "")[:2500],
            }
        )
    prompt = (
        "你是一名每天筛选开源项目的工程师。为每个仓库写中文简报，供用户决定要不要本地下载学习。"
        "只返回 JSON 数组，不要 markdown。每项字段：\n"
        "full_name, summary_zh（80-140字，说清它解决什么问题、和同类差在哪）,"
        "why_useful（一句话，值不值得本地下载、适合谁）,"
        "tags（2-4个中文短标签）,"
        "score（1-5，对个人开发者的实用度）。\n"
        f"仓库列表：{json.dumps(payload, ensure_ascii=False)}"
    )
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.responses.create(model=model, input=prompt)
    text = getattr(resp, "output_text", "") or ""
    data = _extract_json_array(text)
    out: dict[str, dict] = {}
    for item in data:
        name = (item.get("full_name") or "").strip()
        if not name:
            continue
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        out[name.lower()] = {
            "summary_zh": (item.get("summary_zh") or "").strip(),
            "why_useful": (item.get("why_useful") or "").strip(),
            "tags": tags[:4],
            "score": _safe_score(item.get("score")),
        }
    return out


def summarize_items(rows: Iterable[dict], settings: dict) -> list[dict]:
    rows = list(rows)
    ai_map: dict[str, dict] = {}
    if settings.get("xai_api_key"):
        chunk: list[dict] = []
        for row in rows:
            chunk.append(row)
            if len(chunk) >= 8:
                try:
                    ai_map.update(summarize_with_grok(chunk, settings))
                except Exception:
                    pass
                chunk = []
        if chunk:
            try:
                ai_map.update(summarize_with_grok(chunk, settings))
            except Exception:
                pass
    results = []
    for row in rows:
        base = heuristic_summary(row, row)
        extra = ai_map.get((row.get("full_name") or "").lower())
        if extra:
            if extra.get("summary_zh"):
                base["summary_zh"] = extra["summary_zh"]
            if extra.get("why_useful"):
                base["why_useful"] = extra["why_useful"]
            if extra.get("tags"):
                base["tags"] = extra["tags"]
            if extra.get("score"):
                base["score"] = extra["score"]
        results.append(base)
    return results


def _safe_score(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, n))


def _extract_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
