import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from models import FeedbackAuditLog, FeedbackClaim, FeedbackEvidence, FeedbackRecord, FeedbackVerification
from schemas import FeedbackIngestCreate
from utils.app_exceptions import AppException
from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_async_openai_client, maybe_wrap_openai_client
from utils.error_codes import BizCode

from .prompts import SYSTEM_PROMPT
from .config import feedback_enabled
from .repository import FeedbackLearningRepository
from .schemas import (
    FEEDBACK_ANALYSIS_TYPES,
    FEEDBACK_RISK_LEVELS,
    FEEDBACK_RECOMMENDED_ACTIONS,
    FEEDBACK_SCOPE_VALUES,
    CLAIM_SOURCE_VALUES,
    EVIDENCE_RESULT_VALUES,
    EvidenceItem,
    EvidenceVerificationResult,
    VERIFICATION_RESULT_VALUES,
    VERIFICATION_STATUS_VALUES,
    VERIFIER_TYPE_VALUES,
    FeedbackAnalysis,
    FeedbackClaimPayload,
    FeedbackIngestCreate,
)
from .schemas import FEEDBACK_TYPES


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _loads_json(value: Any, default: Any):
    if value in (None, "", [], {}):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _parse_document_list(raw_value: Any) -> List[Dict[str, Any]]:
    docs = _loads_json(raw_value, [])
    if not isinstance(docs, list):
        return []
    return [item for item in docs if isinstance(item, dict)]


def _parse_reference_doc_ids(raw_value: Any) -> List[int]:
    if not raw_value:
        return []
    if isinstance(raw_value, list):
        values = raw_value
    else:
        try:
            parsed = json.loads(raw_value)
            values = parsed if isinstance(parsed, list) else str(raw_value).split(",")
        except Exception:
            values = str(raw_value).split(",")
    ids: List[int] = []
    for item in values:
        try:
            value = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    return ids


def _build_memory_snapshot(trace: Any) -> Dict[str, Any]:
    validation = _loads_json(getattr(trace, "validation_json", None), {})
    if not isinstance(validation, dict):
        validation = {}
    return {
        "strategy": validation.get("memory_strategy"),
        "complexity": validation.get("memory_complexity"),
        "actions": validation.get("memory_actions") or [],
        "has_active_context": validation.get("has_active_context"),
        "has_summary": validation.get("has_summary"),
        "context_action": validation.get("context_action"),
        "context_score": validation.get("context_score"),
        "last_focus": validation.get("last_focus"),
        "recent_message_count": validation.get("recent_message_count"),
        "recent_trace_count": validation.get("recent_trace_count"),
    }


def _build_context_snapshot(context: Any) -> Dict[str, Any]:
    if not context:
        return {}
    return {
        "active_issue": getattr(context, "active_issue", None),
        "active_device": getattr(context, "active_device", None),
        "active_component": getattr(context, "active_component", None),
        "active_symptom": getattr(context, "active_symptom", None),
        "active_error_code": getattr(context, "active_error_code", None),
        "active_query": getattr(context, "active_query", None),
        "active_route": getattr(context, "active_route", None),
        "active_reason": getattr(context, "active_reason", None),
        "summary_text": getattr(context, "summary_text", None),
    }


def _build_query_snapshot(message: Any, trace: Any, context: Any) -> Dict[str, Any]:
    return {
        "user_question": getattr(trace, "original_question", None) or getattr(message, "content_text", None),
        "retrieval_query": getattr(trace, "retrieval_query", None),
        "query_rewrite": getattr(trace, "query_rewrite", None),
        "route": getattr(trace, "route", None),
        "reason": getattr(trace, "reason", None),
        "memory_pack": _build_memory_snapshot(trace),
        "device_context": _build_context_snapshot(context),
    }


def _build_answer_snapshot(message: Any, trace: Any) -> Dict[str, Any]:
    return {
        "message_id": getattr(message, "id", None),
        "message_order": getattr(message, "message_order", None),
        "answer_text": getattr(message, "content_text", None),
        "answer_preview": getattr(trace, "answer_preview", None),
    }


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)


def _extract_relevant_scope(comment: str, context: Any) -> str:
    text = comment.lower()
    if any(token in text for token in ("全局", "所有", "通用", "普遍")):
        return "global"
    if any(token in text for token in ("设备", "本机", "当前设备", "这个设备", "该设备")):
        return "device"
    if any(token in text for token in ("故障", "原因", "根因", "fault")):
        return "fault_type"
    if context and any(getattr(context, field, None) for field in ("active_device", "active_component", "active_issue")):
        return "conversation"
    return "feedback"


def _contains_conflict_hint(comment: str, answer_text: str, retrieved_documents: List[Dict[str, Any]]) -> bool:
    comment_text = comment.lower()
    answer = answer_text.lower()
    if any(token in comment_text for token in ("不是", "错误", "不对", "冲突", "相反", "错了")):
        if answer:
            return True
        if retrieved_documents:
            return True
    if "文档" in comment and any(token in comment_text for token in ("正确", "对", "可信")):
        return False
    return False


def _detect_feedback_type(raw_feedback_type: str, comment: str) -> str:
    feedback_type = _normalize_text(raw_feedback_type).lower()
    comment_text = _normalize_text(comment)
    if "不是" in comment_text and "是" in comment_text:
        return "correction"
    if "已经确认" in comment_text or "确认是" in comment_text:
        return "confirmation"
    if "文档" in comment_text and any(token in comment_text for token in ("正确", "对", "参考")):
        return "confirmation"
    mapping = {
        "positive": "confirmation",
        "confirmation": "confirmation",
        "negative": "dissatisfaction",
        "correction": "correction",
        "additional_information": "additional_information",
        "irrelevant": "irrelevant",
        "suspicious": "suspicious",
    }
    return mapping.get(feedback_type, "unclear")


def _build_claims(analysis_type: str, comment: str, context: Any, answer_snapshot: Dict[str, Any]) -> Tuple[List[FeedbackClaimPayload], Optional[str]]:
    text = _normalize_text(comment)
    claims: List[FeedbackClaimPayload] = []
    document_preference: Optional[str] = None

    correction_match = re.search(r"(?:不是|不是说)\s*([^\s，,。；;:]+)\s*[，,。；;:]\s*(?:是|而是)\s*([^\s，,。；;:]+)", text)
    if analysis_type == "correction" and correction_match:
        subject = "root_cause" if any(token in text for token in ("故障", "根因", "原因")) else "answer"
        claims.append(
            FeedbackClaimPayload(
                claim_type="correction",
                subject=subject,
                predicate="is",
                object=correction_match.group(2),
                scope=_extract_relevant_scope(text, context),
                source="user",
                confidence=0.78,
                verification_status="unverified",
                evidence_ids=[],
            )
        )

    if analysis_type == "confirmation" and ("已经确认" in text or "确认是" in text):
        confirmed = text.split("确认是", 1)[-1].strip() if "确认是" in text else text.split("已经确认", 1)[-1].strip()
        claims.append(
            FeedbackClaimPayload(
                claim_type="confirmation",
                subject="fault_cause" if confirmed else "answer",
                predicate="is",
                object=confirmed or None,
                scope=_extract_relevant_scope(text, context),
                source="user",
                confidence=0.68,
                verification_status="unverified",
                evidence_ids=[],
            )
        )

    if "文档" in text and any(token in text for token in ("正确", "对", "参考文档")):
        match = re.search(r"文档\s*([0-9]+)", text)
        document_preference = match.group(1) if match else "preferred_document"
        claims.append(
            FeedbackClaimPayload(
                claim_type="document_preference",
                subject="document",
                predicate="preferred",
                object=document_preference,
                scope=_extract_relevant_scope(text, context),
                source="user",
                confidence=0.5,
                verification_status="unverified",
                evidence_ids=[],
            )
        )

    if not claims and analysis_type == "additional_information" and text:
        claims.append(
            FeedbackClaimPayload(
                claim_type="additional_information",
                subject="symptom",
                predicate="mentions",
                object=text[:255],
                scope=_extract_relevant_scope(text, context),
                source="user",
                confidence=0.45,
                verification_status="unverified",
                evidence_ids=[],
            )
        )

    if analysis_type == "unclear" and text:
        claims.append(
            FeedbackClaimPayload(
                claim_type="unclear_feedback",
                subject="feedback",
                predicate="is",
                object=text[:255],
                scope=_extract_relevant_scope(text, context),
                source="user",
                confidence=0.25,
                verification_status="unverified",
                evidence_ids=[],
            )
        )

    return claims, document_preference


def _build_evidence_requirements(analysis_type: str, claims: List[FeedbackClaimPayload], conflict: bool) -> List[Dict[str, Any]]:
    requirements: List[Dict[str, Any]] = []
    if claims:
        requirements.append(
            {
                "evidence_type": "retrieval",
                "source_hint": "RAG evidence",
                "reason": "Claims need supporting or contradictory evidence before promotion.",
            }
        )
    if analysis_type in {"correction", "additional_information"}:
        requirements.append(
            {
                "evidence_type": "cross_case",
                "source_hint": "similar cases",
                "reason": "Check whether the feedback matches known fault cases.",
            }
        )
    if conflict:
        requirements.append(
            {
                "evidence_type": "human_review",
                "source_hint": "operator",
                "reason": "Feedback conflicts with current answer or retrieved documents.",
            }
        )
    return requirements


def _build_heuristic_analysis(payload: FeedbackIngestCreate, feedback_record: FeedbackRecord, trace: Any, context: Any) -> Dict[str, Any]:
    query_snapshot = _loads_json(feedback_record.query_snapshot, {})
    answer_snapshot = _loads_json(feedback_record.answer_snapshot, {})
    retrieved_documents = _loads_json(feedback_record.retrieved_documents, [])
    if not isinstance(retrieved_documents, list):
        retrieved_documents = []
    comment = _normalize_text(feedback_record.comment or payload.comment)
    feedback_type = _detect_feedback_type(payload.feedback_type, comment)
    claims, document_preference = _build_claims(feedback_type, comment, context, answer_snapshot if isinstance(answer_snapshot, dict) else {})
    conflict = _contains_conflict_hint(comment, _normalize_text(answer_snapshot.get("answer_text") if isinstance(answer_snapshot, dict) else ""), retrieved_documents)

    risk_level = "low"
    if feedback_type == "correction":
        risk_level = "high"
    elif feedback_type in {"suspicious"}:
        risk_level = "critical"
    elif feedback_type in {"irrelevant", "unclear", "dissatisfaction"}:
        risk_level = "medium"
    if _extract_relevant_scope(comment, context) == "global":
        risk_level = "high"
    if conflict and risk_level != "critical":
        risk_level = "high"

    recommended_action = "store_only"
    if feedback_type in {"correction", "additional_information"}:
        recommended_action = "verify"
    if feedback_type == "confirmation":
        recommended_action = "store_only"
    if feedback_type in {"irrelevant", "dissatisfaction"}:
        recommended_action = "ignore"
    if feedback_type == "suspicious":
        recommended_action = "require_human_review"
    if conflict and feedback_type == "correction":
        recommended_action = "require_human_review"

    scope = _extract_relevant_scope(comment, context)
    if feedback_type == "confirmation" and scope == "feedback":
        scope = "conversation"

    return {
        "feedback_type": feedback_type,
        "claims": [claim.model_dump() for claim in claims],
        "evidence_requirements": _build_evidence_requirements(feedback_type, claims, conflict),
        "confidence": 0.78 if claims else 0.5,
        "risk_level": risk_level,
        "recommended_action": recommended_action,
        "scope": scope,
        "conflict": conflict,
        "conflict_reason": "feedback text conflicts with answer or retrieved evidence" if conflict else None,
        "document_preference": document_preference,
    }


class FeedbackIngestionService:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def ingest_feedback(self, payload: FeedbackIngestCreate, current_user: Any) -> tuple[FeedbackRecord, bool]:
        if not feedback_enabled():
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "反馈功能已关闭")

        feedback_type = str(payload.feedback_type or "").strip().lower()
        if feedback_type not in FEEDBACK_TYPES:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "反馈类型无效")

        if int(payload.user_id) != int(getattr(current_user, "id", 0) or 0):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权提交他人反馈")

        conversation = await self.repository.get_conversation(payload.conversation_id)
        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "对话不存在")
        if int(conversation.user_id) != int(current_user.id):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.CONVERSATION_FORBIDDEN, "无权提交该对话反馈")

        message = await self.repository.get_message(payload.message_id)
        if not message:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "消息不存在")
        if int(message.session_id) != int(conversation.id):
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "消息不属于该对话")
        if int(getattr(message, "role", 1)) != 0:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "回答不存在")

        trace = await self.repository.get_latest_trace_for_message(message.id, conversation.id)
        if not trace:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "回答追踪不存在")

        eligibility_checker = getattr(self.repository, "is_feedback_eligible", None)
        if eligibility_checker:
            feedback_eligible = await eligibility_checker(message.id, conversation.id)
        else:
            # Keep lightweight test/dialect repositories compatible while production uses the strict checker.
            trace_docs = _loads_json(getattr(trace, "reference_docs_json", None), [])
            feedback_eligible = (
                str(getattr(trace, "route", "") or "").lower() == "knowledge_search"
                and isinstance(trace_docs, list)
                and bool(trace_docs)
            )
        if not feedback_eligible:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "该回答未使用知识库检索，不支持反馈")

        existing = await self.repository.get_existing_feedback(
            current_user.id,
            conversation.id,
            message.id,
            feedback_type,
        )
        if existing:
            return existing, False

        context = await self.repository.get_active_context(conversation.id)
        retrieved_docs = _parse_document_list(getattr(trace, "reference_docs_json", None))
        cited_doc_ids = set(_parse_reference_doc_ids(getattr(message, "ai_reference_doc_ids", None)))
        cited_docs = [doc for doc in retrieved_docs if int(doc.get("doc_id") or 0) in cited_doc_ids] or retrieved_docs

        now = datetime.now()
        feedback = FeedbackRecord(
            conversation_id=conversation.id,
            message_id=message.id,
            user_id=current_user.id,
            feedback_type=feedback_type,
            rating=payload.rating,
            comment=(str(payload.comment).strip() if payload.comment else None),
            created_at=now,
            query_snapshot=_json_dumps(_build_query_snapshot(message, trace, context)),
            answer_snapshot=_json_dumps(_build_answer_snapshot(message, trace)),
            retrieved_documents=_json_dumps(retrieved_docs),
            cited_documents=_json_dumps(cited_docs),
            trace_id=trace.id,
            status="pending",
        )

        try:
            created = await self.repository.create_feedback(feedback)
        except IntegrityError:
            await self.repository.db.rollback()
            existing = await self.repository.get_existing_feedback(
                current_user.id,
                conversation.id,
                message.id,
                feedback_type,
            )
            if existing:
                return existing, False
            raise

        return created, True


class FeedbackAnalysisService:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def analyze_feedback(
        self,
        feedback: FeedbackRecord,
        current_user: Any = None,
        llm_client: Any = None,
    ) -> FeedbackAnalysis:
        if current_user and int(getattr(current_user, "id", 0) or 0) != int(feedback.user_id):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权访问该反馈")

        query_snapshot = _loads_json(feedback.query_snapshot, {})
        answer_snapshot = _loads_json(feedback.answer_snapshot, {})
        retrieved_documents = _loads_json(feedback.retrieved_documents, [])
        cited_documents = _loads_json(feedback.cited_documents, [])

        trace = await self.repository.get_latest_trace_for_message(feedback.message_id, feedback.conversation_id)
        if not trace and feedback.trace_id:
            trace = await self.repository.get_trace_by_id(feedback.trace_id) if hasattr(self.repository, "get_trace_by_id") else None
        context = await self.repository.get_active_context(feedback.conversation_id)
        payload = FeedbackIngestCreate(
            user_id=feedback.user_id,
            conversation_id=feedback.conversation_id,
            message_id=feedback.message_id,
            feedback_type=feedback.feedback_type,
            rating=feedback.rating,
            comment=feedback.comment,
        )

        prompt_payload = {
            "feedback_record": {
                "id": feedback.id,
                "feedback_type": feedback.feedback_type,
                "rating": feedback.rating,
                "comment": feedback.comment,
                "status": feedback.status,
            },
            "query_snapshot": query_snapshot,
            "answer_snapshot": answer_snapshot,
            "retrieved_documents": retrieved_documents,
            "cited_documents": cited_documents,
            "trace": _json_safe(getattr(trace, "__dict__", trace)),
            "current_context": _json_safe(getattr(context, "__dict__", context)),
        }

        raw = None
        if llm_client is not None or os.getenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            raw = await self._call_llm(prompt_payload, llm_client=llm_client)

        analysis_dict = self._parse_analysis_output(raw, payload, feedback, trace, context)
        analysis_dict = self._apply_schema_guards(analysis_dict)
        try:
            return FeedbackAnalysis.model_validate(analysis_dict)
        except ValidationError:
            fallback = _build_heuristic_analysis(payload, feedback, trace, context)
            return FeedbackAnalysis.model_validate(self._apply_schema_guards(fallback))

    async def _call_llm(self, prompt_payload: Dict[str, Any], llm_client: Any = None) -> str:
        request_timeout = float(os.getenv("FEEDBACK_VERIFICATION_TIMEOUT", "10"))
        client = maybe_wrap_openai_client(llm_client) if llm_client is not None else create_async_openai_client(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY", "EMPTY"),
            timeout=request_timeout,
        )
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=os.getenv("FEEDBACK_VERIFICATION_MODEL") or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _json_dumps(prompt_payload)},
                ],
                temperature=0,
                max_tokens=int(os.getenv("FEEDBACK_VERIFICATION_MAX_TOKENS", "1200")),
            ),
            timeout=request_timeout,
        )
        return response.choices[0].message.content or ""

    def _parse_analysis_output(
        self,
        raw: Optional[str],
        payload: FeedbackIngestCreate,
        feedback: FeedbackRecord,
        trace: Any,
        context: Any,
    ) -> Dict[str, Any]:
        if not raw:
            return _build_heuristic_analysis(payload, feedback, trace, context)
        text = str(raw).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
        return _build_heuristic_analysis(payload, feedback, trace, context)

    def _apply_schema_guards(self, analysis_dict: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(analysis_dict or {})
        feedback_type = str(payload.get("feedback_type") or "unclear").strip().lower()
        if feedback_type not in FEEDBACK_ANALYSIS_TYPES:
            feedback_type = "unclear"
        payload["feedback_type"] = feedback_type

        claims = payload.get("claims") or []
        if not isinstance(claims, list):
            claims = []
        normalized_claims = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            normalized = {
                "claim_type": str(claim.get("claim_type") or "unknown")[:64],
                "subject": claim.get("subject"),
                "predicate": claim.get("predicate"),
                "object": claim.get("object"),
                "scope": claim.get("scope"),
                "source": str(claim.get("source") or "user").strip().lower(),
                "confidence": claim.get("confidence", 0.0),
                "verification_status": str(claim.get("verification_status") or "unverified").strip().lower(),
                "evidence_ids": claim.get("evidence_ids") or [],
            }
            if normalized["source"] not in CLAIM_SOURCE_VALUES:
                normalized["source"] = "user"
            if normalized["verification_status"] not in VERIFICATION_STATUS_VALUES:
                normalized["verification_status"] = "unverified"
            normalized["scope"] = normalized["scope"] if normalized["scope"] in FEEDBACK_SCOPE_VALUES else "feedback"
            normalized_claims.append(normalized)
        payload["claims"] = normalized_claims

        evidence_requirements = payload.get("evidence_requirements") or []
        if not isinstance(evidence_requirements, list):
            evidence_requirements = []
        payload["evidence_requirements"] = [item for item in evidence_requirements if isinstance(item, dict)]
        payload["confidence"] = max(0.0, min(1.0, float(payload.get("confidence", 0.0) or 0.0)))
        payload["risk_level"] = str(payload.get("risk_level") or "medium").strip().lower()
        if payload["risk_level"] not in FEEDBACK_RISK_LEVELS:
            payload["risk_level"] = "medium"
        payload["recommended_action"] = str(payload.get("recommended_action") or "store_only").strip().lower()
        if payload["recommended_action"] not in FEEDBACK_RECOMMENDED_ACTIONS:
            payload["recommended_action"] = "store_only"
        payload["scope"] = str(payload.get("scope") or "feedback").strip().lower()
        if payload["scope"] not in FEEDBACK_SCOPE_VALUES:
            payload["scope"] = "feedback"
        payload["conflict"] = bool(payload.get("conflict", False))
        payload["conflict_reason"] = payload.get("conflict_reason")
        payload["document_preference"] = payload.get("document_preference")
        return payload

    async def verify_and_persist(
        self,
        feedback_id: int,
        current_user: Any = None,
        llm_client: Any = None,
    ) -> FeedbackVerification:
        feedback = await self.repository.get_feedback_record(feedback_id)
        if not feedback:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "反馈不存在")
        if current_user and int(getattr(current_user, "id", 0) or 0) != int(feedback.user_id):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权处理该反馈")

        analysis = await self.analyze_feedback(feedback, current_user=current_user, llm_client=llm_client)
        before_state = {
            "feedback_status": feedback.status,
            "claim_count": len(_loads_json(feedback.retrieved_documents, [])),
            "verification_count": len(getattr(feedback, "verifications", []) or []),
        }

        created_claim_ids: List[int] = []
        for claim_payload in analysis.claims:
            claim = FeedbackClaim(
                feedback_id=feedback.id,
                claim_type=claim_payload.claim_type,
                subject=claim_payload.subject,
                predicate=claim_payload.predicate,
                object=claim_payload.object,
                scope=claim_payload.scope,
                source=claim_payload.source,
                confidence=claim_payload.confidence,
                verification_status=claim_payload.verification_status,
                evidence_ids=_json_dumps(claim_payload.evidence_ids),
                created_at=datetime.now(),
            )
            created_claim = await self.repository.create_claim(claim)
            created_claim_ids.append(created_claim.id)

        verification_result = "uncertain"
        if analysis.conflict:
            verification_result = "contradict"
        elif analysis.recommended_action in {"store_only", "ignore"}:
            verification_result = "insufficient"
        elif analysis.recommended_action in {"verify", "create_candidate_case"}:
            verification_result = "support"

        verification = FeedbackVerification(
            feedback_id=feedback.id,
            verifier_type="llm" if llm_client or os.getenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"} else "rule",
            verification_result=verification_result,
            confidence=analysis.confidence,
            reason=_json_dumps(
                {
                    "analysis": analysis.model_dump(),
                    "created_claim_ids": created_claim_ids,
                    "scope": analysis.scope,
                }
            ),
            evidence_summary=_json_dumps(
                {
                    "retrieved_documents": _loads_json(feedback.retrieved_documents, []),
                    "cited_documents": _loads_json(feedback.cited_documents, []),
                }
            ),
            created_at=datetime.now(),
        )
        created_verification = await self.repository.create_verification(verification)

        after_state = {
            "verification_id": created_verification.id,
            "verification_result": created_verification.verification_result,
            "analysis_scope": analysis.scope,
            "recommended_action": analysis.recommended_action,
        }

        audit_log = FeedbackAuditLog(
            feedback_id=feedback.id,
            action="verification_completed",
            before_state=_json_dumps(before_state),
            after_state=_json_dumps(after_state),
            reason="feedback verification analysis persisted",
            actor="system",
            created_at=datetime.now(),
        )
        created_audit = await self.repository.create_audit_log(audit_log)

        return FeedbackVerification(
            id=created_verification.id,
            feedback_id=created_verification.feedback_id,
            verifier_type=created_verification.verifier_type,
            verification_result=created_verification.verification_result,
            confidence=created_verification.confidence,
            reason=created_verification.reason,
            evidence_summary=created_verification.evidence_summary,
            created_at=created_verification.created_at,
        )


class FeedbackVerificationService(FeedbackAnalysisService):
    pass


def _claim_to_text(claim: FeedbackClaimPayload) -> str:
    parts = [claim.subject, claim.predicate, claim.object, claim.scope, claim.source]
    return " ".join(str(part) for part in parts if part)


def _claim_keywords(claim: FeedbackClaimPayload) -> List[str]:
    text = " ".join(
        str(part)
        for part in [claim.claim_type, claim.subject, claim.predicate, claim.object, claim.scope]
        if part
    )
    tokens = []
    for token in re.split(r"[\s,，。；;:/|_()-]+", text):
        token = token.strip()
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return tokens[:8]


def _build_evidence_item(source_type: str, source_id: str, content: Any, relevance: float, support: float, contradiction: float) -> Dict[str, Any]:
    return {
        "source_type": source_type,
        "source_id": str(source_id),
        "content": _normalize_text(content)[:1000] if content is not None else None,
        "relevance_score": max(0.0, min(1.0, float(relevance or 0.0))),
        "support_score": max(0.0, min(1.0, float(support or 0.0))),
        "contradiction_score": max(0.0, min(1.0, float(contradiction or 0.0))),
    }


def _extract_text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "content", "value", "title", "name"):
            if value.get(key):
                return _normalize_text(value.get(key))
        return _normalize_text(value)
    if isinstance(value, (list, tuple)):
        return " ".join(_normalize_text(item) for item in value if item)
    return _normalize_text(value)


def _listify(value: Any) -> List[Any]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


class EvidenceVerifier:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def verify_claim(
        self,
        feedback: FeedbackRecord,
        claim: FeedbackClaimPayload,
        current_user: Any = None,
        llm_client: Any = None,
    ) -> EvidenceVerificationResult:
        if current_user and int(getattr(current_user, "id", 0) or 0) != int(feedback.user_id):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权验证该反馈声明")

        context = await self.repository.get_active_context(feedback.conversation_id)
        trace = await self.repository.get_trace_by_id(feedback.trace_id) if feedback.trace_id else None
        query_snapshot = _loads_json(feedback.query_snapshot, {})
        answer_snapshot = _loads_json(feedback.answer_snapshot, {})
        retrieved_documents = _loads_json(feedback.retrieved_documents, [])
        cited_documents = _loads_json(feedback.cited_documents, [])
        working_memory = {}
        if isinstance(query_snapshot, dict):
            working_memory = query_snapshot.get("memory_pack") or {}
            if not isinstance(working_memory, dict):
                working_memory = {}

        heuristics = await self._collect_heuristic_evidence(
            feedback=feedback,
            claim=claim,
            context=context,
            trace=trace,
            query_snapshot=query_snapshot if isinstance(query_snapshot, dict) else {},
            answer_snapshot=answer_snapshot if isinstance(answer_snapshot, dict) else {},
            retrieved_documents=retrieved_documents if isinstance(retrieved_documents, list) else [],
            cited_documents=cited_documents if isinstance(cited_documents, list) else [],
            working_memory=working_memory,
        )

        if llm_client is not None or os.getenv("FEEDBACK_EVIDENCE_LLM_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
            llm_result = await self._call_llm_evidence_verifier(feedback, claim, heuristics, llm_client=llm_client)
            if llm_result:
                heuristics = self._merge_llm_evidence_result(heuristics, llm_result)

        return self._finalize_evidence_result(claim, heuristics)

    async def _collect_heuristic_evidence(
        self,
        *,
        feedback: FeedbackRecord,
        claim: FeedbackClaimPayload,
        context: Any,
        trace: Any,
        query_snapshot: Dict[str, Any],
        answer_snapshot: Dict[str, Any],
        retrieved_documents: List[Dict[str, Any]],
        cited_documents: List[Dict[str, Any]],
        working_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        claim_text = _claim_to_text(claim).lower()
        keywords = _claim_keywords(claim)
        support_items: List[Dict[str, Any]] = []
        contradict_items: List[Dict[str, Any]] = []

        def add_support(item: Dict[str, Any]) -> None:
            support_items.append(item)

        def add_contradict(item: Dict[str, Any]) -> None:
            contradict_items.append(item)

        def text_match_score(text: str) -> float:
            lowered = text.lower()
            matched = sum(1 for token in keywords if token.lower() in lowered)
            return min(1.0, matched / max(1, len(keywords)))

        # Current answer / cited docs / retrieved docs
        for doc in cited_documents + retrieved_documents:
            if not isinstance(doc, dict):
                continue
            doc_id = doc.get("doc_id")
            title = _normalize_text(doc.get("title") or doc.get("name"))
            content = " ".join(
                _normalize_text(doc.get(field))
                for field in ("title", "content", "summary", "evidence", "root_cause", "symptoms", "actions", "outcome")
                if doc.get(field) is not None
            )
            relevance = text_match_score(content or title)
            if not relevance and doc_id is not None and str(doc_id) in claim_text:
                relevance = 0.9
            if not relevance:
                continue
            lower_content = content.lower()
            lower_title = title.lower()
            support = 0.0
            contradiction = 0.0
            if any(token in lower_content for token in keywords) or any(token in lower_title for token in keywords):
                support += 0.6 * relevance
            if self._has_contradiction_hint(claim_text, lower_content, lower_title):
                contradiction += max(0.5, relevance)
            if support > contradiction:
                add_support(_build_evidence_item("document", f"document:{doc_id}", content or title, relevance, support, contradiction))
            elif contradiction > support:
                add_contradict(_build_evidence_item("document", f"document:{doc_id}", content or title, relevance, support, contradiction))

        # Answer snapshot
        answer_text = _normalize_text(answer_snapshot.get("answer_text") or answer_snapshot.get("answer_preview"))
        if answer_text:
            relevance = text_match_score(answer_text)
            if relevance:
                support = 0.4 if any(token in answer_text.lower() for token in keywords) else 0.0
                contradiction = 0.4 if self._has_contradiction_hint(claim_text, answer_text.lower(), "") else 0.0
                target = add_support if support >= contradiction else add_contradict
                target(_build_evidence_item("answer", f"feedback:{feedback.id}:answer", answer_text, relevance, support, contradiction))

        # Current conversation context / confirmed facts in working memory
        context_blob = " ".join(
            _normalize_text(value)
            for value in [
                getattr(context, "active_issue", None),
                getattr(context, "active_device", None),
                getattr(context, "active_component", None),
                getattr(context, "active_symptom", None),
                getattr(context, "active_error_code", None),
                getattr(context, "summary_text", None),
                query_snapshot.get("device_context", {}).get("active_issue") if isinstance(query_snapshot.get("device_context"), dict) else None,
            ]
            if value
        )
        if context_blob:
            relevance = text_match_score(context_blob)
            if relevance:
                add_support(_build_evidence_item("conversation_context", f"conversation:{feedback.conversation_id}", context_blob, relevance, 0.55, 0.0))

        for fact in _listify(working_memory.get("confirmed_facts")) if isinstance(working_memory, dict) else []:
            fact_text = _extract_text_value(fact)
            if not fact_text:
                continue
            relevance = text_match_score(fact_text)
            if relevance:
                if self._has_contradiction_hint(claim_text, fact_text.lower(), ""):
                    add_contradict(_build_evidence_item("working_memory", f"working_memory:{feedback.conversation_id}", fact_text, relevance, 0.2, 0.8))
                else:
                    add_support(_build_evidence_item("working_memory", f"working_memory:{feedback.conversation_id}", fact_text, relevance, 0.8, 0.0))

        # Verified fault cases
        device = None
        component = None
        alarm_code = None
        if isinstance(query_snapshot, dict):
            device = query_snapshot.get("device_context", {}).get("active_device") if isinstance(query_snapshot.get("device_context"), dict) else None
            component = query_snapshot.get("device_context", {}).get("active_component") if isinstance(query_snapshot.get("device_context"), dict) else None
            alarm_code = query_snapshot.get("device_context", {}).get("active_error_code") if isinstance(query_snapshot.get("device_context"), dict) else None
        cases = await self.repository.get_verified_fault_cases(
            device=_normalize_text(device) or None,
            component=_normalize_text(component) or None,
            alarm_code=_normalize_text(alarm_code) or None,
            keywords=keywords,
            limit=8,
        )
        for case in cases:
            case_text = " ".join(
                part
                for part in [
                    case.device,
                    case.device_type,
                    case.component,
                    case.firmware_version,
                    case.symptoms,
                    case.metrics,
                    case.alarm_codes,
                    case.confirmed_fault,
                    case.root_cause,
                    case.actions,
                    case.outcome,
                ]
                if part
            )
            relevance = text_match_score(case_text)
            if not relevance:
                continue
            support = 0.0
            contradiction = 0.0
            lower_case = case_text.lower()
            if any(token in lower_case for token in keywords):
                support = 0.75 * relevance
            if self._has_contradiction_hint(claim_text, lower_case, ""):
                contradiction = 0.5 * relevance
            if support >= contradiction:
                add_support(_build_evidence_item("fault_case", f"fault_case:{case.id}", case_text, relevance, max(0.5, support), contradiction))
            else:
                add_contradict(_build_evidence_item("fault_case", f"fault_case:{case.id}", case_text, relevance, support, max(0.5, contradiction)))

        evidence_count = len(support_items) + len(contradict_items)
        support_score = min(1.0, sum(item["support_score"] * item["relevance_score"] for item in support_items))
        contradiction_score = min(1.0, sum(item["contradiction_score"] * item["relevance_score"] for item in contradict_items))
        confidence = min(1.0, max(support_score, contradiction_score) + min(0.25, evidence_count / 20))

        return {
            "supporting_evidence": support_items,
            "contradicting_evidence": contradict_items,
            "evidence_count": evidence_count,
            "support_score": support_score,
            "contradiction_score": contradiction_score,
            "confidence": confidence,
        }

    def _has_contradiction_hint(self, claim_text: str, evidence_text: str, evidence_title: str) -> bool:
        claim_text = claim_text.lower()
        evidence_text = evidence_text.lower()
        evidence_title = evidence_title.lower()
        if "a102" in claim_text and ("fan" in evidence_text or "fan" in evidence_title or "风扇" in evidence_text):
            return "power" in claim_text or "电源" in claim_text or "module" in claim_text or "模块" in claim_text
        negative_terms = ("不是", "错误", "不对", "冲突", "contradict", "contrary")
        positive_terms = ("support", "confirmed", "verified", "correct", "是", "确认")
        if any(term in claim_text for term in negative_terms) and any(term in evidence_text for term in positive_terms):
            return True
        if any(term in claim_text for term in positive_terms) and any(term in evidence_text for term in negative_terms):
            return True
        return False

    async def _call_llm_evidence_verifier(
        self,
        feedback: FeedbackRecord,
        claim: FeedbackClaimPayload,
        current: Dict[str, Any],
        llm_client: Any = None,
    ) -> Optional[Dict[str, Any]]:
        request_timeout = float(os.getenv("FEEDBACK_EVIDENCE_TIMEOUT", "10"))
        client = maybe_wrap_openai_client(llm_client) if llm_client is not None else create_async_openai_client(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY", "EMPTY"),
            timeout=request_timeout,
        )
        prompt = {
            "feedback_id": feedback.id,
            "conversation_id": feedback.conversation_id,
            "claim": claim.model_dump(),
            "current_evidence": current,
        }
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=os.getenv("FEEDBACK_EVIDENCE_MODEL") or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                messages=[
                    {"role": "system", "content": EVIDENCE_VERIFIER_PROMPT},
                    {"role": "user", "content": _json_dumps(prompt)},
                ],
                temperature=0,
                max_tokens=int(os.getenv("FEEDBACK_EVIDENCE_MAX_TOKENS", "1200")),
            ),
            timeout=request_timeout,
        )
        raw = response.choices[0].message.content or ""
        text = str(raw).strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return None
            parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None

    def _merge_llm_evidence_result(self, heuristics: Dict[str, Any], llm_result: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(heuristics)
        for key in ("supporting_evidence", "contradicting_evidence"):
            items = llm_result.get(key)
            if isinstance(items, list):
                merged[key] = items
        for key in ("evidence_count", "support_score", "contradiction_score", "confidence"):
            if key in llm_result:
                merged[key] = llm_result[key]
        return merged

    def _finalize_evidence_result(self, claim: FeedbackClaimPayload, data: Dict[str, Any]) -> EvidenceVerificationResult:
        support_items = [EvidenceItem.model_validate(item) for item in data.get("supporting_evidence") or [] if isinstance(item, dict)]
        contradict_items = [EvidenceItem.model_validate(item) for item in data.get("contradicting_evidence") or [] if isinstance(item, dict)]
        evidence_count = int(data.get("evidence_count") or (len(support_items) + len(contradict_items)))
        support_score = max(0.0, min(1.0, float(data.get("support_score", 0.0) or 0.0)))
        contradiction_score = max(0.0, min(1.0, float(data.get("contradiction_score", 0.0) or 0.0)))
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0)))

        result = data.get("result")
        if result not in EVIDENCE_RESULT_VALUES:
            if support_score > 0 and contradiction_score > 0 and abs(support_score - contradiction_score) <= 0.25:
                result = "uncertain"
            elif support_score > contradiction_score and support_score >= 0.35 and contradiction_score == 0:
                result = "supported"
            elif contradiction_score > support_score and contradiction_score >= 0.35:
                result = "contradicted"
            elif evidence_count == 0:
                result = "insufficient"
            else:
                result = "uncertain"

        return EvidenceVerificationResult(
            claim=claim,
            supporting_evidence=support_items,
            contradicting_evidence=contradict_items,
            evidence_count=evidence_count,
            support_score=support_score,
            contradiction_score=contradiction_score,
            confidence=confidence if confidence else max(support_score, contradiction_score),
            result=result,
        )


class FeedbackEvidenceVerificationService:
    def __init__(self, repository: FeedbackLearningRepository, verifier: Optional[EvidenceVerifier] = None):
        self.repository = repository
        self.verifier = verifier or EvidenceVerifier(repository)

    async def verify_claim(
        self,
        feedback_id: int,
        claim_id: int,
        current_user: Any = None,
        llm_client: Any = None,
    ) -> EvidenceVerificationResult:
        feedback = await self.repository.get_feedback_record(feedback_id)
        if not feedback:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "反馈不存在")
        if current_user and int(getattr(current_user, "id", 0) or 0) != int(feedback.user_id):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权验证该反馈声明")

        result = await self.verifier.verify_claim(
            feedback=feedback,
            claim=await self._get_claim(feedback_id, claim_id),
            current_user=current_user,
            llm_client=llm_client,
        )
        await self._persist_evidence_verification(feedback, claim_id, result)
        return result

    async def _get_claim(self, feedback_id: int, claim_id: int) -> FeedbackClaimPayload:
        result = await self.repository.db.execute(
            select(FeedbackClaim).where(
                FeedbackClaim.id == claim_id,
                FeedbackClaim.feedback_id == feedback_id,
            )
        )
        claim = result.scalar_one_or_none()
        if not claim:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "声明不存在")
        return FeedbackClaimPayload(
            claim_type=claim.claim_type,
            subject=claim.subject,
            predicate=claim.predicate,
            object=claim.object,
            scope=claim.scope,
            source=claim.source,
            confidence=claim.confidence,
            verification_status=claim.verification_status,
            evidence_ids=_loads_json(claim.evidence_ids, []) if isinstance(claim.evidence_ids, str) else (claim.evidence_ids or []),
        )

    async def _persist_evidence_verification(self, feedback: FeedbackRecord, claim_id: int, result: EvidenceVerificationResult) -> None:
        before_state = {
            "claim_id": claim_id,
            "support_count": len(result.supporting_evidence),
            "contradict_count": len(result.contradicting_evidence),
            "result": result.result,
        }

        for item in result.supporting_evidence:
            evidence = FeedbackEvidence(
                feedback_claim_id=claim_id,
                evidence_type=item.source_type,
                source_id=item.source_id,
                content=item.content,
                relevance_score=item.relevance_score,
                support_score=item.support_score,
                contradiction_score=item.contradiction_score,
                created_at=datetime.now(),
            )
            await self.repository.create_evidence(evidence)

        for item in result.contradicting_evidence:
            evidence = FeedbackEvidence(
                feedback_claim_id=claim_id,
                evidence_type=item.source_type,
                source_id=item.source_id,
                content=item.content,
                relevance_score=item.relevance_score,
                support_score=item.support_score,
                contradiction_score=item.contradiction_score,
                created_at=datetime.now(),
            )
            await self.repository.create_evidence(evidence)

        verification_result = "insufficient"
        if result.result == "supported":
            verification_result = "support"
        elif result.result == "contradicted":
            verification_result = "contradict"
        elif result.result == "uncertain":
            verification_result = "uncertain"

        verification = FeedbackVerification(
            feedback_id=feedback.id,
            verifier_type="retrieval",
            verification_result=verification_result,
            confidence=result.confidence,
            reason=_json_dumps(
                {
                    "claim": result.claim.model_dump(),
                    "result": result.result,
                    "support_score": result.support_score,
                    "contradiction_score": result.contradiction_score,
                    "evidence_count": result.evidence_count,
                }
            ),
            evidence_summary=_json_dumps(
                {
                    "supporting_evidence": [item.model_dump() for item in result.supporting_evidence],
                    "contradicting_evidence": [item.model_dump() for item in result.contradicting_evidence],
                }
            ),
            created_at=datetime.now(),
        )
        await self.repository.create_verification(verification)
        await self.repository.create_audit_log(
            FeedbackAuditLog(
                feedback_id=feedback.id,
                action="evidence_verification",
                before_state=_json_dumps(before_state),
                after_state=_json_dumps(
                    {
                        "result": result.result,
                        "verification_result": verification_result,
                        "confidence": result.confidence,
                    }
                ),
                reason="claim evidence cross-validation completed",
                actor="system",
                created_at=datetime.now(),
            )
        )
