import secrets


API_KEY_PREFIX = "mas_"


def generate_api_key() -> str:
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def looks_like_api_key(value: str) -> bool:
    return bool(value and value.startswith(API_KEY_PREFIX))
