"""Bilibili: legacy non-WBI playurl returns higher DASH qualities while WBI endpoint does not for anonymous."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx

from app.bilibili_cookie import resolve_bilibili_cookie

_BILI_HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def _bilibili_headers(bvid: str | None = None) -> dict[str, str]:
    headers = dict(_BILI_HEADERS_BASE)
    if bvid:
        headers["Referer"] = f"https://www.bilibili.com/video/{bvid}/"
    ck = resolve_bilibili_cookie()
    if ck:
        headers["Cookie"] = ck
    return headers


def extract_bilibili_bvid(url: str) -> str | None:
    m = re.search(r"(BV[\w]+)", url, re.I)
    return m.group(1) if m else None


def extract_bilibili_aid(url: str) -> str | None:
    m = re.search(r"/video/av(\d+)", url, re.I)
    return m.group(1) if m else None


def resolve_bilibili_bvid(url: str, timeout: float = 25.0) -> str | None:
    """BV from URL, or resolve classic av/aid links via view API."""
    bvid = extract_bilibili_bvid(url)
    if bvid:
        return bvid
    aid = extract_bilibili_aid(url)
    if not aid:
        return None
    r = httpx.get(
        "https://api.bilibili.com/x/web-interface/view",
        params={"aid": aid},
        headers=_bilibili_headers(),
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        return None
    data = body.get("data") or {}
    out = data.get("bvid")
    return str(out) if out else None


def fetch_cid(bvid: str, timeout: float = 25.0) -> int | None:
    r = httpx.get(
        "https://api.bilibili.com/x/player/pagelist",
        params={"bvid": bvid},
        headers=_bilibili_headers(bvid),
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        return None
    rows = data.get("data") or []
    if not rows:
        return None
    cid = rows[0].get("cid")
    return int(cid) if cid is not None else None


def fetch_legacy_playurl_dash(bvid: str, cid: int, timeout: float = 25.0) -> dict[str, Any] | None:
    """Call classic playurl (not wbi); anonymous often receives qn 80/64 dash streams."""
    params = {
        "otype": "json",
        "fnval": 4048,
        "cid": cid,
        "bvid": bvid,
        "qn": 80,
        "try_look": 1,
        "fourk": 1,
    }
    url = "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode(params)
    r = httpx.get(
        url,
        headers=_bilibili_headers(bvid),
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        return None
    return body.get("data") or None


_QN_HEIGHT = {
    127: 4320,
    126: 4320,
    125: 2160,
    120: 2160,
    116: 1080,
    112: 1080,
    80: 1080,
    74: 720,
    64: 720,
    48: 720,
    32: 480,
    16: 360,
}


def _pick_video_stream(video_streams: list[dict[str, Any]], qn: int) -> dict[str, Any] | None:
    """Prefer AVC (codecid 7) for ffmpeg copy compatibility."""
    candidates = [v for v in video_streams if v.get("id") == qn]
    if not candidates:
        return None
    for prefer in (7, 12, 13):  # avc, hevc, av1
        for v in candidates:
            if v.get("codecid") == prefer:
                return v
    return candidates[0]


def _pick_best_audio(audio_streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not audio_streams:
        return None
    return max(audio_streams, key=lambda a: int(a.get("bandwidth") or 0))


def build_legacy_format_entries(bvid: str, cid: int, data: dict[str, Any]) -> list[dict[str, Any]]:
    dash = data.get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    if not videos:
        return []

    audio_pick = _pick_best_audio([x for x in audios if isinstance(x, dict)])
    audio_bandwidth = int(audio_pick.get("bandwidth") or 0) if audio_pick else 0

    seen_qn: set[int] = set()
    formats: list[dict[str, Any]] = []
    for v in videos:
        qn = v.get("id")
        if not isinstance(qn, int):
            continue
        if qn in seen_qn:
            continue
        stream = _pick_video_stream(videos, qn)
        if not stream:
            continue
        seen_qn.add(qn)
        height = int(stream.get("height") or _QN_HEIGHT.get(qn, 0) or 0)
        width = int(stream.get("width") or 0)
        bw = int(stream.get("bandwidth") or 0)
        url_v = stream.get("baseUrl") or stream.get("base_url")
        if not url_v:
            continue
        tbr_kbps = (bw + audio_bandwidth) / 1000.0 if bw else 0.0
        formats.append(
            {
                "format_id": f"bilibili_legacy|{qn}",
                "ext": "mp4",
                "height": height,
                "width": width,
                "vcodec": "h264",
                "acodec": "none",
                "tbr": tbr_kbps,
                "url": url_v,
                "protocol": "https",
                "format_note": "legacy_playurl+dash",
                "_bilibili_legacy_qn": qn,
                "_bilibili_bvid": bvid,
                "_bilibili_cid": cid,
            }
        )
    by_height: dict[int, dict[str, Any]] = {}
    for fmt in formats:
        h = int(fmt.get("height") or 0)
        prev = by_height.get(h)
        cur_tbr = float(fmt.get("tbr") or 0)
        if prev is None or cur_tbr > float(prev.get("tbr") or 0):
            by_height[h] = fmt
    return sorted(by_height.values(), key=lambda x: int(x.get("height") or 0), reverse=True)


def merge_legacy_formats_into_info(webpage_url: str, info: dict[str, Any]) -> None:
    """Mutate yt-dlp info dict to add higher-resolution video-only formats."""
    bvid = resolve_bilibili_bvid(webpage_url) or extract_bilibili_bvid(str(info.get("id") or ""))
    if not bvid:
        return
    cid = fetch_cid(bvid)
    if cid is None:
        return
    try:
        pdata = fetch_legacy_playurl_dash(bvid, cid)
        if not pdata:
            return
        extras = build_legacy_format_entries(bvid, cid, pdata)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return

    fmts = list(info.get("formats") or [])
    existing_ids = {f.get("format_id") for f in fmts}
    for ef in extras:
        fid = ef.get("format_id")
        if not fid or fid in existing_ids:
            continue
        fmts.append(ef)
        existing_ids.add(fid)

    info["formats"] = fmts


def ffmpeg_headers_arg_for_bilibili(bvid: str) -> str:
    """HTTP headers block for ffmpeg `-headers` (CRLF-separated key: value lines)."""
    hdr = _bilibili_headers(bvid)
    lines = [f"Referer: {hdr['Referer']}", f"User-Agent: {hdr['User-Agent']}"]
    if hdr.get("Cookie"):
        lines.append(f"Cookie: {hdr['Cookie']}")
    return "\r\n".join(lines) + "\r\n"


def resolve_legacy_mux_urls(bvid: str, cid: int, qn: int) -> tuple[str, str]:
    """Return (video_url, audio_url) for ffmpeg merge."""
    pdata = fetch_legacy_playurl_dash(bvid, cid)
    if not pdata:
        raise RuntimeError("legacy playurl 无数据")
    dash = pdata.get("dash") or {}
    videos = dash.get("video") or []
    audios = dash.get("audio") or []
    v = _pick_video_stream(videos, qn)
    a = _pick_best_audio([x for x in audios if isinstance(x, dict)])
    if not v or not a:
        raise RuntimeError(f"legacy DASH 缺少 qn={qn} 的视频或音频轨")
    vu = v.get("baseUrl") or v.get("base_url")
    au = a.get("baseUrl") or a.get("base_url")
    if not vu or not au:
        raise RuntimeError("legacy DASH URL 为空")
    return vu, au
