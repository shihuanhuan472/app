from enum import Enum
import unicodedata
from typing import Optional, TypeAlias

from pydantic import BaseModel, Field, field_validator, model_validator


def semantic_query_key(query: str) -> str:
    """Ignore formatting, but preserve technical symbols and all words/numbers."""
    return "".join(
        char for char in query.casefold()
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


class RetrievalMode(str, Enum):
    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    CLARIFICATION = "clarification"


class QuestionUnderstanding(BaseModel):
    intent: str
    user_goal: str
    device: Optional[str] = None
    # 一个问题可能同时涉及多个部件；兼容旧模型返回的单个字符串。
    component: list[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    symptoms: list[str] = Field(default_factory=list)
    operating_conditions: list[str] = Field(default_factory=list)
    requested_information: list[str] = Field(default_factory=list)
    relation_request: bool = False
    joint_diagnosis_required: bool = False

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
    original_queries: RetrievalQueries = Field(default_factory=RetrievalQueries)
    symptom_queries: RetrievalQueries = Field(default_factory=RetrievalQueries)
    rewritten_queries: RetrievalQueries = Field(default_factory=RetrievalQueries)
    relation_queries: RetrievalQueries = Field(default_factory=RetrievalQueries)
    semantic_checks: list[str] = Field(default_factory=list)

    @field_validator("hard_filters", "soft_filters", mode="before")
    @classmethod
    def remove_null_filters(cls, value):
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if item is not None}
        return value

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

    @model_validator(mode="after")
    def preserve_question(self) -> "QueryAnalysis":
        strategy = self.retrieval_strategy
        if strategy.mode == RetrievalMode.CLARIFICATION:
            return self
        # The literal question is authoritative even when the model rewrites it.
        strategy.original_queries = RetrievalQueries(semantic=[self.original_question])
        symptoms = list(dict.fromkeys(s.strip() for s in self.understanding.symptoms if s.strip()))
        # Keep each symptom intact. Never distribute one symptom's component
        # (e.g. 二链) onto all other symptoms (e.g. barcode or 漏液).
        context = " ".join(filter(None, [self.understanding.device, *self.understanding.operating_conditions]))
        goal = " ".join(self.understanding.requested_information)
        checks = []
        def keeps_assumptions(query):
            unsupported = ("同时发生", "同一批次", "同批次", "导致", "引起")
            return not any(term in query and term not in self.original_question for term in unsupported)

        generated = [q.strip() for q in strategy.symptom_queries.semantic if q.strip() and keeps_assumptions(q)]
        validated = []
        for symptom in symptoms[:6]:
            # Require verbatim technical phrases; conservative fallback avoids
            # accepting fluent rewrites that silently lose qualifiers.
            matches = [q for q in generated if symptom.lower() in q.lower()
                       and (not self.understanding.device or self.understanding.device.lower() in q.lower())
                       and all(condition.lower() in q.lower() for condition in self.understanding.operating_conditions)
                       and not any(component in q and component not in symptom
                                   for component in self.understanding.component)
                       and not any(other.lower() in q.lower() for other in symptoms if other != symptom)]
            validated.append(matches[0] if matches else " ".join(filter(None, [context, symptom, goal])))
            checks.append(f"单现象[{symptom}]：{'采用模型查询' if matches else '使用保守兜底'}")
        strategy.symptom_queries = RetrievalQueries(semantic=list(dict.fromkeys(validated)))
        rewrites = []
        for query in strategy.rewritten_queries.semantic:
            query = query.strip()
            required = [*symptoms, *self.understanding.operating_conditions]
            if self.understanding.device:
                required.append(self.understanding.device)
            if query and keeps_assumptions(query) and all(term.lower() in query.lower() for term in required):
                rewrites.append(query)
            else:
                checks.append("改写问题缺少原始现象或限定，已移除")
        if len(symptoms) > 1:
            self.understanding.joint_diagnosis_required = True
            combined = "、".join(symptoms)
            strategy.relation_queries = RetrievalQueries(semantic=[
                f"{context} {combined} 联合故障案例 原因及处理".strip(),
                f"{context} {combined} 是否存在关联或共同原因 证据".strip(),
            ])
        else:
            strategy.relation_queries = RetrievalQueries()
        # Only one ordinary rewrite; original and symptom queries already cover
        # their literal content. Do not fabricate another near-duplicate fallback.
        covered = {semantic_query_key(q) for q in [self.original_question, *validated]}
        rewrites = [q for q in rewrites if semantic_query_key(q) not in covered][:1]
        # Keep joint-relation retrieval as a rewrite, rather than another
        # independently generated route that could duplicate RRF votes.
        rewrites.extend(strategy.relation_queries.semantic)
        strategy.rewritten_queries = RetrievalQueries(semantic=list(dict.fromkeys(rewrites))[:3])
        strategy.relation_queries = RetrievalQueries()
        strategy.semantic_checks = [
            "保留完整原问题参与检索",
            "保留现象短语及其局部限定，不跨现象传播部件限定",
            "关联查询仅提出问题，不预设共同原因或因果关系",
            "普通改写最多1条，忽略格式后与原问题或单现象重复则移除",
            *checks,
        ]
        if len(symptoms) > 6:
            strategy.semantic_checks.append("单现象查询上限6条，完整原问题和联合查询仍覆盖全部现象")
        return self
