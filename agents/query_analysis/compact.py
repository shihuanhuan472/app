"""Small model-facing protocol, expanded into the existing retrieval contract."""
from pydantic import BaseModel, Field

from .schemas import (
    DomainTerms, FilterValue, QueryAnalysis, QuestionUnderstanding,
    RetrievalMode, RetrievalQueries, RetrievalStrategy,
)


class CompactQueryAnalysis(BaseModel):
    intent: str
    mode: RetrievalMode
    device: str | None = None
    component: list[str] = Field(default_factory=list)
    error_code: str | None = None
    primary_terms: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    operating_conditions: list[str] = Field(default_factory=list)
    requested_information: list[str] = Field(default_factory=list)
    relation_request: bool = False
    joint_diagnosis_required: bool = False
    broad_keywords: list[str] = Field(default_factory=list, max_length=3)
    precise_keywords: list[str] = Field(default_factory=list, max_length=3)
    symptom_queries: list[str] = Field(default_factory=list, max_length=6)
    rewritten_query: str | None = None
    hard_filters: dict[str, FilterValue] = Field(default_factory=dict)
    soft_filters: dict[str, FilterValue] = Field(default_factory=dict)
    clarification_question: str | None = None
    confidence: float = Field(ge=0, le=1)

    def expand(self, question: str) -> QueryAnalysis:
        """Preserve existing scope, rewrite checks and joint-evidence queries."""
        return QueryAnalysis(
            original_question=question,
            understanding=QuestionUnderstanding(
                intent=self.intent,
                user_goal="、".join(self.requested_information) or question,
                device=self.device, component=self.component, error_code=self.error_code,
                symptoms=self.symptoms, operating_conditions=self.operating_conditions,
                requested_information=self.requested_information,
                relation_request=self.relation_request,
                joint_diagnosis_required=self.joint_diagnosis_required,
            ),
            domain_terms=DomainTerms(
                professional_terms=list(dict.fromkeys(self.primary_terms + self.symptoms)),
                discriminative_terms=self.primary_terms,
            ),
            retrieval_strategy=RetrievalStrategy(
                mode=self.mode, reason="compact_v1：按模型计划检索",
                broad_queries=RetrievalQueries(
                    keyword=self.broad_keywords,
                    # Literal original is already an independent retrieval route;
                    # executor deduplication prevents an extra RRF vote.
                    semantic=[question] if self.mode != RetrievalMode.CLARIFICATION else [],
                ),
                precise_queries=RetrievalQueries(keyword=self.precise_keywords),
                primary_terms=self.primary_terms,
                symptom_queries=RetrievalQueries(semantic=self.symptom_queries),
                rewritten_queries=RetrievalQueries(
                    semantic=[self.rewritten_query] if self.rewritten_query else [],
                ),
                hard_filters=self.hard_filters, soft_filters=self.soft_filters,
                need_clarification=bool(self.clarification_question),
                clarification_question=self.clarification_question,
            ),
            confidence=self.confidence,
        )
