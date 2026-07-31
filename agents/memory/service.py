import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.intent import RouteDecision
from models import (
    AiMessageTrace,
    ConversationContext,
    ConversationContextEvent,
    ConversationSummary,
    Message,
)
from utils.desensitize import desensitize_value


ANSWER_AUDIT_ROUTE = "answer_audit"
CONTEXTUAL_FOLLOWUP_REASON = "contextual_followup"
CONTEXTUAL_RETRY_REASON = "contextual_retry"
TRACE_PREVIEW_MAX_CHARS = 1200


def get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def json_dumps(value: Any) -> str:
    return json.dumps(desensitize_value(value), ensure_ascii=False)


def json_loads(value: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def answer_preview(answer: str) -> str:
    return str(answer or "").strip()[:TRACE_PREVIEW_MAX_CHARS]


def normalize_reference_image_values(raw_value: Any) -> List[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except Exception:
            raw_value = raw_value.split(",")
    if isinstance(raw_value, dict):
        raw_value = raw_value.values()
    elif not isinstance(raw_value, (list, tuple, set)):
        raw_value = [raw_value]

    images: List[str] = []
    for item in raw_value:
        text = str(item or "").strip()
        if text and text not in images:
            images.append(text)
    return images


def normalize_stored_reference_chunk(chunk: Any) -> Dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    normalized = dict(chunk)
    if not normalized.get("content") and normalized.get("preview"):
        normalized["content"] = normalized.get("preview")
    return normalized


def normalize_stored_reference_doc(doc: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(doc, dict) or doc.get("doc_id") is None:
        return None
    try:
        doc_id = int(doc.get("doc_id"))
    except (TypeError, ValueError):
        return None

    image_urls = doc.get("image_urls") or doc.get("reference_images") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    raw_chunks = doc.get("chunks") or [
        {"preview": preview}
        for preview in (doc.get("chunk_previews") or [])
        if preview
    ]

    return {
        "doc_id": doc_id,
        "library_type": normalize_library_type(doc.get("library_type", "breakdown")),
        "title": doc.get("title", ""),
        "score": float(doc.get("score", 0.0) or 0.0),
        "chunks": [
            chunk
            for chunk in (
                normalize_stored_reference_chunk(chunk)
                for chunk in raw_chunks
            )
            if chunk
        ],
        "matched_image_urls": doc.get("matched_image_urls") or image_urls,
        "evidence_image_urls": doc.get("evidence_image_urls") or image_urls,
        "image_urls": image_urls,
        "evidence_section_ids": doc.get("evidence_section_ids") or [],
        "evidence_section_titles": doc.get("evidence_section_titles") or [],
        "reused_from_history": True,
    }


def parse_reference_documents_payload(raw_payload: Any) -> List[Dict[str, Any]]:
    payload = json_loads(raw_payload, [])
    if not isinstance(payload, list):
        return []
    reference_docs = []
    for item in payload:
        normalized = normalize_stored_reference_doc(item)
        if normalized:
            reference_docs.append(normalized)
    return reference_docs


def reference_docs_for_trace(reference_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    docs = []
    for doc in reference_docs or []:
        if doc.get("doc_id") is None:
            continue
        docs.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": normalize_library_type(doc.get("library_type", "breakdown")),
            "title": doc.get("title", ""),
            "score": float(doc.get("score", 0.0) or 0.0),
            "reused_from_history": bool(doc.get("reused_from_history")),
            "evidence_section_titles": doc.get("evidence_section_titles") or [],
            "chunk_previews": [
                str(chunk.get("content") or chunk.get("preview") or "")[:180]
                for chunk in (doc.get("chunks") or [])[:3]
                if isinstance(chunk, dict)
            ],
        })
    return docs


def reference_images_for_trace(reference_docs: List[Dict[str, Any]]) -> List[str]:
    images: List[str] = []
    for doc in reference_docs or []:
        for raw_value in (
            doc.get("evidence_image_urls"),
            doc.get("image_urls"),
            doc.get("matched_image_urls"),
        ):
            for image in normalize_reference_image_values(raw_value):
                if image and image not in images:
                    images.append(image)
        for chunk in (doc.get("chunks") or [])[:5]:
            if isinstance(chunk, dict):
                for image in normalize_reference_image_values(chunk.get("image_url")):
                    if image and image not in images:
                        images.append(image)
    return images[:10]


def route_value(decision: RouteDecision) -> str:
    return getattr(getattr(decision, "route", None), "value", str(getattr(decision, "route", "")))


def slots_to_dict(decision: RouteDecision) -> Dict[str, Any]:
    slots = getattr(decision, "slots", None)
    if not slots:
        return {}
    if hasattr(slots, "model_dump"):
        return slots.model_dump(exclude_none=True)
    if hasattr(slots, "dict"):
        return slots.dict(exclude_none=True)
    if isinstance(slots, dict):
        return {key: value for key, value in slots.items() if value is not None}
    return {}


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def load_recent_messages(
        self,
        session_id: int,
        before_order: int,
        limit: int = 6,
    ) -> List[Message]:
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id, Message.message_order < before_order)
            .order_by(desc(Message.message_order))
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def load_previous_reference_documents(
        self,
        session_id: int,
        before_order: int,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        history = await self.load_recent_messages(session_id, before_order, limit=limit)
        for message in reversed(history or []):
            if int(getattr(message, "role", 0) or 0) != 0:
                continue
            reference_docs = parse_reference_documents_payload(
                getattr(message, "ai_reference_doc_ids", None)
            )
            if reference_docs:
                return reference_docs
        return []

    async def load_recent_ai_traces(
        self,
        session_id: int,
        limit: int = 6,
        exclude_routes: Optional[Iterable[str]] = None,
    ) -> List[AiMessageTrace]:
        result = await self.db.execute(
            select(AiMessageTrace)
            .where(AiMessageTrace.session_id == session_id)
            .order_by(desc(AiMessageTrace.created_time), desc(AiMessageTrace.id))
            .limit(limit)
        )
        traces = list(result.scalars().all())
        traces.reverse()
        excluded = set(exclude_routes or [])
        if excluded:
            traces = [trace for trace in traces if trace.route not in excluded]
        return traces

    async def get_active_context(self, session_id: int) -> Optional[ConversationContext]:
        result = await self.db.execute(
            select(ConversationContext).where(ConversationContext.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_active_context_dict(self, session_id: int) -> Optional[Dict[str, Any]]:
        context = await self.get_active_context(session_id)
        return self.context_to_dict(context) if context else None

    async def load_recent_context_events(
        self,
        session_id: int,
        limit: int = 5,
    ) -> List[ConversationContextEvent]:
        result = await self.db.execute(
            select(ConversationContextEvent)
            .where(ConversationContextEvent.session_id == session_id)
            .order_by(desc(ConversationContextEvent.created_time), desc(ConversationContextEvent.id))
            .limit(limit)
        )
        events = list(result.scalars().all())
        events.reverse()
        return events

    async def load_latest_summary(self, session_id: int) -> Optional[ConversationSummary]:
        result = await self.db.execute(
            select(ConversationSummary)
            .where(ConversationSummary.session_id == session_id)
            .order_by(desc(ConversationSummary.end_message_order), desc(ConversationSummary.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    def context_to_dict(self, context: ConversationContext) -> Dict[str, Any]:
        return {
            "session_id": context.session_id,
            "status": context.status,
            "active_issue": context.active_issue,
            "active_device": context.active_device,
            "active_component": context.active_component,
            "active_symptom": context.active_symptom,
            "active_error_code": context.active_error_code,
            "active_question": context.active_question,
            "active_query": context.active_query,
            "active_route": context.active_route,
            "active_reason": context.active_reason,
            "active_reference_docs": json_loads(context.active_reference_docs_json, []),
            "active_reference_images": json_loads(context.active_reference_images_json, []),
            "slots": json_loads(context.slots_json, {}),
            "summary_text": context.summary_text,
            "last_user_message_id": context.last_user_message_id,
            "last_ai_message_id": context.last_ai_message_id,
            "last_trace_id": context.last_trace_id,
            "turn_count": context.turn_count,
            "updated_time": context.updated_time.isoformat() if context.updated_time else None,
        }

    async def create_ai_trace(
        self,
        user_message: Message,
        ai_message: Optional[Message],
        decision: RouteDecision,
        retrieval_query: str,
        reference_docs: List[Dict[str, Any]],
        actions: List[str],
        used_previous_refs: bool = False,
        validation: Optional[Dict[str, Any]] = None,
        answer: str = "",
        status_value: str = "pending",
        error_message: Optional[str] = None,
        model_name: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
    ) -> Optional[int]:
        if not get_bool_env("TRACE_PERSIST_ENABLED", True):
            return None
        try:
            trace = AiMessageTrace(
                session_id=user_message.session_id,
                user_message_id=user_message.id,
                ai_message_id=ai_message.id if ai_message else None,
                route=route_value(decision),
                reason=decision.reason,
                original_question=user_message.content_text,
                query_rewrite=decision.query_rewrite,
                retrieval_query=retrieval_query,
                used_previous_refs=1 if used_previous_refs else 0,
                reference_docs_json=json_dumps(reference_docs_for_trace(reference_docs)),
                reference_images_json=json_dumps(reference_images_for_trace(reference_docs)),
                actions_json=json_dumps(actions or []),
                validation_json=json_dumps(validation or {}),
                answer_preview=answer_preview(answer),
                model_name=model_name or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                status=status_value,
                error_message=(error_message[:1000] if error_message else None),
                created_time=datetime.now(),
                updated_time=datetime.now(),
            )
            self.db.add(trace)
            await self.db.commit()
            await self.db.refresh(trace)
            return trace.id
        except Exception as error:
            await self.db.rollback()
            print(f"[MemoryService] create_ai_trace failed: {type(error).__name__}: {error}")
            return None

    async def update_ai_trace(
        self,
        trace_id: Optional[int],
        answer: str = "",
        status_value: Optional[str] = None,
        error_message: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        if not trace_id or not get_bool_env("TRACE_PERSIST_ENABLED", True):
            return
        try:
            result = await self.db.execute(select(AiMessageTrace).where(AiMessageTrace.id == trace_id))
            trace = result.scalar_one_or_none()
            if not trace:
                return
            if answer:
                trace.answer_preview = answer_preview(answer)
            if status_value:
                trace.status = status_value
            if error_message is not None:
                trace.error_message = error_message[:1000]
            if input_tokens is not None:
                trace.input_tokens = input_tokens
            if output_tokens is not None:
                trace.output_tokens = output_tokens
            if latency_ms is not None:
                trace.latency_ms = latency_ms
            trace.updated_time = datetime.now()
            await self.db.commit()
        except Exception as error:
            await self.db.rollback()
            print(f"[MemoryService] update_ai_trace failed: {type(error).__name__}: {error}")

    def should_update_active_context(self, decision: RouteDecision, reference_docs: List[Dict[str, Any]]) -> bool:
        route = route_value(decision)
        if route in {ANSWER_AUDIT_ROUTE, "casual_chat", "clarify"}:
            return False
        if route == "knowledge_search":
            return True
        return bool(reference_docs)

    async def update_active_context(
        self,
        user_message: Message,
        ai_message: Optional[Message],
        decision: RouteDecision,
        retrieval_query: str,
        reference_docs: List[Dict[str, Any]],
        trace_id: Optional[int] = None,
        source: str = "completion",
    ) -> Optional[ConversationContext]:
        if not get_bool_env("MEMORY_ACTIVE_CONTEXT_ENABLED", True):
            return None
        if not self.should_update_active_context(decision, reference_docs):
            return await self.get_active_context(user_message.session_id)

        now = datetime.now()
        slots = slots_to_dict(decision)
        reference_docs_trace = reference_docs_for_trace(reference_docs)
        reference_images = reference_images_for_trace(reference_docs)
        active_issue = retrieval_query or user_message.content_text
        previous = await self.get_active_context(user_message.session_id)
        previous_snapshot = self.context_to_dict(previous) if previous else None

        if previous:
            context = previous
            event_type = (
                "context_switched"
                if compact_text(previous.active_issue) != compact_text(active_issue)
                else "context_refreshed"
            )
        else:
            context = ConversationContext(
                session_id=user_message.session_id,
                created_time=now,
            )
            self.db.add(context)
            event_type = "context_created"

        context.status = "active"
        context.active_issue = active_issue
        context.active_device = slots.get("device") or context.active_device
        context.active_component = slots.get("component") or context.active_component
        context.active_symptom = slots.get("symptom") or active_issue
        context.active_error_code = slots.get("error_code") or context.active_error_code
        context.active_question = user_message.content_text
        context.active_query = retrieval_query
        context.active_route = route_value(decision)
        context.active_reason = decision.reason
        context.active_reference_docs_json = json_dumps(reference_docs_trace)
        context.active_reference_images_json = json_dumps(reference_images)
        context.slots_json = json_dumps(slots)
        context.last_user_message_id = user_message.id
        context.last_ai_message_id = ai_message.id if ai_message else None
        context.last_trace_id = trace_id
        context.turn_count = int(context.turn_count or 0) + 1
        context.updated_time = now

        await self.db.flush()
        new_snapshot = self.context_to_dict(context)
        event = ConversationContextEvent(
            session_id=user_message.session_id,
            user_message_id=user_message.id,
            ai_message_id=ai_message.id if ai_message else None,
            event_type=event_type,
            route=route_value(decision),
            reason=decision.reason,
            source=source,
            previous_context_json=json_dumps(previous_snapshot or {}),
            new_context_json=json_dumps(new_snapshot),
            created_time=now,
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(context)
        return context

    def active_context_reference_documents(self, active_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not active_context:
            return []
        context_images = active_context.get("active_reference_images") or []
        docs = []
        for item in active_context.get("active_reference_docs") or []:
            normalized = normalize_stored_reference_doc(item)
            if normalized:
                if context_images and not normalized.get("evidence_image_urls"):
                    normalized["evidence_image_urls"] = context_images
                    normalized["image_urls"] = context_images
                docs.append(normalized)
        return docs

    def build_active_context_prompt(self, active_context: Optional[Dict[str, Any]]) -> str:
        if not active_context:
            return ""
        lines = []
        if active_context.get("active_issue"):
            lines.append(f"Current main issue: {active_context['active_issue']}")
        if active_context.get("active_device"):
            lines.append(f"Device: {active_context['active_device']}")
        if active_context.get("active_component"):
            lines.append(f"Component: {active_context['active_component']}")
        if active_context.get("active_error_code"):
            lines.append(f"Error code: {active_context['active_error_code']}")
        if active_context.get("active_symptom"):
            lines.append(f"Symptom: {active_context['active_symptom']}")
        if active_context.get("active_query"):
            lines.append(f"Last retrieval query: {active_context['active_query']}")
        return "\n".join(lines)

    async def maybe_refresh_summary(
        self,
        session_id: int,
        keep_recent_messages: Optional[int] = None,
    ) -> Optional[ConversationSummary]:
        if not get_bool_env("MEMORY_SUMMARY_ENABLED", True):
            return None

        keep_recent = keep_recent_messages or get_positive_int_env("MEMORY_SUMMARY_KEEP_RECENT_MESSAGES", 8)
        min_messages = get_positive_int_env("MEMORY_SUMMARY_MIN_MESSAGES", 14)
        max_chars = get_positive_int_env("MEMORY_SUMMARY_MAX_CHARS", 1800)

        count_result = await self.db.execute(
            select(func.count(Message.id)).where(Message.session_id == session_id)
        )
        total_messages = int(count_result.scalar() or 0)
        if total_messages < min_messages:
            return await self.load_latest_summary(session_id)

        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(asc(Message.message_order))
        )
        messages = list(result.scalars().all())
        if len(messages) <= keep_recent:
            return await self.load_latest_summary(session_id)

        candidates = messages[:-keep_recent]
        if not candidates:
            return await self.load_latest_summary(session_id)

        latest = await self.load_latest_summary(session_id)
        end_order = int(candidates[-1].message_order)
        if latest and int(latest.end_message_order or 0) >= end_order:
            return latest

        summary_text = self._build_extractive_summary(candidates, max_chars=max_chars)
        if not summary_text:
            return latest

        token_estimate = max(1, len(summary_text) // 2)
        now = datetime.now()
        summary = ConversationSummary(
            session_id=session_id,
            start_message_order=int(candidates[0].message_order),
            end_message_order=end_order,
            message_count=len(candidates),
            token_count=token_estimate,
            summary_text=summary_text,
            created_time=now,
            updated_time=now,
        )
        self.db.add(summary)

        context = await self.get_active_context(session_id)
        if context:
            context.summary_text = summary_text
            context.updated_time = now

        await self.db.commit()
        await self.db.refresh(summary)
        return summary

    def _build_extractive_summary(self, messages: List[Message], max_chars: int) -> str:
        lines = []
        for message in messages:
            text = re.sub(r"\s+", " ", str(message.content_text or "").strip())
            if not text or text == "回答生成中，请稍后刷新。":
                continue
            role = "User" if int(message.role or 0) == 1 else "Assistant"
            if len(text) > 220:
                text = text[:220].rstrip() + "..."
            lines.append(f"{role}: {text}")

        if not lines:
            return ""
        summary = "Long conversation summary (extractive):\n" + "\n".join(lines[-20:])
        if len(summary) > max_chars:
            summary = summary[-max_chars:].lstrip()
            summary = "Long conversation summary (extractive, truncated):\n" + summary
        return summary
