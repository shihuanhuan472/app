import json
import os
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


@lru_cache(maxsize=1)
def _load_sensitive_terms() -> Dict[str, str]:
    terms_file = Path(os.getenv("SENSITIVE_TERMS_FILE", str(DEFAULT_TERMS_FILE)))
    if not terms_file.is_file():
        return {}
    try:
        with terms_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as error:
        print(f"[脱敏] 读取敏感词配置失败: {error}")
        return {}

    if not isinstance(data, dict):
        return {}
    return {
        str(source).strip(): str(mask).strip()
        for source, mask in data.items()
        if str(source).strip() and str(mask).strip()
    }


def desensitize_text(text: Any) -> Any:
    if not isinstance(text, str) or not text:
        return text

    result = text
    for source, mask in sorted(_load_sensitive_terms().items(), key=lambda item: len(item[0]), reverse=True):
        result = result.replace(source, mask)
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
