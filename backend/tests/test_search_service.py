from app.search_service import (
    _normalize_bilibili_api_row,
    _parse_duration,
    _strip_html,
)


def test_strip_html_removes_keyword_em_tags():
    raw = '黑马<em class="keyword">测试</em>教程'
    assert _strip_html(raw) == "黑马测试教程"


def test_parse_duration_colon_formats():
    assert _parse_duration("12:34") == 12 * 60 + 34
    assert _parse_duration("1:02:03") == 3600 + 2 * 60 + 3
    assert _parse_duration(90) == 90.0


def test_normalize_bilibili_api_row_maps_metadata():
    row = {
        "title": "<b>标题</b>",
        "arcurl": "http://www.bilibili.com/video/BV1test",
        "bvid": "BV1test",
        "author": "UP主",
        "duration": "3:05",
        "pic": "//i0.hdslb.com/bfs/archive/test.jpg",
    }
    out = _normalize_bilibili_api_row(row, 0)
    assert out is not None
    assert out["title"] == "标题"
    assert out["url"] == "https://www.bilibili.com/video/BV1test"
    assert out["uploader"] == "UP主"
    assert out["duration"] == 185.0
    assert out["thumbnail"] == "https://i0.hdslb.com/bfs/archive/test.jpg"
    assert out["source"] == "bilibili"
