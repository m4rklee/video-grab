from pathlib import Path

from app import batch_download_service as bds
from app import services
from app.batch_download_service import (
    DownloadBatch,
    BatchItem,
    _package_batch_zip,
    create_download_batch,
    get_batch,
    on_child_job_updated,
    resolve_format_for_url,
)
from app.services import DownloadJob


def test_resolve_format_youtube_and_bilibili():
    yt = "https://www.youtube.com/watch?v=abc"
    assert resolve_format_for_url(yt, "best") == "bv*+ba/b"
    assert "1080" in resolve_format_for_url(yt, "1080")
    assert "720" in resolve_format_for_url(yt, "720")
    assert "480" in resolve_format_for_url(yt, "480")
    bili = "https://www.bilibili.com/video/BV1xx411c7mD"
    assert resolve_format_for_url(bili, "720") == "bilibili_legacy|64"
    assert resolve_format_for_url(bili, "480") == "bilibili_legacy|32"
    assert resolve_format_for_url("https://www.douyin.com/video/7123456789", "1080") is None


def test_create_batch_and_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "BATCH_ROOT", tmp_path / "batches")
    bds.BATCH_ROOT.mkdir(parents=True, exist_ok=True)

    job_ids: list[str] = []

    def fake_create(url: str, format_id: str | None = None):
        jid = f"job-{len(job_ids)}"
        work = tmp_path / jid
        work.mkdir()
        video = work / "clip.mp4"
        video.write_bytes(b"fake")
        job = DownloadJob(
            job_id=jid,
            status="completed",
            progress=100,
            filename="clip.mp4",
            file_path=str(video),
            work_dir=work,
        )
        services.JOBS[jid] = job
        job_ids.append(jid)
        return job

    monkeypatch.setattr(bds, "create_download_job", fake_create)

    batch = create_download_batch(
        ["https://www.youtube.com/watch?v=a", "https://www.bilibili.com/video/BV1"],
        "720",
        titles={"https://www.youtube.com/watch?v=a": "YT Clip"},
    )
    assert batch.batch_id
    assert len(batch.items) == 2
    assert all(i.job_id for i in batch.items)

    loaded = get_batch(batch.batch_id)
    assert loaded is not None
    assert loaded.status in ("completed", "partial", "running")
    if loaded.zip_path:
        assert Path(loaded.zip_path).is_file()


def test_on_child_job_updated_triggers_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(bds, "BATCH_ROOT", tmp_path / "batches")
    bds.BATCH_ROOT.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "j1"
    work.mkdir()
    video = work / "a.mp4"
    video.write_bytes(b"x")
    jid = "j1-test"
    services.JOBS[jid] = DownloadJob(
        job_id=jid,
        status="completed",
        progress=100,
        filename="a.mp4",
        file_path=str(video),
        work_dir=work,
    )

    batch_id = "batch-test-1"
    batch = DownloadBatch(
        batch_id=batch_id,
        quality_preset="best",
        items=[BatchItem(url="https://youtu.be/x", title="T", job_id=jid, status="completed")],
        work_dir=tmp_path / "b1",
    )
    batch.work_dir.mkdir(parents=True, exist_ok=True)
    bds._BATCHES[batch_id] = batch
    bds._JOB_TO_BATCH[jid] = batch_id

    on_child_job_updated(jid)
    assert batch.zip_path
    assert Path(batch.zip_path).is_file()


def test_package_zip_dedup_names(tmp_path):
    work = tmp_path / "batch"
    work.mkdir()
    f1 = work / "same.mp4"
    f2 = work / "other.mp4"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")
    batch = DownloadBatch(
        batch_id="z",
        quality_preset="best",
        work_dir=work,
        items=[
            BatchItem(url="u1", title="same.mp4", job_id="a", status="completed", filename="same.mp4"),
            BatchItem(url="u2", title="same.mp4", job_id="b", status="completed", filename="same.mp4"),
        ],
    )
    j1 = DownloadJob(job_id="a", status="completed", file_path=str(f1), filename="same.mp4", work_dir=work)
    j2 = DownloadJob(job_id="b", status="completed", file_path=str(f2), filename="same.mp4", work_dir=work)
    services.JOBS["a"] = j1
    services.JOBS["b"] = j2
    bds._JOB_TO_BATCH["a"] = "z"
    bds._JOB_TO_BATCH["b"] = "z"

    _package_batch_zip(batch)
    import zipfile

    with zipfile.ZipFile(batch.zip_path) as zf:
        names = zf.namelist()
    assert len(names) == 2
    assert names[0] != names[1]
