"""Resolve Bilibili Cookie from per-request header (browser settings) or server env."""

from __future__ import annotations

import os
from contextvars import ContextVar

bilibili_cookie_var: ContextVar[str | None] = ContextVar("bilibili_cookie", default=None)


def _normalize_cookie(raw: str) -> str:
    s = raw.strip()
    if not s:
        return ""
    if "SESSDATA=" in s or ";" in s:
        return s
    return f"SESSDATA={s}"


def resolve_bilibili_cookie() -> str | None:
    """Request header (settings UI) overrides BILIBILI_COOKIE / BILIBILI_SESSDATA env."""
    ctx = bilibili_cookie_var.get()
    if ctx:
        normalized = _normalize_cookie(ctx)
        return normalized or None
    raw = (os.environ.get("BILIBILI_COOKIE") or "").strip()
    if raw:
        return raw
    sess = (os.environ.get("BILIBILI_SESSDATA") or "").strip()
    if sess:
        return f"SESSDATA={sess}"
    return None
