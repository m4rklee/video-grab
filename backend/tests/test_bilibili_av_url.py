from app.bilibili_legacy import extract_bilibili_aid, extract_bilibili_bvid, resolve_bilibili_bvid
from app.search_service import _normalize_bilibili_api_row


def test_extract_bilibili_aid():
    assert extract_bilibili_aid("http://www.bilibili.com/video/av676876181") == "676876181"
    assert extract_bilibili_bvid("https://www.bilibili.com/video/BV1xx411c7mD") == "BV1xx411c7mD"


def test_normalize_bilibili_prefers_bvid_url():
    row = _normalize_bilibili_api_row(
        {
            "arcurl": "http://www.bilibili.com/video/av116566721041492",
            "bvid": "BV1test12345",
            "aid": 116566721041492,
            "title": "瓜摊斗牛",
        },
        0,
    )
    assert row is not None
    assert row["url"] == "https://www.bilibili.com/video/BV1test12345"


def test_merge_legacy_keeps_1080_when_ytdlp_lists_1080(monkeypatch):
    from app.bilibili_legacy import build_legacy_format_entries, merge_legacy_formats_into_info

    def fake_playurl(bvid, cid, timeout=25.0):
        return {
            "dash": {
                "video": [{"id": 80, "codecid": 7, "height": 1080, "width": 1920, "bandwidth": 2_000_000, "baseUrl": "https://v.example/v"}],
                "audio": [{"bandwidth": 128_000, "baseUrl": "https://v.example/a"}],
            }
        }

    monkeypatch.setattr("app.bilibili_legacy.fetch_legacy_playurl_dash", fake_playurl)
    monkeypatch.setattr("app.bilibili_legacy.fetch_cid", lambda bvid: 1)
    monkeypatch.setattr("app.bilibili_legacy.resolve_bilibili_bvid", lambda url: "BV1test")

    info = {
        "formats": [
            {
                "format_id": "30080",
                "height": 1080,
                "vcodec": "avc1",
                "acodec": "none",
            }
        ]
    }
    merge_legacy_formats_into_info("https://www.bilibili.com/video/BV1test", info)
    ids = [f["format_id"] for f in info["formats"]]
    assert "bilibili_legacy|80" in ids
    assert "30080" in ids


def test_resolve_bilibili_bvid_from_aid(monkeypatch):
    def fake_get(url, **kwargs):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"code": 0, "data": {"bvid": "BVresolved001"}}

        return R()

    monkeypatch.setattr("app.bilibili_legacy.httpx.get", fake_get)
    assert (
        resolve_bilibili_bvid("http://www.bilibili.com/video/av676876181")
        == "BVresolved001"
    )
