import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _clean_text(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _truncate_text(text: Any, max_chars: int) -> str:
    text = _clean_text(text)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _memory_item_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "title", "question", "value"):
            text = _clean_text(item.get(key))
            if text:
                return text
        if item.get("doc_id") is not None:
            return f"{item.get('library_type') or 'breakdown'}:{item.get('doc_id')}"
        return ""
    return _clean_text(item)


def _memory_item_status(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("status") or "active")
    return "active"


def _memory_item_suffix(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    suffixes = []
    confidence = item.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = None
    if confidence_value is not None:
        suffixes.append(f"conf={confidence_value:.2f}")
    if item.get("source"):
        suffixes.append(f"source={item.get('source')}")
    if item.get("source_turn_id") is not None:
        suffixes.append(f"turn={item.get('source_turn_id')}")
    if not suffixes:
        return ""
    return " (" + ", ".join(suffixes[:3]) + ")"


def _memory_values(values: Any, statuses: Optional[set[str]] = None) -> List[Any]:
    if isinstance(values, list):
        items = values
    elif values:
        items = [values]
    else:
        return []
    if statuses is None:
        statuses = {"active", "open", "proposed", "candidate"}
    return [item for item in items if _memory_item_status(item) in statuses and _memory_item_text(item)]


def _message_role_label(message: Any) -> str:
    return "用户" if int(getattr(message, "role", 0) or 0) == 1 else "AI助手"


def _message_to_dict(message: Any, max_chars: int = 360) -> Dict[str, Any]:
    return {
        "id": getattr(message, "id", None),
        "message_order": getattr(message, "message_order", None),
        "role": int(getattr(message, "role", 0) or 0),
        "role_label": _message_role_label(message),
        "content_text": _truncate_text(getattr(message, "content_text", ""), max_chars),
        "has_uploaded_images": bool(str(getattr(message, "user_uploaded_images", "") or "").strip()),
        "has_reference_docs": bool(str(getattr(message, "ai_reference_doc_ids", "") or "").strip()),
    }


def _message_to_prompt_line(message: Any, max_chars: int = 360) -> str:
    text = _truncate_text(getattr(message, "content_text", ""), max_chars)
    if not text or text == "回答生成中，请稍后刷新。":
        return ""
    suffixes = []
    if str(getattr(message, "user_uploaded_images", "") or "").strip():
        suffixes.append("含用户图片")
    if str(getattr(message, "ai_reference_doc_ids", "") or "").strip():
        suffixes.append("含参考文档")
    suffix = f"（{'; '.join(suffixes)}）" if suffixes else ""
    return f"{_message_role_label(message)}：{text}{suffix}"


def _context_event_to_dict(event: Any) -> Dict[str, Any]:
    created_time = getattr(event, "created_time", None)
    return {
        "event_type": getattr(event, "event_type", None),
        "route": getattr(event, "route", None),
        "reason": getattr(event, "reason", None),
        "source": getattr(event, "source", None),
        "created_time": created_time.isoformat() if created_time else None,
    }


def _context_event_to_prompt_line(event: Any) -> str:
    event_type = str(getattr(event, "event_type", "") or "").strip()
    if not event_type:
        return ""
    route = str(getattr(event, "route", "") or "").strip()
    reason = str(getattr(event, "reason", "") or "").strip()
    labels = [event_type]
    if route:
        labels.append(f"route={route}")
    if reason:
        labels.append(f"reason={reason}")
    return "- " + "，".join(labels)


@dataclass
class AdaptiveRagPlan:
    strategy: str
    complexity: str
    top_k: int = -1
    top_k_documents: int = -1
    iterative_retrieval: bool = False
    image_retrieval: bool = False
    reason: str = ""
    actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "complexity": self.complexity,
            "top_k": self.top_k,
            "top_k_documents": self.top_k_documents,
            "iterative_retrieval": self.iterative_retrieval,
            "image_retrieval": self.image_retrieval,
            "reason": self.reason,
            "actions": list(self.actions),
        }


@dataclass
class MemoryPack:
    session_id: int
    route: str
    reason: str
    dialog_act: Optional[str] = None
    target: Optional[str] = None
    active_context: Optional[Dict[str, Any]] = None
    recent_messages: List[Any] = field(default_factory=list)
    recent_traces: List[Any] = field(default_factory=list)
    recent_context_events: List[Any] = field(default_factory=list)
    summary: Optional[str] = None
    context_analysis: Optional[Dict[str, Any]] = None
    working_memory: Dict[str, Any] = field(default_factory=dict)
    slot_confidence: Dict[str, Any] = field(default_factory=dict)
    adaptive_rag: AdaptiveRagPlan = field(
        default_factory=lambda: AdaptiveRagPlan(strategy="direct", complexity="simple")
    )
    actions: List[str] = field(default_factory=list)

    @property
    def strategy(self) -> str:
        return self.adaptive_rag.strategy

    @property
    def complexity(self) -> str:
        return self.adaptive_rag.complexity

    @property
    def has_memory(self) -> bool:
        return bool(
            self.active_context
            or self.recent_messages
            or self.recent_traces
            or self.recent_context_events
            or self.summary
            or self.context_analysis
            or self.working_memory
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "route": self.route,
            "reason": self.reason,
            "dialog_act": self.dialog_act,
            "target": self.target,
            "active_context": self.active_context,
            "recent_messages": [_message_to_dict(message) for message in self.recent_messages or []],
            "recent_trace_count": len(self.recent_traces or []),
            "recent_context_events": [
                _context_event_to_dict(event) for event in self.recent_context_events or []
            ],
            "summary": self.summary,
            "context_analysis": self.context_analysis,
            "working_memory": self.working_memory,
            "slot_confidence": self.slot_confidence,
            "adaptive_rag": self.adaptive_rag.to_dict(),
            "actions": list(self.actions),
        }

    def to_prompt(
        self,
        include_recent_messages: bool = True,
        include_context_events: bool = False,
        max_recent_messages: int = 6,
        max_message_chars: int = 360,
        max_reference_docs: int = 3,
    ) -> str:
        parts: List[str] = []

        summary = _clean_text(self.summary)
        if summary:
            parts.append(f"【长对话摘要】\n{summary}")

        context = self.active_context or {}
        context_lines = []
        context_fields = [
            ("当前主问题", context.get("active_issue")),
            ("当前设备", context.get("active_device")),
            ("当前部件", context.get("active_component")),
            ("当前故障现象", context.get("active_symptom")),
            ("当前报错码", context.get("active_error_code")),
            ("上一轮检索问题", context.get("active_query")),
        ]
        for label, value in context_fields:
            value = _clean_text(value)
            if value:
                context_lines.append(f"{label}：{value}")

        reference_docs = context.get("active_reference_docs") or []
        doc_lines = []
        for doc in reference_docs[:max_reference_docs]:
            if not isinstance(doc, dict) or doc.get("doc_id") is None:
                continue
            library_type = str(doc.get("library_type") or "breakdown")
            title = _truncate_text(doc.get("title") or "未命名文档", 80)
            doc_lines.append(f"- {library_type}:{doc.get('doc_id')}《{title}》")
        if doc_lines:
            context_lines.append("已关联参考文档：")
            context_lines.extend(doc_lines)
        if context_lines:
            parts.append("【当前会话主题】\n" + "\n".join(context_lines))

        working_memory = self.working_memory or context.get("working_memory") or {}
        working_lines = []
        focus = _clean_text(working_memory.get("last_focus"))
        if focus:
            working_lines.append(f"当前回答焦点：{focus}")
        working_sections = [
            ("已确认事实", working_memory.get("confirmed_facts")),
            ("已排除原因", working_memory.get("excluded_causes")),
            ("待补充问题", working_memory.get("pending_questions")),
            ("已建议步骤", working_memory.get("suggested_steps")),
        ]
        for label, values in working_sections:
            values = _memory_values(values)
            values = [
                _truncate_text(_memory_item_text(value), 120) + _memory_item_suffix(value)
                for value in values
            ]
            if values:
                working_lines.append(f"{label}：")
                working_lines.extend(f"- {value}" for value in values[-5:])
        candidate_values = _memory_values(working_memory.get("candidate_facts"))
        if candidate_values:
            working_lines.append("Candidate facts awaiting confirmation:")
            working_lines.extend(
                "- " + _truncate_text(_memory_item_text(value), 120) + _memory_item_suffix(value)
                for value in candidate_values[-5:]
            )

        retrieval_meta = working_memory.get("_retrieval") if isinstance(working_memory, dict) else None
        if isinstance(retrieval_meta, dict):
            query = _truncate_text(retrieval_meta.get("query"), 120)
            if query:
                working_lines.append(f"Memory retrieval query: {query}")

        cited_docs = working_memory.get("cited_docs") or []
        cited_lines = []
        for doc in cited_docs[-5:] if isinstance(cited_docs, list) else []:
            if not isinstance(doc, dict) or doc.get("doc_id") is None:
                continue
            if _memory_item_status(doc) not in {"active", "candidate", "open", "proposed"}:
                continue
            title = _truncate_text(doc.get("title") or "未命名文档", 80)
            section_titles = doc.get("evidence_section_titles") or []
            section_suffix = ""
            if section_titles:
                section_suffix = "；章节：" + "、".join(
                    _truncate_text(title, 40) for title in section_titles[:3] if _clean_text(title)
                )
            cited_lines.append(
                f"- {doc.get('library_type') or 'breakdown'}:{doc.get('doc_id')}《{title}》{section_suffix}"
            )
        if cited_lines:
            working_lines.append("本轮引用文档：")
            working_lines.extend(cited_lines)
        if working_lines:
            parts.append("【维修工作记忆】\n" + "\n".join(working_lines))

        analysis = self.context_analysis or context.get("context_analysis") or {}
        if analysis:
            action = analysis.get("action")
            action_label = {
                "new": "新建会话主题",
                "continue": "延续当前主题",
                "clarify": "上下文不确定",
                "switch": "可能切换新主题",
            }.get(str(action or ""), str(action or "未知"))
            analysis_lines = [f"判断：{action_label}"]
            if analysis.get("score") is not None:
                analysis_lines.append(f"匹配分：{analysis.get('score')}")
            if analysis.get("last_focus"):
                analysis_lines.append(f"回答焦点：{analysis.get('last_focus')}")
            parts.append("【本轮上下文判断】\n" + "\n".join(analysis_lines))

        if include_recent_messages and self.recent_messages:
            recent_lines = [
                line
                for line in (
                    _message_to_prompt_line(message, max_chars=max_message_chars)
                    for message in (self.recent_messages or [])[-max_recent_messages:]
                )
                if line
            ]
            if recent_lines:
                parts.append("【最近对话】\n" + "\n".join(recent_lines))

        if include_context_events and self.recent_context_events:
            event_lines = [
                line
                for line in (
                    _context_event_to_prompt_line(event)
                    for event in (self.recent_context_events or [])[-5:]
                )
                if line
            ]
            if event_lines:
                parts.append("【最近上下文事件】\n" + "\n".join(event_lines))

        return "\n\n".join(parts).strip()

    def to_trace_validation(self) -> Dict[str, Any]:
        return {
            "memory_strategy": self.strategy,
            "memory_complexity": self.complexity,
            "dialog_act": self.dialog_act,
            "target": self.target,
            "memory_actions": list(self.actions),
            "has_active_context": bool(self.active_context),
            "has_summary": bool(self.summary),
            "has_short_term_memory": self.has_memory,
            "recent_message_count": len(self.recent_messages or []),
            "recent_trace_count": len(self.recent_traces or []),
            "recent_context_event_count": len(self.recent_context_events or []),
            "context_action": (self.context_analysis or {}).get("action"),
            "context_score": (self.context_analysis or {}).get("score"),
            "last_focus": (self.context_analysis or {}).get("last_focus")
            or (self.working_memory or {}).get("last_focus"),
            "working_memory_counts": {
                key: len(value or [])
                for key, value in (self.working_memory or {}).items()
                if isinstance(value, list)
            },
            "adaptive_rag": self.adaptive_rag.to_dict(),
        }
