import json

from app.search_service import iter_search_stream


def _parse_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().split("\n") if line.strip()]


def test_iter_search_stream_meta_items_done(monkeypatch):
    def fake_youtube(q: str, out_q):
        out_q.put(("items", "youtube", [{"id": "y:1", "title": "Y1", "url": "https://www.youtube.com/watch?v=a", "thumbnail": None, "duration": 1.0, "uploader": "u", "source": "youtube", "extractor": "youtube"}]))
        out_q.put(("done", "youtube", None))

    def fake_bilibili(q: str, out_q):
        out_q.put(("items", "bilibili", [{"id": "b:1", "title": "B1", "url": "https://www.bilibili.com/video/BV1", "thumbnail": None, "duration": 2.0, "uploader": "up", "source": "bilibili", "extractor": "bilibili"}]))
        out_q.put(("done", "bilibili", None))

    monkeypatch.setattr("app.search_service._stream_youtube_producer", fake_youtube)
    monkeypatch.setattr("app.search_service._stream_bilibili_producer", fake_bilibili)

    lines = _parse_lines("".join(iter_search_stream(["youtube", "bilibili"], "cats")))
    types = [e["type"] for e in lines]
    assert types[0] == "meta"
    assert types[-1] == "done"
    assert "items" in types
    assert lines[-1]["total"] == 2


def test_iter_search_stream_dedupes_urls(monkeypatch):
    dup = {
        "id": "y:1",
        "title": "Same",
        "url": "https://www.youtube.com/watch?v=dup",
        "thumbnail": None,
        "duration": None,
        "uploader": None,
        "source": "youtube",
        "extractor": "youtube",
    }

    def fake_youtube(q: str, out_q):
        out_q.put(("items", "youtube", [dup, dup]))
        out_q.put(("done", "youtube", None))

    def fake_bilibili(q: str, out_q):
        out_q.put(("done", "bilibili", None))

    monkeypatch.setattr("app.search_service._stream_youtube_producer", fake_youtube)
    monkeypatch.setattr("app.search_service._stream_bilibili_producer", fake_bilibili)

    lines = _parse_lines("".join(iter_search_stream(["youtube", "bilibili"], "x")))
    item_events = [e for e in lines if e["type"] == "items"]
    assert sum(len(e["items"]) for e in item_events) == 1
    assert lines[-1]["total"] == 1
