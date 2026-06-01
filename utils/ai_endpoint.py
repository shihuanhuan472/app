import os


def _normalize_base_url(url: str) -> str:
    return (url or "").rstrip("/")


def _build_base_url_from_legacy(server_ip: str, port: str) -> str:
    return f"http://{server_ip}:{port}/v1"


def get_ai_base_url() -> str:
    direct_url = os.getenv("AI_BASE_URL")
    if direct_url:
        return _normalize_base_url(direct_url)

    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    port = os.getenv("AI_PORT", "8000")
    return _build_base_url_from_legacy(server_ip, port)


def get_ai_base_url_alt() -> str:
    direct_url = os.getenv("AI_BASE_URL_ALT")
    if direct_url:
        return _normalize_base_url(direct_url)

    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    port = os.getenv("AI_PORT_ALT", "8001")
    return _build_base_url_from_legacy(server_ip, port)
