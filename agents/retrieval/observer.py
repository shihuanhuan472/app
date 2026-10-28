from utils.qa_timing import timed_step
import json
import asyncio
import os
import time
from uuid import uuid4
from utils.retrieval_log import log_retrieval
from typing import Any

from pydantic import BaseModel, Field

from ..query_analysis.schemas import QueryAnalysis
from .schemas import RetrievalResult
from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_async_openai_client, create_chat_completion, parse_chat_completion_json


OBSERVER_SYSTEM_PROMPT = """你是检索结果评估器。逐篇核对证据，只输出紧凑JSON，不输出Markdown或解释。
必填字段：is_sufficient(bool), next_action(str answer/refine/replan), document_scores(array)。
document_scores必须覆盖每个输入document_id且不重复；每项为{document_id:string,score:number(0到100),evidence:string}。
evidence只写一句不超过30字的关键依据，不复述问题、标题或整段原文；不相关文档简述不匹配点。
缺失证据时输出missing_aspects(string数组)，无缺失可省略。is_sufficient和next_action须结合问题整体覆盖及缺失证据判断，不能仅凭单篇高分判定充分。
不要输出reason、relevance、matched_primary_terms、supporting_documents、irrelevant_documents；不写总结。"""


class ObservationDecision(BaseModel):
    is_sufficient: bool
    relevance: str = "none"
    next_action: str
    reason: str = ""
    matched_primary_terms: list[str] = Field(default_factory=list)
    supporting_documents: list[str] = Field(default_factory=list)
    document_scores: list["DocumentScore"] = Field(default_factory=list)
    irrelevant_documents: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)


class DocumentScore(BaseModel):
    """Semantic evidence score for every candidate shown to the Observer."""

    document_id: str
    score: float = Field(ge=0.0, le=100.0)
    evidence: str = ""


class ResultObserver:
    """Evaluate retrieved evidence and choose the next controlled workflow state."""

    def __init__(self, *, min_results: int = 1, verbose: bool = True) -> None:
        self.min_results = max(1, min_results)
        self.verbose = verbose

    @staticmethod
    def _normalize_scores(decision: ObservationDecision, candidate_ids: list[str]) -> ObservationDecision:
        """Derive relevance classes from scores instead of trusting model labels."""
        allowed = list(dict.fromkeys(str(document_id) for document_id in candidate_ids))
        score_by_id = {}
        for item in decision.document_scores:
            if item.document_id in allowed and item.document_id not in score_by_id:
                score_by_id[item.document_id] = item
        # A missing score is not evidence of support. Keep it observable as zero.
        normalized = [
            score_by_id.get(document_id)
            or DocumentScore(document_id=document_id, score=0.0, evidence="模型未返回该候选文档的评分")
            for document_id in allowed
        ]
        strong = [item.document_id for item in normalized if item.score >= 80.0]
        partial = [item.document_id for item in normalized if 60.0 <= item.score < 80.0]
        irrelevant = [item.document_id for item in normalized if item.score < 60.0]
        decision.document_scores = normalized
        if not decision.reason:
            decision.reason = "；".join(decision.missing_aspects) or (
                "证据充分" if decision.is_sufficient else "证据不足，需继续检索"
            )
        decision.supporting_documents = strong + partial
        decision.irrelevant_documents = irrelevant
        if strong:
            decision.relevance = "strong"
        elif partial:
            decision.relevance = "partial"
        else:
            decision.relevance = "none"
            decision.is_sufficient = False
            if decision.next_action == "answer":
                decision.next_action = "refine"
        return decision

    @staticmethod
    def _document_payload(result: RetrievalResult) -> dict[str, Any]:
        """Build bounded evidence input from hits and their available neighbours."""
        snippets = []
        seen = set()
        representative = str(result.content or "").strip()[:1800]
        ranked_chunks = sorted(
            result.chunks, key=lambda chunk: float(chunk.get("score", 0.0)), reverse=True
        )
        seed_chunks = ranked_chunks[:3]
        selected = []
        selected_ids = set()

        def chunk_position(chunk):
            metadata = chunk.get("metadata") or {}
            if not isinstance(metadata, dict):
                return None
            value = metadata.get("chunk_index")
            if value is None:
                value = metadata.get("subchunk_index")
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        # Keep each top hit, followed by chunks immediately around it when those
        # chunks are present in the merged multi-query retrieval evidence.
        for seed in seed_chunks:
            seed_position = chunk_position(seed)
            group = [seed]
            if seed_position is not None:
                group.extend(
                    chunk for chunk in result.chunks
                    if chunk is not seed and chunk_position(chunk) in {seed_position - 1, seed_position + 1}
                )
            for chunk in sorted(group, key=lambda item: (chunk_position(item) is None, chunk_position(item) or 0)):
                identity = str((chunk.get("metadata") or {}).get("chunk_id") or id(chunk))
                if identity not in selected_ids:
                    selected.append(chunk)
                    selected_ids.add(identity)

        for chunk in selected:
            content = str(chunk.get("content") or "").strip()
            # The representative body already supplies this evidence verbatim.
            if not content or content in seen or content in representative:
                continue
            seen.add(content)
            metadata = chunk.get("metadata") or {}
            snippets.append({
                "content": content[:800],
                "score": float(chunk.get("score", 0.0)),
                "section_title": metadata.get("section_title") if isinstance(metadata, dict) else None,
                "content_type": metadata.get("content_type") if isinstance(metadata, dict) else None,
            })
            if len(snippets) == 6:
                break
        return {
            "document_id": result.document_id, "title": result.title,
            "content": representative, "matched_chunks": snippets,
        }

    @timed_step('retrieval.observer_llm')
    async def observe_with_llm(self, analysis: QueryAnalysis, results: list[RetrievalResult]) -> ObservationDecision:
        """Use the LLM for semantic evidence judgement, with deterministic fallback."""
        documents = [self._document_payload(result) for result in results[:8]]
        prompt = {
            "question": analysis.original_question,
            "understanding": analysis.understanding.model_dump(mode="json"),
            "primary_terms": analysis.retrieval_strategy.primary_terms,
            "documents": documents,
            "instruction": "对 documents 中每篇候选文档逐篇进行语义证据评分，不能漏评。content 是代表正文，matched_chunks 是各路向量检索实际命中的原文片段，应优先用于核对直接证据。80到100表示直接覆盖问题对象、现象、工况及所需信息；60到79表示有部分直接证据但覆盖不完整；0到59表示不支持、仅表面相似或适用对象不符。不要因关键词重复给高分，不得臆造别名、原因或适用关系。多现象问题须评估逐项覆盖及关系证据；多个独立案例不能证明共同根因。缺失证据写入 missing_aspects。",
        }
        try:
            timeout = float(os.getenv("RESULT_OBSERVER_TIMEOUT", "90"))
            model = os.getenv("QUERY_ANALYSIS_MODEL") or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
            call_id = uuid4().hex
            client = create_async_openai_client(base_url=get_ai_base_url(), api_key=os.getenv("API_KEY", "EMPTY"), timeout=timeout)
            messages = [
                {"role": "system", "content": OBSERVER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ]
            self._print("llm_request", {
                "call_id": call_id, "model": model, "messages": messages,
                "temperature": 0, "max_tokens": 1600, "stream": False,
                "timeout_seconds": timeout, "document_count": len(documents),
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_object"} if os.getenv("AI_RESPONSE_FORMAT_JSON", "0") == "1" else None,
            })
            started = time.perf_counter()
            response = None
            request_status = "ok"
            try:
                response = await asyncio.wait_for(create_chat_completion(
                    client, model=model, messages=messages, temperature=0,
                    max_tokens=1600, json_mode=True,
                ), timeout=timeout)
            except BaseException as exc:
                request_status = type(exc).__name__
                raise
            finally:
                usage = getattr(response, "usage", None)
                def usage_value(key):
                    return usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
                choices = getattr(response, "choices", None) or []
                self._print("llm_complete", {
                    "call_id": call_id, "model": model, "status": request_status,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "prompt_tokens": usage_value("prompt_tokens"),
                    "completion_tokens": usage_value("completion_tokens"),
                    "total_tokens": usage_value("total_tokens"),
                    "finish_reason": getattr(choices[0], "finish_reason", None) if choices else None,
                })
            decision = ObservationDecision.model_validate(parse_chat_completion_json(response))
            decision = self._normalize_scores(decision, [document["document_id"] for document in documents])
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
        evidence_text = "\n".join(f"{item.title} {item.content}".lower() for item in results)
        for symptom in (analysis.understanding.symptoms if analysis.understanding.joint_diagnosis_required else []):
            if symptom.lower() not in evidence_text:
                missing.append(f"现象证据待验证：{symptom}")
        if analysis.understanding.joint_diagnosis_required:
            # Lexical co-occurrence cannot establish a causal relationship.
            missing.append("多个现象的关联或共同原因需要直接证据验证")
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
                is_sufficient=not missing, relevance="strong" if len(supporting) >= 2 and not missing else "partial",
                next_action="answer" if len(supporting) >= self.min_results and not missing else "refine",
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
        log_retrieval(f"ResultObserver.{stage}", payload)
        if self.verbose:
            print(f"[ResultObserver][{stage}] {json.dumps(payload, ensure_ascii=False, default=str)}")
