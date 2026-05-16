from app.bilibili_cookie import _normalize_cookie, bilibili_cookie_var, resolve_bilibili_cookie


def test_normalize_cookie_sessdata_only():
    assert _normalize_cookie("abc123") == "SESSDATA=abc123"


def test_normalize_cookie_full_header():
    raw = "SESSDATA=abc; buvid3=xyz"
    assert _normalize_cookie(raw) == raw


def test_bilibili_legacy_headers_include_cookie(monkeypatch):
    monkeypatch.delenv("BILIBILI_COOKIE", raising=False)
    monkeypatch.delenv("BILIBILI_SESSDATA", raising=False)
    from app.bilibili_legacy import _bilibili_headers

    token = bilibili_cookie_var.set("SESSDATA=legacy_test")
    try:
        hdr = _bilibili_headers("BV1test")
        assert "SESSDATA=legacy_test" in hdr.get("Cookie", "")
    finally:
        bilibili_cookie_var.reset(token)


def test_resolve_prefers_request_context(monkeypatch):
    monkeypatch.delenv("BILIBILI_COOKIE", raising=False)
    monkeypatch.delenv("BILIBILI_SESSDATA", raising=False)
    token = bilibili_cookie_var.set("SESSDATA=from_browser")
    try:
        assert resolve_bilibili_cookie() == "SESSDATA=from_browser"
    finally:
        bilibili_cookie_var.reset(token)
