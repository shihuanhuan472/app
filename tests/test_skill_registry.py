from agents.intent.schemas import RouteDecision
from agents.intent.taxonomy import IntentRoute
from agents.memory.schemas import AdaptiveRagPlan, MemoryPack
from agents.skills import SkillPromptBuilder, SkillRegistry


def _decision(route, use_rag, reason, dialog_act=None):
    return RouteDecision(
        route=route,
        use_rag=use_rag,
        confidence=0.9,
        reason=reason,
        dialog_act=dialog_act,
    )


def _memory_pack(route, reason, dialog_act=None):
    return MemoryPack(
        session_id=1,
        route=route,
        reason=reason,
        dialog_act=dialog_act,
        active_context={"active_issue": "注射泵运行不到指定位置"},
        adaptive_rag=AdaptiveRagPlan(strategy="rag_once", complexity="standard"),
    )


def test_skill_registry_selects_fault_qa_for_knowledge_search():
    skill = SkillRegistry().select(
        _decision(IntentRoute.KNOWLEDGE_SEARCH, True, "knowledge_signal"),
        _memory_pack("knowledge_search", "knowledge_signal"),
    )

    assert skill.name == "fault_qa"
    assert skill.use_rag_context is True


def test_skill_registry_selects_contextual_retry_before_fault_qa():
    skill = SkillRegistry().select(
        _decision(IntentRoute.KNOWLEDGE_SEARCH, True, "contextual_retry"),
        _memory_pack("knowledge_search", "contextual_retry"),
    )

    assert skill.name == "contextual_retry"


def test_skill_registry_selects_topic_redirect_for_low_value_feedback():
    skill = SkillRegistry().select(
        _decision(IntentRoute.CASUAL_CHAT, False, "low_value_feedback", dialog_act="topic_redirect"),
        _memory_pack("casual_chat", "low_value_feedback", dialog_act="topic_redirect"),
    )

    assert skill.name == "topic_redirect"
    assert skill.use_rag_context is False


def test_skill_registry_selects_answer_audit():
    skill = SkillRegistry().select(
        _decision(IntentRoute.ANSWER_AUDIT, False, "answer_audit_pattern"),
        _memory_pack("answer_audit", "answer_audit_pattern"),
    )

    assert skill.name == "answer_audit"


def test_skill_prompt_builder_includes_selected_skill_and_rag_context():
    memory_pack = _memory_pack("knowledge_search", "knowledge_signal")
    skill = SkillRegistry().select_for_memory(memory_pack)

    prompt = SkillPromptBuilder().build_completion_prompt(
        question="注射泵运行不到指定位置怎么办？",
        skill=skill,
        memory_pack=memory_pack,
        memory_prompt="【会话级 active_context】\n当前主问题：注射泵运行不到指定位置",
        rag_prompt="以下是一些相关的知识文档，供你参考：检查注射器固定螺丝。",
        retrieval_query="注射泵运行不到指定位置解决方案",
    )

    assert "【业务 Skill：fault_qa】" in prompt
    assert "【知识库检索结果】" in prompt
    assert "结合上下文后的检索问题" in prompt
    assert "不要输出图片路径" in prompt
