"""QA stage timings shared by console and retrieval JSONL logs."""
import logging
import time
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4

from utils.retrieval_log import _request_id, log_retrieval

_current = ContextVar("qa_timing", default=None)
_logger = logging.getLogger("uvicorn.error.qa_timing")


def timing_event(stage, status="ok", started=None):
    state = _current.get()
    if state is None:
        return
    if stage == "answer.stream_error":
        state["error"] = status
    if stage == "request.total" and status == "ok":
        status = state.get("error", status)
    now = time.perf_counter()
    elapsed = (now - (started if started is not None else state["started"])) * 1000
    total = (now - state["started"]) * 1000
    payload = {"session_id": state["session_id"], "status": status,
               "elapsed_ms": round(elapsed, 3), "total_ms": round(total, 3)}
    log_retrieval("Timing." + stage, payload)
    _logger.info("[QA_TIMING] request_id=%s session_id=%s stage=%s status=%s elapsed_ms=%.1f total_ms=%.1f",
                 state["id"], state["session_id"], stage, status, elapsed, total)


def timed_step(stage):
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            started = time.perf_counter()
            timing_event(stage, "start", started)
            status = "ok"
            try:
                return await function(*args, **kwargs)
            except BaseException as exc:
                status = type(exc).__name__
                raise
            finally:
                timing_event(stage, status, started)
        return wrapped
    return decorate


def trace_qa(function):
    @wraps(function)
    async def wrapped(message, *args, **kwargs):
        state = {"id": uuid4().hex, "session_id": message.session_id,
                 "started": time.perf_counter()}
        token = _current.set(state)
        retrieval_token = _request_id.set(state["id"])
        deferred = False
        status = "ok"
        timing_event("request", "start")
        try:
            result = await function(message, *args, **kwargs)
            if hasattr(result, "body_iterator"):
                iterator = result.body_iterator

                async def stream():
                    stream_token = _current.set(state)
                    stream_retrieval_token = _request_id.set(state["id"])
                    stream_status = "ok"
                    try:
                        async for chunk in iterator:
                            yield chunk
                    except BaseException as exc:
                        stream_status = type(exc).__name__
                        raise
                    finally:
                        timing_event("request.total", stream_status)
                        _request_id.reset(stream_retrieval_token)
                        _current.reset(stream_token)

                result.body_iterator = stream()
                deferred = True
                timing_event("prepare.complete")
            return result
        except BaseException as exc:
            status = type(exc).__name__
            raise
        finally:
            if not deferred:
                timing_event("request.total", status)
            _request_id.reset(retrieval_token)
            _current.reset(token)
    return wrapped
