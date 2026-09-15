import json
from types import SimpleNamespace

import pytest

from feedback_learning.fault_case import CaseCandidateBuilder, CaseVerificationService, FaultCaseService
from feedback_learning.schemas import EvidenceItem, EvidenceVerificationResult, FeedbackClaimPayload
from models import FaultCaseMemory


class FakeDB:
    async def rollback(self):
        return None


class FakeRepo:
    def __init__(self, existing=None):
        self.db = FakeDB()
        self.existing = existing
        self.cases = []
        self.saved = []

    async def get_fault_case_by_key(self, case_key):
        if self.existing and self.existing.case_key == case_key:
            return self.existing
        return None

    async def get_fault_case_by_id(self, case_id):
        for case in self.cases + ([self.existing] if self.existing else []):
            if case and case.id == case_id:
                return case
        return None

    async def create_fault_case(self, case):
        case.id = len(self.cases) + 1
        self.cases.append(case)
        return case

    async def save_fault_case(self, case):
        self.saved.append(case)
        if case not in self.cases and case is not self.existing:
            self.cases.append(case)
        return case

    async def get_fault_cases_by_scope(self, **kwargs):
        return list(self.cases)


def make_feedback(feedback_id=1, firmware="FW-03"):
    return SimpleNamespace(
        id=feedback_id,
        feedback_type="correction",
        query_snapshot=json.dumps(
            {
                "device_context": {
                    "active_device": "Analyzer-01",
                    "active_device_type": "analyzer",
                    "active_component": "fan",
                    "active_error_code": "A102",
                    "active_symptom": "fan rpm low",
                },
                "memory_pack": {"firmware_version": firmware},
            },
            ensure_ascii=False,
        ),
        answer_snapshot=json.dumps({"answer_text": "fan rpm abnormal"}, ensure_ascii=False),
        retrieved_documents=json.dumps([{"doc_id": 101, "title": "A102 fan"}], ensure_ascii=False),
    )


def make_claim_result(result="insufficient", confidence=0.45):
    claim = FeedbackClaimPayload(
        claim_type="correction",
        subject="root_cause",
        predicate="is",
        object="power module failure",
        scope="device",
        source="user",
        confidence=0.8,
        verification_status="unverified",
    )
    support = []
    if result == "supported":
        support = [EvidenceItem(source_type="document", source_id="document:101", content="power module", relevance_score=0.9, support_score=0.8)]
    return EvidenceVerificationResult(
        claim=claim,
        supporting_evidence=support,
        contradicting_evidence=[],
        evidence_count=len(support),
        support_score=0.8 if support else 0.0,
        contradiction_score=0.0,
        confidence=confidence,
        result=result,
    )


@pytest.mark.asyncio
async def test_correction_creates_candidate_case():
    repo = FakeRepo()
    service = FaultCaseService(repo)

    case = await service.create_candidate_case(make_feedback(), make_claim_result("insufficient"))

    assert case.verification_status == "candidate"
    assert case.device == "Analyzer-01"
    assert case.scope


@pytest.mark.asyncio
async def test_evidence_support_promotes_to_verified():
    repo = FakeRepo()
    service = FaultCaseService(repo)
    case = await service.create_candidate_case(make_feedback(), make_claim_result("supported", 0.82))
    repo.existing = case

    promoted = await service.verify_case(case.id)

    assert promoted.verification_status == "verified"


@pytest.mark.asyncio
async def test_maintenance_outcome_promotes_to_expert_verified():
    repo = FakeRepo()
    service = FaultCaseService(repo)
    case = await service.create_candidate_case(make_feedback(), make_claim_result("supported", 0.82))
    repo.existing = case
    verifier = CaseVerificationService(repo)

    promoted = await verifier.verify_case(case.id, maintenance_outcome={"outcome": "replaced power module and recovered"})

    assert promoted.verification_status == "expert_verified"
    assert promoted.outcome == "replaced power module and recovered"


@pytest.mark.asyncio
async def test_contradiction_creates_rejected_case_not_verified():
    repo = FakeRepo()
    service = FaultCaseService(repo)

    case = await service.create_candidate_case(make_feedback(), make_claim_result("contradicted"))

    assert case.verification_status == "rejected"
    assert case.verification_status not in {"verified", "expert_verified"}

@pytest.mark.asyncio
async def test_insufficient_evidence_stays_candidate():
    repo = FakeRepo()
    service = FaultCaseService(repo)

    case = await service.create_candidate_case(make_feedback(), make_claim_result("insufficient"))

    assert case.verification_status == "candidate"
    assert case.confidence < 0.6


@pytest.mark.asyncio
async def test_device_and_firmware_scope_are_preserved():
    repo = FakeRepo()
    service = FaultCaseService(repo)

    case = await service.create_candidate_case(make_feedback(firmware="FW-03"), make_claim_result("supported"))
    scope = json.loads(case.scope)

    assert scope["device"] == "Analyzer-01"
    assert scope["firmware_version"] == "FW-03"
    assert scope["fault_type"] == "A102"


@pytest.mark.asyncio
async def test_duplicate_case_merges_evidence_and_feedback_ids():
    repo = FakeRepo()
    service = FaultCaseService(repo)
    first = await service.create_candidate_case(make_feedback(1), make_claim_result("supported"))
    repo.existing = first

    second = await service.create_candidate_case(make_feedback(2), make_claim_result("supported"))

    assert second.id == first.id
    assert "1" in json.loads(second.source_feedback_ids)
    assert "2" in json.loads(second.source_feedback_ids)
    assert "document:101" in json.loads(second.evidence_ids)


@pytest.mark.asyncio
async def test_search_similar_cases_does_not_touch_rag():
    repo = FakeRepo()
    service = FaultCaseService(repo)
    await service.create_candidate_case(make_feedback(), make_claim_result("supported"))

    results = await service.search_similar_cases(device="Analyzer-01", firmware_version="FW-03")

    assert len(results) == 1
