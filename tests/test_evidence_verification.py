import json
from types import SimpleNamespace

import pytest

from feedback_learning.repository import FeedbackLearningRepository
from feedback_learning.service import EvidenceVerifier, FeedbackEvidenceVerificationService
from feedback_learning.schemas import FeedbackClaimPayload
from models import FeedbackClaim, FeedbackRecord
from utils.app_exceptions import AppException


class FakeDB:
    def __init__(self, claim=None):
        self.claim = claim
        self.created = []

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.claim)

    def add(self, obj):
        self.created.append(obj)

    async def flush(self):
        return None

    async def refresh(self, obj):
        return None


class FakeRepo:
    def __init__(self, feedback=None, trace=None, context=None, claim=None, cases=None):
        self.db = FakeDB(claim=claim)
        self.feedback = feedback
        self.trace = trace
        self.context = context
        self.cases = cases or []
        self.evidences = []
        self.verifications = []
        self.audit_logs = []

    async def get_feedback_record(self, feedback_id):
        return self.feedback if self.feedback and self.feedback.id == feedback_id else None

    async def get_trace_by_id(self, trace_id):
        if self.trace and self.trace.id == trace_id:
            return self.trace
        return None

    async def get_active_context(self, conversation_id):
        if self.context and self.context.session_id == conversation_id:
            return self.context
        return None

    async def get_verified_fault_cases(self, device=None, component=None, alarm_code=None, keywords=None, limit=5):
        return list(self.cases)

    async def create_evidence(self, evidence):
        evidence.id = len(self.evidences) + 1
        self.evidences.append(evidence)
        return evidence

    async def create_verification(self, verification):
        verification.id = len(self.verifications) + 1
        self.verifications.append(verification)
        return verification

    async def create_audit_log(self, audit_log):
        audit_log.id = len(self.audit_logs) + 1
        self.audit_logs.append(audit_log)
        return audit_log


def make_feedback(query_snapshot, answer_snapshot, retrieved_documents, cited_documents, trace_id=88):
    return FeedbackRecord(
        id=1,
        conversation_id=10,
        message_id=20,
        user_id=7,
        feedback_type="correction",
        rating=1,
        comment="不是风扇，是电源模块",
        created_at=None,
        query_snapshot=json.dumps(query_snapshot, ensure_ascii=False),
        answer_snapshot=json.dumps(answer_snapshot, ensure_ascii=False),
        retrieved_documents=json.dumps(retrieved_documents, ensure_ascii=False),
        cited_documents=json.dumps(cited_documents, ensure_ascii=False),
        trace_id=trace_id,
        status="pending",
    )


def make_case():
    return SimpleNamespace(
        id=501,
        device="Cooling fan",
        device_type="fan",
        component="Power module",
        firmware_version="1.0",
        symptoms="fan rpm low",
        metrics="rpm=0",
        alarm_codes="A102",
        confirmed_fault="fan failure",
        root_cause="fan failure",
        actions="replace fan",
        outcome="recovered",
        verification_status="verified",
        updated_at=None,
    )


def make_context():
    return SimpleNamespace(
        session_id=10,
        active_issue="fan rpm low",
        active_device="Cooling fan",
        active_component="Power module",
        active_symptom="fan rpm low",
        active_error_code="A102",
        summary_text="confirmed fan rpm low",
    )


@pytest.fixture(autouse=True)
def disable_evidence_llm(monkeypatch):
    monkeypatch.setenv("FEEDBACK_EVIDENCE_LLM_ENABLED", "false")


@pytest.mark.asyncio
async def test_claim_has_direct_document_support():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal", "content": "A102 -> Fan RPM abnormal"}],
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal"}],
    )
    repo = FakeRepo(feedback=feedback, context=make_context())
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 fan rpm abnormal", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result == "supported"
    assert result.supporting_evidence
    assert result.supporting_evidence[0].source_id.startswith("document:")


@pytest.mark.asyncio
async def test_claim_conflicts_with_document():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal", "content": "A102 -> Fan RPM abnormal"}],
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal"}],
    )
    repo = FakeRepo(feedback=feedback, context=make_context())
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 power failure", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result in {"contradicted", "uncertain"}
    assert result.contradicting_evidence or result.supporting_evidence


@pytest.mark.asyncio
async def test_claim_without_evidence_is_insufficient():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [],
        [],
    )
    repo = FakeRepo(feedback=feedback, context=make_context())
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="root_cause", predicate="is", object="unknown issue", scope="conversation", source="user", confidence=0.3)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result == "insufficient"
    assert result.evidence_count == 0


@pytest.mark.asyncio
async def test_multiple_evidence_consistent():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal", "content": "A102 -> Fan RPM abnormal"}],
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal"}],
    )
    repo = FakeRepo(feedback=feedback, context=make_context(), cases=[make_case()])
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 fan rpm abnormal", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result == "supported"
    assert result.evidence_count >= 2


@pytest.mark.asyncio
async def test_multiple_evidence_conflict_returns_uncertain():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal", "content": "A102 -> Fan RPM abnormal"}],
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal"}],
    )
    repo = FakeRepo(feedback=feedback, context=make_context(), cases=[make_case()])
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 power module fault", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result in {"contradicted", "uncertain"}
    assert result.supporting_evidence or result.contradicting_evidence


@pytest.mark.asyncio
async def test_history_case_supports_claim():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [],
        [],
    )
    repo = FakeRepo(feedback=feedback, context=make_context(), cases=[make_case()])
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 fan failure", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert any(item.source_type == "fault_case" for item in result.supporting_evidence)


@pytest.mark.asyncio
async def test_working_memory_conflict_is_detected():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once", "confirmed_facts": ["A102 表示风扇 RPM 异常"]}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [],
        [],
    )
    repo = FakeRepo(feedback=feedback, context=make_context())
    verifier = EvidenceVerifier(repo)
    claim = FeedbackClaimPayload(claim_type="correction", subject="alarm_code", predicate="is", object="A102 power module fault", scope="conversation", source="user", confidence=0.8)

    result = await verifier.verify_claim(feedback, claim)

    assert result.result in {"contradicted", "uncertain"}


@pytest.mark.asyncio
async def test_missing_claim_raises_clear_error():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [],
        [],
    )
    repo = FakeRepo(feedback=feedback, context=make_context(), claim=None)
    service = FeedbackEvidenceVerificationService(repo)

    with pytest.raises(AppException) as exc:
        await service.verify_claim(1, 999, SimpleNamespace(id=7))

    assert exc.value.message == "声明不存在"


@pytest.mark.asyncio
async def test_persisted_evidence_verification_writes_evidence_and_audit():
    feedback = make_feedback(
        {"memory_pack": {"strategy": "rag_once"}},
        {"answer_text": "A102 表示风扇 RPM 异常"},
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal", "content": "A102 -> Fan RPM abnormal"}],
        [{"doc_id": 101, "title": "A102 Fan RPM abnormal"}],
    )
    claim_row = FeedbackClaim(
        id=11,
        feedback_id=1,
        claim_type="correction",
        subject="alarm_code",
        predicate="is",
        object="A102 fan rpm abnormal",
        scope="conversation",
        source="user",
        confidence=0.8,
        verification_status="unverified",
        evidence_ids="[]",
    )
    repo = FakeRepo(feedback=feedback, context=make_context(), claim=claim_row, cases=[make_case()])
    service = FeedbackEvidenceVerificationService(repo)

    result = await service.verify_claim(1, 11, SimpleNamespace(id=7))

    assert result.result in {"supported", "uncertain", "contradicted"}
    assert repo.evidences
    assert repo.verifications
    assert repo.audit_logs
