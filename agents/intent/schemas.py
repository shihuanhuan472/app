from typing import Optional

from pydantic import BaseModel, Field

from .taxonomy import IntentRoute


class IntentSlots(BaseModel):
    device: Optional[str] = None
    component: Optional[str] = None
    error_code: Optional[str] = None
    metric: Optional[str] = None
    symptom: Optional[str] = None


class RouteDecision(BaseModel):
    route: IntentRoute
    use_rag: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    dialog_act: Optional[str] = None
    target: Optional[str] = None
    query_rewrite: Optional[str] = None
    need_clarification: bool = False
    clarification_question: Optional[str] = None
    slots: IntentSlots = Field(default_factory=IntentSlots)
    source: str = "semantic_router"
