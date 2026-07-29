import os
from typing import Optional

from .llm_classifier import classify_with_llm
from .semantic_router import route_semantically
from .schemas import RouteDecision
from .taxonomy import IntentRoute


def _env_enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class IntentRouterAgent:
    """Two-stage router: semantic fast path, LLM inference, then knowledge fallback."""

    async def route(self, question: str, uploaded_images: Optional[str] = None) -> RouteDecision:
        has_images = bool([item for item in str(uploaded_images or "").split(",") if item.strip()])
        semantic_result = route_semantically(question, has_images=has_images)
        if semantic_result.high_confidence:
            return semantic_result.decision

        if not _env_enabled("INTENT_ROUTER_LLM_ENABLED", True):
            return self._fallback_to_knowledge(question, "llm_router_disabled")

        try:
            return await classify_with_llm(question)
        except Exception as error:
            return self._fallback_to_knowledge(question, f"llm_router_error:{type(error).__name__}")

    @staticmethod
    def _fallback_to_knowledge(question: str, reason: str) -> RouteDecision:
        return RouteDecision(
            route=IntentRoute.KNOWLEDGE_SEARCH,
            use_rag=True,
            confidence=0.0,
            reason=reason,
            query_rewrite=str(question or "").strip() or None,
            source="fallback",
        )
