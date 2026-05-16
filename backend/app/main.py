from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import HttpUrl

from .schemas import (
    DownloadJobResponse,
    DownloadRequest,
    HealthResponse,
    JobStatusResponse,
    ProbeRequest,
    ProbeResponse,
)
from .services import (
    check_ffmpeg,
    check_ytdlp,
    create_download_job,
    get_job,
    probe_video,
)

app = FastAPI(title="VideoGrab AI API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9280",
        "http://127.0.0.1:9280",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ffmpeg_available, ffmpeg_path = check_ffmpeg()
    return HealthResponse(
        status="ok",
        ytdlp_available=check_ytdlp(),
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=ffmpeg_path,
    )


@app.post("/api/video/probe", response_model=ProbeResponse)
def probe(request: ProbeRequest) -> ProbeResponse:
    try:
        return ProbeResponse(**probe_video(str(request.url)))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"无法解析该视频链接：{exc}") from exc


@app.post("/api/downloads", response_model=DownloadJobResponse)
def create_download(request: DownloadRequest) -> DownloadJobResponse:
    job = create_download_job(str(request.url), request.format_id)
    return DownloadJobResponse(job_id=job.job_id)


@app.get("/api/downloads/{job_id}", response_model=JobStatusResponse)
def download_status(job_id: str) -> JobStatusResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="下载任务不存在或已过期")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        progress=job.progress,
        filename=job.filename,
        error=job.error,
        download_url=f"/api/downloads/{job_id}/file" if job.status == "completed" else None,
    )


@app.get("/api/downloads/{job_id}/file")
def download_file(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="下载任务不存在或已过期")
    if job.status != "completed" or not job.file_path:
        raise HTTPException(status_code=409, detail="视频尚未下载完成")

    path = Path(job.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="下载文件已被清理")

    return FileResponse(path=path, filename=job.filename or path.name, media_type="application/octet-stream")


@app.get("/api/image-proxy")
def image_proxy(url: HttpUrl = Query(...)) -> Response:
    image_url = str(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=12) as client:
            response = client.get(
                image_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Referer": _referer_for_image(image_url),
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"封面图片加载失败：{exc}") from exc

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="目标地址不是图片")

    return Response(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _referer_for_image(url: str) -> str:
    if "hdslb.com" in url or "bilibili.com" in url:
        return "https://www.bilibili.com/"
    return "https://www.google.com/"
