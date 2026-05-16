"""Batch download jobs: quality presets, child job aggregation, ZIP packaging."""

from __future__ import annotations

import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from . import douyin_web
from .services import DOWNLOAD_ROOT, create_download_job, get_job

QualityPreset = Literal["best", "1080", "720", "480"]
BatchStatus = Literal["queued", "running", "packaging", "completed", "failed", "partial"]
ItemStatus = Literal["queued", "downloading", "completed", "failed"]

BATCH_ROOT = DOWNLOAD_ROOT / "batches"
BATCH_ROOT.mkdir(parents=True, exist_ok=True)

MAX_BATCH_ITEMS = 30
LIST_BATCH_LIMIT = 20

_BATCH_LOCK = threading.Lock()
_BATCHES: dict[str, DownloadBatch] = {}
_JOB_TO_BATCH: dict[str, str] = {}


@dataclass
class BatchItem:
    url: str
    title: str
    job_id: str | None = None
    status: ItemStatus = "queued"
    progress: float = 0.0
    error: str | None = None
    filename: str | None = None


@dataclass
class DownloadBatch:
    batch_id: str
    quality_preset: QualityPreset
    status: BatchStatus = "queued"
    progress: float = 0.0
    items: list[BatchItem] = field(default_factory=list)
    zip_path: str | None = None
    zip_filename: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    work_dir: Path = field(default_factory=Path)


def _url_label(url: str) -> str:
    u = url.strip()
    if len(u) <= 72:
        return u
    return u[:69] + "..."


def _host_kind(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "bilibili.com" in host:
        return "bilibili"
    if douyin_web.is_douyin_video_url(url):
        return "douyin"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return "generic"


def resolve_format_for_url(url: str, preset: QualityPreset) -> str | None:
    """Map abstract quality preset to per-platform format_id / yt-dlp selector."""
    kind = _host_kind(url)
    if kind == "bilibili":
        qn = {"best": 80, "1080": 80, "720": 64, "480": 32}[preset]
        return f"bilibili_legacy|{qn}"
    if kind == "douyin":
        return None
    if preset == "best":
        return "bv*+ba/b"
    height = {"1080": 1080, "720": 720, "480": 480}[preset]
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"


def _map_job_status(job_status: str) -> ItemStatus:
    if job_status == "completed":
        return "completed"
    if job_status == "failed":
        return "failed"
    if job_status == "downloading":
        return "downloading"
    return "queued"


def _sync_item_from_job(item: BatchItem) -> None:
    if not item.job_id:
        return
    job = get_job(item.job_id)
    if job is None:
        item.status = "failed"
        item.error = item.error or "子任务不存在"
        return
    item.status = _map_job_status(job.status)
    item.progress = float(job.progress or 0)
    item.error = job.error
    item.filename = job.filename
    if job.filename and item.title == _url_label(item.url):
        item.title = job.filename


def _aggregate_batch(batch: DownloadBatch) -> None:
    if not batch.items:
        batch.progress = 0.0
        batch.status = "failed"
        batch.error = "无下载项"
        return

    for item in batch.items:
        _sync_item_from_job(item)

    total = len(batch.items)
    completed = sum(1 for i in batch.items if i.status == "completed")
    failed = sum(1 for i in batch.items if i.status == "failed")
    active = total - completed - failed

    if active > 0:
        batch.status = "running"
    elif completed == 0:
        batch.status = "failed"
        batch.error = batch.error or "全部下载失败"
    elif failed > 0:
        batch.status = "partial"
    else:
        batch.status = "completed"

    progress_sum = 0.0
    for item in batch.items:
        if item.status == "completed":
            progress_sum += 100.0
        elif item.status == "failed":
            progress_sum += 0.0
        else:
            progress_sum += min(99.0, max(0.0, item.progress))
    batch.progress = round(progress_sum / total, 2)


def _package_batch_zip(batch: DownloadBatch) -> None:
    if batch.zip_path and Path(batch.zip_path).is_file():
        return
    batch.status = "packaging"
    batch.progress = max(batch.progress, 99.0)
    success_files: list[tuple[str, Path]] = []
    for item in batch.items:
        if item.status != "completed" or not item.job_id:
            continue
        job = get_job(item.job_id)
        if not job or not job.file_path:
            continue
        path = Path(job.file_path)
        if path.is_file():
            arcname = item.filename or path.name
            success_files.append((arcname, path))

    if not success_files:
        batch.status = "failed" if batch.status != "partial" else "partial"
        batch.error = batch.error or "没有可打包的成功文件"
        return

    zip_name = f"batch_{batch.batch_id[:12]}.zip"
    zip_path = batch.work_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used_names: set[str] = set()
        for arcname, path in success_files:
            name = arcname
            base, ext = Path(name).stem, Path(name).suffix
            n = 1
            while name in used_names:
                name = f"{base}_{n}{ext}"
                n += 1
            used_names.add(name)
            zf.write(path, arcname=name)

    batch.zip_path = str(zip_path)
    batch.zip_filename = zip_name
    batch.progress = 100.0
    if batch.status == "packaging":
        failed = any(i.status == "failed" for i in batch.items)
        batch.status = "partial" if failed else "completed"


def on_child_job_updated(job_id: str) -> None:
    batch_id = _JOB_TO_BATCH.get(job_id)
    if not batch_id:
        return
    with _BATCH_LOCK:
        batch = _BATCHES.get(batch_id)
        if batch is None:
            return
        _aggregate_batch(batch)
        terminal = all(i.status in ("completed", "failed") for i in batch.items)
        needs_zip = terminal and batch.status in ("completed", "partial") and not batch.zip_path
        if needs_zip and batch.status != "packaging":
            try:
                _package_batch_zip(batch)
            except Exception as exc:
                batch.error = str(exc)
                if batch.status == "packaging":
                    batch.status = "partial" if any(
                        i.status == "completed" for i in batch.items
                    ) else "failed"


def create_download_batch(
    urls: list[str],
    quality_preset: QualityPreset,
    titles: dict[str, str] | None = None,
) -> DownloadBatch:
    if not urls:
        raise ValueError("至少选择一个视频")
    if len(urls) > MAX_BATCH_ITEMS:
        raise ValueError(f"单次最多 {MAX_BATCH_ITEMS} 个视频")
    if quality_preset not in ("best", "1080", "720", "480"):
        raise ValueError("无效的画质档位")

    batch_id = uuid.uuid4().hex
    work_dir = BATCH_ROOT / batch_id
    work_dir.mkdir(parents=True, exist_ok=True)

    items: list[BatchItem] = []
    for raw in urls:
        url = raw.strip()
        if not url:
            continue
        title = (titles or {}).get(url) or _url_label(url)
        items.append(BatchItem(url=url, title=title))

    batch = DownloadBatch(
        batch_id=batch_id,
        quality_preset=quality_preset,
        status="running",
        items=items,
        work_dir=work_dir,
    )

    with _BATCH_LOCK:
        _BATCHES[batch_id] = batch

    for item in batch.items:
        try:
            fmt = resolve_format_for_url(item.url, quality_preset)
            job = create_download_job(item.url, fmt)
            item.job_id = job.job_id
            item.status = "queued"
            _JOB_TO_BATCH[job.job_id] = batch_id
        except Exception as exc:
            item.status = "failed"
            item.error = str(exc)

    with _BATCH_LOCK:
        _aggregate_batch(batch)

    for item in batch.items:
        if item.job_id:
            on_child_job_updated(item.job_id)

    return batch


def get_batch(batch_id: str) -> DownloadBatch | None:
    with _BATCH_LOCK:
        batch = _BATCHES.get(batch_id)
        if batch is None:
            return None
        _aggregate_batch(batch)
        terminal = all(i.status in ("completed", "failed") for i in batch.items)
        if (
            terminal
            and batch.status in ("completed", "partial")
            and not batch.zip_path
            and batch.status != "packaging"
        ):
            try:
                _package_batch_zip(batch)
            except Exception as exc:
                batch.error = str(exc)
        return batch


def list_batches(limit: int = LIST_BATCH_LIMIT) -> list[DownloadBatch]:
    with _BATCH_LOCK:
        batches = sorted(_BATCHES.values(), key=lambda b: b.created_at, reverse=True)
        out: list[DownloadBatch] = []
        for batch in batches[:limit]:
            _aggregate_batch(batch)
            out.append(batch)
        return out


def batch_to_payload(batch: DownloadBatch) -> dict[str, Any]:
    completed = sum(1 for i in batch.items if i.status == "completed")
    failed = sum(1 for i in batch.items if i.status == "failed")
    running = len(batch.items) - completed - failed
    return {
        "batch_id": batch.batch_id,
        "quality_preset": batch.quality_preset,
        "status": batch.status,
        "progress": batch.progress,
        "error": batch.error,
        "zip_ready": bool(batch.zip_path and Path(batch.zip_path).is_file()),
        "zip_download_url": (
            f"/api/download-batches/{batch.batch_id}/zip" if batch.zip_path else None
        ),
        "counts": {
            "total": len(batch.items),
            "completed": completed,
            "failed": failed,
            "running": running,
        },
        "items": [
            {
                "url": it.url,
                "title": it.title,
                "job_id": it.job_id,
                "status": it.status,
                "progress": it.progress,
                "error": it.error,
                "filename": it.filename,
                "download_url": (
                    f"/api/downloads/{it.job_id}/file"
                    if it.job_id and it.status == "completed"
                    else None
                ),
            }
            for it in batch.items
        ],
        "created_at": batch.created_at,
    }


def get_batch_zip_path(batch_id: str) -> Path | None:
    batch = get_batch(batch_id)
    if batch is None or not batch.zip_path:
        return None
    path = Path(batch.zip_path)
    return path if path.is_file() else None
