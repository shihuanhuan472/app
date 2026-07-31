import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models import AiUsageLog


def _usage_attr(obj: Any, name: str) -> Optional[int]:
    value = getattr(obj, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def extract_ai_usage(response: Any) -> dict:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "raw_usage_json": None,
        }

    input_tokens = _usage_attr(usage, "input_tokens")
    output_tokens = _usage_attr(usage, "output_tokens")
    prompt_tokens = _usage_attr(usage, "prompt_tokens")
    completion_tokens = _usage_attr(usage, "completion_tokens")
    total_tokens = _usage_attr(usage, "total_tokens")

    if input_tokens is None:
        input_tokens = prompt_tokens or 0
    if output_tokens is None:
        output_tokens = completion_tokens or 0
    if total_tokens is None:
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    raw_usage_json = None
    try:
        raw_usage_json = json.dumps(usage.model_dump() if hasattr(usage, "model_dump") else dict(usage), ensure_ascii=False)
    except Exception:
        try:
            raw_usage_json = json.dumps(str(usage), ensure_ascii=False)
        except Exception:
            raw_usage_json = None

    return {
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int(total_tokens or 0),
        "prompt_tokens": int(prompt_tokens or input_tokens or 0),
        "completion_tokens": int(completion_tokens or output_tokens or 0),
        "raw_usage_json": raw_usage_json,
    }


async def record_ai_usage(
    db: AsyncSession,
    *,
    user_id: Optional[int] = None,
    session_id: Optional[int] = None,
    message_id: Optional[int] = None,
    provider: str = "openai",
    model: str = "",
    request_type: str = "",
    response: Any = None,
    usage: Optional[dict] = None,
    status: str = "success",
    error_message: Optional[str] = None,
) -> AiUsageLog:
    payload = usage or extract_ai_usage(response)
    log = AiUsageLog(
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        provider=provider,
        model=model or "",
        request_type=request_type or "",
        status=status,
        input_tokens=int(payload.get("input_tokens") or 0),
        output_tokens=int(payload.get("output_tokens") or 0),
        total_tokens=int(payload.get("total_tokens") or 0),
        prompt_tokens=int(payload.get("prompt_tokens") or 0),
        completion_tokens=int(payload.get("completion_tokens") or 0),
        raw_usage_json=payload.get("raw_usage_json"),
        error_message=error_message,
        created_time=datetime.now(),
    )
    db.add(log)
    return log
