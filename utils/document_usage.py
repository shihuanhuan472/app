"""Collect document-processing model usage without sharing DB sessions across threads."""

import asyncio
import logging
from contextvars import ContextVar, copy_context
from datetime import datetime

from database import AsyncSessionLocal
from utils.ai_usage import extract_ai_usage, record_ai_usage

logger = logging.getLogger(__name__)
_collector = ContextVar("document_model_usage", default=None)


def capture_document_usage(response, model):
    collector = _collector.get()
    if collector is not None:
        collector.append({"response": response, "model": model, "created_time": datetime.now()})


async def persist_document_usage(records, request_type, user_id=None):
    if not records:
        return
    try:
        # A later parsing/JSON/vectorization failure must not erase consumed tokens.
        async with AsyncSessionLocal() as db:
            for record in records:
                response = record["response"]
                usage = extract_ai_usage(response)
                missing = usage["raw_usage_json"] is None
                log = await record_ai_usage(
                    db, user_id=user_id, model=record["model"] or "",
                    request_type=request_type, usage=usage,
                    status="usage_missing" if missing else "success",
                    error_message="Model response did not include usage" if missing else None,
                )
                log.created_time = record["created_time"]
                if missing:
                    logger.warning("Document model response missing usage: %s / %s", request_type, record["model"])
            await db.commit()
    except Exception:
        logger.exception("Failed to persist document model usage: %s (%s calls)", request_type, len(records))


async def document_model_to_thread(func, *args, request_type="parse_document", user_id=None, **kwargs):
    records = []
    token = _collector.set(records)
    try:
        context = copy_context()
        return await asyncio.to_thread(context.run, func, *args, **kwargs)
    finally:
        _collector.reset(token)
        await persist_document_usage(records, request_type, user_id)
