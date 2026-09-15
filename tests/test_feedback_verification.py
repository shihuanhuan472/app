import json
from types import SimpleNamespace

import pytest

from feedback_learning.service import FeedbackAnalysisService
from feedback_learning.schemas import FeedbackAnalysis
from models import FeedbackRecord


class FakeDB:
    async def rollback(self):
        return None


class FakeRepo:
    def __init__(self, feedback=None, trace=None, context=None):
        self.db = FakeDB()
        self.feedback = feedback
        self.trace = trace
        self.context = context
        self.claims = []
        self.verifications = []
        self.audit_logs = []

    async def get_feedback_record(self, feedback_id):
        return self.feedback if self.feedback and self.feedback.id == feedback_id else None

    async def get_latest_trace_for_message(self, message_id, conversation_id):
        if self.trace and self.trace.ai_message_id == message_id and self.trace.session_id == conversation_id:
            return self.trace
        return None

    async def get_trace_by_id(self, trace_id):
        if self.trace and self.trace.id == trace_id:
            return self.trace
        return None

    async def get_active_context(self, conversation_id):
        if self.context and self.context.session_id == conversation_id:
            return self.context
        return None

    async def create_claim(self, claim):
        claim.id = len(self.claims) + 1
        self.claims.append(claim)
        return claim

    async def create_verification(self, verification):
        verification.id = len(self.verifications) + 1
        self.verifications.append(verification)
        return verification

    async def create_audit_log(self, audit_log):
        audit_log.id = len(self.audit_logs) + 1
        self.audit_logs.append(audit_log)
        return audit_log


def make_trace():
    return SimpleNamespace(
        id=88,
        session_id=10,
        ai_message_id=20,
        route="knowledge_search",
        reason="semantic_match",
        original_question="风扇故障怎么查",
        query_rewrite="风扇故障怎么查",
        retrieval_query="fan alarm",
        answer_preview="检查风扇电源模块",
        reference_docs_json=json.dumps(
            [
                {"doc_id": 101, "title": "风扇手册"},
                {"doc_id": 102, "title": "电源模块手册"},
            ],
            ensure_ascii=False,
        ),
        validation_json=json.dumps(
            {
                "memory_strategy": "rag_once",
                "memory_complexity": "standard",
                "memory_actions": ["load_active_context"],
                "has_active_context": True,
                "has_summary": True,
                "context_action": "continue",
                "context_score": 0.82,
                "last_focus": "风扇故障",
                "recent_message_count": 4,
                "recent_trace_count": 2,
            },
            ensure_ascii=False,
        ),
    )


def make_context():
    return SimpleNamespace(
        session_id=10,
        active_issue="风扇故障",
        active_device="Cooling fan",
        active_component="Power module",
        active_symptom="fan stop",
        active_error_code="E201",
        active_query="fan alarm",
        active_route="knowledge_search",
        active_reason="semantic_match",
        summary_text="Short summary",
    )


def make_feedback(comment, feedback_type="negative"):
    return FeedbackRecord(
        id=1,
        conversation_id=10,
        message_id=20,
        user_id=7,
        feedback_type=feedback_type,
        rating=1,
        comment=comment,
        created_at=None,
        query_snapshot=json.dumps({"memory_pack": {"strategy": "rag_once"}}, ensure_ascii=False),
        answer_snapshot=json.dumps({"answer_text": "检查风扇电源模块", "answer_preview": "检查风扇电源模块"}, ensure_ascii=False),
        retrieved_documents=json.dumps([{"doc_id": 101}, {"doc_id": 102}], ensure_ascii=False),
        cited_documents=json.dumps([{"doc_id": 101}], ensure_ascii=False),
        trace_id=88,
        status="pending",
    )


class CurrentUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeLLMClient:
    def __init__(self, content):
        self.content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


@pytest.fixture(autouse=True)
def disable_feedback_llm(monkeypatch):
    monkeypatch.setenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "false")


@pytest.mark.asyncio
async def test_thumbs_up_becomes_confirmation_without_claim_pollution(monkeypatch):
    monkeypatch.setenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "false")
    repo = FakeRepo(feedback=make_feedback("", "positive"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type == "confirmation"
    assert analysis.recommended_action == "store_only"
    assert analysis.claims == []


@pytest.mark.asyncio
async def test_thumbs_down_without_text_stays_dissatisfaction(monkeypatch):
    monkeypatch.setenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "false")
    repo = FakeRepo(feedback=make_feedback(""), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type == "dissatisfaction"
    assert analysis.scope in {"conversation", "feedback"}


@pytest.mark.asyncio
async def test_correction_claim_is_extracted():
    repo = FakeRepo(feedback=make_feedback("不是风扇，是电源模块", "correction"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type == "correction"
    assert analysis.claims
    assert analysis.claims[0].claim_type == "correction"
    assert analysis.claims[0].source == "user"
    assert analysis.risk_level == "high"


@pytest.mark.asyncio
async def test_confirmation_claim_is_extracted():
    repo = FakeRepo(feedback=make_feedback("我已经确认是电源模块", "confirmation"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type == "confirmation"
    assert analysis.claims[0].verification_status == "unverified"
    assert analysis.claims[0].claim_type == "confirmation"


@pytest.mark.asyncio
async def test_dissatisfaction_without_resolution_is_not_knowledge():
    repo = FakeRepo(feedback=make_feedback("这个答案没有解决问题", "negative"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type == "dissatisfaction"
    assert analysis.recommended_action in {"ignore", "store_only"}
    assert analysis.claims == [] or analysis.claims[0].verification_status == "unverified"


@pytest.mark.asyncio
async def test_document_preference_is_captured():
    repo = FakeRepo(feedback=make_feedback("参考文档 3 是正确的", "confirmation"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.document_preference == "3"
    assert any(claim.claim_type == "document_preference" for claim in analysis.claims)


@pytest.mark.asyncio
async def test_meaningless_text_defaults_to_unclear_or_dissatisfaction():
    repo = FakeRepo(feedback=make_feedback("哈哈哈", "negative"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.feedback_type in {"dissatisfaction", "unclear"}


@pytest.mark.asyncio
async def test_conflict_with_existing_evidence_is_flagged():
    repo = FakeRepo(feedback=make_feedback("不是风扇，是电源模块", "correction"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7))

    assert analysis.conflict is True
    assert analysis.recommended_action in {"verify", "require_human_review"}


@pytest.mark.asyncio
async def test_invalid_json_falls_back_to_heuristic(monkeypatch):
    monkeypatch.setenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "true")
    repo = FakeRepo(feedback=make_feedback("不是风扇，是电源模块", "correction"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)
    llm = FakeLLMClient("not-json")

    analysis = await service.analyze_feedback(repo.feedback, CurrentUser(7), llm_client=llm)

    assert isinstance(analysis, FeedbackAnalysis)
    assert analysis.feedback_type == "correction"


@pytest.mark.asyncio
async def test_verify_persists_claim_verification_and_audit(monkeypatch):
    monkeypatch.setenv("FEEDBACK_VERIFICATION_LLM_ENABLED", "false")
    repo = FakeRepo(feedback=make_feedback("不是风扇，是电源模块", "correction"), trace=make_trace(), context=make_context())
    service = FeedbackAnalysisService(repo)

    verification = await service.verify_and_persist(1, CurrentUser(7))

    assert verification.verification_result in {"support", "contradict", "uncertain", "insufficient"}
    assert len(repo.claims) >= 1
    assert len(repo.verifications) == 1
    assert len(repo.audit_logs) == 1
