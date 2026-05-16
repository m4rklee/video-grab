"""Fetch timed transcripts: platform official APIs first, then yt-dlp."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL

from . import douyin_web


@dataclass
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


def detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "bilibili.com" in u or "b23.tv" in u:
        return "bilibili"
    if douyin_web.is_douyin_video_url(url):
        return "douyin"
    return "generic"


def extract_youtube_video_id(url: str) -> str | None:
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    host = (p.hostname or "").lower()
    if host in ("youtu.be",):
        seg = (p.path or "").strip("/").split("/")
        return seg[0] if seg and re.match(r"^[\w-]{11}$", seg[0]) else None
    if "youtube.com" in host:
        q = parse_qs(p.query)
        if "v" in q and q["v"]:
            vid = q["v"][0]
            return vid if re.match(r"^[\w-]{11}$", vid) else None
        m = re.search(r"/embed/([\w-]{11})", p.path or "")
        if m:
            return m.group(1)
    return None


def extract_bilibili_bvid(url: str) -> str | None:
    m = re.search(r"(BV[\w]+)", url, re.I)
    return m.group(1) if m else None


def _bilibili_cookie_header() -> str | None:
    """Optional login cookie (browser settings header or backend/.env)."""
    from app.bilibili_cookie import resolve_bilibili_cookie

    return resolve_bilibili_cookie()


def _bilibili_browser_headers(bvid: str | None) -> dict[str, str]:
    referer = f"https://www.bilibili.com/video/{bvid}/" if bvid else "https://www.bilibili.com/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": referer,
    }
    ck = _bilibili_cookie_header()
    if ck:
        headers["Cookie"] = ck
    return headers


def _bilibili_subtitle_lang_rank(item: dict[str, Any]) -> tuple[int, str]:
    lan = str(item.get("lan") or "").lower()
    if lan.startswith("zh"):
        tier = 0
    elif lan.startswith("ai-"):
        tier = 1
    else:
        tier = 2
    return (tier, lan)


def _extract_initial_state_json(html: str) -> dict[str, Any] | None:
    """Parse `window.__INITIAL_STATE__={...}` from Bilibili watch page HTML (brace-balanced)."""
    marker = "window.__INITIAL_STATE__="
    idx = html.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    while start < len(html) and html[start] in " \t":
        start += 1
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    for j in range(start, len(html)):
        ch = html[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[start : j + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return None
    return None


def _bilibili_embedded_subtitle_rank(item: dict[str, Any]) -> tuple[int, str]:
    return _bilibili_subtitle_lang_rank(item)


def _try_bilibili_embedded_subtitles(
    bvid: str, client: httpx.Client, hdr: dict[str, str]
) -> tuple[list[TranscriptSegment], str] | None:
    """少数稿件在 SSR 的 __INITIAL_STATE__ 里带了 subtitle.list（匿名与 player API 同源，不保证有 AI 轨）。"""
    page = f"https://www.bilibili.com/video/{bvid}/"
    try:
        r = client.get(page, headers=hdr)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    data = _extract_initial_state_json(r.text)
    if not data:
        return None
    lst = ((data.get("videoData") or {}).get("subtitle") or {}).get("list") or []
    if not lst:
        return None
    for item in sorted(lst, key=_bilibili_embedded_subtitle_rank):
        sub_url = item.get("subtitle_url") or item.get("url")
        if not sub_url:
            continue
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        try:
            tr = client.get(sub_url, headers=hdr)
            tr.raise_for_status()
            segs = _bilibili_json_to_segments(tr.json())
            if segs:
                label = (
                    "bilibili_embed_aisubtitle"
                    if "aisubtitle.hdslb.com" in sub_url
                    else "bilibili_embed_ssr"
                )
                return segs, label
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _youtube_captions_list(video_id: str, api_key: str) -> list[dict[str, Any]]:
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/captions",
        params={"part": "snippet", "videoId": video_id, "key": api_key},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    return list(data.get("items") or [])


def _parse_vtt(vtt: str) -> list[TranscriptSegment]:
    out: list[TranscriptSegment] = []
    # WebVTT cue blocks: optional timing line WEBVTT header skip
    lines = vtt.replace("\r\n", "\n").split("\n")
    i = 0
    time_re = re.compile(
        r"^(\d{2}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(\d{2}:)?\d{2}:\d{2}\.\d{3}"
    )
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line and time_re.match(line):
            parts = line.split("-->")
            start_s = _vtt_ts_to_ms(parts[0].strip())
            end_s = _vtt_ts_to_ms(parts[1].strip().split()[0])
            i += 1
            buf: list[str] = []
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                buf.append(lines[i].strip())
                i += 1
            text = " ".join(buf).strip()
            if text:
                out.append(TranscriptSegment(start_ms=start_s, end_ms=end_s, text=text))
            continue
        i += 1
    return out


def _vtt_ts_to_ms(ts: str) -> int:
    ts = ts.strip()
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", ts):
        ts = "00:" + ts
    m = re.match(r"^(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})$", ts)
    if not m:
        return 0
    h = int(m.group(1) or 0)
    mi = int(m.group(2))
    s = int(m.group(3))
    ms = int(m.group(4))
    return ((h * 60 + mi) * 60 + s) * 1000 + ms


def _parse_bilibili_danmaku_xml(xml_text: str, *, bucket_sec: float = 25.0, max_d_lines: int = 12000) -> list[TranscriptSegment]:
    """Turn Bilibili bullet-screen XML into coarse timed segments (UP 未传字幕时的兜底)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    rows: list[tuple[int, str]] = []
    for el in root.iter("d"):
        if el.tag != "d":
            continue
        p_attr = el.get("p") or ""
        parts = p_attr.split(",")
        if not parts:
            continue
        try:
            t_sec = float(parts[0])
        except ValueError:
            continue
        text = (el.text or "").strip()
        if not text:
            continue
        rows.append((int(t_sec * 1000), text))
        if len(rows) >= max_d_lines:
            break

    if not rows:
        return []

    rows.sort(key=lambda x: x[0])
    bucket_ms = max(int(bucket_sec * 1000), 1000)
    buckets: defaultdict[int, list[str]] = defaultdict(list)
    for t_ms, text in rows:
        key = (t_ms // bucket_ms) * bucket_ms
        buckets[key].append(text)

    out: list[TranscriptSegment] = []
    for start_ms in sorted(buckets.keys()):
        merged = " ".join(dict.fromkeys(buckets[start_ms])).strip()
        if merged:
            end_ms = start_ms + bucket_ms
            out.append(TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=merged))
    return out


def _transcript_via_ytdlp_bilibili_danmaku(url: str) -> list[TranscriptSegment]:
    """Anonymous Bilibili fallback: yt-dlp exposes timed bullet comments as subtitle lang danmaku (.xml)."""
    with tempfile.TemporaryDirectory(prefix="vgdan_") as tmp:
        outtmpl = str(Path(tmp) / "dm")
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": False,
            "subtitleslangs": ["danmaku"],
            "outtmpl": outtmpl + ".%(ext)s",
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception:
            return []
        for p in sorted(Path(tmp).iterdir()):
            if not p.is_file():
                continue
            name_low = p.name.lower()
            if not name_low.endswith(".xml"):
                continue
            raw = p.read_text(encoding="utf-8", errors="replace")
            if "chat.bilibili.com" not in raw and "<d " not in raw and "<d>" not in raw:
                continue
            segs = _parse_bilibili_danmaku_xml(raw)
            if segs:
                return segs
    return []


def _parse_youtube_json3(raw: str) -> list[TranscriptSegment]:
    """Minimal parser for timedtext JSON (events with segs)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    events = data.get("events") or []
    out: list[TranscriptSegment] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start = int(ev.get("tStartMs", 0) or 0)
        dur = int(ev.get("dDurationMs", ev.get("d", 0)) or 0)
        segs = ev.get("segs") or []
        texts: list[str] = []
        for s in segs:
            if isinstance(s, dict) and s.get("utf8"):
                texts.append(str(s["utf8"]).replace("\n", " "))
        text = "".join(texts).strip()
        if text:
            end = start + (dur if dur > 0 else max(500, len(text) * 80))
            out.append(TranscriptSegment(start_ms=start, end_ms=end, text=text))
    return out


def _youtube_timedtext_headers(page_url: str) -> dict[str, str]:
    ref = page_url if isinstance(page_url, str) and page_url.startswith("http") else "https://www.youtube.com/"
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": ref,
        "Accept": "*/*",
    }


def _youtube_merge_sub_tracks(info: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    langs = set(manual) | set(auto)
    merged: dict[str, list[dict[str, Any]]] = {}
    for lang in langs:
        merged[lang] = list(manual.get(lang) or []) + list(auto.get(lang) or [])
    return merged


def _youtube_lang_iteration_order(pref: tuple[str, ...], merged: dict[str, list[Any]]) -> list[str]:
    out: list[str] = []
    for lang in pref:
        if lang in merged and lang not in out:
            out.append(lang)
    for lang in sorted(merged.keys()):
        if str(lang).startswith("zh") and lang not in out:
            out.append(lang)
    for lang in sorted(merged.keys()):
        if lang not in out:
            out.append(lang)
    return out


def _youtube_sub_ext_rank(ext: str | None) -> int:
    order = ("vtt", "json3", "srv3", "json")
    e = (ext or "").lower()
    try:
        return order.index(e)
    except ValueError:
        return 99


def _parse_youtube_subtitle_payload(raw: str, ext: str | None) -> list[TranscriptSegment]:
    ext_l = (ext or "").lower()
    head = raw.lstrip()[:24].upper()
    if ext_l == "vtt" or head.startswith("WEBVTT"):
        return _parse_vtt(raw)
    if ext_l in ("json3", "json", "srv3"):
        return _parse_youtube_json3(raw)
    return []


def _transcript_from_youtube_extract_info(
    info: dict[str, Any], page_url: str, lang_pref: tuple[str, ...]
) -> list[TranscriptSegment]:
    merged = _youtube_merge_sub_tracks(info)
    if not merged:
        return []
    headers = _youtube_timedtext_headers(page_url)
    for lang in _youtube_lang_iteration_order(lang_pref, merged):
        entries = sorted(merged.get(lang) or [], key=lambda it: _youtube_sub_ext_rank(it.get("ext")))
        for ent in entries:
            sub_url = ent.get("url")
            ext = ent.get("ext")
            if not sub_url:
                continue
            for attempt in range(2):
                try:
                    r = httpx.get(
                        sub_url,
                        headers=headers,
                        timeout=45,
                        follow_redirects=True,
                        trust_env=False,
                    )
                    if r.status_code == 429 and attempt == 0:
                        time.sleep(1.6)
                        continue
                    if r.status_code >= 400:
                        break
                    segs = _parse_youtube_subtitle_payload(r.text, str(ext) if ext else None)
                    if segs:
                        return segs
                    break
                except httpx.HTTPError:
                    break
    return []


_YT_SUB_LANG_PREF: tuple[str, ...] = (
    "zh-Hans",
    "zh-CN",
    "zh-TW",
    "zh-Hant",
    "zh-HK",
    "zh",
    "en",
    "en-US",
    "en-GB",
)


def _transcript_via_ytdlp(url: str, lang_pref: tuple[str, ...] | None = None) -> list[TranscriptSegment]:
    """Prefer YouTube timedtext direct fetch (language fallbacks + 429 retry);其他站点保留写盘回退。"""
    pref = lang_pref or _YT_SUB_LANG_PREF
    ydl_opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return []

    ie = str(info.get("extractor_key") or "")
    if extract_youtube_video_id(url) or "youtube" in ie.lower():
        segs = _transcript_from_youtube_extract_info(info, url, pref)
        if segs:
            return segs

    with tempfile.TemporaryDirectory(prefix="vgsub_") as tmp:
        outtmpl = str(Path(tmp) / "sub")
        fallback_opts: dict[str, Any] = {
            **ydl_opts,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "ignoreerrors": True,
            "subtitleslangs": list(pref),
            "outtmpl": outtmpl + ".%(ext)s",
        }
        try:
            with YoutubeDL(fallback_opts) as ydl:
                ydl.download([url])
        except Exception:
            pass
        for p in sorted(Path(tmp).iterdir()):
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf == ".vtt":
                parsed = _parse_vtt(p.read_text(encoding="utf-8", errors="replace"))
                if parsed:
                    return parsed
            if suf in (".json3", ".srv3", ".json"):
                parsed = _parse_youtube_json3(p.read_text(encoding="utf-8", errors="replace"))
                if parsed:
                    return parsed
    return []


def fetch_youtube_transcript(url: str) -> tuple[list[TranscriptSegment], str]:
    """YouTube: try Data API caption list (official), then yt-dlp subtitle files."""
    vid = extract_youtube_video_id(url)
    if not vid:
        raise ValueError("无法从链接解析 YouTube videoId")
    api_key = (os.environ.get("YOUTUBE_DATA_API_KEY") or "").strip()
    if api_key:
        try:
            items = _youtube_captions_list(vid, api_key)
            if items:
                subs = _transcript_via_ytdlp(url)
                if subs:
                    return subs, "youtube_data_api_list+yt-dlp"
        except (httpx.HTTPError, KeyError, ValueError):
            pass
    subs = _transcript_via_ytdlp(url)
    if subs:
        return subs, "yt-dlp"
    raise RuntimeError(
        "未获取到 YouTube 字幕：常见原因包括 · 视频无任何字幕轨 · "
        "翻译/自动生成轨遭遇限流（429），可多试几次或稍后重试 · "
        "会员/地域/年龄验证视频需配置 Cookie 或 PoToken。"
        "已尝试 yt-dlp 枚举 timedtext URL 并按语言优先级直连下载。"
    )


def fetch_bilibili_transcript(url: str) -> tuple[list[TranscriptSegment], str]:
    """Bilibili: player/v2 subtitle URLs（登录后可见 AI 字幕，多在 aisubtitle.hdslb.com），再 yt-dlp / 弹幕。"""
    bvid = extract_bilibili_bvid(url)
    if not bvid:
        raise ValueError("无法从链接解析 Bilibili BV 号")
    hdr = _bilibili_browser_headers(bvid)
    with httpx.Client(timeout=25, follow_redirects=True, trust_env=False) as client:
        emb = _try_bilibili_embedded_subtitles(bvid, client, hdr)
        if emb:
            return emb
        r = client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            headers=hdr,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("code") != 0:
            raise RuntimeError(j.get("message") or "B站 view 接口异常")
        data = j.get("data") or {}
        pages = data.get("pages") or []
        cid = (pages[0].get("cid") if pages else None) or data.get("cid")
        aid = data.get("aid")
        if not cid:
            raise RuntimeError("B站稿件无 cid")
        r2 = client.get(
            "https://api.bilibili.com/x/player/v2",
            params={"cid": cid, "aid": aid, "bvid": bvid},
            headers=hdr,
        )
        r2.raise_for_status()
        j2 = r2.json()
        if j2.get("code") != 0:
            raise RuntimeError(j2.get("message") or "B站 player/v2 异常")
        sub = (j2.get("data") or {}).get("subtitle") or {}
        subs_list = sorted(sub.get("subtitles") or [], key=_bilibili_subtitle_lang_rank)
        for item in subs_list:
            sub_url = item.get("subtitle_url") or item.get("url")
            if not sub_url:
                continue
            if sub_url.startswith("//"):
                sub_url = "https:" + sub_url
            tr = client.get(sub_url, headers=hdr)
            tr.raise_for_status()
            body = tr.json()
            segs = _bilibili_json_to_segments(body)
            if segs:
                label = (
                    "bilibili_aisubtitle"
                    if "aisubtitle.hdslb.com" in sub_url
                    else "bilibili_player_v2"
                )
                return segs, label
    subs = _transcript_via_ytdlp(url)
    if subs:
        return subs, "yt-dlp"
    dm = _transcript_via_ytdlp_bilibili_danmaku(url)
    if dm:
        return dm, "bilibili_yt-dlp_danmaku"
    raise RuntimeError(
        "B站无可用字幕：未上传字幕时，AI 轨与 player 接口一样通常需登录 Cookie（"
        "BILIBILI_COOKIE / BILIBILI_SESSDATA）；"
        "已尝试 SSR 内嵌字幕、player/v2、yt-dlp 字幕与弹幕兜底。"
    )


def _bilibili_json_to_segments(body: Any) -> list[TranscriptSegment]:
    out: list[TranscriptSegment] = []
    if not isinstance(body, dict):
        return out
    body_list = body.get("body") or []
    for row in body_list:
        if not isinstance(row, dict):
            continue
        from_ms = int(float(row.get("from", 0)) * 1000)
        to_ms = int(float(row.get("to", 0)) * 1000)
        content = (row.get("content") or "").strip()
        if content:
            out.append(TranscriptSegment(start_ms=from_ms, end_ms=max(to_ms, from_ms + 100), text=content))
    return out


def _segments_from_douyin_detail(detail: dict[str, Any]) -> list[TranscriptSegment]:
    video = detail.get("video") or {}
    # clip / subtitle_infos vary by version
    for key in ("subtitle_infos", "cla_info", "misc_download_addrs"):
        block = video.get(key)
        if isinstance(block, list) and block:
            for item in block:
                if isinstance(item, dict):
                    url_list = item.get("url_list") or item.get("UrlList") or []
                    for u in url_list:
                        if isinstance(u, str) and u.startswith("http"):
                            try:
                                r = httpx.get(u, timeout=20, follow_redirects=True, trust_env=False)
                                r.raise_for_status()
                                if "json" in (r.headers.get("content-type") or ""):
                                    segs = _bilibili_json_to_segments(r.json())
                                    if segs:
                                        return segs
                            except httpx.HTTPError:
                                continue
    return []


def fetch_douyin_transcript(url: str) -> tuple[list[TranscriptSegment], str]:
    """Douyin: embedded subtitle URLs in aweme detail / IES item, then yt-dlp."""
    aweme_id = douyin_web.extract_aweme_id(url)
    if not aweme_id:
        raise ValueError("无法识别抖音作品 ID")
    detail = douyin_web.fetch_aweme_detail(aweme_id)
    segs = _segments_from_douyin_detail(detail)
    if segs:
        return segs, "douyin_aweme_embedded"
    subs = _transcript_via_ytdlp(f"https://www.douyin.com/video/{aweme_id}")
    if subs:
        return subs, "yt-dlp"
    raise RuntimeError("抖音稿件无内嵌字幕且 yt-dlp 未取到字幕（可后续接 ASR API）")


def _normalize_url_for_transcript(url: str) -> str:
    text = url.strip()
    if "v.douyin.com" in text.lower():
        text = douyin_web.resolve_share_redirect(text)
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    host = (parsed.hostname or "").lower()
    if "douyin.com" not in host:
        return text
    query = parse_qs(parsed.query, keep_blank_values=False)
    for key in ("modal_id", "modalId", "aweme_id", "awemeId"):
        values = query.get(key)
        if not values:
            continue
        raw = (values[0] or "").strip()
        if raw.isdigit():
            return f"https://www.douyin.com/video/{raw}"
    return text


def fetch_transcript(url: str) -> tuple[list[TranscriptSegment], str]:
    text = _normalize_url_for_transcript(url.strip())
    if detect_platform(text) == "youtube":
        return fetch_youtube_transcript(text)
    if detect_platform(text) == "bilibili":
        return fetch_bilibili_transcript(text)
    if detect_platform(text) == "douyin":
        return fetch_douyin_transcript(text)
    subs = _transcript_via_ytdlp(text)
    if subs:
        return subs, "yt-dlp"
    raise RuntimeError("当前平台未单独对接字幕，且 yt-dlp 未取到字幕")


def segments_to_plain_text(segments: list[TranscriptSegment], max_chars: int = 120_000) -> str:
    parts: list[str] = []
    n = 0
    for s in segments:
        line = f"[{s.start_ms // 1000}s] {s.text}"
        if n + len(line) > max_chars:
            break
        parts.append(line)
        n += len(line) + 1
    return "\n".join(parts)


def segments_to_llm_chunks_json(segments: list[TranscriptSegment], max_chars: int = 100_000) -> str:
    """Structured transcript for summarize LLM — exact ms boundaries per chunk (must match UI timeline)."""
    blocks: list[dict[str, Any]] = []
    size = 0
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        obj: dict[str, Any] = {"t_start_ms": int(s.start_ms), "t_end_ms": int(s.end_ms), "text": text}
        fragment = json.dumps(obj, ensure_ascii=False)
        if size + len(fragment) + 2 > max_chars:
            break
        blocks.append(obj)
        size += len(fragment) + 2
    return json.dumps(blocks, ensure_ascii=False)
