from app.llm_runtime import llm_configured, openai_api_key_var, resolve_openai_config


def test_llm_configured_from_header(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    t = openai_api_key_var.set("sk-test-header")
    try:
        assert llm_configured()
        assert resolve_openai_config()[0] == "sk-test-header"
    finally:
        openai_api_key_var.reset(t)


def test_llm_configured_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    t = openai_api_key_var.set(None)
    try:
        assert llm_configured()
        assert resolve_openai_config()[0] == "sk-env"
    finally:
        openai_api_key_var.reset(t)
