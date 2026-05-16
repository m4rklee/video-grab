from typing import Literal

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


class HealthResponse(BaseModel):
    status: Literal["ok"]
    ytdlp_available: bool
    ffmpeg_available: bool
    ffmpeg_path: str | None


class DownloadJobResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "downloading", "completed", "failed"]
    progress: float = 0
    filename: str | None = None
    error: str | None = None
    download_url: str | None = None
