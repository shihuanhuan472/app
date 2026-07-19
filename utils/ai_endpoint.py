import os


DEFAULT_AI_BASE_URL = "http://192.168.246.200:8000/v1"
DEFAULT_AI_BASE_URL_ALT = "http://192.168.246.200:8001/v1"


def _normalize_base_url(url: str) -> str:
    return (url or "").rstrip("/")


def get_ai_base_url() -> str:
    return _normalize_base_url(os.getenv("AI_BASE_URL") or DEFAULT_AI_BASE_URL)


def get_ai_base_url_alt() -> str:
    return _normalize_base_url(os.getenv("AI_BASE_URL_ALT") or DEFAULT_AI_BASE_URL_ALT)
