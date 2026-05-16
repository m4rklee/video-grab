from pathlib import Path

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


def test_health_reports_dependencies():
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "ytdlp_available" in data
    assert "ffmpeg_available" in data


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
