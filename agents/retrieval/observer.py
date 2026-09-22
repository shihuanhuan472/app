import json
import asyncio
import os
from typing import Any

from pydantic import BaseModel, Field

from ..query_analysis.schemas import QueryAnalysis
from .schemas import RetrievalResult
from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_async_openai_client, create_chat_completion, parse_chat_completion_json


class ObservationDecision(BaseModel):
    is_sufficient: bool
    relevance: str
    next_action: str
    reason: str
    matched_primary_terms: list[str] = Field(default_factory=list)
    supporting_documents: list[str] = Field(default_factory=list)
    irrelevant_documents: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)


class ResultObserver:
    """Evaluate retrieved evidence and choose the next controlled workflow state."""

    def __init__(self, *, min_results: int = 1, verbose: bool = True) -> None:
        self.min_results = max(1, min_results)
        self.verbose = verbose

    async def observe_with_llm(self, analysis: QueryAnalysis, results: list[RetrievalResult]) -> ObservationDecision:
        """Use the LLM for semantic evidence judgement, with deterministic fallback."""
        documents = [{"document_id": r.document_id, "title": r.title, "content": r.content[:1800]} for r in results[:12]]
        prompt = {
            "question": analysis.original_question,
            "understanding": analysis.understanding.model_dump(mode="json"),
            "primary_terms": analysis.retrieval_strategy.primary_terms,
            "documents": documents,
            "instruction": "判断候选文档是否语义上支持用户问题。不要因关键词缺失就判无关；但也不要臆造别名或把仅有相邻部件的文档判为同一部件。只有有证据的文档才能进入 supporting_documents。",
        }
        try:
            timeout = float(os.getenv("RESULT_OBSERVER_TIMEOUT", "45"))
            client = create_async_openai_client(base_url=get_ai_base_url(), api_key=os.getenv("API_KEY", "EMPTY"), timeout=timeout)
            response = await asyncio.wait_for(create_chat_completion(client, model=os.getenv("QUERY_ANALYSIS_MODEL") or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"), messages=[
                {"role": "system", "content": "你是检索结果评估器。仅输出JSON。字段：is_sufficient(bool), relevance(str strong/partial/weak/none), next_action(str answer/refine/replan), reason(str), matched_primary_terms(array), supporting_documents(array), irrelevant_documents(array), missing_aspects(array)。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ], temperature=0, max_tokens=900, json_mode=True), timeout=timeout)
            decision = ObservationDecision.model_validate(parse_chat_completion_json(response))
            self._print("llm_decision", decision.model_dump(mode="json"))
            return decision
        except Exception as exc:
            self._print("llm_error", {"error": f"{type(exc).__name__}: {exc}"})
            raise

    def observe(self, analysis: QueryAnalysis, results: list[RetrievalResult]) -> ObservationDecision:
        self._print("input", {"result_count": len(results), "primary_terms": analysis.retrieval_strategy.primary_terms})
        strategy = analysis.retrieval_strategy
        primary_terms = [str(term).strip().lower() for term in strategy.primary_terms if str(term).strip()]
        requested = " ".join(analysis.understanding.requested_information).lower()
        supporting: list[str] = []
        matched: set[str] = set()
        irrelevant: list[str] = []

        for item in results:
            text = f"{item.title} {item.content}".lower()
            item_terms = {term.lower() for term in item.matched_exact_terms}
            item_terms.update(term for term in primary_terms if term in text)
            matched.update(item_terms)
            has_primary = bool(item_terms) if primary_terms else True
            has_action_evidence = any(word in text for word in ("原因", "排查", "检查", "处理", "解决", "措施", "建议"))
            if has_primary and (has_action_evidence or not requested):
                supporting.append(item.document_id)
            elif not has_primary and primary_terms:
                irrelevant.append(item.document_id)

        missing: list[str] = []
        if primary_terms and not matched:
            missing.append("未找到明确的主语或专业参数匹配")
        if requested and supporting and not any(
            any(word in f"{item.title} {item.content}".lower() for word in ("原因", "排查", "检查", "处理", "解决", "措施"))
            for item in results if item.document_id in supporting
        ):
            missing.append("缺少原因或处理证据")

        if not results:
            decision = ObservationDecision(
                is_sufficient=False, relevance="none", next_action="refine",
                reason="没有召回任何候选文档", missing_aspects=["相关知识文档"],
            )
        elif supporting:
            decision = ObservationDecision(
                is_sufficient=True, relevance="strong" if len(supporting) >= 2 else "partial",
                next_action="answer" if len(supporting) >= self.min_results else "refine",
                reason="存在命中核心主语且包含原因或处理信息的候选文档",
                matched_primary_terms=sorted(matched), supporting_documents=supporting,
                irrelevant_documents=irrelevant, missing_aspects=missing,
            )
        elif primary_terms and matched:
            decision = ObservationDecision(
                is_sufficient=False, relevance="partial", next_action="refine",
                reason="命中了核心主语，但候选内容不足以支撑用户请求",
                matched_primary_terms=sorted(matched), irrelevant_documents=irrelevant,
                missing_aspects=missing or ["原因或处理证据"],
            )
        else:
            decision = ObservationDecision(
                is_sufficient=False, relevance="weak", next_action="replan",
                reason="候选结果未命中问题中的核心主语，当前检索方向可能不正确",
                irrelevant_documents=[item.document_id for item in results],
                missing_aspects=missing or ["核心实体匹配"],
            )

        self._print("decision", decision.model_dump(mode="json"))
        return decision

    def _print(self, stage: str, payload: dict[str, Any]) -> None:
        if self.verbose:
            print(f"[ResultObserver][{stage}] {json.dumps(payload, ensure_ascii=False, default=str)}")
