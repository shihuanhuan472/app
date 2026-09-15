import os

def _normalize_base_url(url: str) -> str:
    return (url or "").rstrip("/")


def get_ai_base_url() -> str:
    return _normalize_base_url(os.getenv("AI_BASE_URL"))


def get_ai_base_url_alt() -> str:
    return _normalize_base_url(os.getenv("AI_BASE_URL_ALT"))

def get_qwen_no_thinking_options() -> dict:
    """Disable Qwen3 reasoning output for OpenAI-compatible servers."""
    return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
