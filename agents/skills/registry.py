from typing import Optional

from agents.intent import RouteDecision
from agents.memory import MemoryPack

from .schemas import AgentSkill


SKILLS = {
    "fault_qa": AgentSkill(
        name="fault_qa",
        description="基于知识库和短期记忆回答设备故障、维护、参数和排查类问题。",
        prompt_file="fault_qa.md",
        use_rag_context=True,
        include_memory=True,
        actions=["skill:fault_qa"],
    ),
    "contextual_retry": AgentSkill(
        name="contextual_retry",
        description="用户要求重新回答当前主问题时，恢复会话主问题并重新组织答案。",
        prompt_file="contextual_retry.md",
        use_rag_context=True,
        include_memory=True,
        actions=["skill:contextual_retry"],
    ),
    "answer_audit": AgentSkill(
        name="answer_audit",
        description="复盘最近相关回答的依据、检索和图片差异，给出用户可读解释。",
        prompt_file="answer_audit.md",
        use_rag_context=False,
        include_memory=True,
        actions=["skill:answer_audit"],
    ),
    "clarify_fault": AgentSkill(
        name="clarify_fault",
        description="问题指代不明或故障信息不足时，追问可用于排查的关键信息。",
        prompt_file="clarify_fault.md",
        use_rag_context=False,
        include_memory=True,
        actions=["skill:clarify_fault"],
    ),
    "topic_redirect": AgentSkill(
        name="topic_redirect",
        description="低价值确认、无关字符、投诉和跑题内容，简短回应并引导回故障问答。",
        prompt_file="topic_redirect.md",
        use_rag_context=False,
        include_memory=True,
        actions=["skill:topic_redirect"],
    ),
    "casual_chat": AgentSkill(
        name="casual_chat",
        description="寒暄、感谢、普通交流，保持简短并说明系统主要服务故障排查。",
        prompt_file="casual_chat.md",
        use_rag_context=False,
        include_memory=False,
        actions=["skill:casual_chat"],
    ),
    "tool_call_guard": AgentSkill(
        name="tool_call_guard",
        description="用户要求调用未接入工具时，说明当前能力边界并引导回维护问答。",
        prompt_file="tool_call_guard.md",
        use_rag_context=False,
        include_memory=False,
        actions=["skill:tool_call_guard"],
    ),
}


class SkillRegistry:
    """Selects a business skill from intent and short-term memory state."""

    def select(self, decision: RouteDecision, memory_pack: Optional[MemoryPack] = None) -> AgentSkill:
        route = _route_value(decision)
        reason = str(getattr(decision, "reason", "") or "")
        dialog_act = str(getattr(decision, "dialog_act", "") or "")

        if route == "answer_audit":
            return SKILLS["answer_audit"]
        if reason == "contextual_retry":
            return SKILLS["contextual_retry"]
        if route == "clarify" or bool(getattr(decision, "need_clarification", False)):
            return SKILLS["clarify_fault"]
        if route == "casual_chat" and dialog_act == "topic_redirect":
            return SKILLS["topic_redirect"]
        if route == "casual_chat" and reason in {"low_value_feedback", "feedback_pattern"}:
            return SKILLS["topic_redirect"]
        if route == "casual_chat":
            return SKILLS["casual_chat"]
        if route == "tool_call":
            return SKILLS["tool_call_guard"]
        if route == "knowledge_search":
            return SKILLS["fault_qa"]
        return SKILLS["casual_chat"]

    def select_for_memory(self, memory_pack: Optional[MemoryPack]) -> AgentSkill:
        if not memory_pack:
            return SKILLS["casual_chat"]
        route = str(getattr(memory_pack, "route", "") or "")
        reason = str(getattr(memory_pack, "reason", "") or "")
        dialog_act = str(getattr(memory_pack, "dialog_act", "") or "")

        if route == "answer_audit":
            return SKILLS["answer_audit"]
        if reason == "contextual_retry":
            return SKILLS["contextual_retry"]
        if route == "clarify":
            return SKILLS["clarify_fault"]
        if route == "casual_chat" and (dialog_act == "topic_redirect" or reason in {"low_value_feedback", "feedback_pattern"}):
            return SKILLS["topic_redirect"]
        if route == "knowledge_search":
            return SKILLS["fault_qa"]
        if route == "tool_call":
            return SKILLS["tool_call_guard"]
        return SKILLS["casual_chat"]


def _route_value(decision: RouteDecision) -> str:
    route = getattr(decision, "route", "")
    return str(getattr(route, "value", route) or "")
