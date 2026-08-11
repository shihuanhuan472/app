import re
from typing import Optional

from agents.intent import RouteDecision
from models import Message

from .context_scorer import build_slot_confidence, slots_from_decision
from .schemas import AdaptiveRagPlan, MemoryPack
from .service import (
    ANSWER_AUDIT_ROUTE,
    CONTEXTUAL_FOLLOWUP_REASON,
    CONTEXTUAL_RETRY_REASON,
    MemoryService,
    get_positive_int_env,
    route_value,
)


RECENT_MESSAGE_ROUTES = {"knowledge_search", ANSWER_AUDIT_ROUTE}
CONTEXTUAL_REASONS = {CONTEXTUAL_FOLLOWUP_REASON, CONTEXTUAL_RETRY_REASON}

COMPLEX_QUERY_RE = re.compile(
    r"结合|综合|对比|比较|复盘|重新检查|完整分析|根因|定位|"
    r"多份|多个|图片|图像|照片|日志|报表|曲线|数据|"
    r"原因.*解决|解决.*原因|先.*再|一步步|详细排查"
)


class MemoryPackBuilder:
    def __init__(self, memory_service: MemoryService):
        self.memory_service = memory_service

    async def build(
        self,
        message_now: Message,
        decision: RouteDecision,
    ) -> MemoryPack:
        route = route_value(decision)
        actions = ["load_active_context"]
        active_context = await self.memory_service.get_active_context_dict(message_now.session_id)
        decision_slots = slots_from_decision(decision)
        context_analysis = await self.memory_service.analyze_context_transition(
            current_text=message_now.content_text,
            active_context=active_context,
            decision_slots=decision_slots,
            retrieval_query=getattr(decision, "query_rewrite", None) or "",
        )
        raw_working_memory = self.memory_service.merge_structured_working_memory(
            ((active_context or {}).get("working_memory") or {}) if isinstance(active_context, dict) else {},
            context_analysis.working_memory_delta,
            should_reset=context_analysis.should_switch,
            current_turn_id=getattr(message_now, "message_order", None) or getattr(message_now, "id", None),
            source_turn_id=getattr(message_now, "message_order", None) or getattr(message_now, "id", None),
            source="pack_context_delta",
        )
        working_memory = self.memory_service.retrieve_relevant_working_memory(
            raw_working_memory,
            query=" ".join(
                part
                for part in [
                    message_now.content_text,
                    getattr(decision, "query_rewrite", None),
                    (active_context or {}).get("active_query") if isinstance(active_context, dict) else "",
                ]
                if part
            ),
            active_context=active_context,
            context_analysis={
                **context_analysis.to_dict(),
                "current_turn_id": getattr(message_now, "message_order", None) or getattr(message_now, "id", None),
            },
        )
        slot_confidence = build_slot_confidence(decision_slots, context_analysis)
        actions.extend([f"context:{context_analysis.action}", "retrieve_working_memory"])

        recent_messages = []
        if self._should_load_recent_messages(route, decision.reason):
            recent_messages = await self.memory_service.load_recent_messages(
                message_now.session_id,
                message_now.message_order,
                limit=get_positive_int_env("MEMORY_RECENT_MESSAGES_LIMIT", 8),
            )
            actions.append("load_recent_messages")

        recent_traces = []
        recent_context_events = []
        if self._should_load_audit_state(route):
            recent_traces = await self.memory_service.load_recent_ai_traces(
                message_now.session_id,
                limit=get_positive_int_env("ANSWER_AUDIT_HISTORY_LIMIT", 12),
                exclude_routes={ANSWER_AUDIT_ROUTE},
            )
            recent_context_events = await self.memory_service.load_recent_context_events(
                message_now.session_id,
                limit=get_positive_int_env("MEMORY_CONTEXT_EVENT_AUDIT_LIMIT", 5),
            )
            actions.extend(["load_recent_traces", "load_recent_context_events"])

        summary = None
        latest_summary = await self.memory_service.load_latest_summary(message_now.session_id)
        if latest_summary:
            summary = latest_summary.summary_text
            actions.append("load_conversation_summary")

        adaptive_rag = self._build_adaptive_rag_plan(
            message_now,
            decision,
            active_context,
            context_analysis=context_analysis.to_dict(),
        )
        actions.extend(adaptive_rag.actions)

        return MemoryPack(
            session_id=message_now.session_id,
            route=route,
            reason=decision.reason,
            dialog_act=getattr(decision, "dialog_act", None),
            target=getattr(decision, "target", None),
            active_context=active_context,
            recent_messages=recent_messages,
            recent_traces=recent_traces,
            recent_context_events=recent_context_events,
            summary=summary,
            context_analysis=context_analysis.to_dict(),
            working_memory=working_memory,
            slot_confidence=slot_confidence,
            adaptive_rag=adaptive_rag,
            actions=actions,
        )

    def _should_load_recent_messages(self, route: str, reason: str) -> bool:
        return route in RECENT_MESSAGE_ROUTES or reason in CONTEXTUAL_REASONS

    def _should_load_audit_state(self, route: str) -> bool:
        return route == ANSWER_AUDIT_ROUTE

    def _build_adaptive_rag_plan(
        self,
        message_now: Message,
        decision: RouteDecision,
        active_context: Optional[dict],
        context_analysis: Optional[dict] = None,
    ) -> AdaptiveRagPlan:
        route = route_value(decision)
        question = str(message_now.content_text or "").strip()
        has_images = bool(str(message_now.user_uploaded_images or "").strip())

        if route == ANSWER_AUDIT_ROUTE:
            return AdaptiveRagPlan(
                strategy="answer_audit",
                complexity="audit",
                reason="audit_route",
                actions=["adaptive_answer_audit"],
            )

        if not decision.use_rag:
            return AdaptiveRagPlan(
                strategy="direct",
                complexity="simple",
                reason="rag_disabled_by_intent",
                actions=["adaptive_direct"],
            )

        context_action = str((context_analysis or {}).get("action") or "")
        if (
            decision.reason in {CONTEXTUAL_FOLLOWUP_REASON, CONTEXTUAL_RETRY_REASON}
            and context_action != "switch"
        ):
            return AdaptiveRagPlan(
                strategy="contextual_rag",
                complexity="contextual",
                top_k=get_positive_int_env("ADAPTIVE_RAG_CONTEXT_TOP_K", -1),
                top_k_documents=get_positive_int_env("ADAPTIVE_RAG_CONTEXT_TOP_K_DOCUMENTS", -1),
                iterative_retrieval=False,
                image_retrieval=has_images,
                reason=decision.reason,
                actions=["adaptive_contextual_rag"],
            )

        if self._is_complex_question(question, has_images, active_context):
            return AdaptiveRagPlan(
                strategy="react_rag",
                complexity="complex",
                top_k=get_positive_int_env("ADAPTIVE_RAG_COMPLEX_TOP_K", 16),
                top_k_documents=get_positive_int_env("ADAPTIVE_RAG_COMPLEX_TOP_K_DOCUMENTS", 4),
                iterative_retrieval=True,
                image_retrieval=has_images,
                reason="complex_question",
                actions=["adaptive_complex_rag"],
            )

        if self._is_simple_knowledge_question(question):
            return AdaptiveRagPlan(
                strategy="rag_once",
                complexity="simple_knowledge",
                top_k=get_positive_int_env("ADAPTIVE_RAG_SIMPLE_TOP_K", 6),
                top_k_documents=get_positive_int_env("ADAPTIVE_RAG_SIMPLE_TOP_K_DOCUMENTS", 1),
                iterative_retrieval=False,
                image_retrieval=has_images,
                reason="simple_knowledge_question",
                actions=["adaptive_simple_rag"],
            )

        return AdaptiveRagPlan(
            strategy="rag_once",
            complexity="standard",
            top_k=get_positive_int_env("ADAPTIVE_RAG_STANDARD_TOP_K", -1),
            top_k_documents=get_positive_int_env("ADAPTIVE_RAG_STANDARD_TOP_K_DOCUMENTS", -1),
            iterative_retrieval=False,
            image_retrieval=has_images,
            reason="standard_knowledge_question",
            actions=["adaptive_standard_rag"],
        )

    def _is_complex_question(self, question: str, has_images: bool, active_context: Optional[dict]) -> bool:
        if has_images:
            return True
        compact = re.sub(r"\s+", "", question)
        if len(compact) >= get_positive_int_env("ADAPTIVE_RAG_COMPLEX_MIN_CHARS", 60):
            return True
        if COMPLEX_QUERY_RE.search(compact):
            return True
        if active_context and any(word in compact for word in ("继续", "进一步", "再分析", "复盘")):
            return True
        return False

    def _is_simple_knowledge_question(self, question: str) -> bool:
        compact = re.sub(r"\s+", "", question)
        if len(compact) > get_positive_int_env("ADAPTIVE_RAG_SIMPLE_MAX_CHARS", 28):
            return False
        if COMPLEX_QUERY_RE.search(compact):
            return False
        return bool(compact)
