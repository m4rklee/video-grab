"""OpenAI-compatible LLM config from per-request headers (browser settings) or server env."""

from __future__ import annotations

import os
from contextvars import ContextVar

openai_api_key_var: ContextVar[str | None] = ContextVar("openai_api_key", default=None)
openai_base_url_var: ContextVar[str | None] = ContextVar("openai_base_url", default=None)
summarize_model_var: ContextVar[str | None] = ContextVar("summarize_model", default=None)


def resolve_openai_config() -> tuple[str, str, str]:
    key = (openai_api_key_var.get() or os.environ.get("OPENAI_API_KEY") or "").strip()
    base = (
        openai_base_url_var.get()
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.openai.com/v1"
    ).strip().rstrip("/")
    model = (summarize_model_var.get() or os.environ.get("SUMMARIZE_MODEL") or "gpt-4o-mini").strip()
    return key, base, model


def llm_configured() -> bool:
    return bool(resolve_openai_config()[0])
