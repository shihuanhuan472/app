import json
from types import SimpleNamespace

import pytest

from feedback_learning.service import FeedbackIngestionService
from schemas import FeedbackIngestCreate
from utils.app_exceptions import AppException


class FakeDB:
    def __init__(self):
        self.rolled_back = False

    async def rollback(self):
        self.rolled_back = True


class FakeRepo:
    def __init__(self, conversation=None, message=None, trace=None, context=None, existing=None):
        self.db = FakeDB()
        self.conversation = conversation
        self.message = message
        self.trace = trace
        self.context = context
        self.existing = existing
        self.created = []

    async def get_conversation(self, conversation_id):
        return self.conversation if self.conversation and self.conversation.id == conversation_id else None

    async def get_message(self, message_id):
        return self.message if self.message and self.message.id == message_id else None

    async def get_latest_trace_for_message(self, message_id, conversation_id):
        if not self.trace:
            return None
        if self.trace.ai_message_id != message_id or self.trace.session_id != conversation_id:
            return None
        return self.trace

    async def get_active_context(self, conversation_id):
        if self.context and self.context.session_id == conversation_id:
            return self.context
        return None

    async def get_existing_feedback(self, user_id, conversation_id, message_id, feedback_type):
        if not self.existing:
            return None
        if (
            self.existing.user_id == user_id
            and self.existing.conversation_id == conversation_id
            and self.existing.message_id == message_id
            and self.existing.feedback_type == feedback_type
        ):
            return self.existing
        return None

    async def create_feedback(self, feedback):
        self.created.append(feedback)
        feedback.id = len(self.created)
        return feedback


def make_trace():
    return SimpleNamespace(
        id=88,
        session_id=10,
        ai_message_id=20,
        route="knowledge_search",
        reason="semantic_match",
        original_question="Fan alarm troubleshooting",
        query_rewrite="Fan alarm troubleshooting in module A",
        retrieval_query="fan alarm module A",
        answer_preview="Check the fan power module.",
        reference_docs_json=json.dumps([
            {"doc_id": 101, "library_type": "knowledge", "title": "Fan manual"},
            {"doc_id": 102, "library_type": "breakdown", "title": "Power module guide"},
        ]),
        validation_json=json.dumps({
            "memory_strategy": "rag_once",
            "memory_complexity": "standard",
            "memory_actions": ["load_active_context", "load_recent_messages"],
            "has_active_context": True,
            "has_summary": True,
            "context_action": "continue",
            "context_score": 0.82,
            "last_focus": "fan alarm",
            "recent_message_count": 4,
            "recent_trace_count": 2,
        }),
    )


def make_context():
    return SimpleNamespace(
        session_id=10,
        active_issue="Fan alarm",
        active_device="Cooling fan",
        active_component="Power module",
        active_symptom="fan stop",
        active_error_code="E201",
        active_query="fan alarm module A",
        active_route="knowledge_search",
        active_reason="semantic_match",
        summary_text="Short summary",
    )


def make_repo(existing=None, trace=None, message=None, conversation=None, context=None):
    return FakeRepo(
        conversation=conversation or SimpleNamespace(id=10, user_id=7),
        message=message or SimpleNamespace(id=20, session_id=10, role=0, content_text="Check the power module.", message_order=18, ai_reference_doc_ids='[101, 102]'),
        trace=trace or make_trace(),
        context=context or make_context(),
        existing=existing,
    )


def make_payload(feedback_type="positive", user_id=7, conversation_id=10, message_id=20, rating=5, comment=""):
    return FeedbackIngestCreate(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        feedback_type=feedback_type,
        rating=rating,
        comment=comment or None,
    )


class CurrentUser:
    def __init__(self, user_id):
        self.id = user_id


@pytest.mark.asyncio
async def test_positive_feedback_ingestion_saves_pending_snapshot():
    repo = make_repo()
    service = FeedbackIngestionService(repo)

    record, created = await service.ingest_feedback(make_payload("positive", rating=5), CurrentUser(7))

    assert created is True
    assert record.status == "pending"
    assert record.feedback_type == "positive"
    assert json.loads(record.query_snapshot)["memory_pack"]["strategy"] == "rag_once"
    assert json.loads(record.answer_snapshot)["answer_text"] == "Check the power module."
    assert len(json.loads(record.retrieved_documents)) == 2
    assert len(json.loads(record.cited_documents)) == 2


@pytest.mark.asyncio
async def test_feedback_ingestion_is_rejected_when_feature_is_disabled(monkeypatch):
    monkeypatch.setenv("FEEDBACK_ENABLED", "false")
    repo = make_repo()
    service = FeedbackIngestionService(repo)

    with pytest.raises(AppException) as exc:
        await service.ingest_feedback(make_payload(), CurrentUser(7))

    assert exc.value.message == "反馈功能已关闭"
    assert repo.created == []


@pytest.mark.asyncio
async def test_negative_feedback_ingestion_saves_pending_snapshot():
    repo = make_repo()
    service = FeedbackIngestionService(repo)

    record, created = await service.ingest_feedback(make_payload("negative", rating=1, comment="Wrong module"), CurrentUser(7))

    assert created is True
    assert record.feedback_type == "negative"
    assert json.loads(record.query_snapshot)["device_context"]["active_device"] == "Cooling fan"
    assert record.comment == "Wrong module"


@pytest.mark.asyncio
async def test_correction_feedback_ingestion_preserves_comment():
    repo = make_repo()
    service = FeedbackIngestionService(repo)

    record, created = await service.ingest_feedback(make_payload("correction", rating=2, comment="It is the power module."), CurrentUser(7))

    assert created is True
    assert record.feedback_type == "correction"
    assert record.comment == "It is the power module."


@pytest.mark.asyncio
async def test_duplicate_feedback_returns_existing_record():
    existing = SimpleNamespace(
        id=99,
        user_id=7,
        conversation_id=10,
        message_id=20,
        feedback_type="positive",
        status="pending",
    )
    repo = make_repo(existing=existing)
    service = FeedbackIngestionService(repo)

    record, created = await service.ingest_feedback(make_payload("positive"), CurrentUser(7))

    assert created is False
    assert record.id == 99
    assert len(repo.created) == 0


@pytest.mark.asyncio
async def test_invalid_message_id_returns_clear_error():
    repo = FakeRepo(
        conversation=SimpleNamespace(id=10, user_id=7),
        message=None,
        trace=make_trace(),
        context=make_context(),
    )
    service = FeedbackIngestionService(repo)

    with pytest.raises(AppException) as exc:
        await service.ingest_feedback(make_payload(), CurrentUser(7))

    assert exc.value.message == "消息不存在"


@pytest.mark.asyncio
async def test_unauthorized_user_is_rejected():
    repo = make_repo(conversation=SimpleNamespace(id=10, user_id=9))
    service = FeedbackIngestionService(repo)

    with pytest.raises(AppException) as exc:
        await service.ingest_feedback(make_payload(user_id=7), CurrentUser(7))

    assert exc.value.message == "无权提交该对话反馈"


@pytest.mark.asyncio
async def test_trace_missing_is_rejected():
    repo = FakeRepo(
        conversation=SimpleNamespace(id=10, user_id=7),
        message=SimpleNamespace(id=20, session_id=10, role=0, content_text="Check the power module.", message_order=18, ai_reference_doc_ids='[101, 102]'),
        trace=None,
        context=make_context(),
    )
    service = FeedbackIngestionService(repo)

    with pytest.raises(AppException) as exc:
        await service.ingest_feedback(make_payload(), CurrentUser(7))

    assert exc.value.message == "回答追踪不存在"


@pytest.mark.asyncio
async def test_answer_missing_is_rejected():
    repo = make_repo(message=SimpleNamespace(id=20, session_id=10, role=1, content_text="User text", message_order=18, ai_reference_doc_ids=""))
    service = FeedbackIngestionService(repo)

    with pytest.raises(AppException) as exc:
        await service.ingest_feedback(make_payload(), CurrentUser(7))

    assert exc.value.message == "回答不存在"
