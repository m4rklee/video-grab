"""Tests for summarize segment timeline normalization."""

import json

from app.summarize_llm import _normalize_segments_cover_chunks


def test_normalize_segments_appends_missing_tail():
    chunks_json = json.dumps(
        [
            {"t_start_ms": 0, "t_end_ms": 5000, "text": "intro"},
            {"t_start_ms": 450_000, "t_end_ms": 483_840, "text": "final minute dialogue"},
        ]
    )
    data = {
        "segments": [
            {"t_start_ms": 0, "t_end_ms": 5000, "summary": "开场"},
            {"t_start_ms": 5000, "t_end_ms": 455_000, "summary": "中段概括截止过早"},
        ]
    }
    _normalize_segments_cover_chunks(chunks_json, data)
    assert data["segments"][-1]["t_end_ms"] == 483_840
    assert "final minute" in data["segments"][-1]["summary"]
