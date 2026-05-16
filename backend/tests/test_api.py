from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient

from app import services
from app.main import app

client = TestClient(app)


class FakeYoutubeDL:
    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        if "fail" in url:
            raise RuntimeError("unsupported url")
        return {
            "title": "Demo Video",
            "webpage_url": url,
            "extractor_key": "Demo",
            "duration": 42.5,
            "thumbnail": "https://example.com/thumb.jpg",
            "formats": [
                {
                    "format_id": "v1",
                    "height": 1080,
                    "vcodec": "h264",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 1800,
                    "filesize_approx": 9_000_000,
                },
                {
                    "format_id": "v2",
                    "height": 720,
                    "vcodec": "h264",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 1200,
                    "filesize": 5_000_000,
                },
                {"format_id": "a1", "vcodec": "none", "acodec": "aac", "abr": 128, "ext": "m4a", "filesize": 1_000_000},
            ],
        }

    def download(self, urls):
        hooks = self.opts.get("progress_hooks", [])
        for hook in hooks:
            hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
            hook({"status": "finished"})
        output_dir = Path(self.opts["paths"]["home"])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "Demo Video [demo].mp4").write_bytes(b"video")


class FakeYoutubeDLBiliLow:
    """Simulates yt-dlp returning only ≤480p on Bilibili; legacy merge supplies higher tiers."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, url, download=False):
        if "fail" in url:
            raise RuntimeError("unsupported url")
        return {
            "title": "Bili Demo标题",
            "id": "BVTEST",
            "webpage_url": "https://www.bilibili.com/video/BVTEST/",
            "extractor_key": "Bilibili",
            "duration": 120.0,
            "thumbnail": "https://example.com/thumb.jpg",
            "formats": [
                {
                    "format_id": "30216",
                    "height": 360,
                    "vcodec": "h264",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 400,
                },
                {
                    "format_id": "30232",
                    "height": 480,
                    "vcodec": "h264",
                    "acodec": "none",
                    "ext": "mp4",
                    "tbr": 600,
                },
                {
                    "format_id": "ba30280",
                    "vcodec": "none",
                    "acodec": "aac",
                    "abr": 128,
                    "ext": "m4a",
                    "filesize": 500_000,
                },
            ],
        }

    def download(self, urls):
        raise AssertionError("legacy download path must not call yt-dlp.download")


def _fake_merge_legacy_for_tests(webpage_url: str, info: dict):
    fmts = list(info.get("formats") or [])
    fmts.extend(
        [
            {
                "format_id": "bilibili_legacy|80",
                "height": 1080,
                "width": 1920,
                "vcodec": "h264",
                "acodec": "none",
                "ext": "mp4",
                "tbr": 3500,
            },
            {
                "format_id": "bilibili_legacy|64",
                "height": 720,
                "width": 1280,
                "vcodec": "h264",
                "acodec": "none",
                "ext": "mp4",
                "tbr": 2000,
            },
        ]
    )
    info["formats"] = fmts


def test_probe_bilibili_adds_legacy_format_rows(monkeypatch):
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDLBiliLow)
    monkeypatch.setattr(services.bilibili_legacy, "merge_legacy_formats_into_info", _fake_merge_legacy_for_tests)

    response = client.post("/api/video/probe", json={"url": "https://www.bilibili.com/video/BVTEST/"})
    assert response.status_code == 200
    data = response.json()
    ids = [f["format_id"] for f in data["formats"]]
    assert ids[0] == "bilibili_legacy|80"
    assert "bilibili_legacy|64" in ids
    assert "bv*+ba/b" in ids
    assert data.get("recommended_format_id") == "bilibili_legacy|80"
    by_id = {f["format_id"]: f for f in data["formats"]}
    assert by_id["bilibili_legacy|80"]["filesize"] > by_id["bilibili_legacy|64"]["filesize"]


def _fake_subprocess_run_bili_legacy(cmd, **kwargs):
    out = Path(cmd[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"ffmpeg-muxed")
    return CompletedProcess(cmd, 0, "", "")


def test_download_bilibili_legacy_runs_ffmpeg_mux(monkeypatch):
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDLBiliLow)
    monkeypatch.setattr(
        services.bilibili_legacy,
        "resolve_legacy_mux_urls",
        lambda bvid, cid, qn: ("https://example.com/v.m4s", "https://example.com/a.m4s"),
    )
    monkeypatch.setattr(services.bilibili_legacy, "fetch_cid", lambda bvid: 123456789)
    monkeypatch.setattr(services.shutil, "which", lambda cmd: "/fake/ffmpeg" if cmd == "ffmpeg" else None)
    monkeypatch.setattr(services.subprocess, "run", _fake_subprocess_run_bili_legacy)

    created = client.post(
        "/api/downloads",
        json={"url": "https://www.bilibili.com/video/BVTEST/", "format_id": "bilibili_legacy|80"},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    status = client.get(f"/api/downloads/{job_id}")
    for _ in range(30):
        status = client.get(f"/api/downloads/{job_id}")
        if status.json()["status"] in ("completed", "failed"):
            break

    data = status.json()
    assert data["status"] == "completed"
    assert data["progress"] == 100
    file_response = client.get(data["download_url"])
    assert file_response.status_code == 200
    assert file_response.content == b"ffmpeg-muxed"


def test_health_reports_dependencies():
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "ytdlp_available" in data
    assert "ffmpeg_available" in data
    assert "summarize_llm_ready" in data


def test_probe_video_returns_recommended_formats(monkeypatch):
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDL)

    response = client.post("/api/video/probe", json={"url": "https://example.com/watch?v=demo"})

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Demo Video"
    assert data["duration"] == 42.5
    assert data["formats"][0]["format_id"] == "bv*+ba/b"
    assert data["formats"][0]["filesize"] == 10_000_000
    assert any(item["resolution"] == "1080p" for item in data["formats"])
    assert next(item for item in data["formats"] if item["resolution"] == "720p")["filesize"] == 6_000_000


def test_probe_rejects_invalid_url():
    response = client.post("/api/video/probe", json={"url": "not-a-url"})

    assert response.status_code == 422


def test_download_job_completes_and_serves_file(monkeypatch):
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDL)

    created = client.post(
        "/api/downloads",
        json={"url": "https://example.com/watch?v=demo", "format_id": "bv*+ba/b"},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    status = client.get(f"/api/downloads/{job_id}")
    for _ in range(30):
        status = client.get(f"/api/downloads/{job_id}")
        if status.json()["status"] == "completed":
            break

    data = status.json()
    assert data["status"] == "completed"
    assert data["progress"] == 100
    assert data["download_url"] == f"/api/downloads/{job_id}/file"

    file_response = client.get(data["download_url"])
    assert file_response.status_code == 200
    assert file_response.content == b"video"


def test_unknown_job_returns_404():
    response = client.get("/api/downloads/missing")

    assert response.status_code == 404


def test_normalize_video_url_douyin_modal_id():
    from app.services import normalize_video_url

    jingxuan = "https://www.douyin.com/jingxuan?modal_id=7633651322524273947"
    assert normalize_video_url(jingxuan) == "https://www.douyin.com/video/7633651322524273947"
    assert normalize_video_url("https://m.douyin.com/foo?modalId=1234567890123456789") == (
        "https://www.douyin.com/video/1234567890123456789"
    )
    assert normalize_video_url("https://www.douyin.com/note/x?aweme_id=99") == "https://www.douyin.com/video/99"


def test_extract_aweme_id_iesdouyin_share():
    from app.douyin_web import extract_aweme_id

    assert extract_aweme_id("https://www.iesdouyin.com/share/video/12345/") == "12345"


def test_extract_ms_token_from_embedded_json():
    from app.douyin_web import _extract_ms_token

    html = '<script>window._ROUTER_DATA = {"msToken":"abcdefghijklmnop"}</script>'
    assert _extract_ms_token(html) == "abcdefghijklmnop"
    assert _extract_ms_token("<html></html>") is None


def test_prefer_play_url_strips_wm_marker():
    from app.douyin_web import _prefer_play_url

    assert "playwm" not in _prefer_play_url("https://aweme.snssdk.com/aweme/v1/playwm/?video_id=x")


def test_probe_douyin_branch(monkeypatch):
    fake_aweme = {
        "aweme_id": "7633651322524273947",
        "desc": "演示",
        "video": {
            "duration": 65000,
            "height": 720,
            "play_addr": {
                "url_list": ["https://example.com/video.mp4"],
                "data_size": 4096,
                "height": 720,
            },
            "cover": {"url_list": ["https://example.com/cover.jpg"]},
        },
        "music": {"duration": 65},
    }

    monkeypatch.setattr("app.douyin_web.fetch_aweme_detail", lambda _aid: fake_aweme)

    response = client.post("/api/video/probe", json={"url": "https://www.douyin.com/video/7633651322524273947"})

    assert response.status_code == 200
    data = response.json()
    assert data["extractor"] == "DouyinWeb"
    assert data["formats"][0]["format_id"].startswith("douyin|")


def test_probe_douyin_falls_back_to_ytdlp_when_web_fails(monkeypatch):
    def _fail_probe(_url: str):
        raise RuntimeError("抖音 Web 不可用")

    monkeypatch.setattr(services.douyin_web, "fetch_probe_for_douyin_url", _fail_probe)
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDL)

    response = client.post("/api/video/probe", json={"url": "https://www.douyin.com/video/7633651322524273947"})

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Demo Video"
    assert data["extractor"] == "Demo"
    assert data["formats"][0]["format_id"] == "bv*+ba/b"


class _FakeStreamResp:
    headers = {"content-length": "4"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self, chunk_size=None):
        yield b"abcd"


def test_download_douyin_writes_file(monkeypatch):
    fake_aweme = {
        "aweme_id": "7633651322524273947",
        "desc": "演示",
        "video": {
            "duration": 1000,
            "height": 720,
            "play_addr": {
                "url_list": ["https://example.com/video.mp4"],
                "data_size": 4,
                "height": 720,
            },
        },
    }
    monkeypatch.setattr("app.douyin_web.fetch_aweme_detail", lambda _aid: fake_aweme)
    monkeypatch.setattr(services.httpx, "stream", lambda *a, **k: _FakeStreamResp())

    created = client.post(
        "/api/downloads",
        json={"url": "https://www.douyin.com/video/7633651322524273947", "format_id": "douyin|play_addr"},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    status = client.get(f"/api/downloads/{job_id}")
    for _ in range(30):
        status = client.get(f"/api/downloads/{job_id}")
        if status.json()["status"] == "completed":
            break

    data = status.json()
    assert data["status"] == "completed"
    file_response = client.get(data["download_url"])
    assert file_response.status_code == 200
    assert file_response.content == b"abcd"


def test_download_douyin_falls_back_to_ytdlp_when_web_fails(monkeypatch):
    monkeypatch.setattr(
        "app.douyin_web.fetch_aweme_detail",
        lambda _aid: (_ for _ in ()).throw(RuntimeError("抖音接口空响应")),
    )
    monkeypatch.setattr(services, "YoutubeDL", FakeYoutubeDL)

    created = client.post(
        "/api/downloads",
        json={"url": "https://www.douyin.com/video/7633651322524273947", "format_id": "douyin|play_addr"},
    )
    assert created.status_code == 200
    job_id = created.json()["job_id"]

    status = client.get(f"/api/downloads/{job_id}")
    for _ in range(30):
        status = client.get(f"/api/downloads/{job_id}")
        if status.json()["status"] in ("completed", "failed"):
            break

    data = status.json()
    assert data["status"] == "completed"
    file_response = client.get(data["download_url"])
    assert file_response.status_code == 200
    assert file_response.content == b"video"


def test_normalize_video_url_passthrough():
    from app.services import normalize_video_url

    other = "https://example.com/watch?v=demo"
    assert normalize_video_url(other) == other
    assert normalize_video_url("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"


def test_probe_douyin_jingxuan_normalizes_and_uses_web_branch(monkeypatch):
    fake_aweme = {
        "aweme_id": "7633651322524273947",
        "desc": "Demo",
        "video": {
            "duration": 5000,
            "height": 1080,
            "play_addr": {
                "url_list": ["https://example.com/v.mp4"],
                "data_size": 100,
                "height": 1080,
            },
        },
    }
    monkeypatch.setattr("app.douyin_web.fetch_aweme_detail", lambda _aid: fake_aweme)

    jingxuan = "https://www.douyin.com/jingxuan?modal_id=7633651322524273947"
    response = client.post("/api/video/probe", json={"url": jingxuan})

    assert response.status_code == 200
    data = response.json()
    assert data["extractor"] == "DouyinWeb"
    assert data["webpage_url"] == "https://www.douyin.com/video/7633651322524273947"


def test_image_proxy_rejects_non_image(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            class FakeResponse:
                headers = {"content-type": "text/html"}
                content = b"<html></html>"

                def raise_for_status(self):
                    return None

            return FakeResponse()

    monkeypatch.setattr("app.main.httpx.Client", FakeClient)

    response = client.get("/api/image-proxy", params={"url": "https://example.com/not-image"})

    assert response.status_code == 415


def test_summarize_requires_openai_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = client.post("/api/summarize", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert response.status_code == 422


def test_summarize_job_and_chat(monkeypatch, tmp_path):
    import app.summarize_service as ss

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    root = tmp_path / "summarize_root"
    root.mkdir()
    monkeypatch.setattr(ss, "SUMMARIZE_ROOT", root)
    with ss.SUMMARIZE_LOCK:
        ss.SUMMARIZE_JOBS.clear()

    class SyncExec:
        def submit(self, fn, *a, **k):
            fn(*a, **k)

            class _F:
                def cancel(self):
                    return None

            return _F()

    monkeypatch.setattr(ss, "SUMMARIZE_EXECUTOR", SyncExec())
    from app.transcript import TranscriptSegment

    monkeypatch.setattr(
        "app.transcript.fetch_transcript",
        lambda url: ([TranscriptSegment(0, 1000, "hello")], "mock"),
    )

    def fake_probe(url):
        return {
            "title": "Demo",
            "webpage_url": url,
            "extractor": "YouTube",
            "duration": 60.0,
            "thumbnail": None,
            "formats": [],
        }

    monkeypatch.setattr("app.services.probe_video", fake_probe)
    monkeypatch.setattr(
        "app.summarize_llm.summarize_from_transcript",
        lambda chunks_json, title: {
            "outline": "out",
            "key_points": ["k"],
            "segments": [{"t_start_ms": 0, "t_end_ms": 1000, "summary": "s"}],
            "mindmap": {"id": "r", "label": "R", "children": []},
        },
    )
    monkeypatch.setattr("app.summarize_llm.chat_about_video", lambda **kwargs: "助手回复")

    created = client.post("/api/summarize", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert created.status_code == 200
    job_id = created.json()["job_id"]
    st = client.get(f"/api/summarize/{job_id}")
    assert st.status_code == 200
    body = st.json()
    assert body["status"] == "completed"
    assert body["result"]["outline"] == "out"
    assert body["subtitle_source"] == "mock"

    chat = client.post(f"/api/summarize/{job_id}/chat", json={"message": "讲什么"})
    assert chat.status_code == 200
    assert chat.json()["reply"] == "助手回复"


def test_extract_youtube_video_id():
    from app.transcript import extract_youtube_video_id

    assert extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_search_empty_query():
    r = client.post("/api/search", json={"query": " ", "sources": ["youtube"]})
    assert r.status_code == 422


def test_search_requires_sources():
    r = client.post("/api/search", json={"query": "cats", "sources": []})
    assert r.status_code == 422


def test_search_mocked_stream(monkeypatch):
    import json

    def fake_stream(sources, query):
        yield json.dumps({"type": "meta", "sources": sources}, ensure_ascii=False) + "\n"
        yield json.dumps(
            {
                "type": "items",
                "source": "youtube",
                "items": [
                    {
                        "id": "youtube:test:1",
                        "title": "Hello",
                        "url": "https://www.youtube.com/watch?v=testid",
                        "thumbnail": None,
                        "duration": 12.0,
                        "uploader": "u",
                        "source": "youtube",
                        "extractor": "youtube",
                    }
                ],
            },
            ensure_ascii=False,
        ) + "\n"
        yield json.dumps({"type": "done", "total": 1}, ensure_ascii=False) + "\n"

    monkeypatch.setattr("app.main.iter_search_stream", fake_stream)
    with client.stream(
        "POST",
        "/api/search",
        json={"query": "cats", "sources": ["youtube", "bilibili"]},
    ) as r:
        assert r.status_code == 200
        assert "ndjson" in (r.headers.get("content-type") or "")
        events = [json.loads(line) for line in r.iter_lines() if line]
    assert events[0]["type"] == "meta"
    assert events[0]["sources"] == ["youtube", "bilibili"]
    items_ev = next(e for e in events if e["type"] == "items")
    assert items_ev["items"][0]["title"] == "Hello"
    assert events[-1]["total"] == 1


def test_batch_download_legacy_gone():
    r = client.post(
        "/api/downloads/batch",
        json={"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]},
    )
    assert r.status_code == 410


def test_download_batches_create_and_status(monkeypatch, tmp_path):
    from app import batch_download_service as bds

    monkeypatch.setattr(bds, "BATCH_ROOT", tmp_path / "batches")
    bds.BATCH_ROOT.mkdir(parents=True, exist_ok=True)

    def fake_create(url: str, format_id: str | None = None):
        jid = "api-job-1"
        work = tmp_path / jid
        work.mkdir(exist_ok=True)
        p = work / "v.mp4"
        p.write_bytes(b"1")
        job = services.DownloadJob(
            job_id=jid,
            status="completed",
            progress=100,
            filename="v.mp4",
            file_path=str(p),
            work_dir=work,
        )
        services.JOBS[jid] = job
        return job

    monkeypatch.setattr(bds, "create_download_job", fake_create)

    r = client.post(
        "/api/download-batches",
        json={
            "urls": ["https://www.youtube.com/watch?v=abc"],
            "quality_preset": "1080",
        },
    )
    assert r.status_code == 200
    batch_id = r.json()["batch_id"]
    assert batch_id

    r2 = client.get(f"/api/download-batches/{batch_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["quality_preset"] == "1080"
    assert body["counts"]["total"] == 1
    assert len(body["items"]) == 1

    r3 = client.get("/api/download-batches")
    assert r3.status_code == 200
    assert any(b["batch_id"] == batch_id for b in r3.json()["batches"])


def test_download_batches_validation():
    r = client.post("/api/download-batches", json={"urls": [], "quality_preset": "best"})
    assert r.status_code == 422
