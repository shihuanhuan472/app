from enum import Enum
from typing import Optional, TypeAlias

from pydantic import BaseModel, Field, model_validator


class RetrievalMode(str, Enum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    CLARIFICATION = "clarification"


class QuestionUnderstanding(BaseModel):
    intent: str
    user_goal: str
    device: Optional[str] = None
    component: Optional[str] = None
    error_code: Optional[str] = None
    symptoms: list[str] = Field(default_factory=list)
    operating_conditions: list[str] = Field(default_factory=list)
    requested_information: list[str] = Field(default_factory=list)


class DomainTerms(BaseModel):
    professional_terms: list[str] = Field(default_factory=list)
    discriminative_terms: list[str] = Field(default_factory=list)
    generic_terms: list[str] = Field(default_factory=list)


class RetrievalQueries(BaseModel):
    """Queries at one recall level, split by retrieval backend."""

    keyword: list[str] = Field(default_factory=list)
    semantic: list[str] = Field(default_factory=list)


FilterValue: TypeAlias = str | list[str]


class RetrievalStrategy(BaseModel):
    mode: RetrievalMode
    reason: str
    broad_queries: RetrievalQueries
    precise_queries: RetrievalQueries
    primary_terms: list[str] = Field(default_factory=list)
    hard_filters: dict[str, FilterValue] = Field(default_factory=dict)
    soft_filters: dict[str, FilterValue] = Field(default_factory=dict)
    need_clarification: bool = False
    clarification_question: Optional[str] = None

    @model_validator(mode="after")
    def validate_clarification(self) -> "RetrievalStrategy":
        if self.mode == RetrievalMode.CLARIFICATION:
            self.need_clarification = True
        if self.need_clarification and not self.clarification_question:
            raise ValueError("需要澄清时必须给出 clarification_question")
        if self.mode != RetrievalMode.CLARIFICATION:
            if not self.broad_queries.keyword and not self.broad_queries.semantic:
                raise ValueError("非澄清策略必须至少提供一条宽查询")
            if self.mode in {RetrievalMode.KEYWORD, RetrievalMode.HYBRID} and not self.broad_queries.keyword:
                raise ValueError("关键词或混合检索必须提供宽泛关键词查询")
            if self.mode in {RetrievalMode.SEMANTIC, RetrievalMode.HYBRID} and not self.broad_queries.semantic:
                raise ValueError("语义或混合检索必须提供宽泛语义查询")
        return self


class QueryAnalysis(BaseModel):
    original_question: str
    understanding: QuestionUnderstanding
    domain_terms: DomainTerms
    retrieval_strategy: RetrievalStrategy
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = "llm"
