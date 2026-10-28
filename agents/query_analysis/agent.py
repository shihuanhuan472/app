from utils.qa_timing import timed_step
import asyncio
import json
import logging
import os
import time
from urllib.parse import urlsplit
from utils.retrieval_log import _request_id, log_retrieval
from typing import Any, Callable, Optional

from pydantic import ValidationError

from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_async_openai_client, create_chat_completion, parse_chat_completion_json

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schemas import QueryAnalysis
from .compact import CompactQueryAnalysis


_timing_logger = logging.getLogger("uvicorn.error.qa_timing")


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

    @timed_step('query.analysis_llm')
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
        setup_started = time.perf_counter()
        attempt = 0
        phase_starts = {}

        async def trace_http(event, info):
            # httpcore trace events expose TCP/TLS, upload, header wait and body read.
            phase, _, state = event.rpartition(".")
            key = (attempt, phase)
            if state == "started":
                phase_starts[key] = time.perf_counter()
            started = phase_starts.get(key, time.perf_counter())
            details = {
                "attempt": attempt, "event": event,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            if state == "failed":
                details["error_type"] = type(info.get("exception")).__name__
            self._emit("http_phase", details)

        async def on_request(request):
            nonlocal attempt
            attempt += 1
            request.extensions["trace"] = trace_http
            target = urlsplit(str(request.url))
            self._emit("http_attempt", {
                "attempt": attempt, "endpoint": target.scheme + "://" + target.netloc + target.path,
                "since_setup_ms": round((time.perf_counter() - setup_started) * 1000, 3),
            })

        async def on_response(response):
            self._emit("http_headers", {"attempt": attempt, "status_code": response.status_code})

        client_options = {}
        if self.client is None:
            import httpx
            client_options["http_client"] = httpx.AsyncClient(
                timeout=request_timeout,
                event_hooks={"request": [on_request], "response": [on_response]},
            )
        client = self.client or create_async_openai_client(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY", "EMPTY"),
            timeout=request_timeout,
            **client_options,
        )
        self._emit("client_setup", {
            "elapsed_ms": round((time.perf_counter() - setup_started) * 1000, 3),
            "http_trace_enabled": self.client is None,
        })

        self._emit(
            "request",
            {
                "model": os.getenv("QUERY_ANALYSIS_MODEL")
                or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                "timeout_seconds": request_timeout,
            },
        )
        llm_started = time.perf_counter()
        llm_status = "ok"
        response = None
        try:
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
                    max_tokens=int(os.getenv("QUERY_ANALYSIS_MAX_TOKENS", "2200")),
                    json_mode=True,
                ),
                timeout=request_timeout,
            )
        except BaseException as error:
            llm_status = type(error).__name__
            raise
        finally:
            elapsed_ms = (time.perf_counter() - llm_started) * 1000
            usage = getattr(response, "usage", None) if response is not None else None
            self._emit("http_complete", {
                "status": llm_status, "elapsed_ms": round(elapsed_ms, 3), "attempts": attempt,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
            })
            _timing_logger.info(
                "[QA_TIMING] request_id=%s stage=query.analysis_http status=%s elapsed_ms=%.1f "
                "prompt_tokens=%s completion_tokens=%s",
                _request_id.get(), llm_status, elapsed_ms,
                getattr(usage, "prompt_tokens", "-"),
                getattr(usage, "completion_tokens", "-"),
            )

        parse_started = time.perf_counter()
        payload = parse_chat_completion_json(response)
        self._emit("json_parse", {"elapsed_ms": round((time.perf_counter() - parse_started) * 1000, 3)})
        self._emit("model_output", dict(payload))
        validation_started = time.perf_counter()
        try:
            if "retrieval_strategy" in payload:
                # Accept old responses while deployments/prompts transition.
                payload["original_question"] = question
                payload["source"] = "llm"
                analysis = QueryAnalysis.model_validate(payload)
            else:
                analysis = CompactQueryAnalysis.model_validate(payload).expand(question)
        except ValidationError as error:
            self._emit("validation_error", {"errors": error.errors(include_url=False)})
            raise

        self._emit("schema_validation", {"elapsed_ms": round((time.perf_counter() - validation_started) * 1000, 3)})

        self._emit("understanding", analysis.understanding.model_dump(mode="json"))
        self._emit("domain_terms", analysis.domain_terms.model_dump(mode="json"))
        self._emit("retrieval_strategy", analysis.retrieval_strategy.model_dump(mode="json"))
        self._emit("result", analysis.model_dump(mode="json"))
        return analysis

    def _emit(self, stage: str, payload: dict[str, Any]) -> None:
        log_retrieval(f"QueryAnalysis.{stage}", payload)
        if self.verbose:
            serialized = json.dumps(payload, ensure_ascii=False, default=str)
            print(f"[QueryAnalysis][{stage}] {serialized}")
        if self.trace_sink is not None:
            self.trace_sink(stage, payload)
