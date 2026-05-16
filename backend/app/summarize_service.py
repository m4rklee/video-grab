"""Async summarize jobs: transcript -> LLM structured result + optional chat."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import summarize_llm, transcript

BASE_DIR = Path(__file__).resolve().parent.parent
SUMMARIZE_ROOT = BASE_DIR / "summarize_jobs"
SUMMARIZE_ROOT.mkdir(parents=True, exist_ok=True)

SUMMARIZE_EXECUTOR = ThreadPoolExecutor(max_workers=2)
SUMMARIZE_LOCK = threading.Lock()
SUMMARIZE_JOBS: dict[str, SummarizeJob] = {}


@dataclass
class SummarizeJob:
    job_id: str
    work_dir: Path
    status: str = "queued"
    progress: float = 0.0
    error: str | None = None
    result: dict[str, Any] | None = None
    subtitle_source: str | None = None
    webpage_url: str | None = None


def get_summarize_job(job_id: str) -> SummarizeJob | None:
    with SUMMARIZE_LOCK:
        return SUMMARIZE_JOBS.get(job_id)


def _set_summarize_job(job_id: str, **updates: Any) -> None:
    with SUMMARIZE_LOCK:
        job = SUMMARIZE_JOBS[job_id]
        for k, v in updates.items():
            setattr(job, k, v)


def create_summarize_job(url: str) -> SummarizeJob:
    if not summarize_llm.llm_configured():
        raise RuntimeError("未配置 OPENAI_API_KEY，无法生成 AI 总结")
    job_id = uuid.uuid4().hex
    work_dir = SUMMARIZE_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    job = SummarizeJob(job_id=job_id, work_dir=work_dir)
    with SUMMARIZE_LOCK:
        SUMMARIZE_JOBS[job_id] = job
    SUMMARIZE_EXECUTOR.submit(_run_summarize, job_id, url.strip())
    return job


def _run_summarize(job_id: str, url: str) -> None:
    from .services import probe_video

    try:
        _set_summarize_job(job_id, status="running", progress=5.0)
        info = probe_video(url)
        _set_summarize_job(
            job_id,
            progress=15.0,
            webpage_url=info.get("webpage_url") or url,
        )
        segs, sub_src = transcript.fetch_transcript(url)
        plain = transcript.segments_to_plain_text(segs)
        chunks_json = transcript.segments_to_llm_chunks_json(segs)
        with SUMMARIZE_LOCK:
            wd = SUMMARIZE_JOBS[job_id].work_dir
        (wd / "transcript.txt").write_text(plain, encoding="utf-8")
        _set_summarize_job(job_id, progress=45.0, subtitle_source=sub_src)

        title = (info.get("title") or "视频").strip()
        result = summarize_llm.summarize_from_transcript(chunks_json, title)
        result["title"] = title
        result["webpage_url"] = info.get("webpage_url") or url
        result["subtitle_source"] = sub_src
        result["duration"] = info.get("duration")

        with SUMMARIZE_LOCK:
            wd = SUMMARIZE_JOBS[job_id].work_dir
        out_path = wd / "result.json"
        out_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        _set_summarize_job(job_id, status="completed", progress=100.0, result=result, error=None)
    except Exception as exc:
        _set_summarize_job(job_id, status="failed", progress=0.0, error=str(exc), result=None)


def summarize_chat(job_id: str, user_message: str) -> str:
    if not user_message.strip():
        raise ValueError("消息不能为空")
    if not summarize_llm.llm_configured():
        raise RuntimeError("未配置 OPENAI_API_KEY")
    job = get_summarize_job(job_id)
    if job is None:
        raise RuntimeError("总结任务不存在")
    if job.status != "completed":
        raise RuntimeError("总结尚未完成，无法对话")
    work_dir = job.work_dir
    res_path = work_dir / "result.json"
    if not res_path.exists() and job.result:
        res_path.write_text(json.dumps(job.result, ensure_ascii=False), encoding="utf-8")
    if not res_path.exists():
        raise RuntimeError("缺少总结结果文件")
    result = json.loads(res_path.read_text(encoding="utf-8"))
    tx_path = work_dir / "transcript.txt"
    excerpt = tx_path.read_text(encoding="utf-8", errors="replace") if tx_path.exists() else ""
    hist_path = work_dir / "chat.json"
    hist: list[dict[str, str]] = []
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            hist = []
    reply = summarize_llm.chat_about_video(
        video_title=str(result.get("title") or ""),
        outline=str(result.get("outline") or ""),
        transcript_excerpt=excerpt,
        history=hist,
        user_message=user_message.strip(),
    )
    hist.append({"role": "user", "content": user_message.strip()})
    hist.append({"role": "assistant", "content": reply})
    hist_path.write_text(json.dumps(hist, ensure_ascii=False), encoding="utf-8")
    return reply


def summarize_job_payload(job: SummarizeJob) -> dict[str, Any]:
    """API payload; reload result from disk if in-memory result was cleared."""
    result = job.result
    if job.status == "completed" and result is None:
        p = job.work_dir / "result.json"
        if p.exists():
            result = json.loads(p.read_text(encoding="utf-8"))
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "error": job.error,
        "subtitle_source": job.subtitle_source,
        "webpage_url": job.webpage_url,
        "result": result,
    }
