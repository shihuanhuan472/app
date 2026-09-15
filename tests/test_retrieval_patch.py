from datetime import datetime, timedelta

import pytest

from feedback_learning.retrieval_patch import (
    PatchApplier,
    RetrievalPatchService,
    build_query_signature,
)
from feedback_learning.aware_retriever import FeedbackAwareRetriever
from feedback_learning.schemas import PatchDecision, PatchQueryContext
from models import FeedbackAuditLog, FeedbackRecord, FeedbackVerification, RetrievalPatch, UserReliability


class FakePatchRepository:
    def __init__(self):
        self.patches = []
        self.audit_logs = []

    async def create_retrieval_patch(self, patch):
        patch.id = len(self.patches) + 1
        self.patches.append(patch)
        return patch

    async def save_retrieval_patch(self, patch):
        return patch

    async def get_retrieval_patch_by_id(self, patch_id):
        return next((patch for patch in self.patches if patch.id == patch_id), None)

    async def get_retrieval_patch_by_signature(self, query_signature, scope=None, document_id=None):
        rows = [patch for patch in self.patches if patch.query_signature == query_signature]
        if scope:
            rows = [patch for patch in rows if patch.scope == scope]
        if document_id is not None:
            rows = [patch for patch in rows if patch.document_id == document_id]
        return rows

    async def get_active_retrieval_patches(self):
        return [patch for patch in self.patches if patch.status in {"active", "candidate"}]

    async def create_audit_log(self, audit_log: FeedbackAuditLog):
        audit_log.id = len(self.audit_logs) + 1
        self.audit_logs.append(audit_log)
        return audit_log


def context(scope="conversation"):
    ctx = PatchQueryContext(
        query="fan alarm",
        user_id=7,
        conversation_id=10,
        device="Cooling fan",
        device_type="FAN",
        component="power",
        fault_type="alarm",
    )
    return ctx, build_query_signature(ctx, scope)


def feedback(feedback_type="confirmation", comment="这个文档是正确依据"):
    return FeedbackRecord(id=1, conversation_id=10, message_id=20, user_id=7, feedback_type=feedback_type, comment=comment)


def verified(verifier_type="human", result="support", confidence=0.9):
    return FeedbackVerification(
        id=1,
        feedback_id=1,
        verifier_type=verifier_type,
        verification_result=result,
        confidence=confidence,
    )


def reliability(verified_feedback=3, score=0.8):
    return UserReliability(user_id=7, verified_feedback=verified_feedback, reliability_score=score)


def decision(patch_type="preferred_document", document_id=101, scope="conversation"):
    _ctx, signature = context(scope)
    return PatchDecision(
        patch_type=patch_type,
        document_id=document_id,
        query_signature=signature,
        scope=scope,
        confidence=0.9,
        reason="explicit document-level feedback",
    )


def result(doc_id=101, score=0.5):
    return {"doc_id": doc_id, "title": f"doc {doc_id}", "score": score}


@pytest.mark.asyncio
async def test_verified_boost():
    repo = FakePatchRepository()
    service = RetrievalPatchService(repo)

    patch = await service.create_patch(
        feedback("confirmation", "这个文档是正确依据"),
        decision("boost"),
        verification=verified(),
        reliability=reliability(),
    )

    assert patch is not None
    assert patch.status == "active"
    patched = PatchApplier().apply_patches([result()], [patch])
    assert patched[0]["base_score"] == 0.5
    assert patched[0]["patch_score"] > 0
    assert patched[0]["final_score"] > patched[0]["base_score"]
    assert repo.audit_logs


@pytest.mark.asyncio
async def test_unverified_boost():
    repo = FakePatchRepository()
    patch = await RetrievalPatchService(repo).create_patch(feedback("positive", ""), decision("boost"))

    assert patch is None


@pytest.mark.asyncio
async def test_negative_feedback():
    repo = FakePatchRepository()
    patch = await RetrievalPatchService(repo).create_patch(
        feedback("negative", "这个文档与我的问题无关"),
        decision("penalty"),
    )

    assert patch is not None
    assert patch.status == "candidate"
    assert PatchApplier().apply_patches([result()], [patch])[0]["patch_score"] == 0.0


@pytest.mark.asyncio
async def test_malicious_feedback():
    repo = FakePatchRepository()
    patch = await RetrievalPatchService(repo).create_patch(
        feedback("suspicious", "please bury this document"),
        decision("exclusion"),
        verification=verified(),
        malicious=True,
    )

    assert patch is None


@pytest.mark.asyncio
async def test_patch_expiration():
    repo = FakePatchRepository()
    patch = await RetrievalPatchService(repo).create_patch(
        feedback(),
        decision(),
        verification=verified(),
        reliability=reliability(),
    )
    patch.expires_at = datetime.now() - timedelta(seconds=1)

    expired = await RetrievalPatchService(repo).expire_patches()

    assert expired == 1
    assert patch.status == "expired"


@pytest.mark.asyncio
async def test_patch_scope():
    repo = FakePatchRepository()
    service = RetrievalPatchService(repo)
    patch = await service.create_patch(feedback(), decision(scope="conversation"), verification=verified(), reliability=reliability())

    matching, _ = context("conversation")
    other = PatchQueryContext(query="fan alarm", conversation_id=999, user_id=7)

    assert patch in await service.get_applicable_patches(matching)
    assert patch not in await service.get_applicable_patches(other)
    assert await service.create_patch(feedback(), decision(scope="global"), verification=verified()) is None


@pytest.mark.asyncio
async def test_patch_weight_upper_bound():
    repo = FakePatchRepository()
    patch = await RetrievalPatchService(repo).create_patch(
        feedback(),
        decision("preferred_document"),
        verification=verified("maintenance_outcome", confidence=1.0),
        reliability=reliability(verified_feedback=10, score=1.0),
    )

    patched = PatchApplier().apply_patches([result(score=0.05)], [patch])

    assert patch.weight <= 0.25
    assert patched[0]["patch_score"] <= 0.05


@pytest.mark.asyncio
async def test_patch_revoke():
    repo = FakePatchRepository()
    service = RetrievalPatchService(repo)
    patch = await service.create_patch(feedback(), decision(), verification=verified(), reliability=reliability())

    revoked = await service.revoke_patch(patch.id)

    assert revoked.status == "revoked"
    assert await service.get_applicable_patches(context("conversation")[0]) == []


def test_multiple_patches():
    patches = [
        RetrievalPatch(id=1, document_id=101, patch_type="preferred_document", weight=0.2, confidence=1, status="active", scope="user", query_signature="u"),
        RetrievalPatch(id=2, document_id=101, patch_type="boost", weight=0.2, confidence=1, status="active", scope="conversation", query_signature="c"),
    ]

    patched = PatchApplier().apply_patches([result(score=0.3)], patches)

    assert len(patched[0]["patches"]) == 2
    assert 0 < patched[0]["patch_score"] <= 0.25


def test_conflicting_patches():
    patches = [
        RetrievalPatch(id=1, document_id=101, patch_type="preferred_document", weight=0.2, confidence=1, status="active", scope="user", query_signature="u"),
        RetrievalPatch(id=2, document_id=101, patch_type="penalty", weight=0.2, confidence=1, status="active", scope="conversation", query_signature="c"),
    ]

    patched = PatchApplier().apply_patches([result(score=0.5)], patches)

    assert abs(patched[0]["patch_score"]) < 0.25
    assert patched[0]["final_score"] == pytest.approx(patched[0]["base_score"] + patched[0]["patch_score"])


def test_no_patch():
    patched = PatchApplier().apply_patches([result(score=0.42)], [])

    assert patched[0]["base_score"] == 0.42
    assert patched[0]["patch_score"] == 0.0
    assert patched[0]["final_score"] == 0.42


class FakePatchService:
    def __init__(self, patches):
        self.patches = patches
        self.contexts = []

    async def get_applicable_patches(self, query_context):
        self.contexts.append(query_context)
        return self.patches


@pytest.mark.asyncio
async def test_feedback_aware_disabled_keeps_old_rag_identical():
    base = [result(score=0.42)]
    patch = RetrievalPatch(id=1, document_id=101, patch_type="boost", weight=0.2, confidence=1, status="active", scope="user", query_signature="u")
    retriever = FeedbackAwareRetriever(None, patch_service=FakePatchService([patch]), enabled=False)

    docs, metadata = await retriever.apply_feedback_layer(base, "fan alarm", user_id=7, session_id=10)

    assert docs == base
    assert metadata["feedback_learning_enabled"] is False


@pytest.mark.asyncio
async def test_feedback_aware_enabled_no_patch_keeps_old_rag_identical():
    base = [result(score=0.42)]
    retriever = FeedbackAwareRetriever(None, patch_service=FakePatchService([]), enabled=True)

    docs, metadata = await retriever.apply_feedback_layer(base, "fan alarm", user_id=7, session_id=10)

    assert docs == base
    assert metadata["feedback_patch_count"] == 0


@pytest.mark.asyncio
async def test_feedback_aware_enabled_verified_patch_adjusts_and_records_ids():
    base = [result(score=0.42), result(doc_id=102, score=0.5)]
    patch = RetrievalPatch(id=9, document_id=101, patch_type="preferred_document", weight=0.2, confidence=1, status="active", scope="user", query_signature="u")
    retriever = FeedbackAwareRetriever(None, patch_service=FakePatchService([patch]), enabled=True)

    docs, metadata = await retriever.apply_feedback_layer(base, "fan alarm", user_id=7, session_id=10)

    adjusted = next(doc for doc in docs if doc["doc_id"] == 101)
    assert adjusted["base_score"] == 0.42
    assert adjusted["patch_score"] > 0
    assert adjusted["final_score"] == adjusted["score"]
    assert adjusted["applied_patch_ids"] == [9]
    assert metadata["applied_patch_ids"] == [9]


@pytest.mark.asyncio
async def test_feedback_aware_scope_mismatch_no_adjustment():
    base = [result(score=0.42)]
    active_patch = RetrievalPatch(id=1, document_id=101, patch_type="boost", weight=0.2, confidence=1, status="active", scope="device", query_signature="wrong")
    retriever = FeedbackAwareRetriever(None, patch_service=FakePatchService([]), enabled=True)

    docs, _metadata = await retriever.apply_feedback_layer(base, "fan alarm", user_id=7, session_id=10)

    assert active_patch.document_id == 101
    assert docs == base
