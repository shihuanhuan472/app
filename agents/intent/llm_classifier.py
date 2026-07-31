import asyncio
import json
import os
import re
from typing import Any, Dict, Optional

from utils.ai_endpoint import get_ai_base_url

from .prompts import SYSTEM_PROMPT
from .schemas import RouteDecision
from .taxonomy import IntentRoute


def _parse_json_object(content: str) -> Dict[str, Any]:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def classify_with_llm(question: str, timeout: Optional[float] = None) -> RouteDecision:
    from openai import AsyncOpenAI

    request_timeout = timeout or float(os.getenv("INTENT_ROUTER_TIMEOUT", "8"))
    client = AsyncOpenAI(
        base_url=get_ai_base_url(),
        api_key=os.getenv("API_KEY", "EMPTY"),
        timeout=request_timeout,
    )
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=os.getenv("INTENT_ROUTER_MODEL") or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=int(os.getenv("INTENT_ROUTER_MAX_TOKENS", "500")),
        ),
        timeout=request_timeout,
    )
    payload = _parse_json_object(response.choices[0].message.content or "")
    payload["source"] = "llm_router"
    payload["use_rag"] = payload.get("route") == IntentRoute.KNOWLEDGE_SEARCH.value
    return RouteDecision.model_validate(payload)
