from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AdaptiveRagPlan:
    strategy: str
    complexity: str
    top_k: int = -1
    top_k_documents: int = -1
    iterative_retrieval: bool = False
    image_retrieval: bool = False
    reason: str = ""
    actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "complexity": self.complexity,
            "top_k": self.top_k,
            "top_k_documents": self.top_k_documents,
            "iterative_retrieval": self.iterative_retrieval,
            "image_retrieval": self.image_retrieval,
            "reason": self.reason,
            "actions": list(self.actions),
        }


@dataclass
class MemoryPack:
    session_id: int
    route: str
    reason: str
    dialog_act: Optional[str] = None
    target: Optional[str] = None
    active_context: Optional[Dict[str, Any]] = None
    recent_messages: List[Any] = field(default_factory=list)
    recent_traces: List[Any] = field(default_factory=list)
    recent_context_events: List[Any] = field(default_factory=list)
    summary: Optional[str] = None
    adaptive_rag: AdaptiveRagPlan = field(
        default_factory=lambda: AdaptiveRagPlan(strategy="direct", complexity="simple")
    )
    actions: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return self.adaptive_rag.strategy

    @property
    def complexity(self) -> str:
        return self.adaptive_rag.complexity

    def to_trace_validation(self) -> Dict[str, Any]:
        return {
            "memory_strategy": self.strategy,
            "memory_complexity": self.complexity,
            "dialog_act": self.dialog_act,
            "target": self.target,
            "memory_actions": list(self.actions),
            "has_active_context": bool(self.active_context),
            "has_summary": bool(self.summary),
            "recent_trace_count": len(self.recent_traces or []),
            "recent_context_event_count": len(self.recent_context_events or []),
            "adaptive_rag": self.adaptive_rag.to_dict(),
        }
