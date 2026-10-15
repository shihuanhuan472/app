"""Optional structured retrieval logs, isolated by request and process."""
import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4
from threading import Lock


_request_id = ContextVar("retrieval_log_request_id", default=None)
_logger = logging.getLogger(__name__)
_write_lock = Lock()


def log_retrieval(stage, payload):
    if os.getenv("RETRIEVAL_LOG_ENABLED", "false").strip().lower() not in {"true", "1", "yes", "on"}:
        return
    try:
        now = datetime.now().astimezone()
        directory = Path(__file__).resolve().parents[1] / "runtime" / "logs" / "retrieval"
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "time": now.isoformat(),
            "request_id": _request_id.get(),
            "stage": stage,
            "payload": payload,
        }
        path = directory / f"retrieval_{now:%Y%m%d}_{os.getpid()}.jsonl"
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with _write_lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
    except Exception:
        _logger.warning("Unable to write retrieval log", exc_info=True)


def trace_retrieval(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        token = _request_id.set(uuid4().hex)
        log_retrieval("request.start", {"question": args[1] if len(args) > 1 else kwargs.get("question"),
                                        "session_id": kwargs.get("session_id")})
        try:
            result = await function(*args, **kwargs)
            log_retrieval("request.complete", {"documents": result[0], "actions": result[1]})
            return result
        except Exception as exc:
            log_retrieval("request.error", {"type": type(exc).__name__, "error": str(exc)})
            raise
        finally:
            _request_id.reset(token)
    return wrapped
