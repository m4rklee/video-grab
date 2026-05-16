from pathlib import Path
import json
import os

from dotenv import load_dotenv

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND_ROOT.parent
# Do not override keys already set (e.g. CI). Load backend/.env first so it wins over repo-root .env.
load_dotenv(_BACKEND_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / ".env")

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import HttpUrl

from .schemas import (
    BatchDownloadRequest,
    DownloadBatchCreateRequest,
    DownloadBatchCreateResponse,
    DownloadBatchListResponse,
    DownloadBatchStatusResponse,
    DownloadJobResponse,
    DownloadRequest,
    HealthResponse,
    JobStatusResponse,
    ProbeRequest,
    ProbeResponse,
    SearchRequest,
    SummarizeChatRequest,
    SummarizeChatResponse,
    SummarizeJobResponse,
    SummarizeRequest,
    SummarizeStatusResponse,
)
from .batch_download_service import (
    batch_to_payload,
    create_download_batch,
    get_batch,
    get_batch_zip_path,
    list_batches,
)
from . import summarize_llm
from .bilibili_cookie import bilibili_cookie_var
from .llm_runtime import openai_api_key_var, openai_base_url_var, summarize_model_var
from .search_service import iter_search_stream
from .services import (
    check_ffmpeg,
    check_ytdlp,
    create_download_job,
    get_job,
    probe_video,
)
from .summarize_service import (
    create_summarize_job,
    get_summarize_job,
    summarize_chat,
    summarize_job_payload,
)

app = FastAPI(title="拾影视频下载器 API (Video Grab)", version="0.1.0")

# Browsers may call the API from any localhost port (e.g. vite) or from origins listed in CORS_ORIGINS.
_cors_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_extra,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_runtime_settings_from_headers(request: Request, call_next):
    bili = (request.headers.get("X-Bilibili-Cookie") or "").strip() or None
    api_key = (request.headers.get("X-OpenAI-Api-Key") or "").strip() or None
    base_url = (request.headers.get("X-OpenAI-Base-Url") or "").strip() or None
    model = (request.headers.get("X-Summarize-Model") or "").strip() or None
    t_bili = bilibili_cookie_var.set(bili)
    t_key = openai_api_key_var.set(api_key)
    t_base = openai_base_url_var.set(base_url)
    t_model = summarize_model_var.set(model)
    try:
        return await call_next(request)
    finally:
        bilibili_cookie_var.reset(t_bili)
        openai_api_key_var.reset(t_key)
        openai_base_url_var.reset(t_base)
        summarize_model_var.reset(t_model)


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    ffmpeg_available, ffmpeg_path = check_ffmpeg()
    return HealthResponse(
        status="ok",
        ytdlp_available=check_ytdlp(),
        ffmpeg_available=ffmpeg_available,
        ffmpeg_path=ffmpeg_path,
        summarize_llm_ready=summarize_llm.llm_configured(),
    )


@app.post("/api/search")
def search(body: SearchRequest) -> StreamingResponse:
    q = (body.query or "").strip()
    if not q:
        raise HTTPException(status_code=422, detail="搜索关键词不能为空")
    if not body.sources:
        raise HTTPException(status_code=422, detail="请至少选择一个搜索源")

    def generate():
        try:
            yield from iter_search_stream(body.sources, q)
        except ValueError as exc:
            yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False) + "\n"
        except Exception as exc:
            yield json.dumps(
                {"type": "error", "message": f"搜索失败：{exc}"},
                ensure_ascii=False,
            ) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/downloads/batch", deprecated=True)
def batch_download_legacy(body: BatchDownloadRequest) -> None:
    raise HTTPException(
        status_code=410,
        detail="请使用 POST /api/download-batches 创建批量下载任务",
    )


@app.post("/api/download-batches", response_model=DownloadBatchCreateResponse)
def create_download_batch_route(body: DownloadBatchCreateRequest) -> DownloadBatchCreateResponse:
    try:
        batch = create_download_batch(
            [str(u) for u in body.urls],
            body.quality_preset,
            body.titles,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DownloadBatchCreateResponse(batch_id=batch.batch_id)


@app.get("/api/download-batches", response_model=DownloadBatchListResponse)
def list_download_batches(limit: int = Query(default=20, ge=1, le=50)) -> DownloadBatchListResponse:
    batches = list_batches(limit)
    items = []
    for b in batches:
        p = batch_to_payload(b)
        items.append(
            {
                "batch_id": p["batch_id"],
                "quality_preset": p["quality_preset"],
                "status": p["status"],
                "progress": p["progress"],
                "counts": p["counts"],
                "created_at": p["created_at"],
                "zip_ready": p["zip_ready"],
            }
        )
    return DownloadBatchListResponse(batches=items)


@app.get("/api/download-batches/{batch_id}", response_model=DownloadBatchStatusResponse)
def download_batch_status(batch_id: str) -> DownloadBatchStatusResponse:
    batch = get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批量任务不存在或已过期")
    return DownloadBatchStatusResponse(**batch_to_payload(batch))


@app.get("/api/download-batches/{batch_id}/zip")
def download_batch_zip(batch_id: str) -> FileResponse:
    batch = get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="批量任务不存在或已过期")
    path = get_batch_zip_path(batch_id)
    if path is None:
        raise HTTPException(status_code=409, detail="ZIP 尚未就绪")
    return FileResponse(
        path=path,
        filename=batch.zip_filename or path.name,
        media_type="application/zip",
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


@app.post("/api/summarize", response_model=SummarizeJobResponse)
def create_summarize(request: SummarizeRequest) -> SummarizeJobResponse:
    try:
        job = create_summarize_job(str(request.url))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SummarizeJobResponse(job_id=job.job_id)


@app.get("/api/summarize/{job_id}", response_model=SummarizeStatusResponse)
def summarize_status(job_id: str) -> SummarizeStatusResponse:
    job = get_summarize_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="总结任务不存在或已过期")
    return SummarizeStatusResponse(**summarize_job_payload(job))


@app.post("/api/summarize/{job_id}/chat", response_model=SummarizeChatResponse)
def summarize_chat_route(job_id: str, body: SummarizeChatRequest) -> SummarizeChatResponse:
    try:
        reply = summarize_chat(job_id, body.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SummarizeChatResponse(reply=reply)


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
