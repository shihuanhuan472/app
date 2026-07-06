import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection


ANOMALY_WORDS = ("异常", "失败", "报错", "报警", "偏高", "偏低", "过高", "过低", "低", "高", "不稳定", "波动")
DIAGNOSIS_WORDS = ("原因", "处理", "解决", "排查", "检查", "分析", "修复", "怎么办", "怎么处理")
OVERVIEW_WORDS = ("有哪些", "包括哪些", "包含哪些", "目录", "章节", "流程", "步骤", "准备", "主要内容", "概述")
PROCEDURE_WORDS = ("如何", "怎么", "步骤", "流程", "操作", "准备", "注意事项")


@dataclass
class QueryPlan:
    original_query: str
    intent: str
    is_short_query: bool
    core_terms: List[str] = field(default_factory=list)
    expanded_terms: List[str] = field(default_factory=list)
    search_queries: List[str] = field(default_factory=list)
    need_directory_expansion: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QueryEnhancer:
    """Analyze and expand maintenance-domain retrieval queries.

    This module is intentionally independent from VectorService. It can be tested
    first, then wired into retrieval after the expansion strategy is validated.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_plan(self, query: str, query_images: str = None) -> QueryPlan:
        query = self._normalize_query(query)
        intent = self._classify_intent(query, query_images)
        core_terms = self._extract_core_terms(query)
        dynamic_terms = await self._find_dynamic_terms(query, core_terms)
        expanded_terms = self._dedupe(core_terms + dynamic_terms + self._intent_terms(intent, query))
        search_queries = self._build_search_queries(query, intent, expanded_terms)

        return QueryPlan(
            original_query=query,
            intent=intent,
            is_short_query=self._is_short_query(query),
            core_terms=core_terms,
            expanded_terms=expanded_terms,
            search_queries=search_queries,
            need_directory_expansion=intent in {"overview", "procedure"},
            reason=self._reason(intent, query, core_terms, dynamic_terms),
        )

    @staticmethod
    def _normalize_query(query: str) -> str:
        return re.sub(r"\s+", " ", str(query or "").strip())

    @staticmethod
    def _is_short_query(query: str) -> bool:
        query = str(query or "").strip()
        ascii_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", query)
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", query)
        return len(query) <= 16 or (len(ascii_tokens) + len(chinese_chars) <= 8)

    @staticmethod
    def _contains_any(query: str, words) -> bool:
        return any(word and word in query for word in words)

    def _classify_intent(self, query: str, query_images: str = None) -> str:
        if query_images:
            return "multimodal"
        if self._contains_any(query, OVERVIEW_WORDS):
            return "overview"
        if self._contains_any(query, PROCEDURE_WORDS):
            return "procedure"
        if self._contains_any(query, ANOMALY_WORDS):
            return "parameter_anomaly"
        if self._contains_any(query, DIAGNOSIS_WORDS):
            return "diagnosis"
        if self._is_short_query(query):
            return "short_term"
        return "general"

    @staticmethod
    def _extract_core_terms(query: str) -> List[str]:
        terms: List[str] = []
        for value in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", query):
            terms.append(value.upper() if value.isupper() or len(value) <= 5 else value)
        for value in re.findall(r"[A-Za-z]*\d+[A-Za-z0-9_-]*", query):
            terms.append(value.upper())

        cleaned = re.sub(r"[，。！？；,.!?;:：/\\|()\[\]{}]", " ", query)
        for token in cleaned.split():
            token = token.strip()
            if 2 <= len(token) <= 12:
                terms.append(token)

        compact = re.sub(r"\s+", "", query)
        compact = re.sub(r"(异常|失败|报错|报警|偏高|偏低|过高|过低|原因|处理|检查|解决|步骤|流程|有哪些|包括哪些)$", "", compact)
        if 2 <= len(compact) <= 12:
            terms.append(compact)
        return QueryEnhancer._dedupe(terms)[:8]

    async def _find_dynamic_terms(self, query: str, core_terms: List[str], limit: int = 24) -> List[str]:
        seeds = [term for term in core_terms if len(term) >= 2]
        if not seeds:
            seeds = [query] if query else []
        seeds = seeds[:5]
        if not seeds:
            return []

        patterns = [f"%{seed}%" for seed in seeds]
        candidates: List[str] = []

        breakdown_conditions = [
            field.like(pattern)
            for field in (
                DocumentBreakdown.title,
                DocumentBreakdown.problem_intro,
                DocumentBreakdown.causes,
                DocumentBreakdown.evaluation,
                DocumentBreakdown.inspection,
                DocumentBreakdown.solutions,
                DocumentBreakdown.key_points,
            )
            for pattern in patterns
        ]
        if breakdown_conditions:
            result = await self.db.execute(
                select(
                    DocumentBreakdown.title,
                    DocumentBreakdown.problem_intro,
                    DocumentBreakdown.causes,
                    DocumentBreakdown.solutions,
                    DocumentBreakdown.key_points,
                )
                .where(DocumentBreakdown.is_deleted == 0, or_(*breakdown_conditions))
                .limit(limit)
            )
            for row in result.all():
                candidates.extend(self._terms_from_text(" ".join(str(part or "") for part in row), seeds))

        knowledge_conditions = [
            field.like(pattern)
            for field in (
                DocumentKnowledge.title,
                KnowledgeDocumentSection.section_title,
                KnowledgeDocumentSection.plain_text,
            )
            for pattern in patterns
        ]
        if knowledge_conditions:
            result = await self.db.execute(
                select(
                    DocumentKnowledge.title,
                    KnowledgeDocumentSection.section_title,
                    KnowledgeDocumentSection.plain_text,
                )
                .join(KnowledgeDocumentSection, KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
                .where(
                    DocumentKnowledge.is_deleted == 0,
                    KnowledgeDocumentSection.document_library_type == "knowledge",
                    or_(*knowledge_conditions),
                )
                .limit(limit)
            )
            for row in result.all():
                candidates.extend(self._terms_from_text(" ".join(str(part or "") for part in row), seeds))

        return self._dedupe(candidates)[:20]

    @staticmethod
    def _terms_from_text(text: str, seeds: List[str]) -> List[str]:
        text = re.sub(r"\s+", " ", str(text or ""))
        terms: List[str] = []
        for seed in seeds:
            if not seed:
                continue
            for match in re.finditer(re.escape(seed), text, flags=re.IGNORECASE):
                start = max(0, match.start() - 12)
                end = min(len(text), match.end() + 18)
                phrase = text[start:end].strip(" ，。！？；,.!?;:：()[]【】")
                if 2 <= len(phrase) <= 36:
                    terms.append(phrase)
        for value in re.findall(r"[A-Za-z]{2,}[A-Za-z0-9_-]*", text):
            if any(seed.lower() in value.lower() or value.lower() in seed.lower() for seed in seeds):
                terms.append(value)
        return terms

    @staticmethod
    def _intent_terms(intent: str, query: str) -> List[str]:
        if intent == "parameter_anomaly":
            return ["异常原因", "原因分析", "检查步骤", "解决方案", "处理方法", "偏高", "偏低"]
        if intent == "diagnosis":
            return ["故障原因", "排查步骤", "检查项", "处理方案", "解决方法"]
        if intent == "overview":
            return ["主要内容", "包括哪些", "章节", "目录", "概览"]
        if intent == "procedure":
            return ["操作步骤", "流程", "注意事项", "检查项"]
        if intent == "multimodal":
            return ["图像现象", "异常原因", "检查步骤", "处理方案"]
        if intent == "short_term":
            return ["异常", "原因", "检查", "处理", "解决方案"]
        return []

    def _build_search_queries(self, query: str, intent: str, expanded_terms: List[str]) -> List[str]:
        queries = [query]
        compact_terms = [term for term in expanded_terms if term and term not in query]
        if compact_terms:
            queries.append(" ".join(self._dedupe([query] + compact_terms[:8])))
        if intent in {"parameter_anomaly", "diagnosis", "short_term"}:
            queries.append(" ".join(self._dedupe([query, "原因分析", "检查步骤", "解决方案", "处理方法"] + compact_terms[:6])))
        if intent in {"overview", "procedure"}:
            queries.append(" ".join(self._dedupe([query, "章节", "流程", "步骤", "注意事项"] + compact_terms[:6])))
        if any(re.search(r"[A-Za-z]", term) for term in expanded_terms):
            english_terms = [term for term in expanded_terms if re.search(r"[A-Za-z]", term)]
            queries.append(" ".join(self._dedupe([query] + english_terms[:8] + ["troubleshooting", "solution"])))
        return self._dedupe([item for item in queries if item.strip()])[:5]

    @staticmethod
    def _reason(intent: str, query: str, core_terms: List[str], dynamic_terms: List[str]) -> str:
        parts = [f"intent={intent}"]
        if QueryEnhancer._is_short_query(query):
            parts.append("short_query")
        if core_terms:
            parts.append(f"core_terms={','.join(core_terms[:5])}")
        if dynamic_terms:
            parts.append(f"dynamic_terms={len(dynamic_terms)}")
        return "; ".join(parts)

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        result = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result
