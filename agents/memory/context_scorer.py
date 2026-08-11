import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


CONTEXT_CUE_RE = re.compile(
    r"上面|上边|上文|前面|之前|刚才|刚刚|上一(个|条|轮)|前一(个|条|轮)|"
    r"这|那|它|其|这个|那个|该问题|此问题|这个故障|那个故障|继续|接着|再说|重新回答|重新说|详细说|展开"
)
TOPIC_SWITCH_RE = re.compile(r"另一个|换个|新的|新问题|不是这个|不是上面|先不说|另外|重新开始")
ERROR_CODE_RE = re.compile(r"\b(?:[A-Z]{1,6}[-_]?\d{2,}|E\d{2,}|ERR[-_]?[A-Z0-9]+)\b", re.IGNORECASE)
DEVICE_RE = re.compile(
    r"DNBSEQ|DL[-_]?T7|G50|ECR|Q30|FIT|NSB|SBC|DNQ|PLC|CNC|"
    r"测序仪|清洗机|洗衣机|洗碗机|烘干机|干衣机|笔记本|电脑|电视|耳机|设备|仪器",
    re.IGNORECASE,
)
COMPONENT_RE = re.compile(
    r"清洗泵|注射泵|泵|阀|管路|相机|温控|电源|电机|传感器|芯片|试剂|barcode|"
    r"激光|主板|屏幕|电池|压缩机|排水泵",
    re.IGNORECASE,
)
SYMPTOM_RE = re.compile(
    r"报警|报错|异常|失败|超时|堵塞|漏液|漏水|不启动|无法启动|不工作|卡顿|噪音|"
    r"压力|温度|信号|断开|连接不上|脱水|不排水|不加热",
    re.IGNORECASE,
)

FOCUS_RULES = (
    ("solution", r"解决方案|解决办法|处理方法|怎么解决|怎么处理|怎么办|修复|恢复"),
    ("reason", r"原因|为什么|根因|成因"),
    ("troubleshooting", r"排查|检查步骤|操作步骤|步骤|怎么查|定位"),
    ("summary", r"总结|复盘|概括|简述"),
    ("image", r"图片|图|照片|示意|截图"),
    ("parameter", r"参数|阈值|范围|指标|多少"),
)

CONFIRMED_FACT_RE = re.compile(r"已经|已|确认|测得|发现|出现|复现|检查过|试过")
EXCLUDED_CAUSE_RE = re.compile(r"没有|没|不是|排除|未发现|无.*异常|不堵|没堵")
LOW_VALUE_RE = re.compile(r"^(你好|您好|谢谢|多谢|感谢|好的?|收到|明白|ok|okay|没事|再见|拜拜)$", re.IGNORECASE)


@dataclass
class ContextAnalysis:
    action: str
    score: float
    last_focus: str = ""
    reason: str = ""
    signals: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    current_slots: Dict[str, str] = field(default_factory=dict)
    slot_confidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    working_memory_delta: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def should_continue(self) -> bool:
        return self.action == "continue"

    @property
    def should_switch(self) -> bool:
        return self.action == "switch"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "score": round(self.score, 3),
            "last_focus": self.last_focus,
            "reason": self.reason,
            "signals": list(self.signals),
            "conflicts": list(self.conflicts),
            "current_slots": dict(self.current_slots),
            "slot_confidence": dict(self.slot_confidence or {}),
            "working_memory_delta": {
                key: list(value) for key, value in (self.working_memory_delta or {}).items()
            },
        }


def slots_from_decision(decision: Any) -> Dict[str, str]:
    slots = getattr(decision, "slots", None)
    if not slots:
        return {}
    if hasattr(slots, "model_dump"):
        raw_slots = slots.model_dump(exclude_none=True)
    elif hasattr(slots, "dict"):
        raw_slots = slots.dict(exclude_none=True)
    elif isinstance(slots, dict):
        raw_slots = slots
    else:
        return {}
    return {
        key: str(value).strip()
        for key, value in raw_slots.items()
        if value is not None and str(value).strip()
    }


def analyze_context_transition(
    current_text: str,
    active_context: Optional[Dict[str, Any]] = None,
    decision_slots: Optional[Dict[str, str]] = None,
    retrieval_query: str = "",
) -> ContextAnalysis:
    text = str(current_text or "").strip()
    context = active_context or {}
    current_slots = _extract_current_slots(text, retrieval_query, decision_slots or {})
    active_slots = _active_context_slots(context)
    last_focus = _detect_focus(text)
    working_delta = _extract_working_memory_delta(text)

    if not context:
        return ContextAnalysis(
            action="new",
            score=0.0,
            last_focus=last_focus,
            signals=["no_active_context"],
            current_slots=current_slots,
            working_memory_delta=working_delta,
        )

    compact = _compact(text)
    if not compact or LOW_VALUE_RE.fullmatch(compact):
        return ContextAnalysis(
            action="continue",
            score=0.35,
            last_focus=last_focus,
            signals=["low_value_message_keeps_context"],
            current_slots=current_slots,
            working_memory_delta=working_delta,
        )

    score = 0.2
    signals: List[str] = ["has_active_context"]
    conflicts: List[str] = []

    if CONTEXT_CUE_RE.search(compact):
        score += 0.35
        signals.append("contextual_cue")
    if TOPIC_SWITCH_RE.search(compact):
        score -= 0.45
        conflicts.append("topic_switch_cue")

    slot_hits, slot_conflicts = _compare_slots(active_slots, current_slots)
    if slot_hits:
        score += min(0.3, 0.12 * len(slot_hits))
        signals.extend(f"slot_overlap:{slot}" for slot in slot_hits)
    if slot_conflicts:
        score -= min(0.5, 0.18 * len(slot_conflicts))
        conflicts.extend(f"slot_conflict:{slot}" for slot in slot_conflicts)

    if last_focus and not _has_strong_new_anchor(current_slots, active_slots):
        score += 0.12
        signals.append(f"focus_followup:{last_focus}")
    has_working_feedback = bool(
        working_delta.get("confirmed_facts") or working_delta.get("excluded_causes")
    )
    if has_working_feedback and not _has_strong_new_anchor(current_slots, active_slots):
        score += 0.3
        signals.append("working_memory_update")

    if _text_overlap(context, text, retrieval_query):
        score += 0.18
        signals.append("text_overlap")

    score = max(0.0, min(1.0, score))
    if conflicts and score < 0.45:
        action = "switch"
    elif score >= 0.65 or (has_working_feedback and not conflicts and score >= 0.5):
        action = "continue"
    elif score >= 0.4:
        action = "clarify"
    else:
        action = "switch"

    return ContextAnalysis(
        action=action,
        score=score,
        last_focus=last_focus,
        signals=signals,
        conflicts=conflicts,
        current_slots=current_slots,
        working_memory_delta=working_delta,
    )


def merge_working_memory(
    previous_slots_payload: Optional[Dict[str, Any]],
    analysis: ContextAnalysis,
    max_items: int = 8,
) -> Dict[str, Any]:
    previous = {}
    if isinstance(previous_slots_payload, dict):
        previous = previous_slots_payload.get("_working_memory") or {}
    merged = {
        "confirmed_facts": _as_list(previous.get("confirmed_facts")),
        "excluded_causes": _as_list(previous.get("excluded_causes")),
        "pending_questions": _as_list(previous.get("pending_questions")),
        "suggested_steps": _as_list(previous.get("suggested_steps")),
        "last_focus": analysis.last_focus or previous.get("last_focus") or "",
    }
    if analysis.should_switch:
        merged = {
            "confirmed_facts": [],
            "excluded_causes": [],
            "pending_questions": [],
            "suggested_steps": [],
            "last_focus": analysis.last_focus,
        }
    for key, values in (analysis.working_memory_delta or {}).items():
        if key not in merged:
            continue
        for value in values:
            _append_unique(merged[key], value, max_items=max_items)
    return merged


def build_slot_confidence(slots: Dict[str, str], analysis: ContextAnalysis) -> Dict[str, Dict[str, Any]]:
    confidence: Dict[str, Dict[str, Any]] = {}
    for key, value in {**analysis.current_slots, **slots}.items():
        if not value:
            continue
        score = 0.85 if key in slots else 0.62
        if analysis.should_continue:
            score = min(0.95, score + 0.06)
        if any(conflict.endswith(key) for conflict in analysis.conflicts):
            score = max(0.35, score - 0.25)
        confidence[key] = {"value": value, "confidence": round(score, 2)}
    for key, payload in (analysis.slot_confidence or {}).items():
        if not isinstance(payload, dict):
            continue
        value = str(payload.get("value") or confidence.get(key, {}).get("value") or "").strip()
        if not value:
            continue
        try:
            score = float(payload.get("confidence", confidence.get(key, {}).get("confidence", 0.65)))
        except (TypeError, ValueError):
            score = float(confidence.get(key, {}).get("confidence", 0.65))
        confidence[key] = {
            "value": value,
            "confidence": round(max(0.0, min(1.0, score)), 2),
            "source": payload.get("source") or "llm_context_classifier",
        }
    return confidence


def _extract_current_slots(text: str, retrieval_query: str, decision_slots: Dict[str, str]) -> Dict[str, str]:
    source = " ".join(part for part in [text, retrieval_query] if part)
    slots = dict(decision_slots)
    slots.setdefault("device", _first_match(DEVICE_RE, source))
    slots.setdefault("component", _first_match(COMPONENT_RE, source))
    slots.setdefault("error_code", _first_match(ERROR_CODE_RE, source).upper())
    slots.setdefault("symptom", _first_match(SYMPTOM_RE, source))
    return {key: value for key, value in slots.items() if value}


def _active_context_slots(context: Dict[str, Any]) -> Dict[str, str]:
    slots = context.get("slots") if isinstance(context.get("slots"), dict) else {}
    return {
        "device": str(context.get("active_device") or slots.get("device") or "").strip(),
        "component": str(context.get("active_component") or slots.get("component") or "").strip(),
        "error_code": str(context.get("active_error_code") or slots.get("error_code") or "").strip().upper(),
        "symptom": str(context.get("active_symptom") or slots.get("symptom") or "").strip(),
    }


def _compare_slots(active_slots: Dict[str, str], current_slots: Dict[str, str]) -> tuple[List[str], List[str]]:
    hits: List[str] = []
    conflicts: List[str] = []
    for key in ("device", "component", "error_code"):
        active_value = _normalize_slot(active_slots.get(key))
        current_value = _normalize_slot(current_slots.get(key))
        if not active_value or not current_value:
            continue
        if active_value == current_value or active_value in current_value or current_value in active_value:
            hits.append(key)
        elif key in {"device", "error_code"}:
            conflicts.append(key)
    return hits, conflicts


def _detect_focus(text: str) -> str:
    compact = _compact(text)
    for focus, pattern in FOCUS_RULES:
        if re.search(pattern, compact):
            return focus
    return ""


def _extract_working_memory_delta(text: str) -> Dict[str, List[str]]:
    compact_text = _clean_for_memory(text)
    if not compact_text:
        return {}
    delta: Dict[str, List[str]] = {}
    if CONFIRMED_FACT_RE.search(compact_text):
        delta.setdefault("confirmed_facts", []).append(compact_text)
    if EXCLUDED_CAUSE_RE.search(compact_text):
        delta.setdefault("excluded_causes", []).append(compact_text)
    if "？" in text or "?" in text:
        delta.setdefault("pending_questions", []).append(compact_text)
    return delta


def _text_overlap(context: Dict[str, Any], current_text: str, retrieval_query: str) -> bool:
    active_text = " ".join(
        str(context.get(key) or "")
        for key in ("active_issue", "active_question", "active_query", "active_symptom")
    )
    terms = set(_keywords(active_text))
    current_terms = set(_keywords(" ".join([current_text or "", retrieval_query or ""])))
    return bool(terms and current_terms and len(terms.intersection(current_terms)) >= 2)


def _keywords(text: str) -> List[str]:
    text = re.sub(r"[，。！？、；：,.!?;:()\[\]【】\"'“”‘’]", " ", str(text or ""))
    values = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", text)
    stop_words = {"这个", "那个", "问题", "故障", "怎么", "处理", "解决", "原因", "步骤"}
    return [value.lower() for value in values if value and value not in stop_words][:50]


def _has_strong_new_anchor(current_slots: Dict[str, str], active_slots: Dict[str, str]) -> bool:
    for key in ("device", "error_code"):
        current_value = _normalize_slot(current_slots.get(key))
        active_value = _normalize_slot(active_slots.get(key))
        if current_value and active_value and current_value != active_value:
            return True
    return False


def _first_match(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text or "")
    return match.group(0).strip() if match else ""


def _normalize_slot(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _clean_for_memory(text: str, max_chars: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    text = re.sub(r"^[,，。！？!?\s]+|[,，。！？!?\s]+$", "", text)
    return text[:max_chars]


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value:
        return [str(value)]
    return []


def _append_unique(items: List[str], value: str, max_items: int) -> None:
    value = str(value or "").strip()
    if not value or value in items:
        return
    items.append(value)
    if len(items) > max_items:
        del items[:-max_items]
