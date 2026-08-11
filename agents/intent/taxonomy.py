from enum import Enum


class IntentRoute(str, Enum):
    KNOWLEDGE_SEARCH = "knowledge_search"
    TOOL_CALL = "tool_call"
    CASUAL_CHAT = "casual_chat"
    EMOTIONAL_FEEDBACK = "emotional_feedback"
    CLARIFY = "clarify"
    ANSWER_AUDIT = "answer_audit"


ROUTE_VALUES = {item.value for item in IntentRoute}
