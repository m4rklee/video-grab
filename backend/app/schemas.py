from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ProbeRequest(BaseModel):
    url: HttpUrl


class DownloadRequest(BaseModel):
    url: HttpUrl
    format_id: str | None = Field(default=None, max_length=120)


class FormatOption(BaseModel):
    format_id: str
    label: str
    ext: str | None = None
    resolution: str | None = None
    filesize: int | None = None
    note: str | None = None


class ProbeResponse(BaseModel):
    title: str
    webpage_url: str
    extractor: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    formats: list[FormatOption]
    recommended_format_id: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    ytdlp_available: bool
    ffmpeg_available: bool
    ffmpeg_path: str | None
    summarize_llm_ready: bool = False


class DownloadJobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "downloading", "completed", "failed"]
    progress: float = 0
    filename: str | None = None
    error: str | None = None
    download_url: str | None = None


class SummarizeRequest(BaseModel):
    url: HttpUrl


class SummarizeJobResponse(BaseModel):
    job_id: str


class SummarizeStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    progress: float = 0
    error: str | None = None
    subtitle_source: str | None = None
    webpage_url: str | None = None
    result: dict[str, Any] | None = None


class SummarizeChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class SummarizeChatResponse(BaseModel):
    reply: str


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=400)
    sources: list[Literal["youtube", "bilibili"]] = Field(
        default_factory=lambda: ["youtube"],
        min_length=1,
        max_length=2,
    )


class SearchItem(BaseModel):
    id: str
    title: str
    url: str
    thumbnail: str | None = None
    duration: float | None = None
    uploader: str | None = None
    source: Literal["youtube", "bilibili"]
    extractor: str | None = None


class SearchResponse(BaseModel):
    items: list[SearchItem]
    total: int
    sources: list[Literal["youtube", "bilibili"]]
    warning: str | None = None


class BatchDownloadRequest(BaseModel):
    urls: list[HttpUrl] = Field(..., min_length=1, max_length=30)
    format_id: str | None = Field(default=None, max_length=120)


class BatchDownloadJobItem(BaseModel):
    url: str
    job_id: str | None = None
    error: str | None = None


class BatchDownloadResponse(BaseModel):
    jobs: list[BatchDownloadJobItem]


QualityPreset = Literal["best", "1080", "720", "480"]


class DownloadBatchCreateRequest(BaseModel):
    urls: list[HttpUrl] = Field(..., min_length=1, max_length=30)
    quality_preset: QualityPreset = "best"
    titles: dict[str, str] | None = None


class DownloadBatchItemStatus(BaseModel):
    url: str
    title: str
    job_id: str | None = None
    status: Literal["queued", "downloading", "completed", "failed"]
    progress: float = 0
    error: str | None = None
    filename: str | None = None
    download_url: str | None = None


class DownloadBatchCounts(BaseModel):
    total: int
    completed: int
    failed: int
    running: int


class DownloadBatchStatusResponse(BaseModel):
    batch_id: str
    quality_preset: QualityPreset
    status: Literal["queued", "running", "packaging", "completed", "failed", "partial"]
    progress: float = 0
    error: str | None = None
    zip_ready: bool = False
    zip_download_url: str | None = None
    counts: DownloadBatchCounts
    items: list[DownloadBatchItemStatus]
    created_at: float


class DownloadBatchListItem(BaseModel):
    batch_id: str
    quality_preset: QualityPreset
    status: Literal["queued", "running", "packaging", "completed", "failed", "partial"]
    progress: float = 0
    counts: DownloadBatchCounts
    created_at: float
    zip_ready: bool = False


class DownloadBatchListResponse(BaseModel):
    batches: list[DownloadBatchListItem]


class DownloadBatchCreateResponse(BaseModel):
    batch_id: str
