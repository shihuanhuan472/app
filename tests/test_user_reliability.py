import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from feedback_learning.user_reliability import UserReliabilityService
from models import FeedbackAuditLog, FeedbackRecord, FeedbackVerification, UserReliability


class FakeRepo:
    def __init__(self, feedback=None, verification=None, reliability=None, recent_feedbacks=None, audit=None):
        self.feedback = feedback
        self.verification = verification
        self.reliability = reliability
        self.recent_feedbacks = recent_feedbacks or []
        self.audit = audit
        self.saved = []
        self.audit_logs = []

    async def get_feedback_by_id(self, feedback_id):
        return self.feedback if self.feedback and self.feedback.id == feedback_id else None

    async def get_feedback_audit_log(self, feedback_id, action):
        return self.audit

    async def get_or_create_user_reliability(self, user_id):
        if self.reliability and self.reliability.user_id == user_id:
            return self.reliability
        self.reliability = UserReliability(user_id=user_id)
        return self.reliability

    async def get_latest_feedback_verification(self, feedback_id):
        return self.verification

    async def get_recent_feedbacks_for_user(self, user_id, limit=20):
        return list(self.recent_feedbacks)

    async def save_user_reliability(self, row):
        self.reliability = row
        self.saved.append(row)
        return row

    async def create_audit_log(self, audit_log):
        audit_log.id = len(self.audit_logs) + 1
        self.audit_logs.append(audit_log)
        return audit_log


def make_feedback(
    feedback_id=1,
    user_id=7,
    feedback_type="correction",
    comment="不是风扇，是电源模块",
    query_snapshot=None,
    created_at=None,
):
    return FeedbackRecord(
        id=feedback_id,
        conversation_id=10,
        message_id=20,
        user_id=user_id,
        feedback_type=feedback_type,
        rating=1,
        comment=comment,
        created_at=created_at or datetime.now(),
        query_snapshot=json.dumps(query_snapshot or {"device_context": {"active_device": "Cooling fan"}}, ensure_ascii=False),
        answer_snapshot=json.dumps({"answer_text": "A102 表示风扇异常"}, ensure_ascii=False),
        retrieved_documents=json.dumps([], ensure_ascii=False),
        cited_documents=json.dumps([], ensure_ascii=False),
        trace_id=88,
        status="pending",
    )


def make_verification(result="support", verifier_type="retrieval", evidence_summary=None):
    return FeedbackVerification(
        id=11,
        feedback_id=1,
        verifier_type=verifier_type,
        verification_result=result,
        confidence=0.8,
        reason="{}",
        evidence_summary=json.dumps(
            evidence_summary
            or {
                "supporting_evidence": [{"source_type": "document", "source_id": "document:1"}],
                "contradicting_evidence": [],
            },
            ensure_ascii=False,
        ),
        created_at=datetime.now(),
    )


def make_reliability(score=0.5, last_updated_at=None):
    return UserReliability(
        user_id=7,
        total_feedback=0,
        verified_feedback=0,
        rejected_feedback=0,
        correction_feedback=0,
        correct_correction_count=0,
        reliability_score=score,
        decay_factor=0.98,
        recent_verified_weight=0.0,
        recent_rejected_weight=0.0,
        recent_correction_weight=0.0,
        last_updated_at=last_updated_at,
    )


@pytest.mark.asyncio
async def test_new_user_starts_neutral():
    repo = FakeRepo(feedback=make_feedback(), verification=make_verification(), reliability=None)
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert 0.4 <= result["reliability_score"] <= 0.7
    assert result["reliability_score"] != 1.0


@pytest.mark.asyncio
async def test_continuous_correct_feedback_increases_score_gradually():
    reliability = make_reliability(0.5)
    repo = FakeRepo(feedback=make_feedback(feedback_id=1), verification=make_verification("support"), reliability=reliability)
    service = UserReliabilityService(repo)

    first = await service.update_reliability(1)
    repo.feedback = make_feedback(feedback_id=2)
    repo.verification = make_verification("support")
    repo.audit = None
    repo.saved.clear()
    second = await service.update_reliability(2)

    assert second["reliability_score"] >= first["reliability_score"]
    assert second["reliability_score"] <= 0.9


@pytest.mark.asyncio
async def test_continuous_wrong_feedback_decreases_score_gradually():
    reliability = make_reliability(0.75)
    repo = FakeRepo(feedback=make_feedback(feedback_id=1), verification=make_verification("contradict"), reliability=reliability)
    service = UserReliabilityService(repo)

    first = await service.update_reliability(1)
    repo.feedback = make_feedback(feedback_id=2)
    repo.verification = make_verification("contradict")
    repo.audit = None
    second = await service.update_reliability(2)

    assert second["reliability_score"] <= first["reliability_score"]
    assert second["reliability_score"] >= 0.1


@pytest.mark.asyncio
async def test_alternating_feedback_reduces_influence():
    recent = [
        make_feedback(feedback_id=2, feedback_type="positive", comment="ok"),
        make_feedback(feedback_id=3, feedback_type="negative", comment="错了"),
        make_feedback(feedback_id=4, feedback_type="positive", comment="ok"),
        make_feedback(feedback_id=5, feedback_type="negative", comment="错了"),
    ]
    repo = FakeRepo(
        feedback=make_feedback(feedback_id=1),
        verification=make_verification("support"),
        reliability=make_reliability(),
        recent_feedbacks=recent,
    )
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert result["reliability_score"] < 0.7
    assert repo.audit_logs
    after_state = json.loads(repo.audit_logs[-1].after_state)
    assert "alternating_polarity" in after_state["anomaly"]["reasons"]


@pytest.mark.asyncio
async def test_spam_feedback_lowers_influence_weight():
    recent = [make_feedback(feedback_id=i + 2, feedback_type="positive", comment="ok", created_at=datetime.now() - timedelta(minutes=i)) for i in range(8)]
    repo = FakeRepo(
        feedback=make_feedback(feedback_id=1, created_at=datetime.now()),
        verification=make_verification("support"),
        reliability=make_reliability(),
        recent_feedbacks=recent,
    )
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert result["reliability_score"] <= 0.65
    assert repo.audit_logs


@pytest.mark.asyncio
async def test_duplicate_update_is_idempotent():
    repo = FakeRepo(
        feedback=make_feedback(),
        verification=make_verification("support"),
        reliability=make_reliability(),
        audit=FeedbackAuditLog(id=1, feedback_id=1, action="user_reliability_updated", before_state="{}", after_state="{}", reason="{}", actor="system", created_at=datetime.now()),
    )
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert result["applied"] is False
    assert len(repo.saved) == 0


@pytest.mark.asyncio
async def test_reliability_stays_within_bounds():
    reliability = make_reliability(0.99)
    repo = FakeRepo(feedback=make_feedback(), verification=make_verification("support"), reliability=reliability)
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert 0.0 <= result["reliability_score"] <= 1.0


@pytest.mark.asyncio
async def test_bayesian_smoothing_keeps_new_user_moderate():
    repo = FakeRepo(feedback=make_feedback(), verification=make_verification("support"), reliability=None)
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert result["reliability_score"] < 0.8


@pytest.mark.asyncio
async def test_single_feedback_does_not_move_score_too_much():
    reliability = make_reliability(0.5)
    repo = FakeRepo(feedback=make_feedback(), verification=make_verification("support"), reliability=reliability)
    service = UserReliabilityService(repo)

    result = await service.update_reliability(1)

    assert abs(result["reliability_score"] - 0.5) <= 0.18
