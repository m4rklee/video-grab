"""Keyword video search: YouTube via yt-dlp; Bilibili via official search API."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Any, Literal

import httpx
from yt_dlp import YoutubeDL

from app.transcript import _bilibili_cookie_header

SearchSource = Literal["youtube", "bilibili"]

# yt-dlp bundles one batch per call; 100 ≈ 7s, 1000 ≈ 50s+ (override via SEARCH_YOUTUBE_MAX)
_YOUTUBE_SEARCH_MAX = max(20, min(int(os.environ.get("SEARCH_YOUTUBE_MAX", "100")), 300))
_BILIBILI_PAGE_WORKERS = 8
_BILIBILI_YTDLP_FALLBACK_MAX = 100
_YOUTUBE_STREAM_BATCH = 10
_BILIBILI_412_HINT = (
    "哔哩哔哩搜索被风控拦截（HTTP 412）。请在应用「设置」填写 B 站 Cookie，"
    "或在 backend/.env 配置 BILIBILI_SESSDATA / BILIBILI_COOKIE 后重试。"
)


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or "")).strip()


def _parse_duration(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return float(s)
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def _bilibili_search_headers() -> dict[str, str]:
    cookie_parts = [f"buvid3={uuid.uuid4().hex}"]
    extra = _bilibili_cookie_header()
    if extra:
        cookie_parts.insert(0, extra)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://search.bilibili.com/",
        "Cookie": "; ".join(cookie_parts),
    }


def _normalize_bilibili_api_row(row: dict[str, Any], idx: int) -> dict[str, Any] | None:
    url = (row.get("arcurl") or "").strip()
    bvid = row.get("bvid")
    aid = row.get("aid")
    if bvid:
        url = f"https://www.bilibili.com/video/{bvid}"
    elif not url and aid:
        url = f"https://www.bilibili.com/video/av{aid}"
    if not url:
        return None
    eid = bvid or aid or idx
    title = _strip_html(str(row.get("title") or ""))
    if not title:
        title = _strip_html(str(row.get("description") or "")) or "Untitled"
    pic = row.get("pic") or row.get("cover")
    thumb = None
    if pic:
        pic_s = str(pic)
        thumb = ("https:" + pic_s) if pic_s.startswith("//") else pic_s
    author = row.get("author")
    return {
        "id": f"bilibili:{eid}:{hash(url) & 0xFFFFFFFF}",
        "title": title[:500],
        "url": url,
        "thumbnail": thumb,
        "duration": _parse_duration(row.get("duration")),
        "uploader": str(author)[:200] if author else None,
        "source": "bilibili",
        "extractor": "bilibili",
    }


def _bilibili_api_page(
    client: httpx.Client,
    query: str,
    page: int,
    headers: dict[str, str],
) -> tuple[int, list[dict[str, Any]], int]:
    r = client.get(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={"search_type": "video", "keyword": query, "page": page},
        headers=headers,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(body.get("message") or "B站搜索接口异常")
    data = body.get("data") or {}
    results = data.get("result") or []
    items: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        norm = _normalize_bilibili_api_row(row, len(items))
        if norm:
            items.append(norm)
    num_pages = int(data.get("numPages") or 1)
    return page, items, num_pages


def _bilibili_api_page_thread(query: str, page: int, headers: dict[str, str]) -> tuple[int, list[dict[str, Any]], int]:
    with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
        return _bilibili_api_page(client, query, page, headers)


def _is_bilibili_access_denied(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 412:
        return True
    msg = str(exc).lower()
    return "412" in msg or "precondition failed" in msg


def _fetch_bilibili_ytdlp(query: str) -> list[dict[str, Any]]:
    """Fallback when search API is blocked; may lack titles without login cookies."""
    pseudo = f"bilisearch{_BILIBILI_YTDLP_FALLBACK_MAX}:{query}"
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": True,
    }
    ck = _bilibili_cookie_header()
    if ck:
        ydl_opts["headers"] = {"Cookie": ck}

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(pseudo, download=False)

    entries_raw = info.get("entries") if isinstance(info, dict) else None
    if not entries_raw:
        return []

    flat: list[dict[str, Any]] = []
    for i, ent in enumerate(entries_raw):
        if not ent:
            continue
        norm = _normalize_entry("bilibili", ent, i)
        if norm:
            flat.append(norm)
    return flat


def _fetch_bilibili_search(query: str) -> list[dict[str, Any]]:
    """All Bilibili API pages (parallel); falls back to yt-dlp on HTTP 412."""
    headers = _bilibili_search_headers()
    try:
        with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
            _, page1_items, num_pages = _bilibili_api_page(client, query, 1, headers)
        if num_pages <= 1:
            return page1_items

        by_page: dict[int, list[dict[str, Any]]] = {1: page1_items}
        rest = list(range(2, num_pages + 1))
        with ThreadPoolExecutor(max_workers=_BILIBILI_PAGE_WORKERS) as pool:
            futures = [pool.submit(_bilibili_api_page_thread, query, p, headers) for p in rest]
            for fut in as_completed(futures):
                p, batch, _ = fut.result()
                by_page[p] = batch

        merged: list[dict[str, Any]] = []
        for p in range(1, num_pages + 1):
            merged.extend(by_page.get(p, []))
        return merged
    except Exception as exc:
        if not _is_bilibili_access_denied(exc):
            raise
        items = _fetch_bilibili_ytdlp(query)
        if items:
            return items
        raise RuntimeError(_BILIBILI_412_HINT) from exc


def _pick_thumb(entry: dict[str, Any]) -> str | None:
    thumbs = entry.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        last = thumbs[-1]
        if isinstance(last, dict) and last.get("url"):
            return str(last["url"])
    return entry.get("thumbnail")


def _normalize_entry(
    source: SearchSource,
    entry: dict[str, Any],
    idx: int,
) -> dict[str, Any] | None:
    if not entry or entry.get("ie_key") == "Generic":
        return None
    eid = entry.get("id")
    url = (entry.get("url") or entry.get("webpage_url") or "").strip()
    if not url and eid:
        if source == "youtube":
            url = f"https://www.youtube.com/watch?v={eid}"
        elif source == "bilibili":
            bvid = eid if str(eid).upper().startswith("BV") else entry.get("bvid")
            if bvid:
                url = f"https://www.bilibili.com/video/{bvid}"
            else:
                url = f"https://www.bilibili.com/video/av{eid}"
    if not url:
        return None
    stable_id = f"{source}:{eid or idx}:{hash(url) & 0xFFFFFFFF}"
    title = entry.get("title") or "Untitled"
    duration = entry.get("duration")
    if duration is not None:
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = None
    uploader = entry.get("uploader") or entry.get("channel") or entry.get("uploader_id")
    extractor = entry.get("extractor_key") or entry.get("ie_key")
    return {
        "id": stable_id,
        "title": str(title)[:500],
        "url": url,
        "thumbnail": _pick_thumb(entry),
        "duration": duration,
        "uploader": str(uploader)[:200] if uploader else None,
        "source": source,
        "extractor": str(extractor) if extractor else None,
    }


def _fetch_youtube_search(query: str) -> list[dict[str, Any]]:
    pseudo = f"ytsearch{_YOUTUBE_SEARCH_MAX}:{query}"
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(pseudo, download=False)

    entries_raw = info.get("entries") if isinstance(info, dict) else None
    if not entries_raw:
        return []

    flat: list[dict[str, Any]] = []
    for i, ent in enumerate(entries_raw):
        if not ent:
            continue
        norm = _normalize_entry("youtube", ent, i)
        if norm:
            flat.append(norm)
    return flat


def _fetch_source_all(source: SearchSource, query: str) -> list[dict[str, Any]]:
    if source == "bilibili":
        return _fetch_bilibili_search(query)
    return _fetch_youtube_search(query)


def _emit_stream_event(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _ordered_sources(sources: list[SearchSource]) -> list[SearchSource]:
    order: list[SearchSource] = []
    for s in ("youtube", "bilibili"):
        if s in sources and s not in order:
            order.append(s)
    return order


def _push_item_batches(
    out_q: queue.Queue,
    source: SearchSource,
    items: list[dict[str, Any]],
    batch_size: int,
) -> None:
    batch: list[dict[str, Any]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= batch_size:
            out_q.put(("items", source, batch))
            batch = []
    if batch:
        out_q.put(("items", source, batch))


def _stream_bilibili_producer(query: str, out_q: queue.Queue) -> None:
    label = "哔哩哔哩"
    headers = _bilibili_search_headers()
    try:
        with httpx.Client(timeout=30, follow_redirects=True, trust_env=False) as client:
            _, page1_items, num_pages = _bilibili_api_page(client, query, 1, headers)
        if page1_items:
            out_q.put(("items", "bilibili", page1_items))
        if num_pages > 1:
            rest = list(range(2, num_pages + 1))
            with ThreadPoolExecutor(max_workers=_BILIBILI_PAGE_WORKERS) as pool:
                futures = [pool.submit(_bilibili_api_page_thread, query, p, headers) for p in rest]
                for fut in as_completed(futures):
                    _, batch, _ = fut.result()
                    if batch:
                        out_q.put(("items", "bilibili", batch))
    except Exception as exc:
        if not _is_bilibili_access_denied(exc):
            out_q.put(("warning", None, f"{label}：{exc}"))
        else:
            try:
                items = _fetch_bilibili_ytdlp(query)
                if items:
                    _push_item_batches(out_q, "bilibili", items, _YOUTUBE_STREAM_BATCH)
                else:
                    out_q.put(("warning", None, _BILIBILI_412_HINT))
            except Exception as fallback_exc:
                out_q.put(("warning", None, f"{label}：{fallback_exc}"))
    finally:
        out_q.put(("done", "bilibili", None))


def _stream_youtube_producer(query: str, out_q: queue.Queue) -> None:
    label = "YouTube"
    try:
        pseudo = f"ytsearch{_YOUTUBE_SEARCH_MAX}:{query}"
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "noplaylist": False,
            "ignoreerrors": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(pseudo, download=False)
        entries_raw = info.get("entries") if isinstance(info, dict) else None
        if not entries_raw:
            return
        batch: list[dict[str, Any]] = []
        for i, ent in enumerate(entries_raw):
            if not ent:
                continue
            norm = _normalize_entry("youtube", ent, i)
            if not norm:
                continue
            batch.append(norm)
            if len(batch) >= _YOUTUBE_STREAM_BATCH:
                out_q.put(("items", "youtube", batch))
                batch = []
        if batch:
            out_q.put(("items", "youtube", batch))
    except Exception as exc:
        out_q.put(("warning", None, f"{label}：{exc}"))
    finally:
        out_q.put(("done", "youtube", None))


def iter_search_stream(
    sources: list[SearchSource],
    query: str,
) -> Iterator[str]:
    """Yield NDJSON lines: meta, items (batched), warning, done or error."""
    q = (query or "").strip()
    if not q:
        raise ValueError("搜索关键词不能为空")
    if not sources:
        raise ValueError("请至少选择一个搜索源")

    order = _ordered_sources(sources)
    yield _emit_stream_event({"type": "meta", "sources": order})

    out_q: queue.Queue = queue.Queue()
    producers = {
        "youtube": _stream_youtube_producer,
        "bilibili": _stream_bilibili_producer,
    }
    threads: list[threading.Thread] = []
    for src in order:
        fn = producers[src]
        t = threading.Thread(target=fn, args=(q, out_q), daemon=True)
        t.start()
        threads.append(t)

    seen_urls: set[str] = set()
    total = 0
    done_count = 0
    warnings: list[str] = []

    while done_count < len(order):
        kind, source, payload = out_q.get()
        if kind == "done":
            done_count += 1
            continue
        if kind == "warning":
            msg = str(payload)
            warnings.append(msg)
            yield _emit_stream_event({"type": "warning", "message": msg})
            continue
        if kind == "items":
            deduped: list[dict[str, Any]] = []
            for item in payload:
                url = item["url"]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                deduped.append(item)
                total += 1
            if deduped:
                yield _emit_stream_event(
                    {"type": "items", "source": source, "items": deduped}
                )

    for t in threads:
        t.join(timeout=0.5)

    if total == 0 and warnings and len(warnings) >= len(order):
        yield _emit_stream_event({"type": "error", "message": "；".join(warnings)})
    yield _emit_stream_event({"type": "done", "total": total})


def search_videos(source: SearchSource, query: str) -> dict[str, Any]:
    """Search a single platform (backward-compatible helper)."""
    return search_videos_multi([source], query)


def search_videos_multi(
    sources: list[SearchSource],
    query: str,
) -> dict[str, Any]:
    """Collect full merged result list from stream (tests / helpers)."""
    merged: list[dict[str, Any]] = []
    warnings: list[str] = []
    order: list[SearchSource] = []
    total = 0

    for line in iter_search_stream(sources, query):
        ev = json.loads(line)
        t = ev.get("type")
        if t == "meta":
            order = ev.get("sources") or []
        elif t == "items":
            merged.extend(ev.get("items") or [])
        elif t == "warning":
            warnings.append(str(ev.get("message") or ""))
        elif t == "error":
            raise RuntimeError(str(ev.get("message") or "搜索失败"))
        elif t == "done":
            total = int(ev.get("total") or len(merged))

    return {
        "items": merged,
        "total": total,
        "sources": order,
        "warning": "；".join(warnings) if warnings else None,
    }
