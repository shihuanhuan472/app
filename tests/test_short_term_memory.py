from types import SimpleNamespace

import pytest

from agents.intent.schemas import RouteDecision
from agents.intent.taxonomy import IntentRoute
from agents.memory.context_scorer import analyze_context_transition, build_slot_confidence, merge_working_memory
from agents.memory.pack_builder import (
    ANSWER_AUDIT_ROUTE,
    CONTEXTUAL_FOLLOWUP_REASON,
    CONTEXTUAL_RETRY_REASON,
    MemoryPackBuilder,
)
from agents.memory.schemas import AdaptiveRagPlan, MemoryPack
from agents.memory.service import MemoryService


def make_message(text: str, role: int = 1, order: int = 1, images: str = ""):
    return SimpleNamespace(
        id=order,
        session_id=100,
        message_order=order,
        role=role,
        content_text=text,
        user_uploaded_images=images,
        ai_reference_doc_ids="",
    )


def make_decision(route: IntentRoute, use_rag: bool, reason: str) -> RouteDecision:
    return RouteDecision(
        route=route,
        use_rag=use_rag,
        confidence=0.9,
        reason=reason,
        source="test",
    )


def test_memory_pack_prompt_includes_active_context_and_references():
    pack = MemoryPack(
        session_id=100,
        route="knowledge_search",
        reason="semantic_match",
        summary="Previous chat about pump alarms.",
        active_context={
            "active_issue": "Pump alarm E102",
            "active_device": "Pump",
            "active_component": "Pressure pump",
            "active_error_code": "E102",
            "active_query": "Pump E102 troubleshooting",
            "active_reference_docs": [
                {
                    "doc_id": 7,
                    "library_type": "knowledge",
                    "title": "Pump troubleshooting manual",
                }
            ],
        },
        recent_messages=[
            make_message("How do I fix it?", role=1, order=1),
            make_message("Check the pipe and pressure.", role=0, order=2),
        ],
        adaptive_rag=AdaptiveRagPlan(strategy="rag_once", complexity="standard"),
    )

    prompt = pack.to_prompt(include_recent_messages=True)
    payload = pack.to_dict()

    assert "Pump alarm E102" in prompt
    assert "knowledge:7" in prompt
    assert "How do I fix it?" in prompt
    assert payload["recent_messages"][0]["role"] == 1
    assert payload["adaptive_rag"]["strategy"] == "rag_once"


def test_recent_message_loading_policy_is_context_aware():
    builder = MemoryPackBuilder(memory_service=None)

    assert builder._should_load_recent_messages("knowledge_search", "semantic_match")
    assert builder._should_load_recent_messages(ANSWER_AUDIT_ROUTE, "audit_request")
    assert builder._should_load_recent_messages("casual_chat", CONTEXTUAL_FOLLOWUP_REASON)
    assert builder._should_load_recent_messages("casual_chat", CONTEXTUAL_RETRY_REASON)
    assert not builder._should_load_recent_messages("casual_chat", "smalltalk")


def test_contextual_followup_uses_contextual_rag_plan():
    builder = MemoryPackBuilder(memory_service=None)
    decision = make_decision(IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, reason=CONTEXTUAL_FOLLOWUP_REASON)

    plan = builder._build_adaptive_rag_plan(
        make_message("What should I do next?"),
        decision,
        active_context={"active_issue": "Pump alarm E102"},
    )

    assert plan.strategy == "contextual_rag"
    assert plan.complexity == "contextual"
    assert not plan.iterative_retrieval


def test_context_scorer_continues_focus_followup():
    analysis = analyze_context_transition(
        "\u4e0a\u9762\u7684\u95ee\u9898\u600e\u4e48\u89e3\u51b3\uff1f",
        active_context={
            "active_issue": "Pump alarm E102",
            "active_device": "Pump",
            "active_component": "Pressure pump",
            "active_error_code": "E102",
            "active_query": "Pump E102 troubleshooting",
        },
    )

    assert analysis.action == "continue"
    assert analysis.last_focus == "solution"
    assert "contextual_cue" in analysis.signals


def test_context_scorer_switches_on_new_device_and_error_code():
    analysis = analyze_context_transition(
        "Another PLC E201 alarm, how do I handle it?",
        active_context={
            "active_issue": "Pump alarm E102",
            "active_device": "Pump",
            "active_component": "Pressure pump",
            "active_error_code": "E102",
        },
    )

    assert analysis.action == "switch"
    assert "slot_conflict:device" in analysis.conflicts
    assert "slot_conflict:error_code" in analysis.conflicts


def test_context_scorer_extracts_working_memory_feedback():
    analysis = analyze_context_transition(
        "\u6211\u5df2\u7ecf\u68c0\u67e5\u8fc7\u7ba1\u8def\uff0c\u6ca1\u6709\u5835\u585e\u3002",
        active_context={
            "active_issue": "Pump alarm E102",
            "active_device": "Pump",
            "active_component": "Pressure pump",
            "active_error_code": "E102",
        },
    )
    working_memory = merge_working_memory({}, analysis)

    assert analysis.action == "continue"
    assert working_memory["confirmed_facts"]
    assert working_memory["excluded_causes"]


def test_memory_pack_prompt_includes_working_memory_and_context_decision():
    pack = MemoryPack(
        session_id=100,
        route="knowledge_search",
        reason="semantic_match",
        active_context={"active_issue": "Pump alarm E102"},
        working_memory={
            "last_focus": "troubleshooting",
            "confirmed_facts": ["checked the pipe"],
            "excluded_causes": ["no blockage"],
        },
        context_analysis={"action": "continue", "score": 0.76, "last_focus": "troubleshooting"},
    )

    prompt = pack.to_prompt(include_recent_messages=False)

    assert "checked the pipe" in prompt
    assert "no blockage" in prompt
    assert "Pump alarm E102" in prompt


def test_memory_pack_prompt_includes_cited_docs():
    pack = MemoryPack(
        session_id=100,
        route="knowledge_search",
        reason="semantic_match",
        active_context={"active_issue": "Pump alarm E102"},
        working_memory={
            "cited_docs": [
                {
                    "doc_id": 7,
                    "library_type": "knowledge",
                    "title": "Cleaning pump manual",
                    "evidence_section_titles": ["Troubleshooting"],
                }
            ]
        },
    )

    prompt = pack.to_prompt(include_recent_messages=False)

    assert "knowledge:7" in prompt
    assert "Cleaning pump manual" in prompt


@pytest.mark.asyncio
async def test_context_classifier_llm_can_override_heuristic(monkeypatch):
    service = MemoryService(db=None)

    async def fake_classifier(**_kwargs):
        return {
            "action": "switch",
            "score": 0.91,
            "reason": "New PLC error code",
            "slots": {
                "device": {"value": "PLC", "confidence": 0.93},
                "error_code": {"value": "E201", "confidence": 0.96},
            },
        }

    monkeypatch.setenv("MEMORY_CONTEXT_CLASSIFIER_LLM_ENABLED", "true")
    monkeypatch.setattr(service, "_classify_context_with_llm", fake_classifier)

    analysis = await service.analyze_context_transition(
        current_text="How do I handle this?",
        active_context={"active_issue": "Pump alarm E102"},
        retrieval_query="PLC E201 alarm",
    )
    confidence = build_slot_confidence({}, analysis)

    assert analysis.action == "switch"
    assert analysis.reason == "New PLC error code"
    assert confidence["error_code"]["value"] == "E201"
    assert confidence["error_code"]["source"] == "llm_context_classifier"


def test_answer_refinement_extracts_structured_working_memory():
    service = MemoryService(db=None)
    reference_docs = [
        {
            "doc_id": 7,
            "library_type": "knowledge",
            "title": "Cleaning pump manual",
            "evidence_section_titles": ["Troubleshooting"],
        }
    ]
    answer = "\n".join(
        [
            "\u5f53\u524d\u95ee\u9898\u7406\u89e3\uff1aPump alarm E102",
            "1. \u8bf7\u68c0\u67e5\u7ba1\u8def\u538b\u529b\u5e76\u8bb0\u5f55\u3002",
            "2. \u6d4b\u91cf\u6cf5\u538b\u5e76\u8bb0\u5f55\u3002",
            "\u8bf7\u8865\u5145\u8f6f\u4ef6\u7248\u672c\u548c\u73b0\u573a\u622a\u56fe\u3002",
        ]
    )

    refinement = service.build_answer_memory_refinement(
        user_text="\u6211\u5df2\u7ecf\u68c0\u67e5\u8fc7\u7ba1\u8def\uff0c\u6ca1\u6709\u5835\u585e\u3002",
        answer=answer,
        reference_docs=reference_docs,
        source_turn_id=12,
        source_ai_message_id=34,
        source_trace_id=56,
    )
    working_memory = service.merge_structured_working_memory({}, refinement, current_turn_id=12)

    assert working_memory["confirmed_facts"]
    assert working_memory["confirmed_facts"][0]["status"] == "active"
    assert working_memory["confirmed_facts"][0]["source_turn_id"] == 12
    assert working_memory["excluded_causes"]
    assert any(item["status"] in {"active", "superseded"} for item in working_memory["excluded_causes"])
    assert working_memory["candidate_facts"]
    assert working_memory["candidate_facts"][0]["status"] == "candidate"
    assert working_memory["suggested_steps"]
    assert working_memory["pending_questions"]
    assert working_memory["cited_docs"][0]["doc_id"] == 7


def test_structured_summary_uses_fault_memory_sections():
    service = MemoryService(db=None)
    summary = service._build_structured_summary_from_context(
        {
            "active_issue": "Pump alarm E102",
            "active_device": "Pump",
            "active_component": "Pressure pump",
            "active_error_code": "E102",
            "working_memory": {
                "confirmed_facts": [{"text": "checked the pipe", "status": "active"}],
                "excluded_causes": [{"text": "no blockage", "status": "active"}],
                "candidate_facts": [{"text": "maybe sensor issue", "status": "candidate"}],
                "pending_questions": [{"text": "Need software version", "status": "open"}],
                "suggested_steps": [{"text": "Measure pump pressure", "status": "proposed"}],
                "cited_docs": [
                    {"doc_id": 7, "library_type": "knowledge", "title": "Cleaning pump manual", "status": "active"}
                ],
            },
        },
        max_chars=1200,
    )

    assert "Long conversation summary (structured)" in summary
    assert "Pump alarm E102" in summary
    assert "checked the pipe" in summary
    assert "no blockage" in summary
    assert "maybe sensor issue" in summary
    assert "Cleaning pump manual" in summary


class FakeMemoryService:
    def __init__(self, active_context=None, recent_messages=None, summary=None):
        self.active_context = active_context
        self.recent_messages = recent_messages or []
        self.summary = summary
        self.delegate = MemoryService(db=None)

    async def get_active_context_dict(self, _session_id):
        return self.active_context

    async def load_recent_messages(self, _session_id, _before_order, limit=6):
        return self.recent_messages[-limit:]

    async def load_recent_ai_traces(self, *_args, **_kwargs):
        return []

    async def load_recent_context_events(self, *_args, **_kwargs):
        return []

    async def load_latest_summary(self, _session_id):
        if not self.summary:
            return None
        return SimpleNamespace(summary_text=self.summary)

    async def analyze_context_transition(self, *args, **kwargs):
        return await self.delegate.analyze_context_transition(*args, **kwargs)

    def merge_structured_working_memory(self, *args, **kwargs):
        return self.delegate.merge_structured_working_memory(*args, **kwargs)

    def retrieve_relevant_working_memory(self, *args, **kwargs):
        return self.delegate.retrieve_relevant_working_memory(*args, **kwargs)


@pytest.mark.asyncio
async def test_multiturn_pack_builder_covers_followup_retry_switch_and_no_context():
    history = [
        make_message("Pump alarm E102, how do I fix it?", role=1, order=1),
        make_message("Check the pipe and pressure.", role=0, order=2),
    ]
    active_context = {
        "active_issue": "Pump alarm E102",
        "active_device": "Pump",
        "active_component": "Pressure pump",
        "active_error_code": "E102",
        "active_query": "Pump E102 troubleshooting",
    }
    builder = MemoryPackBuilder(FakeMemoryService(active_context, history, summary="Structured summary"))

    followup_pack = await builder.build(
        make_message("\u4e0a\u9762\u7684\u95ee\u9898\u600e\u4e48\u89e3\u51b3\uff1f", order=3),
        make_decision(IntentRoute.KNOWLEDGE_SEARCH, True, CONTEXTUAL_FOLLOWUP_REASON),
    )
    retry_pack = await builder.build(
        make_message("\u91cd\u65b0\u56de\u7b54\u6211\u7684\u95ee\u9898", order=3),
        make_decision(IntentRoute.KNOWLEDGE_SEARCH, True, CONTEXTUAL_RETRY_REASON),
    )
    switch_pack = await builder.build(
        make_message("Another PLC E201 alarm needs handling", order=3),
        make_decision(IntentRoute.KNOWLEDGE_SEARCH, True, CONTEXTUAL_FOLLOWUP_REASON),
    )
    no_context_pack = await MemoryPackBuilder(FakeMemoryService()).build(
        make_message("\u91cd\u65b0\u56de\u7b54\u6211\u7684\u95ee\u9898", order=1),
        make_decision(IntentRoute.KNOWLEDGE_SEARCH, True, CONTEXTUAL_RETRY_REASON),
    )

    assert followup_pack.context_analysis["action"] == "continue"
    assert followup_pack.strategy == "contextual_rag"
    assert retry_pack.strategy == "contextual_rag"
    assert switch_pack.context_analysis["action"] == "switch"
    assert switch_pack.strategy != "contextual_rag"
    assert no_context_pack.context_analysis["action"] == "new"


def test_structured_merge_marks_conflicts_and_stale_records():
    service = MemoryService(db=None)
    previous = {
        "confirmed_facts": [
            {
                "text": "old calibration note",
                "confidence": 0.72,
                "status": "active",
                "source": "legacy",
                "last_seen_turn_id": 1,
                "ttl_turns": 1,
            }
        ],
        "excluded_causes": [
            {
                "text": "pump pressure low",
                "confidence": 0.83,
                "status": "active",
                "source": "legacy",
                "last_seen_turn_id": 1,
                "ttl_turns": 12,
            }
        ],
    }
    refinement = {
        "confirmed_facts": [
            {
                "text": "pump pressure low",
                "confidence": 0.93,
                "status": "active",
                "source": "user_feedback",
                "source_turn_id": 3,
                "last_seen_turn_id": 3,
                "ttl_turns": 24,
            }
        ],
        "candidate_facts": [
            {
                "text": "maybe bearing issue",
                "confidence": 0.55,
                "status": "candidate",
                "source": "assistant_answer",
                "source_turn_id": 3,
                "last_seen_turn_id": 3,
                "ttl_turns": 8,
            }
        ],
    }

    merged = service.merge_structured_working_memory(previous, refinement, current_turn_id=5, source_turn_id=3)

    assert any(item["status"] == "stale" for item in merged["confirmed_facts"] if item["text"] == "old calibration note")
    assert any(item["status"] == "active" and item["text"] == "pump pressure low" for item in merged["confirmed_facts"])
    assert merged["excluded_causes"][0]["status"] == "superseded"
    assert merged["memory_conflicts"]
    assert merged["candidate_facts"][0]["status"] == "candidate"


def test_retrieve_relevant_working_memory_prioritizes_query_matches():
    service = MemoryService(db=None)
    working_memory = {
        "confirmed_facts": [
            {
                "text": "pump pressure normal after calibration",
                "confidence": 0.88,
                "status": "active",
                "last_seen_turn_id": 9,
                "ttl_turns": 12,
            },
            {
                "text": "door seal worn",
                "confidence": 0.83,
                "status": "active",
                "last_seen_turn_id": 9,
                "ttl_turns": 12,
            },
        ],
        "pending_questions": [
            {
                "text": "Need model number",
                "confidence": 0.8,
                "status": "open",
                "last_seen_turn_id": 9,
                "ttl_turns": 10,
            }
        ],
        "cited_docs": [
            {
                "doc_id": 7,
                "library_type": "knowledge",
                "title": "pump manual pressure calibration",
                "confidence": 0.95,
                "status": "active",
                "last_seen_turn_id": 8,
                "ttl_turns": 16,
            }
        ],
    }

    selected = service.retrieve_relevant_working_memory(
        working_memory,
        query="pump pressure calibration",
        active_context={"active_issue": "pump pressure calibration"},
        context_analysis={"current_turn_id": 10},
    )

    assert selected["confirmed_facts"][0]["text"] == "pump pressure normal after calibration"
    assert selected["cited_docs"][0]["doc_id"] == 7
    assert selected["_retrieval"]["selected_counts"]["confirmed_facts"] <= 5


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeAsyncSession:
    def __init__(self, trace=None):
        self.trace = trace
        self.statements = []
        self.commit_count = 0
        self.rollback_count = 0

    async def execute(self, statement):
        text = str(statement).lower()
        self.statements.append(text)
        if "ai_message_traces" in text and text.lstrip().startswith("select"):
            return FakeScalarResult(self.trace)
        return FakeScalarResult(None)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


@pytest.mark.asyncio
async def test_delete_session_runtime_state_cleans_memory_trace_and_usage_tables():
    db = FakeAsyncSession()

    await MemoryService(db).delete_session_runtime_state(100)

    joined = "\n".join(db.statements)
    assert "ai_usage_logs" in joined
    assert "ai_message_traces" in joined
    assert "conversation_context_events" in joined
    assert "conversation_contexts" in joined
    assert "conversation_summaries" in joined


@pytest.mark.asyncio
async def test_trace_update_marks_stream_answer_success():
    trace = SimpleNamespace(
        answer_preview="",
        status="pending",
        error_message=None,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        updated_time=None,
    )
    db = FakeAsyncSession(trace=trace)

    await MemoryService(db).update_ai_trace(
        12,
        answer="stream answer complete",
        status_value="success",
        output_tokens=128,
    )

    assert trace.answer_preview == "stream answer complete"
    assert trace.status == "success"
    assert trace.output_tokens == 128
    assert db.commit_count == 1
