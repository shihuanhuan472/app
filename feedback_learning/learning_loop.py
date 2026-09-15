import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from models import FeedbackAuditLog, FeedbackRecord

from .fault_case import FaultCaseService
from .repository import FeedbackLearningRepository
from .retrieval_patch import RetrievalPatchService, build_query_signature
from .schemas import PatchDecision, PatchQueryContext
from .service import EvidenceVerifier, FeedbackAnalysisService, _loads_json
from .user_reliability import UserReliabilityService


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class PromotionCandidate:
    case_id: int
    evidence_count: int
    supporting_feedback_count: int
    supporting_user_reliability: float
    contradiction_count: int
    maintenance_outcome: Optional[Dict[str, Any]]
    confidence: float
    recommended_action: str


class PromotionPolicy:
    def __init__(
        self,
        strong_evidence_threshold: int = 1,
        confidence_threshold: float = 0.72,
        contradiction_threshold: int = 0,
    ):
        self.strong_evidence_threshold = strong_evidence_threshold
        self.confidence_threshold = confidence_threshold
        self.contradiction_threshold = contradiction_threshold

    def evaluate(self, case: Any, reliability: Any = None, maintenance_outcome: Optional[Dict[str, Any]] = None) -> PromotionCandidate:
        evidence_ids = _loads_json(getattr(case, "evidence_ids", None), [])
        source_feedback_ids = _loads_json(getattr(case, "source_feedback_ids", None), [])
        confidence = float(getattr(case, "confidence", 0.0) or 0.0)
        reliability_score = float(getattr(reliability, "reliability_score", 0.5) or 0.5)
        contradiction_count = 0
        if str(getattr(case, "verification_status", "") or "").lower() == "rejected":
            contradiction_count = 1

        candidate = PromotionCandidate(
            case_id=int(case.id),
            evidence_count=len(evidence_ids),
            supporting_feedback_count=len(source_feedback_ids),
            supporting_user_reliability=reliability_score,
            contradiction_count=contradiction_count,
            maintenance_outcome=maintenance_outcome,
            confidence=confidence,
            recommended_action="hold",
        )

        status = str(getattr(case, "verification_status", "") or "").lower()
        has_strong_evidence = candidate.evidence_count >= self.strong_evidence_threshold or confidence >= self.confidence_threshold
        has_no_strong_conflict = candidate.contradiction_count <= self.contradiction_threshold

        if status == "candidate" and has_strong_evidence and has_no_strong_conflict:
            candidate.recommended_action = "promote_to_verified"
        if status == "verified" and has_no_strong_conflict:
            if maintenance_outcome and maintenance_outcome.get("outcome"):
                candidate.recommended_action = "promote_to_expert_verified"
            elif confidence >= 0.9 and reliability_score >= 0.85:
                candidate.recommended_action = "require_human_review_for_expert"
        if _enabled("ENABLE_KNOWLEDGE_PROMOTION", False):
            if (
                status == "expert_verified"
                and candidate.evidence_count >= int(os.getenv("KNOWLEDGE_PROMOTION_EVIDENCE_THRESHOLD", "3"))
                and confidence >= float(os.getenv("KNOWLEDGE_PROMOTION_CONFIDENCE_THRESHOLD", "0.9"))
                and candidate.contradiction_count <= int(os.getenv("KNOWLEDGE_PROMOTION_CONTRADICTION_THRESHOLD", "0"))
            ):
                candidate.recommended_action = "require_human_review_for_global_promotion"

        return candidate


class KnowledgePromotionQueue:
    def __init__(self, repository: FeedbackLearningRepository, policy: Optional[PromotionPolicy] = None):
        self.repository = repository
        self.policy = policy or PromotionPolicy()

    async def evaluate_case(self, case: Any, feedback: Optional[FeedbackRecord] = None) -> PromotionCandidate:
        reliability = None
        if feedback:
            reliability = await self.repository.get_user_reliability(feedback.user_id)
        candidate = self.policy.evaluate(case, reliability=reliability)
        await self.repository.create_audit_log(
            FeedbackAuditLog(
                feedback_id=feedback.id if feedback else int(_loads_json(getattr(case, "source_feedback_ids", []), [0])[0] or 0),
                action="promotion_candidate_evaluated",
                before_state=_json_dumps({"case_status": getattr(case, "verification_status", None)}),
                after_state=_json_dumps(asdict(candidate)),
                reason="policy_controlled_promotion_queue",
                actor="system",
                created_at=datetime.now(),
            )
        )
        return candidate


class FeedbackLearningJob:
    STATES = [
        "pending",
        "analysis",
        "claim_extraction",
        "evidence_verification",
        "reliability_update",
        "patch_decision",
        "case_decision",
        "audit",
        "completed",
    ]

    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository
        self.analysis_service = FeedbackAnalysisService(repository)
        self.evidence_verifier = EvidenceVerifier(repository)
        self.reliability_service = UserReliabilityService(repository)
        self.patch_service = RetrievalPatchService(repository)
        self.case_service = FaultCaseService(repository)
        self.promotion_queue = KnowledgePromotionQueue(repository)

    async def process_pending(self, limit: int = 20) -> List[Dict[str, Any]]:
        feedbacks = await self.repository.get_pending_feedbacks(limit=limit)
        results = []
        for feedback in feedbacks:
            results.append(await self.process_feedback(feedback.id))
        return results

    async def process_feedback(self, feedback_id: int) -> Dict[str, Any]:
        feedback = await self.repository.get_feedback_by_id(feedback_id)
        if not feedback:
            return {"feedback_id": feedback_id, "status": "missing"}
        if feedback.status == "completed":
            return {"feedback_id": feedback_id, "status": "completed", "idempotent": True}

        try:
            await self._mark(feedback, "analysis_started", "analysis")
            verification = await self._verify_once(feedback)
            await self._mark(feedback, "claim_extraction_completed", "claim_extraction")

            evidence_results = []
            claims = await self.repository.get_claims_for_feedback_ids([feedback.id])
            if not await self._has_audit(feedback.id, "evidence_verification_completed"):
                for claim in claims:
                    if str(getattr(claim, "verification_status", "") or "").lower() in {"contradicted", "verified"}:
                        continue
                    payload = self._claim_payload_from_model(claim)
                    evidence_results.append(await self.evidence_verifier.verify_claim(feedback, payload))
                await self._audit(feedback.id, "evidence_verification_completed", {}, {"result_count": len(evidence_results)})
            await self._mark(feedback, "reliability_update_started", "reliability_update")
            await self.reliability_service.update_reliability(feedback.id)

            await self._mark(feedback, "patch_decision_started", "patch_decision")
            patch = await self._create_patch_once(feedback, verification)

            await self._mark(feedback, "case_decision_started", "case_decision")
            case = await self._create_case_once(feedback, evidence_results)
            promotion = await self.promotion_queue.evaluate_case(case, feedback) if case else None

            await self._audit(feedback.id, "feedback_learning_completed", {}, {
                "verification_id": getattr(verification, "id", None),
                "patch_id": getattr(patch, "id", None),
                "case_id": getattr(case, "id", None),
                "promotion": asdict(promotion) if promotion else None,
            })
            feedback.status = "completed"
            await self.repository.save_feedback(feedback)
            return {"feedback_id": feedback.id, "status": "completed"}
        except Exception as error:
            feedback.status = "retry"
            await self.repository.save_feedback(feedback)
            await self._audit(feedback.id, "feedback_learning_failed", {}, {"error": str(error)})
            return {"feedback_id": feedback.id, "status": "retry", "error": str(error)}

    async def _verify_once(self, feedback: FeedbackRecord):
        existing = await self.repository.get_latest_feedback_verification(feedback.id)
        if existing:
            return existing
        return await self.analysis_service.verify_and_persist(feedback.id)

    async def _create_patch_once(self, feedback: FeedbackRecord, verification: Any):
        existing = await self.repository.get_feedback_audit_log(feedback.id, "retrieval_patch_created")
        if existing:
            return None
        docs = _loads_json(feedback.retrieved_documents, [])
        doc = docs[0] if docs and isinstance(docs[0], dict) else {}
        doc_id = doc.get("doc_id")
        if not doc_id:
            return None
        query_snapshot = _loads_json(feedback.query_snapshot, {})
        query = query_snapshot.get("retrieval_query") or query_snapshot.get("user_question") or str(feedback.comment or "")
        scope = "conversation"
        context = PatchQueryContext(query=query, user_id=feedback.user_id, conversation_id=feedback.conversation_id)
        patch_type = "preferred_document" if str(feedback.feedback_type or "").lower() in {"confirmation", "positive"} else "penalty"
        decision = PatchDecision(
            patch_type=patch_type,
            document_id=int(doc_id),
            query_signature=build_query_signature(context, scope),
            scope=scope,
            confidence=float(getattr(verification, "confidence", 0.0) or 0.0),
            reason="feedback_learning_job_patch_decision",
        )
        reliability = await self.repository.get_user_reliability(feedback.user_id)
        return await self.patch_service.create_patch(feedback, decision, verification=verification, reliability=reliability)

    async def _create_case_once(self, feedback: FeedbackRecord, evidence_results: List[Any]):
        existing = await self.repository.get_feedback_audit_log(feedback.id, "fault_case_decision_completed")
        if existing:
            return None
        supported = [item for item in evidence_results if str(getattr(item, "result", "")).lower() in {"supported", "support"}]
        if not supported:
            await self._audit(feedback.id, "fault_case_decision_completed", {}, {"created": False, "reason": "no_supported_claim"})
            return None
        case = await self.case_service.create_candidate_case(feedback, supported[0])
        await self._audit(feedback.id, "fault_case_decision_completed", {}, {"created": True, "case_id": case.id})
        return case

    def _claim_payload_from_model(self, claim: Any):
        from .schemas import FeedbackClaimPayload

        return FeedbackClaimPayload(
            claim_type=claim.claim_type,
            subject=claim.subject,
            predicate=claim.predicate,
            object=claim.object,
            scope=claim.scope,
            source=claim.source,
            confidence=float(claim.confidence or 0.0),
            verification_status=claim.verification_status,
            evidence_ids=_loads_json(claim.evidence_ids, []),
        )

    async def _mark(self, feedback: FeedbackRecord, action: str, status_value: str) -> None:
        if await self._has_audit(feedback.id, action):
            return
        before = {"status": feedback.status}
        feedback.status = status_value
        await self.repository.save_feedback(feedback)
        await self._audit(feedback.id, action, before, {"status": status_value})

    async def _has_audit(self, feedback_id: int, action: str) -> bool:
        return bool(await self.repository.get_feedback_audit_log(feedback_id, action))

    async def _audit(self, feedback_id: int, action: str, before: Dict[str, Any], after: Dict[str, Any]) -> None:
        if await self._has_audit(feedback_id, action):
            return
        await self.repository.create_audit_log(
            FeedbackAuditLog(
                feedback_id=feedback_id,
                action=action,
                before_state=_json_dumps(before),
                after_state=_json_dumps(after),
                reason="feedback_learning_job",
                actor="system",
                created_at=datetime.now(),
            )
        )


class FeedbackLearningEvaluationService:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def correction_lag(self, feedback_id: int) -> Optional[Dict[str, Any]]:
        feedback = await self.repository.get_feedback_by_id(feedback_id)
        if not feedback:
            return None
        patch_audit = await self.repository.get_feedback_audit_log(feedback_id, "retrieval_patch_created")
        if not patch_audit:
            return {"feedback_id": feedback_id, "applied": False, "correction_lag_seconds": None}
        created_at = getattr(feedback, "created_at", None)
        applied_at = getattr(patch_audit, "created_at", None)
        lag = (applied_at - created_at).total_seconds() if created_at and applied_at else None
        return {"feedback_id": feedback_id, "applied": True, "correction_lag_seconds": lag, "first_applied_at": applied_at}

    async def post_feedback_performance(self, feedback_id: int) -> Dict[str, Any]:
        feedback = await self.repository.get_feedback_by_id(feedback_id)
        if not feedback:
            return {"feedback_id": feedback_id, "status": "missing"}
        before_docs = _loads_json(feedback.retrieved_documents, [])
        target_doc_id = before_docs[0].get("doc_id") if before_docs and isinstance(before_docs[0], dict) else None
        patch_audit = await self.repository.get_feedback_audit_log(feedback_id, "retrieval_patch_created")
        reliability_audit = await self.repository.get_feedback_audit_log(feedback_id, "user_reliability_updated")
        before_rank = self._rank(before_docs, target_doc_id)
        return {
            "feedback_id": feedback_id,
            "target_document_id": target_doc_id,
            "before": {
                "retrieval_hit": target_doc_id is not None,
                "target_document_rank": before_rank,
                "user_feedback": feedback.feedback_type,
            },
            "after": {
                "retrieval_hit": bool(patch_audit),
                "target_document_rank": 1 if patch_audit and target_doc_id is not None else None,
                "answer_correctness": None,
                "user_feedback": None,
            },
            "signals": {
                "patch_created": bool(patch_audit),
                "reliability_updated": bool(reliability_audit),
            },
        }

    def _rank(self, docs: List[Dict[str, Any]], target_doc_id: Optional[int]) -> Optional[int]:
        if target_doc_id is None:
            return None
        for index, doc in enumerate(docs or [], start=1):
            if isinstance(doc, dict) and doc.get("doc_id") == target_doc_id:
                return index
        return None
