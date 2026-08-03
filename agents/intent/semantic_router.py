import re
from dataclasses import dataclass

from .schemas import IntentSlots, RouteDecision
from .taxonomy import IntentRoute


CASUAL_PATTERNS = (
    r"(你|您)?好(呀|啊|哈)?|hi|hello|hey|嗨|哈喽|在吗",
    r"谢谢|多谢|感谢|辛苦了|好的?|ok|okay|收到|明白(了)?|再见|拜拜|bye",
    r"你是谁|您是谁|你能做什么|你会什么|介绍一下你自己",
)
ANSWER_AUDIT_PATTERNS = (
    r"(为什么|为啥|怎么).*(两次|这次|上次|前后|第一次|第二次|刚才|之前).*(不一样|不同|区别|差异)",
    r"(为什么|为啥|怎么).*(图片|回答|答案|引用|文档).*(不一样|不同|变了|换了|差异)",
    r"(你|您)?(刚才|上次|前面|之前|第一次|第二次).*(依据|来源|引用|为什么|错|不对|检查|核对|反思)",
    r"(重新|再)(检查|核对|审查|反思).*(回答|答案|引用|图片|文档)?",
    r"(回答|答案|图片|引用|文档).*(依据|来源|为什么|不一样|不同|差异)",
)
LOW_VALUE_FEEDBACK_PATTERNS = (
    r"(你|您)?(回答|答复|回复|答案|结论).*(确定|确认|肯定).*(对|正确|准确|靠谱吗|可靠|没问题)",
    r"(你|您)?(确定|确认|肯定).*(回答|答复|回复|答案|结论).*(对|正确|准确|靠谱吗|可靠|没问题)?",
    r"(这个|该)?(回答|答案|结论).*(对吗|正确吗|准确吗|靠谱吗|可靠吗|确定吗)",
    r"^(真的吗|真的假的|确定吗|对吗|靠谱吗|可靠吗)$",
    r"(你|您)?(是不是)?(胡说|瞎说|乱说|乱答|瞎答|骗我|忽悠我)",
    r"(这个|这答案|你说的).*(能信|可信吗|靠得住吗|有谱吗)",
)
LOW_VALUE_FEEDBACK_AUDIT_BLOCKERS = (
    r"两次|上次|这次|前后|第一(?:次)?|第二(?:次)?|不一样|不同|差异|比较|"
    r"引用|文档|图片|依据|来源|检索|召回|trace|日志|为什么|为啥|怎么"
)
REANSWER_PATTERNS = (
    r"(请)?(重新|再|重新再|再重新).*(回答|解答|说|讲|生成).*(我的)?(问题|这个问题|那个问题|上面|上边|刚才|之前)?",
    r"(请)?(回答|解答).*(刚才|上面|上边|之前|我的).*(问题)",
)
REANSWER_AUDIT_BLOCKERS = (
    r"检查|核对|审查|反思|复盘|依据|来源|为什么|为啥|不一样|不同|差异|图片|引用|文档"
)
FEEDBACK_PATTERNS = (
    r"(你|您)?(回答|答复|回复).*(不对|不准|不准确|不好|有问题|错了|错误)",
    r"(不对|不准|不准确|不好|错了|错误).*(你|您)?(回答|答复|回复)",
    r"(我要|想|需要)?(投诉|反馈|差评)",
    r"(不满意|很差|太差|胡说|乱说|瞎说)",
)
VAGUE_DOMAIN_CHAT_PATTERNS = (
    r"(你们|你这边|你这里|你们的|贵司|厂家)?.*(设备|仪器|机器|产品|系统).*(问题|故障|毛病|异常|报错|坏|出问题).*(多|很多|好多|不少|严重|频繁|经常|老是|总是|容易|故障率|问题率|稳定|可靠|靠谱|质量|是不是|是否)",
    r"(你们|你这边|你这里|你们的|贵司|厂家)?.*(设备|仪器|机器|产品|系统).*(多|很多|好多|不少|严重|频繁|经常|老是|总是|容易|是不是|是否).*(问题|故障|毛病|异常|报错|坏|出问题)",
    r"(你们|你这边|你这里|你们的|贵司|厂家)?.*(设备|仪器|机器|产品|系统).*(质量|稳定性?|可靠性?|故障率|问题率).*(怎么样|如何|好吗|高吗|低吗|靠谱吗|可靠吗|稳定吗|吗|嘛)",
    r"(你们|你这边|你这里|你们的|贵司|厂家)?.*(设备|仪器|机器|产品|系统).*(靠谱吗|可靠吗|稳定吗|质量怎么样|容易坏吗)",
    r"(你们|你这边|你这里|你们的|贵司|厂家).*(设备|仪器|机器|产品|系统).*(有)?(问题|故障|毛病|异常|报错|坏|出问题).*(吗|嘛)",
)
VAGUE_DOMAIN_TASK_BLOCKERS = (
    r"怎么|如何|怎样|怎么办|有哪些|列举|常见|清单|排查|处理|解决|修复|维修|维护|步骤|操作|参数|"
    r"手册|说明书|文档|知识库|阈值|范围|方案|对策|流程|SOP"
)
CONTEXT_REFERENCE_PATTERNS = (
    r"上面|上边|上文|前面|之前|刚才|刚刚|上一(个|条|轮)|前一(个|条|轮)",
    r"相关问题|这个问题|那个问题|该问题|此问题|这个故障|那个故障",
)
FOLLOWUP_TASK_PATTERNS = (
    r"解决方案|解决办法|处理方法|怎么解决|怎么处理|怎么办",
    r"原因|为什么|排查|检查步骤|操作步骤|步骤",
    r"再(给我)?(说|讲|解释|总结)(一下)?|重新(说|讲|解释)|详细(说|讲|解释)|展开(说|讲)?",
)
KNOWLEDGE_TERMS = (
    "知识库", "RAG", "文档", "手册", "说明书", "故障", "报错", "异常", "报警",
    "原因", "排查", "维修", "维护", "修复", "解决", "操作", "步骤", "参数",
    "阈值", "范围", "设备", "仪器", "试剂", "测序", "芯片", "Q30", "ECR",
    "DNBSEQ", "DL-T7", "G50", "FIT", "NSB", "SBC", "DNQ", "Cycle", "PE",
)
TOOL_PATTERNS = (
    r"(查|查询|看|获取)(一下)?(今天|明天|未来|近|最近).*(天气|气温|汇率|航班|快递|股票)",
    r"(调用|执行|运行|发送|创建|删除|更新).*(API|接口|工具|MCP|邮件|工单)",
)
AMBIGUOUS_PATTERNS = (
    r"(这个|那个|它|上面那个)(怎么用|怎么处理|怎么办|是什么)?",
    r"怎么弄|怎么办|帮我看看|不行了|有问题",
)


@dataclass(frozen=True)
class SemanticRouteResult:
    decision: RouteDecision
    high_confidence: bool


def _compact(text: str) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip())
    return re.sub(r"[。！？!?,，.、~～；;：:]+$", "", value)


def _matches_any(patterns, text: str, full_match: bool = False) -> bool:
    matcher = re.fullmatch if full_match else re.search
    return any(matcher(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _contains_any(text: str, terms) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _is_reanswer_request(text: str) -> bool:
    if not _matches_any(REANSWER_PATTERNS, text):
        return False
    return not re.search(REANSWER_AUDIT_BLOCKERS, text, flags=re.IGNORECASE)


def _is_low_value_feedback(text: str) -> bool:
    if not _matches_any(LOW_VALUE_FEEDBACK_PATTERNS, text):
        return False
    return not re.search(LOW_VALUE_FEEDBACK_AUDIT_BLOCKERS, text, flags=re.IGNORECASE)


def _is_vague_domain_chat(text: str, slots: IntentSlots) -> bool:
    if slots.error_code or slots.metric:
        return False
    if not _matches_any(VAGUE_DOMAIN_CHAT_PATTERNS, text):
        return False
    return not re.search(VAGUE_DOMAIN_TASK_BLOCKERS, text, flags=re.IGNORECASE)


def _extract_slots(text: str) -> IntentSlots:
    error_match = re.search(
        r"(?:error|err)[\s:_-]*([a-z0-9_-]+)(?![a-z0-9])|([A-Z]{1,5}[-_]?[0-9]{2,})(?![a-z0-9])",
        text,
        re.IGNORECASE,
    )
    metric_match = re.search(r"\b(Q30|ECR|FIT|NSB|SBC|DNQ|G50)\b", text, re.IGNORECASE)
    return IntentSlots(
        error_code=next((group for group in error_match.groups() if group), None) if error_match else None,
        metric=metric_match.group(1).upper() if metric_match else None,
        symptom=text if _contains_any(text, ("故障", "报错", "异常", "报警", "失败")) else None,
    )


def route_semantically(question: str, has_images: bool = False) -> SemanticRouteResult:
    raw_text = str(question or "").strip()
    compact = _compact(raw_text)
    slots = _extract_slots(raw_text)

    if has_images:
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, confidence=0.99,
            reason="uploaded_image", query_rewrite=raw_text or None, slots=slots,
        ), True)

    if not compact:
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CLARIFY, use_rag=False, confidence=1.0,
            reason="empty_question", need_clarification=True,
            clarification_question="请描述你想咨询的问题。", slots=slots,
        ), True)

    if _matches_any(CASUAL_PATTERNS, compact, full_match=True):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CASUAL_CHAT, use_rag=False, confidence=0.98,
            reason="casual_pattern", slots=slots,
        ), True)

    if _is_low_value_feedback(compact):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CASUAL_CHAT, use_rag=False, confidence=0.97,
            reason="low_value_feedback", dialog_act="topic_redirect",
            target="last_answer", slots=slots,
        ), True)

    if (
        _is_reanswer_request(compact)
        and not (
            _matches_any(CONTEXT_REFERENCE_PATTERNS, compact)
            and _matches_any(FOLLOWUP_TASK_PATTERNS, compact)
        )
    ):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, confidence=0.97,
            reason="contextual_retry", query_rewrite=raw_text, slots=slots,
        ), True)

    if _matches_any(ANSWER_AUDIT_PATTERNS, compact):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.ANSWER_AUDIT, use_rag=False, confidence=0.97,
            reason="answer_audit_pattern", slots=slots,
        ), True)

    if _matches_any(FEEDBACK_PATTERNS, compact):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CASUAL_CHAT, use_rag=False, confidence=0.97,
            reason="feedback_pattern", slots=slots,
        ), True)

    if _is_vague_domain_chat(compact, slots):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CASUAL_CHAT, use_rag=False, confidence=0.96,
            reason="vague_domain_chat", dialog_act="topic_redirect", slots=slots,
        ), True)

    if _matches_any(TOOL_PATTERNS, compact):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.TOOL_CALL, use_rag=False, confidence=0.95,
            reason="tool_pattern", slots=slots,
        ), True)

    if _matches_any(CONTEXT_REFERENCE_PATTERNS, compact) and _matches_any(FOLLOWUP_TASK_PATTERNS, compact):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, confidence=0.96,
            reason="contextual_followup", query_rewrite=raw_text, slots=slots,
        ), True)

    if _matches_any(AMBIGUOUS_PATTERNS, compact, full_match=True):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.CLARIFY, use_rag=False, confidence=0.92,
            reason="ambiguous_reference", need_clarification=True,
            clarification_question="请说明你指的对象，并补充具体问题或现象。", slots=slots,
        ), True)

    if _contains_any(compact, KNOWLEDGE_TERMS):
        return SemanticRouteResult(RouteDecision(
            route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, confidence=0.94,
            reason="knowledge_signal", query_rewrite=raw_text, slots=slots,
        ), True)

    return SemanticRouteResult(RouteDecision(
        route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, confidence=0.0,
        reason="semantic_router_no_match", query_rewrite=raw_text, slots=slots,
    ), False)
