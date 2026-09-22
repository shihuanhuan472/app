import json
from typing import Any

from sqlalchemy import or_, select

from models import DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection

from ..query_analysis.schemas import RetrievalQueries, RetrievalStrategy
from .schemas import RetrievalResult


class RetrievalExecutor:
    """Execute the query plan produced by QueryAnalysisAgent.

    The vector service is injected so this class can be tested without Milvus.
    SQL is intentionally used only for broad keyword candidates; soft filters
    never remove candidates.
    """

    RRF_K = 60
    SOURCE_WEIGHTS = {
        "broad_keyword": 1.0,
        "broad_semantic": 1.0,
        "precise_keyword": 1.2,
        "precise_semantic": 1.2,
    }

    def __init__(self, *, db: Any = None, vector_service: Any = None, top_k: int = 8, verbose: bool = True):
        self.db = db
        self.vector_service = vector_service
        self.top_k = max(1, top_k)
        self.verbose = verbose

    async def execute(self, strategy: RetrievalStrategy) -> list[RetrievalResult]:
        self._print("plan", strategy.model_dump(mode="json"))
        collected: list[tuple[str, list[RetrievalResult]]] = []
        await self._run_queries("broad", strategy.broad_queries, strategy, collected)
        await self._run_queries("precise", strategy.precise_queries, strategy, collected)
        merged = self._rrf_merge(collected, strategy)
        self._print("merged", [item.model_dump(mode="json") for item in merged])
        return merged

    async def _run_queries(
        self,
        level: str,
        queries: RetrievalQueries,
        strategy: RetrievalStrategy,
        out: list[tuple[str, list[RetrievalResult]]],
    ) -> None:
        for query_index, query in enumerate(queries.keyword, start=1):
            self._print(f"{level}_keyword", {"query": query})
            results = await self._keyword_search(query, level, strategy)
            results.sort(key=lambda item: (-item.base_score, item.library_type, item.document_id))
            self._print_sql_results(f"{level}_keyword", query, results)
            out.append((f"{level}_keyword:{query_index}", results))
        for query_index, query in enumerate(queries.semantic, start=1):
            self._print(f"{level}_semantic", {"query": query})
            results = await self._semantic_search(query, level)
            results.sort(key=lambda item: item.base_score, reverse=True)
            out.append((f"{level}_semantic:{query_index}", results))

    async def _keyword_search(self, query: str, level: str, strategy: RetrievalStrategy) -> list[RetrievalResult]:
        if self.db is None:
            return []
        terms = [term for term in str(query).split() if len(term.strip()) >= 2]
        if not terms:
            return []
        results: list[RetrievalResult] = []
        for model, library in ((DocumentBreakdown, "breakdown"), (DocumentKnowledge, "knowledge")):
            fields = [model.title]
            if library == "breakdown":
                fields += [model.problem_intro, model.causes, model.evaluation, model.inspection, model.solutions, model.key_points]
                conditions = [or_(*[field.ilike(f"%{term}%") for field in fields]) for term in terms]
                stmt = select(model).where(model.is_deleted == 0, *conditions).limit(self.top_k)
            else:
                fields += [KnowledgeDocumentSection.section_title, KnowledgeDocumentSection.plain_text]
                conditions = [or_(*[field.ilike(f"%{term}%") for field in fields]) for term in terms]
                stmt = (
                    select(model)
                    .outerjoin(
                        KnowledgeDocumentSection,
                        KnowledgeDocumentSection.document_id == DocumentKnowledge.id,
                    )
                    .where(model.is_deleted == 0, *conditions)
                    .distinct()
                    .limit(self.top_k)
                )
            rows = (await self.db.execute(stmt)).unique().scalars().all()
            for row in rows:
                content = self._row_text(row, library)
                matched = [term for term in terms if term.lower() in content.lower()]
                results.append(RetrievalResult(
                    document_id=str(row.id), library_type=library, title=str(row.title or ""),
                    content=content, score=float(len(matched)), base_score=float(len(matched)),
                    source=f"sql:{level}", matched_terms=matched,
                ))
        return results

    async def _semantic_search(self, query: str, level: str) -> list[RetrievalResult]:
        if self.vector_service is None:
            return []
        raw = await self.vector_service.search_similar_documents(
            query=query,
            top_k_documents=self.top_k,
            apply_domain_term_score=False,
        )
        results = []
        for item in raw or []:
            results.append(RetrievalResult(
                document_id=str(item.get("doc_id")), library_type=str(item.get("library_type", "unknown")),
                title=str(item.get("title", "")), content=str(item.get("content", "")),
                score=float(item.get("score", 0.0)), base_score=float(item.get("score", 0.0)),
                source=f"semantic:{level}", matched_terms=list(item.get("matched_terms") or []),
                metadata=item.get("metadata") or {},
            ))
        return results

    def _rrf_merge(
        self,
        result_lists: list[tuple[str, list[RetrievalResult]]],
        strategy: RetrievalStrategy,
    ) -> list[RetrievalResult]:
        merged: dict[tuple[str, str], RetrievalResult] = {}
        for list_id, items in result_lists:
            source_type = list_id.split(":", 1)[0]
            weight = self.SOURCE_WEIGHTS[source_type]
            seen_in_list: set[tuple[str, str]] = set()
            for rank, item in enumerate(items, start=1):
                key = (item.library_type, item.document_id)
                if key in seen_in_list:
                    continue
                seen_in_list.add(key)
                contribution = weight / (self.RRF_K + rank)
                existing = merged.get(key)
                if existing is None:
                    existing = item.model_copy(deep=True)
                    existing.score = 0.0
                    existing.rrf_score = 0.0
                    existing.sources = []
                    existing.rrf_contributions = []
                    merged[key] = existing
                elif item.base_score > existing.base_score:
                    existing.title = item.title or existing.title
                    existing.content = item.content or existing.content
                    existing.base_score = item.base_score
                    existing.source = item.source
                    existing.metadata = item.metadata or existing.metadata
                existing.rrf_score += contribution
                existing.sources.append(list_id)
                existing.matched_terms = sorted(set(existing.matched_terms + item.matched_terms))
                existing.rrf_contributions.append({
                    "list": list_id,
                    "rank": rank,
                    "weight": weight,
                    "score": round(contribution, 8),
                    "original_score": item.base_score,
                })

        for item in merged.values():
            item.sources = list(dict.fromkeys(item.sources))
            item.soft_filter_bonus = self._soft_bonus(item, strategy.soft_filters)
            item.exact_match_score, item.matched_exact_terms = self._primary_term_bonus(
                item, strategy.primary_terms
            )
            item.rrf_score = round(item.rrf_score, 8)
            item.score = round(
                item.rrf_score + item.soft_filter_bonus + item.exact_match_score,
                8,
            )
        result = sorted(merged.values(), key=lambda item: item.score, reverse=True)
        return result[: self.top_k]

    @staticmethod
    def _soft_bonus(item: RetrievalResult, filters: dict[str, Any]) -> float:
        text = f"{item.title} {item.content}".lower()
        bonus = 0.0
        for value in filters.values():
            values = value if isinstance(value, list) else [value]
            bonus += sum(0.002 for candidate in values if str(candidate).lower() in text)
        return round(min(bonus, 0.01), 8)

    @staticmethod
    def _primary_term_bonus(item: RetrievalResult, primary_terms: list[str]) -> tuple[float, list[str]]:
        """Boost explicit entity/parameter hits after RRF, not state words."""
        title = str(item.title or "").lower()
        content = str(item.content or "").lower()
        matched: list[str] = []
        bonus = 0.0
        for term in primary_terms or []:
            normalized = str(term or "").strip().lower()
            if not normalized:
                continue
            if normalized in title:
                matched.append(str(term))
                bonus += 0.012
            elif normalized in content:
                matched.append(str(term))
                bonus += 0.008
        return round(min(bonus, 0.02), 8), list(dict.fromkeys(matched))

    @staticmethod
    def _row_text(row: Any, library: str) -> str:
        if library == "knowledge":
            return str(getattr(row, "title", ""))
        return "\n".join(str(getattr(row, field, "") or "") for field in (
            "title", "problem_intro", "causes", "evaluation", "inspection", "solutions", "key_points"
        ))

    def _print(self, stage: str, payload: Any) -> None:
        if self.verbose:
            print(f"[Retrieval][{stage}] {json.dumps(payload, ensure_ascii=False, default=str)}")

    def _print_sql_results(self, stage: str, query: str, results: list[RetrievalResult]) -> None:
        if not self.verbose:
            return
        print(f"\n========== SQL DEBUG: {stage} query={query} count={len(results)} ==========")
        for index, item in enumerate(results[: self.top_k], start=1):
            preview = str(item.content or "").replace("\n", " ").strip()[:180]
            print(
                "[SQL DEBUG] "
                f"rank={index} doc={item.library_type}:{item.document_id} "
                f"title={item.title} score={item.base_score:.6f} "
                f"matched_terms={item.matched_terms} preview={preview}"
            )
        print(f"========== SQL DEBUG END: {stage} ==========\n")
