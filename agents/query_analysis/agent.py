import asyncio
import json
import os
from typing import Any, Callable, Optional

from pydantic import ValidationError

from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_async_openai_client, create_chat_completion, parse_chat_completion_json

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schemas import QueryAnalysis


TraceSink = Callable[[str, dict[str, Any]], None]


class QueryAnalysisAgent:
    """Analyze a knowledge question and produce an observable retrieval plan."""

    def __init__(
        self,
        *,
        client: Any = None,
        trace_sink: Optional[TraceSink] = None,
        verbose: bool = True,
    ) -> None:
        self.client = client
        self.trace_sink = trace_sink
        self.verbose = verbose

    async def analyze(
        self,
        question: str,
        *,
        conversation_context: str = "",
        timeout: Optional[float] = None,
    ) -> QueryAnalysis:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question 不能为空")

        self._emit("input", {"question": question, "conversation_context": conversation_context})
        request_timeout = timeout or float(os.getenv("QUERY_ANALYSIS_TIMEOUT", "60"))
        client = self.client or create_async_openai_client(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY", "EMPTY"),
            timeout=request_timeout,
        )

        self._emit(
            "request",
            {
                "model": os.getenv("QUERY_ANALYSIS_MODEL")
                or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                "timeout_seconds": request_timeout,
            },
        )
        response = await asyncio.wait_for(
            create_chat_completion(
                client,
                model=os.getenv("QUERY_ANALYSIS_MODEL")
                or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(question, conversation_context)},
                ],
                temperature=0,
                max_tokens=int(os.getenv("QUERY_ANALYSIS_MAX_TOKENS", "1200")),
                json_mode=True,
            ),
            timeout=request_timeout,
        )

        payload = parse_chat_completion_json(response)
        payload["original_question"] = question
        payload["source"] = "llm"
        try:
            analysis = QueryAnalysis.model_validate(payload)
        except ValidationError as error:
            self._emit("validation_error", {"errors": error.errors(include_url=False)})
            raise

        self._emit("understanding", analysis.understanding.model_dump(mode="json"))
        self._emit("domain_terms", analysis.domain_terms.model_dump(mode="json"))
        self._emit("retrieval_strategy", analysis.retrieval_strategy.model_dump(mode="json"))
        self._emit("result", analysis.model_dump(mode="json"))
        return analysis

    def _emit(self, stage: str, payload: dict[str, Any]) -> None:
        if self.verbose:
            serialized = json.dumps(payload, ensure_ascii=False, default=str)
            print(f"[QueryAnalysis][{stage}] {serialized}")
        if self.trace_sink is not None:
            self.trace_sink(stage, payload)
