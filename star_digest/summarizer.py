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
    body = desc or para
    if body and _mostly_cjk(body):
        summary = body
    else:
        summary = f"{kind}，主要语言 {lang}。中文摘要还没生成，点「用 DeepSeek 写中文摘要」。"
    why_parts = []
    if stars_today:
        why_parts.append(f"今日热度 +{stars_today}")
    if item.get("source", "").startswith("trending"):
        why_parts.append("出现在 GitHub 趋势榜")
    elif item.get("source") == "rising":
        why_parts.append("近几天新仓库里星标靠前")
    if repo.get("stars"):
        why_parts.append(f"累计 {repo['stars']:,} 星")
    why_parts.append("先看中文摘要，再决定要不要下载")
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


def _batch_payload(batch: list[dict]) -> list[dict]:
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
                "readme": (row.get("readme_excerpt") or "")[:400],
            }
        )
    return payload


def _summary_prompt(batch: list[dict]) -> str:
    return (
        "为每个仓库写简体中文简报。只返回 JSON 数组，不要 markdown。"
        "正文必须中文。只有项目名、语言名、专有名词可以保留英文，不要整句英文简介。"
        "字段：full_name, summary_zh（40-70字，说清它做什么）, "
        "why_useful（一句中文，值不值得下载）, tags（2-4个中文标签）, score（1-5）。\n"
        f"{json.dumps(_batch_payload(batch), ensure_ascii=False)}"
    )


def _parse_summaries(text: str) -> dict[str, dict]:
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


def summarize_with_deepseek(batch: list[dict], settings: dict) -> dict[str, dict]:
    api_key = (settings.get("deepseek_api_key") or "").strip()
    if not api_key:
        return {}
    from openai import OpenAI

    model = "deepseek-v4-flash"
    base_url = settings.get("deepseek_base_url") or "https://api.deepseek.com"
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=25.0)
    kwargs = dict(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "只输出 JSON 数组。摘要、理由、标签一律简体中文。",
            },
            {"role": "user", "content": _summary_prompt(batch)},
        ],
        stream=False,
        temperature=0.2,
        max_tokens=700,
        extra_body={"thinking": {"type": "disabled"}},
    )
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception:
        kwargs.pop("extra_body", None)
        resp = client.chat.completions.create(**kwargs)
    text = (resp.choices[0].message.content or "").strip()
    return _parse_summaries(text)


def summarize_with_grok(batch: list[dict], settings: dict) -> dict[str, dict]:
    api_key = (settings.get("xai_api_key") or "").strip()
    if not api_key:
        return {}
    from openai import OpenAI

    model = settings.get("xai_model") or "grok-4.5"
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    resp = client.responses.create(model=model, input=_summary_prompt(batch))
    text = getattr(resp, "output_text", "") or ""
    return _parse_summaries(text)


def _chunks(rows: list[dict], size: int) -> list[list[dict]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _run_batches(rows: list[dict], settings: dict, fn, on_batch=None) -> dict[str, dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ai_map: dict[str, dict] = {}
    batches = _chunks(rows, 6)
    workers = min(3, max(1, len(batches)))
    if workers == 1:
        for i, chunk in enumerate(batches, start=1):
            part = fn(chunk, settings)
            ai_map.update(part)
            if on_batch:
                on_batch(i, len(batches), part)
        return ai_map
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, chunk, settings): idx for idx, chunk in enumerate(batches, start=1)}
        done = 0
        for fut in as_completed(futures):
            part = fut.result()
            ai_map.update(part)
            done += 1
            if on_batch:
                on_batch(done, len(batches), part)
    return ai_map


def merge_summary(row: dict, extra: dict | None) -> dict:
    base = heuristic_summary(row, row)
    extra = extra or {}
    if extra.get("summary_zh"):
        base["summary_zh"] = extra["summary_zh"]
    if extra.get("why_useful"):
        base["why_useful"] = extra["why_useful"]
    if extra.get("tags"):
        base["tags"] = extra["tags"]
    if extra.get("score"):
        base["score"] = extra["score"]
    return base


def summarize_items(rows: Iterable[dict], settings: dict, on_batch=None) -> list[dict]:
    rows = list(rows)
    ai_map: dict[str, dict] = {}
    if settings.get("deepseek_api_key"):
        try:
            ai_map = _run_batches(rows, settings, summarize_with_deepseek, on_batch=on_batch)
        except Exception:
            ai_map = {}
    if not ai_map and settings.get("xai_api_key"):
        try:
            ai_map = _run_batches(rows, settings, summarize_with_grok, on_batch=on_batch)
        except Exception:
            ai_map = {}
    return [merge_summary(row, ai_map.get((row.get("full_name") or "").lower())) for row in rows]


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
