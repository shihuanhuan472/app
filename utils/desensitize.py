import json
import os
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TERMS_FILE = PROJECT_ROOT / "config" / "sensitive_terms.json"

SKIP_KEYS = {
    "image_url",
    "image_urls",
    "matched_image_urls",
    "user_uploaded_images",
    "origin_file_dir",
    "origin_file_name",
    "url",
    "path",
    "file_path",
}


def get_sensitive_terms_file() -> Path:
    return Path(os.getenv("SENSITIVE_TERMS_FILE", str(DEFAULT_TERMS_FILE)))


def normalize_sensitive_terms(data: Any) -> Dict[str, str]:
    if not isinstance(data, dict):
        return {}
    return {
        str(source).strip(): str(mask).strip()
        for source, mask in data.items()
        if str(source).strip() and str(mask).strip()
    }


def read_sensitive_terms(strict: bool = False) -> Dict[str, str]:
    terms_file = get_sensitive_terms_file()
    if not terms_file.is_file():
        return {}
    try:
        with terms_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as error:
        if strict:
            raise
        print(f"[desensitize] failed to read sensitive terms config: {error}")
        return {}

    if strict and not isinstance(data, dict):
        raise ValueError("Sensitive terms config must be a JSON object")
    return normalize_sensitive_terms(data)


def reload_sensitive_terms() -> None:
    _load_sensitive_terms.cache_clear()


def write_sensitive_terms(terms: Dict[str, str]) -> Dict[str, str]:
    normalized_terms = normalize_sensitive_terms(terms)
    terms_file = get_sensitive_terms_file()
    terms_file.parent.mkdir(parents=True, exist_ok=True)

    tmp_file = terms_file.with_name(f".{terms_file.name}.{uuid.uuid4().hex}.tmp")
    with tmp_file.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(normalized_terms, f, ensure_ascii=False, indent=2)
        f.write("\n")

    tmp_file.replace(terms_file)
    reload_sensitive_terms()
    return normalized_terms


@lru_cache(maxsize=1)
def _load_sensitive_terms() -> Dict[str, str]:
    return read_sensitive_terms(strict=False)


def _replace_sensitive_term(text: str, source: str, mask: str) -> str:
    if re.search(r"[A-Za-z]", source):
        return re.sub(re.escape(source), lambda _match: mask, text, flags=re.IGNORECASE)
    return text.replace(source, mask)


def desensitize_text(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text

    result = text
    for source, mask in sorted(_load_sensitive_terms().items(), key=lambda item: len(item[0]), reverse=True):
        result = _replace_sensitive_term(result, source, mask)
    return result


def desensitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return desensitize_text(value)
    if isinstance(value, list):
        return [desensitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(desensitize_value(item) for item in value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key) in SKIP_KEYS:
                result[key] = item
            else:
                result[key] = desensitize_value(item)
        return result
    return value


def max_sensitive_term_length() -> int:
    terms = _load_sensitive_terms()
    return max((len(source) for source in terms.keys()), default=0)


def desensitize_json_payload_string(value: Any) -> Any:
    if not value:
        return value

    text = str(value)
    if not text.strip().startswith(("[", "{")):
        return value

    try:
        return json.dumps(desensitize_value(json.loads(text)), ensure_ascii=False)
    except Exception:
        return value
