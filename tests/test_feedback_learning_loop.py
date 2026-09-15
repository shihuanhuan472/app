from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from feedback_learning.learning_loop import (
    FeedbackLearningEvaluationService,
    FeedbackLearningJob,
    KnowledgePromotionQueue,
    PromotionPolicy,
)
from models import FeedbackAuditLog, FeedbackRecord, FeedbackVerification, FaultCaseMemory, UserReliability


class FakeLoopRepo:
    def __init__(self, feedback=None, case=None, verification=None, reliability=None):
        self.feedback = feedback
        self.case = case
        self.verification = verification
        self.reliability = reliability or UserReliability(user_id=7, reliability_score=0.8, verified_feedback=3)
        self.audit_logs = []
        self.saved_feedbacks = []
        self.db = SimpleNamespace(rollback=lambda: None)

    async def get_pending_feedbacks(self, limit=20):
        return [self.feedback] if self.feedback and self.feedback.status in {"pending", "retry"} else []

    async def get_feedback_by_id(self, feedback_id):
        return self.feedback if self.feedback and self.feedback.id == feedback_id else None

    async def get_feedback_record(self, feedback_id):
        return await self.get_feedback_by_id(feedback_id)

    async def get_latest_feedback_verification(self, feedback_id):
        return self.verification

    async def create_verification(self, verification):
        verification.id = 55
        self.verification = verification
        return verification

    async def create_claim(self, claim):
        claim.id = 100
        return claim

    async def get_claims_for_feedback_ids(self, feedback_ids):
        return []

    async def get_feedback_audit_log(self, feedback_id, action):
        return next((log for log in self.audit_logs if log.feedback_id == feedback_id and log.action == action), None)

    async def create_audit_log(self, audit_log):
        audit_log.id = len(self.audit_logs) + 1
        self.audit_logs.append(audit_log)
        return audit_log

    async def save_feedback(self, feedback):
        self.saved_feedbacks.append(feedback.status)
        return feedback

    async def get_or_create_user_reliability(self, user_id):
        return self.reliability

    async def get_user_reliability(self, user_id):
        return self.reliability

    async def save_user_reliability(self, row):
        return row

    async def get_recent_feedbacks_for_user(self, user_id, limit=20):
        return []

    async def get_fault_case_by_key(self, case_key):
        return self.case

    async def create_fault_case(self, case):
        case.id = 77
        self.case = case
        return case

    async def save_fault_case(self, case):
        self.case = case
        return case

    async def get_verified_fault_cases(self, **kwargs):
        return []


def make_feedback(status="pending"):
    return FeedbackRecord(
        id=1,
        conversation_id=10,
        message_id=20,
        user_id=7,
        feedback_type="negative",
        comment="这个文档与我的问题无关",
        status=status,
        created_at=datetime.now() - timedelta(seconds=30),
        retrieved_documents='[{"doc_id": 101, "score": 0.5}]',
        query_snapshot='{"retrieval_query": "fan alarm"}',
    )


def make_verification(result="support"):
    return FeedbackVerification(
        id=55,
        feedback_id=1,
        verifier_type="human",
        verification_result=result,
        confidence=0.9,
        created_at=datetime.now(),
    )


def make_case(status="candidate", confidence=0.8, evidence='["doc:1"]'):
    return FaultCaseMemory(
        id=77,
        case_key="case77",
        evidence_ids=evidence,
        source_feedback_ids='["1"]',
        confidence=confidence,
        verification_status=status,
    )


@pytest.mark.asyncio
async def test_feedback_learning_job_is_idempotent_for_completed_feedback():
    repo = FakeLoopRepo(feedback=make_feedback(status="completed"))

    result = await FeedbackLearningJob(repo).process_feedback(1)

    assert result["idempotent"] is True
    assert repo.audit_logs == []


@pytest.mark.asyncio
async def test_feedback_learning_job_runs_state_machine_with_existing_verification():
    repo = FakeLoopRepo(feedback=make_feedback(), verification=make_verification())

    result = await FeedbackLearningJob(repo).process_feedback(1)

    assert result["status"] == "completed"
    assert repo.feedback.status == "completed"
    actions = {log.action for log in repo.audit_logs}
    assert "analysis_started" in actions
    assert "user_reliability_updated" in actions or "user_reliability_skipped" in actions
    assert "feedback_learning_completed" in actions


def test_promotion_policy_candidate_to_verified():
    candidate = PromotionPolicy().evaluate(make_case(status="candidate", confidence=0.8))

    assert candidate.recommended_action == "promote_to_verified"


def test_promotion_policy_verified_to_expert_requires_outcome_or_review():
    policy = PromotionPolicy()

    with_outcome = policy.evaluate(make_case(status="verified", confidence=0.8), maintenance_outcome={"outcome": "resolved"})
    without_outcome = policy.evaluate(make_case(status="verified", confidence=0.8))

    assert with_outcome.recommended_action == "promote_to_expert_verified"
    assert without_outcome.recommended_action == "hold"


def test_global_knowledge_promotion_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_KNOWLEDGE_PROMOTION", "false")

    candidate = PromotionPolicy().evaluate(make_case(status="expert_verified", confidence=1.0, evidence='["a","b","c"]'))

    assert candidate.recommended_action != "require_human_review_for_global_promotion"


@pytest.mark.asyncio
async def test_promotion_queue_writes_audit():
    repo = FakeLoopRepo(feedback=make_feedback(), case=make_case())

    candidate = await KnowledgePromotionQueue(repo).evaluate_case(repo.case, repo.feedback)

    assert candidate.case_id == 77
    assert any(log.action == "promotion_candidate_evaluated" for log in repo.audit_logs)


@pytest.mark.asyncio
async def test_correction_lag_and_post_feedback_performance():
    repo = FakeLoopRepo(feedback=make_feedback(), verification=make_verification())
    repo.audit_logs.append(
        FeedbackAuditLog(
            id=1,
            feedback_id=1,
            action="retrieval_patch_created",
            created_at=datetime.now(),
        )
    )
    repo.audit_logs.append(
        FeedbackAuditLog(
            id=2,
            feedback_id=1,
            action="user_reliability_updated",
            created_at=datetime.now(),
        )
    )

    service = FeedbackLearningEvaluationService(repo)
    lag = await service.correction_lag(1)
    performance = await service.post_feedback_performance(1)

    assert lag["applied"] is True
    assert lag["correction_lag_seconds"] is not None
    assert performance["before"]["target_document_rank"] == 1
    assert performance["after"]["retrieval_hit"] is True
