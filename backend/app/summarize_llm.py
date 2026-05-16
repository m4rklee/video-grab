"""OpenAI-compatible chat API for structured summarize + follow-up Q&A."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .llm_runtime import llm_configured, resolve_openai_config

_JSON_INSTRUCTION = """你会收到 transcript_chunks：JSON 数组，每项含 t_start_ms、t_end_ms（毫秒，闭区间语义）、text。
请据此输出 JSON（不要 markdown 围栏），字段：
{
  "outline": "整体大纲，3-8 句中文",
  "key_points": ["要点1", "要点2", "..."],
  "segments": [
    {"t_start_ms": 12345, "t_end_ms": 89012, "summary": "该合并时段讲了什么"}
  ],
  "mindmap": {"id": "root", "label": "视频主题", "children": [{"id": "n1", "label": "分支1", "children": []}]}
}

硬性规则（违反则视为错误输出）：
1. segments 至少 3 段、至多 15 段；每段 summary 中文精炼。
2. 时间轴只允许「合并相邻 chunk」：每一段的 t_start_ms 必须等于它所覆盖的第一个 chunk 的 t_start_ms（该数值必须原样出现在输入里）；t_end_ms 必须等于它所覆盖的最后一个 chunk 的 t_end_ms（也必须原样出现在输入里）。段的时长应反映真实内容跨度，长短不一很正常。
3. 严禁编造整齐的模板刻度（典型错误：0–60000、60000–120000、每分钟一段），除非输入 chunk 边界恰好如此。
4. mindmap 至少两层。
5. **全覆盖**：segments 按时间排序后，第一段必须从 transcript_chunks 里全局最小的 t_start_ms 开始；最后一段必须以全局最大的 t_end_ms 结束——必须概括到最后一条 chunk，禁止在未覆盖末尾 chunk 的情况下提前结束时间轴。"""


def _openai_config() -> tuple[str, str, str]:
    return resolve_openai_config()


def _post_chat(messages: list[dict[str, str]], *, json_mode: bool, temperature: float | None = None) -> str:
    api_key, base, model = _openai_config()
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.3 if temperature is None else temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    r = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180.0,
    )
    r.raise_for_status()
    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("LLM 无返回 choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    if not content.strip():
        raise RuntimeError("LLM 返回空内容")
    return content.strip()


def _parse_json_loose(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if m:
        raw = m.group(1).strip()
    return json.loads(raw)


def _normalize_segments_cover_chunks(transcript_chunks_json: str, data: dict[str, Any]) -> None:
    """若模型漏掉末尾 chunk，补齐到最后一块 t_end_ms（基于真实转写，避免 UI 停在倒数几分钟）。"""
    try:
        chunks = json.loads(transcript_chunks_json)
    except json.JSONDecodeError:
        return
    if not chunks or not isinstance(chunks, list):
        return
    ends = [int(c["t_end_ms"]) for c in chunks if isinstance(c, dict) and "t_end_ms" in c]
    starts = [int(c["t_start_ms"]) for c in chunks if isinstance(c, dict) and "t_start_ms" in c]
    if not ends:
        return
    global_end = max(ends)
    global_start = min(starts) if starts else 0

    segs = data.get("segments")
    if not isinstance(segs, list) or not segs:
        return
    ordered = sorted(
        [s for s in segs if isinstance(s, dict) and "t_start_ms" in s and "t_end_ms" in s],
        key=lambda x: int(x.get("t_start_ms") or 0),
    )
    if not ordered:
        return

    first_start = int(ordered[0].get("t_start_ms") or 0)
    if first_start > global_start + 3000:
        head = [c for c in chunks if isinstance(c, dict) and int(c.get("t_end_ms") or 0) <= first_start]
        texts_h = " ".join((c.get("text") or "").strip() for c in head if (c.get("text") or "").strip())
        if len(texts_h) > 1200:
            texts_h = texts_h[:1200].rsplit(" ", 1)[0] + "…"
        ordered.insert(
            0,
            {
                "t_start_ms": global_start,
                "t_end_ms": first_start,
                "summary": (f"（开场）{texts_h}") if texts_h else "（开场）片头或对白带入，详见转写前段。",
            },
        )

    last_end = int(ordered[-1].get("t_end_ms") or 0)
    if last_end >= global_end - 1500:
        data["segments"] = ordered
        return

    tail = [c for c in chunks if isinstance(c, dict) and int(c.get("t_end_ms") or 0) > last_end]
    if not tail:
        ordered[-1]["t_end_ms"] = global_end
        data["segments"] = ordered
        return

    start_tail = min(int(c["t_start_ms"]) for c in tail)
    blob = " ".join((c.get("text") or "").strip() for c in tail if (c.get("text") or "").strip())
    if len(blob) > 1600:
        blob = blob[:1600].rsplit(" ", 1)[0] + "…"
    ordered.append(
        {
            "t_start_ms": start_tail,
            "t_end_ms": global_end,
            "summary": (f"（片尾）{blob}") if blob else "（片尾）配乐或收尾对白较短。",
        }
    )
    data["segments"] = ordered


_BOOKEND_MARKERS: tuple[tuple[str, str], ...] = (
    ("（片尾）", "片尾"),
    ("（开场）", "开场"),
)


def _clean_subtitle_snippet(text: str) -> str:
    t = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"\s+", " ", t).strip()


def _summarize_bookend_snippet(snippet: str, video_title: str, label_cn: str) -> str:
    """把兜底拼接的外语字幕改成与其它分段一致的中文综述。"""
    snippet = _clean_subtitle_snippet(snippet)
    if len(snippet) < 40:
        return f"（{label_cn}）{snippet}"
    sys_msg = (
        "你将收到一段外语（或其它语种）视频字幕节选，请用流畅的中文写出大意（约 3–10 句），"
        "语气与同视频的其它分段摘要一致；不要复述台词语种；不要添加括号标签或小标题。"
    )
    user_msg = f"视频标题：{video_title}\n\n【{label_cn}节选字幕】\n{snippet[:14000]}"
    messages = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_msg}]
    body = _post_chat(messages, json_mode=False, temperature=0.2).strip()
    return f"（{label_cn}）{body}"


def _rewrite_bookend_summaries_zh(data: dict[str, Any], video_title: str) -> None:
    """开场/片尾补齐段与其它 JSON 分段同源模型摘要（首轮 JSON 未覆盖时间轴时的兜底）。"""
    if not llm_configured():
        return
    segs = data.get("segments")
    if not isinstance(segs, list):
        return
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        summ = str(seg.get("summary") or "")
        for marker, label_cn in _BOOKEND_MARKERS:
            if not summ.startswith(marker):
                continue
            raw = summ[len(marker) :].strip()
            try:
                seg["summary"] = _summarize_bookend_snippet(raw, video_title, label_cn)
            except Exception:
                seg["summary"] = f"{marker}{raw}"[:6000]
            break


def summarize_from_transcript(transcript_chunks_json: str, video_title: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _JSON_INSTRUCTION},
        {
            "role": "user",
            "content": f"视频标题：{video_title}\n\ntranscript_chunks:\n{transcript_chunks_json}",
        },
    ]
    raw = _post_chat(messages, json_mode=True)
    data = _parse_json_loose(raw)
    for key in ("outline", "key_points", "segments", "mindmap"):
        if key not in data:
            raise RuntimeError(f"LLM JSON 缺少字段: {key}")
    if not isinstance(data["key_points"], list):
        raise RuntimeError("key_points 必须为数组")
    if not isinstance(data["segments"], list):
        raise RuntimeError("segments 必须为数组")
    if not isinstance(data["mindmap"], dict):
        raise RuntimeError("mindmap 必须为对象")
    _normalize_segments_cover_chunks(transcript_chunks_json, data)
    _rewrite_bookend_summaries_zh(data, video_title)
    return data


def chat_about_video(
    *,
    video_title: str,
    outline: str,
    transcript_excerpt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> str:
    sys = (
        "你是视频学习助手。根据提供的标题、大纲与转写节选回答用户；"
        "若问题超出节选范围请说明并基于大纲合理推断，勿编造具体未出现的数字与引用。"
    )
    ctx = f"标题：{video_title}\n\n大纲：{outline}\n\n转写节选：\n{transcript_excerpt[:24_000]}"
    messages: list[dict[str, str]] = [{"role": "system", "content": sys + "\n\n" + ctx}]
    for turn in history[-12:]:
        role = turn.get("role") or "user"
        content = turn.get("content") or ""
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return _post_chat(messages, json_mode=False)
