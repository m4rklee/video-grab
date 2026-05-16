"""Unit tests for transcript helpers."""

import json

from app.transcript import (
    TranscriptSegment,
    _extract_initial_state_json,
    _parse_bilibili_danmaku_xml,
    segments_to_llm_chunks_json,
)


def test_parse_bilibili_danmaku_xml_buckets_and_orders():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<i><chatserver>chat.bilibili.com</chatserver>
<d p="1.5,1,25,0,0,0,0,0">hello</d>
<d p="2.0,1,25,0,0,0,0,0">hello</d>
<d p="3.1,1,25,0,0,0,0,0">world</d>
</i>"""
    segs = _parse_bilibili_danmaku_xml(xml, bucket_sec=10.0)
    assert len(segs) >= 1
    assert isinstance(segs[0], TranscriptSegment)
    merged_text = " ".join(s.text for s in segs)
    assert "hello" in merged_text and "world" in merged_text


def test_extract_initial_state_json_balanced():
    html = """<!doctype html><script>window.__INITIAL_STATE__={"videoData":{"subtitle":{"list":[{"subtitle_url":"https://x/t.json","lan":"zh-CN"}]}}};\n</script>"""
    data = _extract_initial_state_json(html)
    assert data is not None
    assert len(((data.get("videoData") or {}).get("subtitle") or {}).get("list") or []) == 1


def test_segments_to_llm_chunks_json_preserves_ms_boundaries():
    segs = [
        TranscriptSegment(0, 25_000, "a"),
        TranscriptSegment(25_000, 51_230, "b"),
    ]
    raw = segments_to_llm_chunks_json(segs)
    blocks = json.loads(raw)
    assert blocks == [
        {"t_start_ms": 0, "t_end_ms": 25_000, "text": "a"},
        {"t_start_ms": 25_000, "t_end_ms": 51_230, "text": "b"},
    ]
