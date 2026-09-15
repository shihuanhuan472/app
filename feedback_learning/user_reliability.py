import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from models import FeedbackAuditLog, FeedbackRecord, FeedbackVerification, UserReliability
from utils.app_exceptions import AppException
from utils.error_codes import BizCode

from .repository import FeedbackLearningRepository


def _loads_json(value: Any, default: Any):
    if value in (None, "", [], {}):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _get_feedback_direction(feedback: FeedbackRecord, verification: Optional[FeedbackVerification]) -> int:
    if not verification:
        return 0
    result = str(verification.verification_result or "").lower()
    if result == "support":
        return 1
    if result == "contradict":
        return -1
    if result in {"insufficient", "uncertain"}:
        return 0
    return 0


def _evidence_strength_from_verification(verification: Optional[FeedbackVerification]) -> float:
    if not verification:
        return 0.0
    base = {
        "maintenance_outcome": 1.0,
        "human": 0.95,
        "expert": 0.9,
        "retrieval": 0.7,
        "rule": 0.7,
        "llm": 0.6,
        "cross_case": 0.8,
    }.get(str(verification.verifier_type or "").lower(), 0.55)
    evidence_summary = _loads_json(verification.evidence_summary, {})
    if isinstance(evidence_summary, dict):
        evidence_items = list(evidence_summary.get("supporting_evidence") or []) + list(evidence_summary.get("contradicting_evidence") or [])
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("source_type") or "").lower()
            if source_type == "fault_case":
                base = max(base, 0.9)
            elif source_type == "document":
                base = max(base, 0.75)
            elif source_type == "conversation_context":
                base = max(base, 0.65)
            elif source_type == "working_memory":
                base = max(base, 0.7)
            elif source_type == "answer":
                base = max(base, 0.55)
    return _clamp(base)


def _feedback_scope(feedback: FeedbackRecord) -> str:
    query_snapshot = _loads_json(feedback.query_snapshot, {})
    if isinstance(query_snapshot, dict):
        device_context = query_snapshot.get("device_context") or {}
        if isinstance(device_context, dict):
            for key in ("active_device", "active_component", "active_error_code", "active_issue"):
                if device_context.get(key):
                    return "conversation"
    comment = str(feedback.comment or "").lower()
    if any(token in comment for token in ("全局", "所有", "通用")):
        return "global"
    return "conversation"


class UserReliabilityService:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def update_reliability(self, feedback_id: int) -> Dict[str, Any]:
        feedback = await self.repository.get_feedback_by_id(feedback_id)
        if not feedback:
            raise AppException(404, BizCode.NOT_FOUND, "反馈不存在")

        audit = await self.repository.get_feedback_audit_log(feedback_id, "user_reliability_updated")
        if audit:
            row = await self.repository.get_or_create_user_reliability(feedback.user_id)
            return self._serialize(self._normalize_row(row), applied=False, reason="duplicate")

        row = await self.repository.get_or_create_user_reliability(feedback.user_id)
        row = self._normalize_row(row)
        verification = await self.repository.get_latest_feedback_verification(feedback_id)
        recent_feedbacks = await self.repository.get_recent_feedbacks_for_user(feedback.user_id, limit=20)
        recent_feedbacks = [item for item in recent_feedbacks if item.id != feedback.id][:19]

        evidence_strength = _evidence_strength_from_verification(verification)
        direction = _get_feedback_direction(feedback, verification)
        anomaly = self._detect_anomaly(feedback, recent_feedbacks)
        influence_weight = evidence_strength * anomaly["multiplier"]
        influence_weight = _clamp(influence_weight, 0.0, 1.0)

        before_state = self._serialize(row, applied=False, reason=None)

        if direction == 0 or evidence_strength <= 0.0:
            audit_log = FeedbackAuditLog(
                feedback_id=feedback.id,
                action="user_reliability_skipped",
                before_state=_json_dumps(before_state),
                after_state=_json_dumps(before_state),
                reason=_json_dumps(
                    {
                        "reason": "insufficient_verified_evidence",
                        "feedback_id": feedback.id,
                        "verification_result": getattr(verification, "verification_result", None),
                    }
                ),
                actor="system",
                created_at=datetime.now(),
            )
            await self.repository.create_audit_log(audit_log)
            return self._serialize(row, applied=False, reason="insufficient_verified_evidence")

        prior_strength = min(12.0, 2.0 + max(0, int(row.total_feedback or 0)) * 0.25)
        if row.last_updated_at:
            days_idle = max(0.0, (datetime.now() - row.last_updated_at).total_seconds() / 86400.0)
            prior_strength *= pow(row.decay_factor or 0.98, days_idle / 7.0)
        prior_alpha = max(1.0, row.reliability_score * prior_strength)
        prior_beta = max(1.0, (1.0 - row.reliability_score) * prior_strength)

        signed_weight = max(0.05, influence_weight)
        if direction < 0:
            signed_weight = -signed_weight
        pos_weight = signed_weight if signed_weight > 0 else 0.0
        neg_weight = abs(signed_weight) if signed_weight < 0 else 0.0

        posterior_alpha = prior_alpha + pos_weight
        posterior_beta = prior_beta + neg_weight
        posterior_score = posterior_alpha / (posterior_alpha + posterior_beta)
        posterior_score = self._apply_delta_cap(row.reliability_score, posterior_score, cap=0.18)
        posterior_score = _clamp(posterior_score)

        row.total_feedback += 1
        row.last_updated_at = datetime.now()
        row.reliability_score = posterior_score
        row.recent_verified_weight = row.recent_verified_weight * row.decay_factor + pos_weight
        row.recent_rejected_weight = row.recent_rejected_weight * row.decay_factor + neg_weight
        if str(feedback.feedback_type or "").lower() == "correction":
            row.correction_feedback += 1
            if direction > 0:
                row.correct_correction_count += 1
            row.recent_correction_weight = row.recent_correction_weight * row.decay_factor + influence_weight
        if direction > 0:
            row.verified_feedback += 1
        elif direction < 0:
            row.rejected_feedback += 1

        await self.repository.save_user_reliability(row)

        after_state = self._serialize(row, applied=True, reason=None)
        after_state.update(
            {
                "evidence_strength": evidence_strength,
                "influence_weight": influence_weight,
                "anomaly": anomaly,
                "verification": self._verification_summary(verification),
            }
        )
        audit_log = FeedbackAuditLog(
            feedback_id=feedback.id,
            action="user_reliability_updated",
            before_state=_json_dumps(before_state),
            after_state=_json_dumps(after_state),
            reason=_json_dumps(
                {
                    "feedback_id": feedback.id,
                    "direction": direction,
                    "evidence_strength": evidence_strength,
                    "influence_weight": influence_weight,
                    "prior_strength": prior_strength,
                    "posterior_score": posterior_score,
                    "scope": _feedback_scope(feedback),
                }
            ),
            actor="system",
            created_at=datetime.now(),
        )
        await self.repository.create_audit_log(audit_log)
        return self._serialize(row, applied=True, reason=None)

    def _verification_summary(self, verification: Optional[FeedbackVerification]) -> Dict[str, Any]:
        if not verification:
            return {}
        return {
            "id": verification.id,
            "verifier_type": verification.verifier_type,
            "verification_result": verification.verification_result,
            "confidence": verification.confidence,
        }

    def _serialize(self, row: UserReliability, applied: bool, reason: Optional[str]) -> Dict[str, Any]:
        return {
            "user_id": row.user_id,
            "total_feedback": row.total_feedback,
            "verified_feedback": row.verified_feedback,
            "rejected_feedback": row.rejected_feedback,
            "correction_feedback": row.correction_feedback,
            "correct_correction_count": row.correct_correction_count,
            "reliability_score": row.reliability_score,
            "recent_verified_weight": row.recent_verified_weight,
            "recent_rejected_weight": row.recent_rejected_weight,
            "recent_correction_weight": row.recent_correction_weight,
            "last_updated_at": row.last_updated_at,
            "applied": applied,
            "reason": reason,
        }

    def _normalize_row(self, row: UserReliability) -> UserReliability:
        row.total_feedback = int(row.total_feedback or 0)
        row.verified_feedback = int(row.verified_feedback or 0)
        row.rejected_feedback = int(row.rejected_feedback or 0)
        row.correction_feedback = int(row.correction_feedback or 0)
        row.correct_correction_count = int(row.correct_correction_count or 0)
        row.reliability_score = _clamp(float(row.reliability_score if row.reliability_score is not None else 0.5))
        row.decay_factor = _clamp(float(row.decay_factor if row.decay_factor is not None else 0.98), 0.0, 1.0)
        row.recent_verified_weight = float(row.recent_verified_weight or 0.0)
        row.recent_rejected_weight = float(row.recent_rejected_weight or 0.0)
        row.recent_correction_weight = float(row.recent_correction_weight or 0.0)
        return row

    def _detect_anomaly(self, feedback: FeedbackRecord, recent_feedbacks: List[FeedbackRecord]) -> Dict[str, Any]:
        multiplier = 1.0
        reasons: List[str] = []
        now = datetime.now()
        recent_window = []
        for item in recent_feedbacks:
            created_at = item.created_at or now
            if now - created_at <= timedelta(hours=24):
                recent_window.append(item)

        if len(recent_window) >= 8:
            multiplier *= 0.7
            reasons.append("high_volume_24h")
        if len(recent_window) >= 4 and all(
            (recent_window[i].feedback_type or "").lower() == (recent_window[0].feedback_type or "").lower()
            for i in range(len(recent_window))
        ):
            multiplier *= 0.75
            reasons.append("same_polarity")
        if len(recent_window) >= 4:
            polarity = [(item.feedback_type or "").lower() in {"positive", "confirmation"} for item in recent_window[:4]]
            if polarity == [True, False, True, False] or polarity == [False, True, False, True]:
                multiplier *= 0.6
                reasons.append("alternating_polarity")

        mentions = Counter()
        for item in recent_window:
            comment = str(item.comment or "").lower()
            query_snapshot = _loads_json(item.query_snapshot, {})
            if isinstance(query_snapshot, dict):
                device_context = query_snapshot.get("device_context") or {}
                if isinstance(device_context, dict):
                    for key in ("active_device", "active_component", "active_error_code", "active_issue"):
                        value = device_context.get(key)
                        if value:
                            mentions[f"{key}:{value}"] += 1
            for token in ("fan", "power", "module", "风扇", "电源", "模块", "A102"):
                if token.lower() in comment:
                    mentions[f"comment:{token.lower()}"] += 1
        if mentions:
            top = mentions.most_common(1)[0]
            if top[1] >= 4:
                multiplier *= 0.8
                reasons.append(f"concentrated:{top[0]}")

        if feedback.comment and len(str(feedback.comment).strip()) <= 2:
            multiplier *= 0.85
            reasons.append("low_signal_comment")

        return {
            "multiplier": _clamp(multiplier, 0.25, 1.0),
            "reasons": reasons,
            "recent_count": len(recent_window),
        }

    def _apply_delta_cap(self, old_score: float, new_score: float, cap: float = 0.18) -> float:
        delta = new_score - old_score
        if delta > cap:
            return old_score + cap
        if delta < -cap:
            return old_score - cap
        return new_score
