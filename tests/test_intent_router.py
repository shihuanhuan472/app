import asyncio

from agents.intent.router_agent import IntentRouterAgent
from agents.intent.semantic_router import route_semantically
from agents.intent.taxonomy import IntentRoute


def test_greeting_is_casual_chat():
    result = route_semantically("你好")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.CASUAL_CHAT
    assert result.decision.use_rag is False


def test_fault_question_uses_knowledge_search():
    result = route_semantically("DNBSEQ设备报错E102怎么排查？")
    assert result.decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert result.decision.slots.error_code == "E102"


def test_image_query_has_highest_priority():
    result = route_semantically("帮我看看", has_images=True)
    assert result.decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert result.decision.use_rag is True


def test_empty_question_requests_clarification():
    result = route_semantically("")
    assert result.decision.route == IntentRoute.CLARIFY
    assert result.decision.need_clarification is True


def test_router_can_disable_llm(monkeypatch):
    monkeypatch.setenv("INTENT_ROUTER_LLM_ENABLED", "false")
    decision = asyncio.run(IntentRouterAgent().route("介绍一下量子力学"))
    assert decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert decision.source == "fallback"


def test_tool_request_routes_to_tool_call():
    result = route_semantically("查一下近7天天气")
    assert result.decision.route == IntentRoute.TOOL_CALL


def test_contextual_followup_uses_knowledge_search():
    result = route_semantically("上面我提到的相关问题的解决方案你再给我说一下")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert result.decision.use_rag is True
    assert result.decision.reason == "contextual_followup"


def test_contextual_followup_accepts_colloquial_reference():
    result = route_semantically("请你再重新说一下上边提到的问题和你给的解决方案")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert result.decision.use_rag is True
    assert result.decision.reason == "contextual_followup"


def test_answer_audit_routes_to_audit():
    result = route_semantically("为什么这两次图片不一样？")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.ANSWER_AUDIT
    assert result.decision.use_rag is False
    assert result.decision.reason == "answer_audit_pattern"


def test_answer_recheck_routes_to_audit():
    result = route_semantically("你重新检查一下刚才的回答")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.ANSWER_AUDIT
    assert result.decision.use_rag is False
    assert result.decision.reason == "answer_audit_pattern"


def test_reanswer_request_routes_to_contextual_retry():
    result = route_semantically("请重新回答我的问题")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.KNOWLEDGE_SEARCH
    assert result.decision.use_rag is True
    assert result.decision.reason == "contextual_retry"


def test_low_value_confidence_challenge_routes_to_casual_chat():
    result = route_semantically("你回答的确定对吗")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.CASUAL_CHAT
    assert result.decision.use_rag is False
    assert result.decision.reason == "low_value_feedback"
    assert result.decision.dialog_act == "topic_redirect"


def test_negative_answer_feedback_is_casual_chat():
    result = route_semantically("不对，你回答的不对")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.CASUAL_CHAT
    assert result.decision.use_rag is False
    assert result.decision.reason == "feedback_pattern"


def test_complaint_feedback_is_casual_chat():
    result = route_semantically("你回答的不好，我要投诉")
    assert result.high_confidence is True
    assert result.decision.route == IntentRoute.CASUAL_CHAT
    assert result.decision.use_rag is False
    assert result.decision.reason == "feedback_pattern"
