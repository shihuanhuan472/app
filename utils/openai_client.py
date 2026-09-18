"""OpenAI-compatible client helpers used by the application.

The project talks to both the official OpenAI API and OpenAI-compatible
inference gateways.  Keeping construction here avoids slightly different
client setup in every router and parser.
"""

import inspect
import json
import os
import re
from types import SimpleNamespace
from typing import Any, Optional

from openai import AsyncOpenAI, OpenAI


def create_openai_client(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> OpenAI:
    """Create a synchronous OpenAI-compatible client."""
    options: dict[str, Any] = {
        "api_key": api_key or "EMPTY",
    }
    if base_url:
        options["base_url"] = base_url
    if timeout is not None:
        options["timeout"] = timeout
    options.update(kwargs)
    return OpenAI(**options)


def create_async_openai_client(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    **kwargs: Any,
) -> AsyncOpenAI:
    """Create an asynchronous OpenAI-compatible client."""
    options: dict[str, Any] = {
        "api_key": api_key or "EMPTY",
    }
    if base_url:
        options["base_url"] = base_url
    if timeout is not None:
        options["timeout"] = timeout
    options.update(kwargs)
    return AsyncOpenAI(**options)


def _merge_extra_body(options: dict[str, Any], extra_body: dict[str, Any]) -> None:
    existing = options.get("extra_body")
    if isinstance(existing, dict):
        merged = dict(existing)
        merged.update(extra_body)
        options["extra_body"] = merged
    else:
        options["extra_body"] = extra_body


def create_chat_completion(
    client: Any,
    *,
    disable_qwen_thinking: bool = True,
    json_mode: bool = False,
    **kwargs: Any,
) -> Any:
    """Create a chat completion with project-wide generation defaults.

    JSON mode is opt-in because some OpenAI-compatible local servers reject
    response_format. Set AI_RESPONSE_FORMAT_JSON=1 after verifying support.
    """
    options = dict(kwargs)
    if disable_qwen_thinking:
        _merge_extra_body(
            options,
            {"chat_template_kwargs": {"enable_thinking": False}},
        )
    if json_mode and os.getenv("AI_RESPONSE_FORMAT_JSON", "0") == "1":
        options.setdefault("response_format", {"type": "json_object"})
    return client.chat.completions.create(**options)


def _strip_markdown_fence(text: str) -> str:
    stripped = (text or "").strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


def _strip_thinking_text(text: str) -> str:
    stripped = re.sub(r"<think\b[^>]*>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    if "</think>" in stripped.lower():
        parts = re.split(r"</think>", stripped, flags=re.IGNORECASE)
        stripped = parts[-1]
    return stripped.strip()


def _extract_first_json_value(text: str) -> str:
    start_chars = {"{": "}", "[": "]"}
    in_string = False
    escaped = False
    stack: list[str] = []
    start_index: int | None = None

    for index, char in enumerate(text or ""):
        if start_index is None:
            if char in start_chars:
                start_index = index
                stack.append(start_chars[char])
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in start_chars:
            stack.append(start_chars[char])
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return text[start_index:index + 1]

    raise ValueError("AI返回内容中未找到完整JSON对象")


def parse_ai_json_response(text: str) -> Any:
    """Parse JSON from model output, tolerating think text and fenced blocks."""
    cleaned = _strip_markdown_fence(_strip_thinking_text(text or ""))
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return json.loads(_extract_first_json_value(cleaned))


def parse_chat_completion_json(response: Any) -> Any:
    """Validate a chat completion and parse an object-shaped JSON response.

    Models occasionally wrap the requested object in a one-item array. Keep
    that harmless deviation compatible, but reject ambiguous/malformed
    structures before callers start treating the value as a mapping.
    """
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("AI未返回候选结果")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise ValueError("AI输出被截断，文件内容过长，请尝试缩短文件")
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not str(content or "").strip():
        raise ValueError("AI返回内容为空")
    payload = parse_ai_json_response(content)
    if isinstance(payload, list):
        if len(payload) == 1 and isinstance(payload[0], dict):
            payload = payload[0]
        else:
            raise ValueError("AI返回JSON顶层必须是对象，不能是多元素数组")
    if not isinstance(payload, dict):
        raise ValueError("AI返回JSON顶层必须是对象")
    return payload


def maybe_wrap_openai_client(client: Any) -> Any:
    """Return an async-compatible client for an injected LLM client.

    Most callers already provide an async OpenAI-compatible client.  Tests and
    integrations may provide a synchronous client, so adapt only its chat
    completion method when necessary.
    """
    if client is None:
        return None

    create = getattr(
        getattr(getattr(client, "chat", None), "completions", None),
        "create",
        None,
    )
    if create is None or inspect.iscoroutinefunction(create):
        return client

    async def create_async(**kwargs: Any) -> Any:
        result = create(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create_async),
        )
    )
