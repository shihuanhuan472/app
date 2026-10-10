from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .taxonomy import IntentRoute


class IntentSlots(BaseModel):
    device: Optional[str] = None
    component: list[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    metric: Optional[str] = None
    symptom: Optional[str] = None

    @field_validator("component", mode="before")
    @classmethod
    def normalize_component(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = value.strip()
            return [value] if value else []
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        return value


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
