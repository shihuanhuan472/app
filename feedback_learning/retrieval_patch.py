import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from models import FeedbackAuditLog, FeedbackRecord, FeedbackVerification, RetrievalPatch, UserReliability
from utils.app_exceptions import AppException
from utils.error_codes import BizCode

from .repository import FeedbackLearningRepository
from .schemas import PatchDecision, PatchQueryContext


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


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)


def _loads_json(value: Any, default: Any):
    if value in (None, "", [], {}):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _scope_values(context: PatchQueryContext, scope: str) -> Dict[str, Any]:
    scope = str(scope or "").strip().lower()
    if scope == "user":
        return {"user_id": context.user_id}
    if scope == "conversation":
        return {"conversation_id": context.conversation_id}
    if scope == "device":
        return {
            "device": context.device,
            "device_type": context.device_type,
            "component": context.component,
            "firmware_version": context.firmware_version,
        }
    if scope == "fault_type":
        return {"fault_type": context.fault_type}
    return {}


def build_query_signature(context: PatchQueryContext, scope: str) -> str:
    payload = {
        "scope": str(scope or "").strip().lower(),
        "query": _normalize_text(context.query).lower(),
        "values": _scope_values(context, scope),
    }
    digest = hashlib.sha1(_json_dumps(payload).encode("utf-8")).hexdigest()
    return f"{payload['scope']}:{digest[:24]}"


def _build_reason_entry(
    patch_type: str,
    decision: PatchDecision,
    feedback: Optional[FeedbackRecord],
    verification: Optional[FeedbackVerification],
    reliability: Optional[UserReliability],
) -> Dict[str, Any]:
    return {
        "patch_type": patch_type,
        "query_signature": decision.query_signature,
        "scope": decision.scope,
        "feedback_id": getattr(feedback, "id", None),
        "verification_id": getattr(verification, "id", None),
        "verification_result": getattr(verification, "verification_result", None),
        "confidence": decision.confidence,
        "weight": decision.weight,
        "user_reliability": getattr(reliability, "reliability_score", None),
        "reason": decision.reason,
    }


class PatchPolicy:
    MAX_PATCH_WEIGHT = 0.25
    MAX_CANDIDATE_WEIGHT = 0.10
    MAX_NEW_USER_WEIGHT = 0.06
    MAX_USER_WEIGHT = 0.14
    PATCH_TTLS = {
        "candidate": timedelta(days=7),
        "active": timedelta(days=21),
        "verified": timedelta(days=30),
        "expert_verified": timedelta(days=45),
    }

    ALLOWED_SCOPES = {"conversation", "device", "fault_type", "user"}
    ALLOWED_TYPES = {"boost", "penalty", "preferred_document", "exclusion"}
    VERIFIED_RESULTS = {"verified", "expert_verified", "support"}
    BLOCKED_RESULTS = {"contradict", "contradicted", "suspicious"}
    IRRELEVANT_MARKERS = ("irrelevant", "unrelated", "not relevant", "无关", "不相关", "与我的问题无关")
    PREFERRED_MARKERS = ("correct basis", "right evidence", "正确依据", "正确的依据", "这个文档是正确")

    def allow_patch(
        self,
        feedback: Optional[FeedbackRecord],
        verification: Optional[FeedbackVerification],
        patch_type: str,
        scope: str,
        malicious: bool = False,
    ) -> bool:
        if malicious:
            return False
        if not feedback:
            return False
        feedback_type = str(getattr(feedback, "feedback_type", "")).lower()
        if feedback_type in {"suspicious"}:
            return False
        verification_result = str(getattr(verification, "verification_result", "")).lower()
        if verification_result in self.BLOCKED_RESULTS:
            return False
        if scope not in self.ALLOWED_SCOPES:
            return False
        if patch_type not in self.ALLOWED_TYPES:
            return False
        comment = str(getattr(feedback, "comment", "") or "").lower()
        if feedback_type == "positive" and patch_type == "boost":
            return False
        if feedback_type == "negative" and patch_type == "penalty":
            return any(marker in comment for marker in self.IRRELEVANT_MARKERS)
        if patch_type == "preferred_document" and verification is None:
            return any(marker in comment for marker in self.PREFERRED_MARKERS)
        return True

    def is_verified(
        self,
        verification: Optional[FeedbackVerification],
        feedback: Optional[FeedbackRecord] = None,
    ) -> bool:
        feedback_status = str(getattr(feedback, "status", "") or "").lower()
        if feedback_status in {"verified", "promoted", "expert_verified"}:
            return True
        if not verification:
            return False
        verification_result = str(getattr(verification, "verification_result", "") or "").lower()
        verifier_type = str(getattr(verification, "verifier_type", "") or "").lower()
        if verification_result in self.VERIFIED_RESULTS:
            return True
        return verification_result == "support" and verifier_type in {"human", "expert", "maintenance_outcome"}

    def compute_weight(
        self,
        feedback: Optional[FeedbackRecord],
        verification: Optional[FeedbackVerification],
        reliability: Optional[UserReliability],
        patch_type: str,
        candidate: bool,
    ) -> float:
        verification_result = str(getattr(verification, "verification_result", "")).lower()
        verifier_type = str(getattr(verification, "verifier_type", "")).lower()
        confidence = float(getattr(verification, "confidence", 0.0) or 0.0)
        reliability_score = float(getattr(reliability, "reliability_score", 0.5) or 0.5)
        if candidate:
            base = 0.04 if patch_type in {"boost", "penalty"} else 0.06
            return _clamp(base * (0.5 + confidence * 0.5) * (0.6 + reliability_score * 0.4), 0.01, self.MAX_CANDIDATE_WEIGHT)

        base = {
            "maintenance_outcome": 1.0,
            "human": 0.95,
            "expert": 0.9,
            "retrieval": 0.75,
            "rule": 0.7,
            "llm": 0.65,
            "cross_case": 0.8,
        }.get(verifier_type, 0.6)
        result_bonus = {
            "support": 1.0,
            "uncertain": 0.45,
            "insufficient": 0.25,
            "contradict": 0.0,
        }.get(verification_result, 0.3)
        user_factor = 0.45 + reliability_score * 0.55
        patch_factor = 1.0 if patch_type in {"preferred_document", "boost"} else 0.9
        weight = _clamp(base * result_bonus * user_factor * patch_factor * (0.55 + confidence * 0.45), 0.01, self.MAX_PATCH_WEIGHT)
        if self.is_new_user(reliability):
            weight = min(weight, self.MAX_NEW_USER_WEIGHT)
        return weight

    def is_new_user(self, reliability: Optional[UserReliability]) -> bool:
        if not reliability:
            return True
        return int(getattr(reliability, "verified_feedback", 0) or 0) < 2

    def determine_status(
        self,
        verification: Optional[FeedbackVerification],
        candidate: bool,
        feedback: Optional[FeedbackRecord] = None,
    ) -> str:
        if candidate:
            return "candidate"
        if self.is_verified(verification, feedback):
            return "active"
        return "candidate"

    def ttl_for(self, status: str) -> timedelta:
        return self.PATCH_TTLS.get(str(status or "").lower(), timedelta(days=7))

    def max_adjustment(self, patch_type: str) -> float:
        return self.MAX_CANDIDATE_WEIGHT if patch_type in {"boost", "penalty"} else self.MAX_PATCH_WEIGHT


class RetrievalPatchService:
    def __init__(self, repository: FeedbackLearningRepository, policy: Optional[PatchPolicy] = None):
        self.repository = repository
        self.policy = policy or PatchPolicy()

    async def create_patch(
        self,
        feedback: FeedbackRecord,
        decision: PatchDecision,
        verification: Optional[FeedbackVerification] = None,
        reliability: Optional[UserReliability] = None,
        malicious: bool = False,
        document_id: Optional[int] = None,
    ) -> Optional[RetrievalPatch]:
        if not self.policy.allow_patch(feedback, verification, decision.patch_type, decision.scope, malicious=malicious):
            return None

        candidate = not self.policy.is_verified(verification, feedback)
        if candidate and decision.patch_type == "boost":
            return None
        weight = self.policy.compute_weight(feedback, verification, reliability, decision.patch_type, candidate)
        weight = min(weight, self.policy.max_adjustment(decision.patch_type))
        if str(getattr(feedback, "feedback_type", "")).lower() == "negative" and decision.patch_type in {"boost", "preferred_document"}:
            weight = min(weight, 0.05)
        status = self.policy.determine_status(verification, candidate, feedback)
        expires_at = datetime.now() + self.policy.ttl_for(status)
        patch = RetrievalPatch(
            feedback_id=feedback.id,
            scope=decision.scope,
            query_signature=decision.query_signature,
            document_id=int(document_id or decision.document_id or 0),
            patch_type=decision.patch_type,
            weight=weight,
            confidence=_clamp(float(decision.confidence or 0.0)),
            expires_at=decision.expires_at or expires_at,
            status=status,
            created_at=datetime.now(),
        )
        if patch.document_id <= 0:
            return None

        created = await self.repository.create_retrieval_patch(patch)
        await self.repository.create_audit_log(
            FeedbackAuditLog(
                feedback_id=feedback.id,
                action="retrieval_patch_created",
                before_state=_json_dumps({}),
                after_state=_json_dumps(created.__dict__),
                reason=_json_dumps(
                    _build_reason_entry(decision.patch_type, decision, feedback, verification, reliability)
                ),
                actor="system",
                created_at=datetime.now(),
            )
        )
        return created

    async def revoke_patch(self, patch_id: int, reason: str = "manual_revoke") -> RetrievalPatch:
        patch = await self.repository.get_retrieval_patch_by_id(patch_id)
        if not patch:
            raise AppException(404, BizCode.NOT_FOUND, "patch不存在")
        patch.status = "revoked"
        patch.expires_at = datetime.now()
        patch_result = await self.repository.save_retrieval_patch(patch)
        await self.repository.create_audit_log(
            FeedbackAuditLog(
                feedback_id=patch.feedback_id,
                action="retrieval_patch_revoked",
                before_state=_json_dumps({}),
                after_state=_json_dumps(patch_result.__dict__),
                reason=reason,
                actor="system",
                created_at=datetime.now(),
            )
        )
        return patch_result

    async def expire_patches(self) -> int:
        patches = await self.repository.get_active_retrieval_patches()
        expired = 0
        now = datetime.now()
        for patch in patches:
            if patch.expires_at and patch.expires_at <= now and patch.status in {"active", "candidate"}:
                patch.status = "expired"
                await self.repository.save_retrieval_patch(patch)
                await self.repository.create_audit_log(
                    FeedbackAuditLog(
                        feedback_id=patch.feedback_id,
                        action="retrieval_patch_expired",
                        before_state=_json_dumps({}),
                        after_state=_json_dumps(patch.__dict__),
                        reason="ttl_expired",
                        actor="system",
                        created_at=now,
                    )
                )
                expired += 1
        return expired

    async def audit_patch(self, patch_id: int) -> Optional[RetrievalPatch]:
        return await self.repository.get_retrieval_patch_by_id(patch_id)

    async def get_applicable_patches(self, query_context: PatchQueryContext) -> List[RetrievalPatch]:
        patches: List[RetrievalPatch] = []
        for scope in ("conversation", "device", "fault_type", "user"):
            signature = build_query_signature(query_context, scope)
            patches.extend(await self.repository.get_retrieval_patch_by_signature(signature, scope=scope))
        now = datetime.now()
        active: List[RetrievalPatch] = []
        seen = set()
        for patch in patches:
            if patch.status in {"revoked", "expired", "rejected"}:
                continue
            if patch.status != "active":
                continue
            if patch.expires_at and patch.expires_at <= now:
                continue
            key = (patch.scope, patch.query_signature, patch.document_id, patch.patch_type)
            if key in seen:
                continue
            seen.add(key)
            active.append(patch)
        return active


class PatchApplier:
    MAX_TOTAL_ADJUSTMENT = 0.25

    def __init__(self, policy: Optional[PatchPolicy] = None):
        self.policy = policy or PatchPolicy()

    def apply_patches(self, results: Sequence[Dict[str, Any]], patches: Sequence[RetrievalPatch]) -> List[Dict[str, Any]]:
        if not patches:
            return [self._normalize_result(item) for item in results]
        patched_results = []
        for result in results:
            item = self._normalize_result(result)
            doc_id = int(item.get("doc_id") or 0)
            contributions = []
            for patch in patches:
                if patch.document_id != doc_id:
                    continue
                contribution = self._patch_contribution(item, patch)
                if contribution == 0:
                    continue
                contributions.append({
                    "patch_id": patch.id,
                    "patch_type": patch.patch_type,
                    "scope": patch.scope,
                    "weight": patch.weight,
                    "confidence": patch.confidence,
                    "contribution": contribution,
                    "query_signature": patch.query_signature,
                    "expires_at": patch.expires_at,
                })
            patch_score = sum(entry["contribution"] for entry in contributions)
            patch_score = max(-self.MAX_TOTAL_ADJUSTMENT, min(self.MAX_TOTAL_ADJUSTMENT, patch_score))
            base_score = float(item.get("base_score", item.get("score", 0.0)) or 0.0)
            if patch_score > 0:
                patch_score = min(patch_score, base_score, 1.0 - base_score)
            else:
                patch_score = max(patch_score, -base_score)
            final_score = max(0.0, min(1.0, base_score + patch_score))
            item["base_score"] = base_score
            item["patch_score"] = patch_score
            item["final_score"] = final_score
            item["score"] = final_score
            item["patches"] = contributions
            patched_results.append(item)
        patched_results.sort(key=lambda x: float(x.get("final_score", x.get("score", 0.0))), reverse=True)
        return patched_results

    def revoke_patch(self, patch: RetrievalPatch) -> RetrievalPatch:
        patch.status = "revoked"
        patch.expires_at = datetime.now()
        return patch

    def expire_patches(self, patches: Sequence[RetrievalPatch]) -> List[RetrievalPatch]:
        now = datetime.now()
        return [patch for patch in patches if not patch.expires_at or patch.expires_at > now]

    def _normalize_result(self, item: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(item)
        base = float(normalized.get("base_score", normalized.get("score", 0.0)) or 0.0)
        normalized.setdefault("base_score", base)
        normalized.setdefault("patch_score", 0.0)
        normalized.setdefault("final_score", base)
        normalized.setdefault("patches", [])
        normalized["score"] = float(normalized.get("score", base) or base)
        return normalized

    def _patch_contribution(self, result: Dict[str, Any], patch: RetrievalPatch) -> float:
        if str(patch.status or "").lower() != "active":
            return 0.0
        base = float(patch.weight or 0.0)
        confidence = float(patch.confidence or 0.0)
        status_factor = {"active": 1.0, "candidate": 0.35}.get(str(patch.status or "").lower(), 0.0)
        patch_type_sign = 1.0 if patch.patch_type in {"boost", "preferred_document"} else -1.0
        doc_score = float(result.get("base_score", result.get("score", 0.0)) or 0.0)
        proximity = 0.5 + min(0.5, doc_score)
        contribution = patch_type_sign * base * (0.5 + confidence * 0.5) * status_factor * proximity
        if patch.patch_type == "preferred_document" and patch.document_id == result.get("doc_id"):
            contribution = abs(contribution)
        if patch.patch_type == "exclusion":
            contribution = -abs(contribution)
        return contribution
