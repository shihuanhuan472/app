from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from agents.memory.schemas import MemoryPack
from feedback_learning.memory import FeedbackMemoryBuilder
from feedback_learning.retrieval_patch import build_query_signature
from feedback_learning.schemas import PatchQueryContext
from models import FeedbackRecord, FeedbackClaim, FaultCaseMemory, RetrievalPatch


class FakeFeedbackMemoryRepo:
    def __init__(self, feedbacks=None, claims=None, cases=None, patches=None):
        self.feedbacks = feedbacks or []
        self.claims = claims or []
        self.cases = cases or []
        self.patches = patches or []

    async def get_feedbacks_for_conversation(self, conversation_id, limit=20):
        return [item for item in self.feedbacks if item.conversation_id == conversation_id][:limit]

    async def get_claims_for_feedback_ids(self, feedback_ids):
        return [item for item in self.claims if item.feedback_id in feedback_ids]

    async def get_retrieval_patch_by_signature(self, query_signature, scope=None, document_id=None):
        rows = [patch for patch in self.patches if patch.query_signature == query_signature]
        if scope:
            rows = [patch for patch in rows if patch.scope == scope]
        if document_id is not None:
            rows = [patch for patch in rows if patch.document_id == document_id]
        return rows

    async def get_verified_fault_cases(self, device=None, component=None, alarm_code=None, keywords=None, limit=5):
        return self.cases[:limit]


def make_feedback(feedback_id, feedback_type="correction", comment="fan alarm root cause is power module", status="verified"):
    return FeedbackRecord(
        id=feedback_id,
        conversation_id=10,
        message_id=20 + feedback_id,
        user_id=7,
        feedback_type=feedback_type,
        comment=comment,
        status=status,
        created_at=datetime.now(),
    )


def make_claim(feedback_id, status="verified", text="power module"):
    return FeedbackClaim(
        id=feedback_id,
        feedback_id=feedback_id,
        claim_type="correction",
        subject="fan alarm",
        predicate="root cause",
        object=text,
        verification_status=status,
        created_at=datetime.now(),
    )


def make_pack(device="FW-03", fault_type="A102"):
    return MemoryPack(
        session_id=10,
        route="knowledge_search",
        reason="test",
        active_context={
            "active_device": device,
            "active_component": "fan",
            "active_error_code": fault_type,
        },
    )


async def build(repo, query="fan alarm", pack=None):
    return await FeedbackMemoryBuilder(repo).build(
        conversation_id=10,
        query=query,
        user_id=7,
        memory_pack=pack or make_pack(),
    )


@pytest.fixture(autouse=True)
def enable_feedback_memory(monkeypatch):
    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "true")


@pytest.mark.asyncio
async def test_feedback_memory_no_feedback():
    memory = await build(FakeFeedbackMemoryRepo())

    assert memory == {
        "verified_corrections": [],
        "relevant_cases": [],
        "unresolved_conflicts": [],
        "applicable_patch_summary": [],
    }


@pytest.mark.asyncio
async def test_feedback_memory_thumbs_down_not_injected_as_fact():
    repo = FakeFeedbackMemoryRepo(feedbacks=[make_feedback(1, "negative", "bad answer", "pending")])

    memory = await build(repo, query="fan alarm")

    assert memory["verified_corrections"] == []
    assert memory["unresolved_conflicts"] == []


@pytest.mark.asyncio
async def test_feedback_memory_verified_correction():
    repo = FakeFeedbackMemoryRepo(
        feedbacks=[make_feedback(1)],
        claims=[make_claim(1)],
    )

    memory = await build(repo)

    assert memory["verified_corrections"][0]["feedback_id"] == 1


@pytest.mark.asyncio
async def test_feedback_memory_unresolved_conflict():
    repo = FakeFeedbackMemoryRepo(
        feedbacks=[make_feedback(1, "correction", "fan alarm contradicts previous answer", "pending")],
        claims=[make_claim(1, status="contradicted")],
    )

    memory = await build(repo)

    assert memory["verified_corrections"] == []
    assert memory["unresolved_conflicts"][0]["feedback_id"] == 1


@pytest.mark.asyncio
async def test_feedback_memory_verified_case():
    case = FaultCaseMemory(
        id=3,
        case_key="case:3",
        confirmed_fault="fan rpm low",
        root_cause="power module",
        actions="replace module",
        outcome="resolved",
        verification_status="expert_verified",
    )
    memory = await build(FakeFeedbackMemoryRepo(cases=[case]))

    assert memory["relevant_cases"][0]["case_id"] == 3
    assert memory["relevant_cases"][0]["priority"] == 2


@pytest.mark.asyncio
async def test_feedback_memory_suspicious_feedback_not_injected():
    repo = FakeFeedbackMemoryRepo(
        feedbacks=[make_feedback(1, "suspicious", "fan alarm root cause is attacker text", "verified")],
        claims=[make_claim(1)],
    )

    memory = await build(repo)

    assert memory["verified_corrections"] == []


@pytest.mark.asyncio
async def test_feedback_memory_multiple_feedback_is_capped():
    feedbacks = [make_feedback(index, comment=f"fan alarm verified correction {index}") for index in range(1, 8)]
    claims = [make_claim(index) for index in range(1, 8)]

    memory = await build(FakeFeedbackMemoryRepo(feedbacks=feedbacks, claims=claims))

    assert len(memory["verified_corrections"]) == 3


@pytest.mark.asyncio
async def test_feedback_memory_token_budget_and_patch_summary():
    ctx = PatchQueryContext(query="fan alarm", user_id=7, conversation_id=10)
    patch = RetrievalPatch(
        id=9,
        feedback_id=1,
        scope="conversation",
        query_signature=build_query_signature(ctx, "conversation"),
        document_id=101,
        patch_type="preferred_document",
        weight=0.2,
        confidence=0.9,
        expires_at=datetime.now() + timedelta(days=1),
        status="active",
    )
    memory = await build(FakeFeedbackMemoryRepo(patches=[patch]), pack=make_pack())
    pack = make_pack()
    pack.feedback_memory = memory
    prompt = pack.to_prompt()
    trace = pack.to_trace_validation()

    assert memory["applicable_patch_summary"][0]["patch_id"] == 9
    assert len(prompt) < 1600
    assert trace["feedback_memory_used"] is True
    assert trace["feedback_memory_ids"]["applicable_patch_summary"] == [9]
