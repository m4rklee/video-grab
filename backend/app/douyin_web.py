"""Fetch Douyin aweme detail: prefer iesdouyin.com share SSR, else signed www.douyin.com web API.

IES 分享页内嵌 ``window._ROUTER_DATA`` 的解析思路参考
`rathodpratham-dev/douyin_video_downloader`（MIT）中的实现。
"""

from __future__ import annotations

import base64
import json
import re
from hashlib import sha256
from typing import Any
from urllib.parse import unquote, urlencode, urlparse

import httpx

from .douyin_abogus import ABogus, BrowserFingerprintGenerator

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None  # type: ignore[assignment]

_DOUYIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)

# Match curl_cffi impersonate="chrome131" (TLS + HTTP/2 fingerprint).
_CHROME131_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_POST_DETAIL_BASE = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
_CURL_IMPERSONATE = "chrome131"

# Share page used by Douyin mobile web; embeds aweme JSON in window._ROUTER_DATA.
_IES_SHARE_PAGE = "https://www.iesdouyin.com/share/video/{aweme_id}/"
_IES_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1"
)

_MS_TOKEN_PATTERNS = (
    re.compile(r'"msToken"\s*:\s*"([^"]+)"'),
    re.compile(r"'msToken'\s*:\s*'([^']+)'"),
    re.compile(r"msToken=([^&\s\"']+)"),
    re.compile(r"%22msToken%22%3A%22([^%\"']+)%22"),
)


def resolve_share_redirect(url: str) -> str:
    """Follow v.douyin.com short links to a landing URL (caller should still normalize_video_url)."""
    text = url.strip()
    if "v.douyin.com" not in text.lower():
        return text
    with httpx.Client(timeout=25, follow_redirects=True, trust_env=False) as client:
        response = client.get(
            text,
            headers={
                "User-Agent": _DOUYIN_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
    response.raise_for_status()
    return str(response.url)


def is_douyin_video_url(url: str) -> bool:
    try:
        return bool(extract_aweme_id(url))
    except ValueError:
        return False


def extract_aweme_id(url: str) -> str | None:
    text = url.strip()
    if "douyin.com" not in text.lower():
        return None
    m = re.search(r"douyin\.com/video/(\d+)", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"iesdouyin\.com/share/video/(\d+)", text, re.I)
    if m:
        return m.group(1)
    return None


def _extract_ms_token(html: str) -> str | None:
    if not html:
        return None
    for pat in _MS_TOKEN_PATTERNS:
        m = pat.search(html)
        if m:
            raw = unquote(m.group(1).strip())
            if 16 <= len(raw) <= 512:
                return raw
    return None


def _decode_urlsafe_b64(value: str) -> bytes:
    normalized = value.replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    return base64.b64decode(normalized)


def _is_waf_challenge_page(html: str) -> bool:
    return "Please wait..." in html and "wci=" in html and "cs=" in html


def _solve_waf_cookie_value(html: str) -> tuple[str, str] | None:
    """Return (cookie_name, cookie_value) for iesdouyin WAF challenge, or None."""
    match = re.search(r'wci="([^"]+)"\s*,\s*cs="([^"]+)"', html)
    if not match:
        return None
    cookie_name, challenge_blob = match.groups()
    try:
        challenge_data = json.loads(_decode_urlsafe_b64(challenge_blob).decode("utf-8"))
        prefix = _decode_urlsafe_b64(challenge_data["v"]["a"])
        expected_digest = _decode_urlsafe_b64(challenge_data["v"]["c"]).hex()
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None
    solved_value: int | None = None
    for candidate in range(1_000_001):
        digest = sha256(prefix + str(candidate).encode("utf-8")).hexdigest()
        if digest == expected_digest:
            solved_value = candidate
            break
    if solved_value is None:
        return None
    challenge_data["d"] = base64.b64encode(str(solved_value).encode("utf-8")).decode("utf-8")
    cookie_value = base64.b64encode(json.dumps(challenge_data, separators=(",", ":")).encode("utf-8")).decode(
        "utf-8"
    )
    return cookie_name, cookie_value


def _extract_router_data_json(html: str) -> dict[str, Any]:
    marker = "window._ROUTER_DATA = "
    start = html.find(marker)
    if start < 0:
        return {}
    index = start + len(marker)
    while index < len(html) and html[index].isspace():
        index += 1
    if index >= len(html) or html[index] != "{":
        return {}
    depth = 0
    in_string = False
    escaped = False
    for cursor in range(index, len(html)):
        char = html[cursor]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                payload = html[index : cursor + 1]
                try:
                    return json.loads(payload)
                except ValueError:
                    return {}
    return {}


def _item_from_router_data(router_data: dict[str, Any]) -> dict[str, Any] | None:
    loader_data = router_data.get("loaderData")
    if not isinstance(loader_data, dict):
        return None
    for node in loader_data.values():
        if not isinstance(node, dict):
            continue
        video_info_res = node.get("videoInfoRes", {})
        if not isinstance(video_info_res, dict):
            continue
        item_list = video_info_res.get("item_list", [])
        if item_list and isinstance(item_list[0], dict):
            return item_list[0]
    return None


def _prefer_play_url(url: str) -> str:
    """Use play endpoint instead of playwm when the path allows (often cleaner stream)."""
    if "playwm" in url:
        return url.replace("playwm", "play", 1)
    return url


def _fetch_aweme_detail_ies_share(aweme_id: str, timeout: float) -> dict[str, Any]:
    share_url = _IES_SHARE_PAGE.format(aweme_id=aweme_id)
    headers = {
        "User-Agent": _IES_MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.douyin.com/",
    }
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        response = client.get(share_url, headers=headers)
        response.raise_for_status()
        html = response.text or ""
        if _is_waf_challenge_page(html):
            solved = _solve_waf_cookie_value(html)
            if solved:
                name, value = solved
                domain = urlparse(share_url).hostname or "www.iesdouyin.com"
                client.cookies.set(name, value, domain=domain, path="/")
                response = client.get(share_url, headers=headers)
                response.raise_for_status()
                html = response.text or ""
        router = _extract_router_data_json(html)
        item = _item_from_router_data(router) if router else None
        if not item:
            raise RuntimeError("iesdouyin 分享页未找到作品数据（无 _ROUTER_DATA 或 item_list）")
        return item


def _post_detail_query_params(aweme_id: str, ms_token: str = "", *, preset: str = "edge") -> dict[str, str]:
    """Mirror Douyin web detail query; msToken is included in the string that gets a_bogus-signed."""
    common: dict[str, str] = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
        "version_code": "290100",
        "version_name": "29.1.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_online": "true",
        "engine_name": "Blink",
        "os_name": "Windows",
        "os_version": "10",
        "cpu_core_num": "12",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "from_user_page": "1",
        "locate_query": "false",
        "need_time_list": "1",
        "pc_libra_divert": "Windows",
        "publish_video_strategy_type": "2",
        "round_trip_time": "50",
        "show_live_replay_strategy": "1",
        "support_dash": "0",
        "support_h265": "1",
        "time_list_query": "0",
        "whale_cut_token": "",
        "update_version_code": "170400",
        "msToken": ms_token,
        "aweme_id": aweme_id,
    }
    if preset == "chrome":
        common["browser_name"] = "Chrome"
        common["browser_version"] = "131.0.0.0"
        common["engine_version"] = "131.0.0.0"
    else:
        common["browser_name"] = "Edge"
        common["browser_version"] = "131.0.0.0"
        common["engine_version"] = "131.0.0.0"
    return common


def _signed_detail_request(aweme_id: str, *, ms_token: str = "", preset: str = "edge") -> tuple[str, str]:
    """Return (full URL including query + a_bogus, user-agent used for signing and requests)."""
    if preset == "chrome":
        ua = _CHROME131_UA
        fp_browser = "Chrome"
    else:
        ua = _DOUYIN_UA
        fp_browser = "Edge"
    params = _post_detail_query_params(aweme_id, ms_token=ms_token, preset=preset)
    query = urlencode(params, safe="")
    fp = BrowserFingerprintGenerator.generate_fingerprint(fp_browser)
    signer = ABogus(user_agent=ua, fp=fp, options=[0, 1, 8])
    full_query, _, _, _ = signer.generate_abogus(query, "")
    return f"{_POST_DETAIL_BASE}?{full_query}", ua


def _html_warm_headers(ua: str) -> dict[str, str]:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    }


def _json_api_headers(ua: str, aweme_id: str) -> dict[str, str]:
    referer = f"https://www.douyin.com/video/{aweme_id}"
    return {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
        "Origin": "https://www.douyin.com",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }


def _empty_detail_message() -> str:
    return (
        "抖音作品接口返回空数据（HTTP 200 但无正文）。常见于：服务器出境/机房 IP 被风控、"
        "未签发 s_v_web_id 等站点 Cookie，或 a_bogus 与当前环境不匹配。"
        "可将后端部署在大陆可直连抖音的网络；若本机浏览器能打开该视频，可尝试用 yt-dlp 并配置浏览器 Cookie。"
    )


def _parse_aweme_detail_payload(response: httpx.Response | Any, *, context: str) -> dict[str, Any]:
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise RuntimeError(_empty_detail_message())
    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"{context}：响应不是合法 JSON（{exc}）") from exc
    detail = data.get("aweme_detail")
    if not isinstance(detail, dict):
        raise RuntimeError(data.get("status_msg") or f"{context}：未返回 aweme_detail")
    return detail


def _fetch_aweme_detail_httpx(aweme_id: str, timeout: float) -> dict[str, Any]:
    preset = "edge"
    last_err: RuntimeError | None = None
    with httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        ua = _DOUYIN_UA
        client.get("https://www.douyin.com/", headers=_html_warm_headers(ua))
        page = client.get(f"https://www.douyin.com/video/{aweme_id}", headers=_html_warm_headers(ua))
        html = page.text or ""
        ms = _extract_ms_token(html) or ""
        api_h = _json_api_headers(ua, aweme_id)
        for _attempt in range(3):
            detail_url, sign_ua = _signed_detail_request(aweme_id, ms_token=ms, preset=preset)
            api_h["User-Agent"] = sign_ua
            resp = client.get(detail_url, headers=api_h)
            resp.raise_for_status()
            raw = (resp.text or "").strip()
            if raw:
                try:
                    return _parse_aweme_detail_payload(resp, context="抖音接口")
                except RuntimeError as exc:
                    last_err = exc
            else:
                last_err = RuntimeError(_empty_detail_message())
        if last_err:
            raise last_err
    raise RuntimeError(_empty_detail_message())


def _fetch_aweme_detail_curl(aweme_id: str, timeout: float) -> dict[str, Any]:
    if curl_requests is None:
        raise RuntimeError("curl-cffi 未安装")
    preset = "chrome"
    session = curl_requests.Session(impersonate=_CURL_IMPERSONATE)
    ua0 = _CHROME131_UA
    session.get("https://www.douyin.com/", headers=_html_warm_headers(ua0), timeout=timeout)
    page = session.get(
        f"https://www.douyin.com/video/{aweme_id}",
        headers=_html_warm_headers(ua0),
        timeout=timeout,
    )
    ms = _extract_ms_token(page.text or "") or ""

    last_err: RuntimeError | None = None
    for _attempt in range(3):
        detail_url, sign_ua = _signed_detail_request(aweme_id, ms_token=ms, preset=preset)
        headers = _json_api_headers(sign_ua, aweme_id)
        resp = session.get(detail_url, headers=headers, timeout=timeout)
        if resp.status_code >= 400:
            resp.raise_for_status()
        raw = (resp.text or "").strip()
        if raw:
            try:
                return _parse_aweme_detail_payload(resp, context="抖音接口")
            except RuntimeError as exc:
                last_err = exc
        else:
            last_err = RuntimeError(_empty_detail_message())
    if last_err:
        raise last_err
    raise RuntimeError(_empty_detail_message())


def fetch_aweme_detail(aweme_id: str, timeout: float = 25.0) -> dict[str, Any]:
    errors: list[Exception] = []
    try:
        return _fetch_aweme_detail_ies_share(aweme_id, timeout)
    except Exception as exc:
        errors.append(exc)
    if curl_requests is not None:
        try:
            return _fetch_aweme_detail_curl(aweme_id, timeout)
        except Exception as exc:
            errors.append(exc)
    try:
        return _fetch_aweme_detail_httpx(aweme_id, timeout)
    except Exception as exc:
        errors.append(exc)
    joined = "；".join(f"{type(e).__name__}：{e}" for e in errors)
    raise RuntimeError(f"抖音详情拉取失败（{joined}）") from errors[-1]


def media_request_headers(aweme_id: str, media_url: str) -> dict[str, str]:
    """Headers that work for CDN redirects from aweme.snssdk.com / douyinvod.com."""
    base = {"Accept": "*/*"}
    if "snssdk.com" in media_url or "douyinvod.com" in media_url:
        return {
            **base,
            "User-Agent": _IES_MOBILE_UA,
            "Referer": "https://www.douyin.com/",
        }
    return {
        **base,
        "User-Agent": _DOUYIN_UA,
        "Referer": f"https://www.douyin.com/video/{aweme_id}",
    }


def aweme_detail_to_probe_info(detail: dict[str, Any], webpage_url: str) -> dict[str, Any]:
    """Shape compatible with services._build_format_options (yt-dlp-like info dict)."""
    video = detail.get("video") or {}
    formats: list[dict[str, Any]] = []

    def push_from_play_addr(play_addr: dict[str, Any], fmt_id: str, tbr: float | None, height: int | None) -> None:
        if not play_addr:
            return
        urls = play_addr.get("url_list") or []
        if not urls:
            return
        stream_url = _prefer_play_url(urls[0])
        h = height if height is not None else play_addr.get("height")
        sz = play_addr.get("data_size")
        formats.append(
            {
                "format_id": fmt_id,
                "height": int(h) if h is not None else None,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
                "tbr": float(tbr or 0),
                "filesize": int(sz) if isinstance(sz, int) and sz > 0 else None,
                "url": stream_url,
            }
        )

    push_from_play_addr(video.get("play_addr"), "play_addr", None, video.get("height"))
    push_from_play_addr(video.get("download_addr"), "download_addr", None, None)

    for br in video.get("bit_rate") or []:
        pa = br.get("play_addr") or {}
        tbr = (br.get("bit_rate") or 0) / 1000.0 if br.get("bit_rate") else None
        name = br.get("gear_name") or "bitrate"
        push_from_play_addr(pa, str(name), tbr, pa.get("height"))

    if not formats:
        raise RuntimeError("作品详情中未找到可播放地址")

    thumb = None
    for key in ("cover", "origin_cover", "dynamic_cover"):
        c = video.get(key) or {}
        lst = c.get("url_list") if isinstance(c, dict) else None
        if lst:
            thumb = lst[0]
            break

    duration = None
    if isinstance(video.get("duration"), (int, float)):
        duration = int(video["duration"] / 1000) if video["duration"] > 1000 else int(video["duration"])
    music = detail.get("music") or {}
    if duration is None and isinstance(music.get("duration"), int):
        duration = music["duration"]

    return {
        "title": (detail.get("desc") or "Douyin video").strip() or "Douyin video",
        "webpage_url": webpage_url,
        "extractor": "DouyinWeb",
        "duration": float(duration) if duration is not None else None,
        "thumbnail": thumb,
        "formats": formats,
    }


def pick_download_url(detail: dict[str, Any], format_id: str | None) -> str:
    """Resolve format_id from our synthetic probe formats to a direct CDN URL."""
    info = aweme_detail_to_probe_info(detail, "")
    by_id = {f["format_id"]: f["url"] for f in info["formats"]}
    clean_id = None
    if format_id and format_id.startswith("douyin|"):
        clean_id = format_id.split("|", 1)[1]
    elif format_id and format_id in by_id:
        clean_id = format_id
    if clean_id and clean_id in by_id:
        return by_id[clean_id]
    best = max(info["formats"], key=lambda x: float(x.get("tbr") or 0))
    return best["url"]


def fetch_probe_for_douyin_url(url: str) -> dict[str, Any]:
    aweme_id = extract_aweme_id(url)
    if not aweme_id:
        raise ValueError("not a douyin video url")
    detail = fetch_aweme_detail(aweme_id)
    return aweme_detail_to_probe_info(detail, url)
