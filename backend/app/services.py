from __future__ import annotations

import contextvars
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL

from . import bilibili_legacy, douyin_web
from .bilibili_cookie import resolve_bilibili_cookie

BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_ROOT = BASE_DIR / "downloads"
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

EXECUTOR = ThreadPoolExecutor(max_workers=2)
JOBS_LOCK = threading.Lock()


@dataclass
class DownloadJob:
    job_id: str
    status: str = "queued"
    progress: float = 0
    filename: str | None = None
    file_path: str | None = None
    error: str | None = None
    work_dir: Path = field(default_factory=Path)


JOBS: dict[str, DownloadJob] = {}


def normalize_video_url(url: str) -> str:
    """Rewrite share links to shapes yt-dlp extractors expect (e.g. Douyin modal_id pages)."""
    text = url.strip()
    if not text:
        return text
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    host = (parsed.hostname or "").lower()
    if "douyin.com" not in host:
        return text
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("modal_id", "modalId", "aweme_id", "awemeId"):
        values = query.get(key)
        if not values:
            continue
        raw = (values[0] or "").strip()
        if raw.isdigit():
            return f"https://www.douyin.com/video/{raw}"
    return text


def check_ffmpeg() -> tuple[bool, str | None]:
    path = shutil.which("ffmpeg")
    return path is not None, path


def check_ytdlp() -> bool:
    return YoutubeDL is not None


def _format_size(size: int | None) -> str | None:
    if not size:
        return None
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _format_filesize(format_info: dict[str, Any]) -> int | None:
    size = format_info.get("filesize") or format_info.get("filesize_approx")
    return size if isinstance(size, int) and size > 0 else None


def _format_bitrate(format_info: dict[str, Any]) -> float:
    return float(format_info.get("tbr") or format_info.get("vbr") or format_info.get("abr") or 0)


def _best_audio_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    audio_formats = [
        f
        for f in formats
        if f.get("acodec") != "none" and f.get("vcodec") == "none" and f.get("format_id")
    ]
    if not audio_formats:
        return None
    return max(audio_formats, key=_format_bitrate)


def _estimate_merged_size(
    formats: list[dict[str, Any]],
    height: int | None,
    best_audio: dict[str, Any] | None,
) -> int | None:
    video_formats = [
        f
        for f in formats
        if f.get("vcodec") != "none"
        and f.get("format_id")
        and isinstance(f.get("height"), int)
        and (height is None or f.get("height") <= height)
    ]
    if not video_formats:
        return None

    best_video = max(video_formats, key=lambda item: (item.get("height") or 0, _format_bitrate(item)))
    video_size = _format_filesize(best_video)
    audio_size = _format_filesize(best_audio) if best_audio else None

    if video_size and audio_size:
        return video_size + audio_size
    if video_size:
        return video_size
    return None


def _estimate_bilibili_legacy_mux_bytes(lf: dict[str, Any], duration_sec: float | int | None) -> int | None:
    """Rough muxed size for bilibili_legacy: lf['tbr'] is (video_bps+audio_bps)/1000 from classic DASH."""
    if duration_sec is None:
        return None
    try:
        dur = float(duration_sec)
    except (TypeError, ValueError):
        return None
    if dur <= 0:
        return None
    try:
        kbps = float(lf.get("tbr") or 0)
    except (TypeError, ValueError):
        return None
    if kbps <= 0:
        return None
    total_bps = kbps * 1000.0
    return int(dur * total_bps / 8.0)


def _recommended_format_id(formats: list[dict[str, Any]], webpage_url: str) -> str | None:
    if not formats:
        return None
    host = (urlparse(webpage_url).hostname or "").lower()
    if "bilibili.com" not in host:
        return formats[0]["format_id"]
    for prefer in ("bilibili_legacy|80", "bilibili_legacy|116", "bilibili_legacy|112"):
        if any(f["format_id"] == prefer for f in formats):
            return prefer
    for f in formats:
        fid = str(f.get("format_id") or "")
        if fid.startswith("bilibili_legacy|"):
            return fid
    return formats[0]["format_id"]


def _build_format_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    formats = info.get("formats") or []
    legacy_fmts = [
        f
        for f in formats
        if isinstance(f.get("format_id"), str) and str(f["format_id"]).startswith("bilibili_legacy|")
    ]
    base_formats = [f for f in formats if f not in legacy_fmts]

    best_audio = _best_audio_format(base_formats)
    heights = sorted(
        {
            f.get("height")
            for f in base_formats
            if f.get("vcodec") != "none" and isinstance(f.get("height"), int)
        },
        reverse=True,
    )

    options: list[dict[str, Any]] = []

    if legacy_fmts:
        legacy_sorted = sorted(legacy_fmts, key=lambda x: int(x.get("height") or 0), reverse=True)
        seen_h: set[int] = set()
        first_rec = True
        for lf in legacy_sorted:
            h = int(lf.get("height") or 0)
            if h in seen_h:
                continue
            seen_h.add(h)
            size_est = _estimate_bilibili_legacy_mux_bytes(lf, info.get("duration"))
            label = f'{h}p · H.264（高清解析）'
            note = "classic playurl DASH 合并音视频，推荐用于 1080p；登录 Cookie 可提高成功率"
            opt = {
                "format_id": str(lf["format_id"]),
                "label": label,
                "ext": "mp4",
                "resolution": f"{h}p",
                "filesize": size_est,
                "note": note,
            }
            if first_rec:
                opt["label"] = f"推荐 · {label}"
                first_rec = False
            options.append(opt)

    options.append(
        {
            "format_id": "bv*+ba/b",
            "label": "自动画质",
            "ext": "mp4",
            "resolution": "自动选择",
            "filesize": _estimate_merged_size(base_formats, None, best_audio),
            "note": "按 yt-dlp 默认解析（部分稿件匿名仅到 480p）",
        }
    )

    for height in heights[:4]:
        estimated_size = _estimate_merged_size(base_formats, height, best_audio)
        options.append(
            {
                "format_id": f"bv*[height<={height}]+ba/b[height<={height}]/b",
                "label": f"{height}p 视频",
                "ext": "mp4",
                "resolution": f"{height}p",
                "filesize": estimated_size,
                "note": "兼顾清晰度与体积，体积为预估值" if estimated_size else "兼顾清晰度与体积",
            }
        )

    if best_audio:
        audio_size = _format_filesize(best_audio)
        size_text = _format_size(audio_size)
        options.append(
            {
                "format_id": "ba",
                "label": "仅音频",
                "ext": best_audio.get("ext"),
                "resolution": "Audio",
                "filesize": audio_size,
                "note": f"适合播客/课程保存{f'，约 {size_text}' if size_text else ''}",
            }
        )

    seen: set[str] = set()
    unique_options: list[dict[str, Any]] = []
    for option in options:
        if option["format_id"] in seen:
            continue
        seen.add(option["format_id"])
        unique_options.append(option)
    return unique_options


def _prepare_video_url(url: str) -> str:
    return normalize_video_url(douyin_web.resolve_share_redirect(url.strip()))


def _build_douyin_ui_format_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    raw = info.get("formats") or []
    options: list[dict[str, Any]] = []
    for item in raw:
        if not item.get("url"):
            continue
        fid = str(item["format_id"])
        height = item.get("height")
        br = float(item.get("tbr") or 0)
        label = f"{height}p" if isinstance(height, int) else fid
        if "download_addr" in fid:
            label = f"{label} · 带水印" if isinstance(height, int) else "带水印"
        note = f"抖音直链（约 {br:.0f} kbps）" if br else "抖音直链"
        size = item.get("filesize")
        if isinstance(size, int) and size > 0:
            note = f"{note} · {_format_size(size)}"
        options.append(
            {
                "format_id": f"douyin|{fid}",
                "label": label,
                "ext": "mp4",
                "resolution": f"{height}p" if isinstance(height, int) else str(fid),
                "filesize": size if isinstance(size, int) else None,
                "note": note,
            }
        )
    if options:
        options[0]["label"] = f"推荐 · {options[0]['label']}"
    return options


def probe_video(url: str) -> dict[str, Any]:
    url = _prepare_video_url(url)
    if douyin_web.is_douyin_video_url(url):
        try:
            inner = douyin_web.fetch_probe_for_douyin_url(url)
        except Exception as web_exc:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
            }
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as ydl_exc:
                raise RuntimeError(
                    f"抖音 Web 接口解析失败：{web_exc}。"
                    f"若浏览器能打开该视频，可为 yt-dlp 配置 Cookie 后再试。yt-dlp 回退：{ydl_exc}"
                ) from web_exc
            return {
                "title": info.get("title") or "Untitled video",
                "webpage_url": info.get("webpage_url") or url,
                "extractor": info.get("extractor_key") or info.get("extractor"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "formats": _build_format_options(info),
            }
        return {
            "title": inner["title"],
            "webpage_url": inner["webpage_url"],
            "extractor": inner["extractor"],
            "duration": inner["duration"],
            "thumbnail": inner["thumbnail"],
            "formats": _build_douyin_ui_format_options(inner),
        }

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    ck = resolve_bilibili_cookie()
    if ck:
        ydl_opts["http_headers"] = {"Cookie": ck}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    webpage = info.get("webpage_url") or url
    host = (urlparse(webpage).hostname or "").lower()
    if "bilibili.com" in host:
        bilibili_legacy.merge_legacy_formats_into_info(webpage, info)

    formats = _build_format_options(info)
    return {
        "title": info.get("title") or "Untitled video",
        "webpage_url": webpage,
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats,
        "recommended_format_id": _recommended_format_id(formats, webpage),
    }


def create_download_job(url: str, format_id: str | None) -> DownloadJob:
    url = _prepare_video_url(url)
    job_id = uuid.uuid4().hex
    work_dir = DOWNLOAD_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    job = DownloadJob(job_id=job_id, work_dir=work_dir)
    with JOBS_LOCK:
        JOBS[job_id] = job
    ctx = contextvars.copy_context()
    EXECUTOR.submit(ctx.run, _run_download, job_id, url, format_id)
    return job


def get_job(job_id: str) -> DownloadJob | None:
    with JOBS_LOCK:
        return JOBS.get(job_id)


def _set_job(job_id: str, **updates: Any) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        for key, value in updates.items():
            setattr(job, key, value)
    try:
        from .batch_download_service import on_child_job_updated

        on_child_job_updated(job_id)
    except Exception:
        pass


def _progress_hook(job_id: str):
    def hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            progress = min(99.0, round(downloaded / total * 100, 2)) if total else 5.0
            _set_job(job_id, status="downloading", progress=progress)
        elif status == "finished":
            _set_job(job_id, status="downloading", progress=99.0)

    return hook


def _pick_downloaded_file(work_dir: Path) -> Path | None:
    candidates = [
        path
        for path in work_dir.iterdir()
        if path.is_file() and not path.name.endswith((".part", ".ytdl", ".temp"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _run_douyin_download(job_id: str, url: str, format_id: str | None, work_dir: Path) -> None:
    aweme_id = douyin_web.extract_aweme_id(url)
    if not aweme_id:
        raise RuntimeError("无法识别抖音作品 ID")

    detail = douyin_web.fetch_aweme_detail(aweme_id)
    media_url = douyin_web.pick_download_url(detail, format_id)
    title = (detail.get("desc") or "douyin").replace("/", "_").strip()[:120] or "douyin"
    ext = "mp4"
    out_path = work_dir / f"{title} [{aweme_id}].{ext}"
    headers = douyin_web.media_request_headers(aweme_id, media_url)
    with httpx.stream(
        "GET",
        media_url,
        headers=headers,
        follow_redirects=True,
        timeout=httpx.Timeout(120.0, connect=30.0),
        trust_env=False,
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        with open(out_path, "wb") as handle:
            for chunk in response.iter_bytes(256 * 1024):
                if not chunk:
                    continue
                handle.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    _set_job(job_id, progress=min(99.0, round(downloaded / total * 100, 2)))
                else:
                    _set_job(job_id, progress=min(99.0, downloaded / (50 * 1024 * 1024) * 99))

    _set_job(
        job_id,
        status="completed",
        progress=100.0,
        filename=out_path.name,
        file_path=str(out_path),
    )


def _sanitize_download_title_fragment(raw: str, fallback: str) -> str:
    text = (raw or fallback).strip() or fallback
    for ch in '<>:"/\\|?*\x00':
        text = text.replace(ch, "_")
    return text[:120]


def _run_bilibili_legacy_download(job_id: str, url: str, format_id: str, work_dir: Path) -> None:
    parts = format_id.split("|", 1)
    if len(parts) != 2 or parts[0] != "bilibili_legacy":
        raise RuntimeError("无效的 bilibili_legacy 格式 ID")
    qn = int(parts[1])
    bvid = bilibili_legacy.resolve_bilibili_bvid(url)
    if not bvid:
        raise RuntimeError("无法识别 Bilibili BV 号")
    cid = bilibili_legacy.fetch_cid(bvid)
    if cid is None:
        raise RuntimeError("无法获取稿件 cid")

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("未检测到 ffmpeg，无法合并音视频")

    meta_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    with YoutubeDL(meta_opts) as ydl:
        meta = ydl.extract_info(url, download=False)
    title_fragment = _sanitize_download_title_fragment(str(meta.get("title") or ""), "bilibili")
    vid = meta.get("id") or bvid
    out_path = work_dir / f"{title_fragment} [{vid}]_{qn}.mp4"

    vu, au = bilibili_legacy.resolve_legacy_mux_urls(bvid, cid, qn)
    hdr = bilibili_legacy.ffmpeg_headers_arg_for_bilibili(bvid)

    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-headers",
        hdr,
        "-i",
        vu,
        "-headers",
        hdr,
        "-i",
        au,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    _set_job(job_id, progress=10.0)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        err_tail = (proc.stderr or "")[-1200:]
        raise RuntimeError(err_tail.strip() or "ffmpeg 合并音视频失败")

    if not out_path.is_file():
        raise RuntimeError("ffmpeg 已完成但未生成输出文件")

    _set_job(
        job_id,
        status="completed",
        progress=100.0,
        filename=out_path.name,
        file_path=str(out_path),
    )


def _run_download(job_id: str, url: str, format_id: str | None) -> None:
    job = get_job(job_id)
    if job is None:
        return

    if format_id and format_id.startswith("bilibili_legacy|"):
        try:
            _set_job(job_id, status="downloading", progress=1.0)
            _run_bilibili_legacy_download(job_id, url, format_id, job.work_dir)
        except Exception as exc:
            _set_job(job_id, status="failed", error=str(exc), progress=0)
        return

    ydl_format = format_id or "bv*+ba/b"
    if douyin_web.is_douyin_video_url(url):
        try:
            _set_job(job_id, status="downloading", progress=1.0)
            _run_douyin_download(job_id, url, format_id, job.work_dir)
            return
        except Exception:
            if format_id and format_id.startswith("douyin|"):
                ydl_format = "bv*+ba/b"

    output_template = str(job.work_dir / "%(title).120B [%(id)s].%(ext)s")
    ydl_opts = {
        "format": ydl_format,
        "outtmpl": output_template,
        "paths": {"home": str(job.work_dir)},
        "restrictfilenames": False,
        "windowsfilenames": True,
        "trim_file_name": 160,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_progress_hook(job_id)],
        "merge_output_format": "mp4",
    }

    try:
        _set_job(job_id, status="downloading", progress=1.0)
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        file_path = _pick_downloaded_file(job.work_dir)
        if file_path is None:
            raise RuntimeError("下载已结束，但没有找到生成的视频文件")

        _set_job(
            job_id,
            status="completed",
            progress=100.0,
            filename=file_path.name,
            file_path=str(file_path),
        )
    except Exception as exc:  # yt-dlp raises many extractor-specific exceptions
        _set_job(job_id, status="failed", error=str(exc), progress=0)
