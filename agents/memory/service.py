import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.intent import RouteDecision
from .context_scorer import (
    ContextAnalysis,
    analyze_context_transition as analyze_context_transition_heuristic,
    build_slot_confidence,
    merge_working_memory,
)
from models import (
    AiUsageLog,
    AiMessageTrace,
    ConversationContext,
    ConversationContextEvent,
    ConversationSummary,
    Message,
)
from utils.desensitize import desensitize_value


ANSWER_AUDIT_ROUTE = "answer_audit"
CONTEXTUAL_FOLLOWUP_REASON = "contextual_followup"
CONTEXTUAL_RETRY_REASON = "contextual_retry"
TRACE_PREVIEW_MAX_CHARS = 1200
MEMORY_REFINEMENT_MAX_ITEMS = 8
MEMORY_RECORD_MAX_ITEMS = 24
MEMORY_ACTIVE_STATUSES = {"active", "open", "proposed", "candidate"}
MEMORY_RENDERABLE_STATUSES = MEMORY_ACTIVE_STATUSES
MEMORY_TEXT_KEYS = (
    "confirmed_facts",
    "excluded_causes",
    "candidate_facts",
    "pending_questions",
    "suggested_steps",
)
MEMORY_RECORD_KEYS = MEMORY_TEXT_KEYS + ("cited_docs",)
MEMORY_CONFLICT_PAIRS = {
    "confirmed_facts": ("excluded_causes",),
    "excluded_causes": ("confirmed_facts",),
}
MEMORY_CATEGORY_DEFAULTS = {
    "confirmed_facts": {"confidence": 0.86, "status": "active", "ttl_turns": 24},
    "excluded_causes": {"confidence": 0.88, "status": "active", "ttl_turns": 24},
    "candidate_facts": {"confidence": 0.58, "status": "candidate", "ttl_turns": 8},
    "pending_questions": {"confidence": 0.78, "status": "open", "ttl_turns": 10},
    "suggested_steps": {"confidence": 0.64, "status": "proposed", "ttl_turns": 12},
    "cited_docs": {"confidence": 0.9, "status": "active", "ttl_turns": 16},
}
MEMORY_CODE_RE = re.compile(r"\b[A-Z]{1,8}[-_]?\d{2,}\b", re.IGNORECASE)
MEMORY_KEYWORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]+")
MEMORY_LOW_SIGNAL_RE = re.compile(
    r"^(?:ok|okay|thanks|thank you|yes|no|hello|hi|"
    r"\u597d\u7684|\u6536\u5230|\u660e\u767d|\u8c22\u8c22|\u4f60\u597d|\u55ef|\u662f\u7684)$",
    re.IGNORECASE,
)
MEMORY_UNCERTAIN_RE = re.compile(
    r"maybe|might|could|possibly|likely|suspect|probable|recommend|"
    r"\u53ef\u80fd|\u7591\u4f3c|\u6000\u7591|\u8003\u8651|\u5efa\u8bae|"
    r"\u5c1d\u8bd5|\u6216\u8bb8|\u5927\u6982|\u5e94\u8be5",
    re.IGNORECASE,
)
CONTEXT_CLASSIFIER_ACTIONS = {"continue", "switch", "clarify"}
CONTEXT_CLASSIFIER_SYSTEM_PROMPT = """你是维修问答系统的轻量上下文分类器。只输出一个 JSON 对象，不要输出 Markdown。

根据当前会话主题、启发式判断和用户当前输入，判断本轮是否延续当前主题、切换新主题，还是需要澄清。

JSON 字段：
- action: continue、switch、clarify 三选一。
- score: 0 到 1，表示判断置信度。
- reason: 一句话说明判断依据。
- slots: 对 device、component、error_code、symptom、metric 的结构化判断；每个槽位形如 {"value": "...", "confidence": 0.0}，未知可省略。

不要编造设备、部件、报错码或用户没有提供的信息。"""

SUGGESTED_STEP_RE = re.compile(
    r"检查|确认|更换|重启|复位|清洁|清理|测量|观察|连接|断电|重新|排查|建议|需要|打开|关闭|插拔|记录|采集"
)
PENDING_QUESTION_RE = re.compile(
    r"请补充|需要补充|建议补充|请确认|需要确认|还需确认|是否|有没有|哪一|什么|多少|版本|型号|照片|截图"
)
CONFIRMED_ANSWER_RE = re.compile(r"当前问题|故障现象|问题理解|报错|报警|异常|已确认|确认|表现为|现象为")
EXCLUDED_ANSWER_RE = re.compile(r"排除|不是|未发现|未检出|无.*异常|没有|没")


def get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def get_positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def json_dumps(value: Any) -> str:
    return json.dumps(desensitize_value(value), ensure_ascii=False)


def json_loads(value: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def answer_preview(answer: str) -> str:
    return str(answer or "").strip()[:TRACE_PREVIEW_MAX_CHARS]


def normalize_reference_image_values(raw_value: Any) -> List[str]:
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        try:
            raw_value = json.loads(raw_value)
        except Exception:
            raw_value = raw_value.split(",")
    if isinstance(raw_value, dict):
        raw_value = raw_value.values()
    elif not isinstance(raw_value, (list, tuple, set)):
        raw_value = [raw_value]

    images: List[str] = []
    for item in raw_value:
        text = str(item or "").strip()
        if text and text not in images:
            images.append(text)
    return images


def normalize_stored_reference_chunk(chunk: Any) -> Dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    normalized = dict(chunk)
    if not normalized.get("content") and normalized.get("preview"):
        normalized["content"] = normalized.get("preview")
    return normalized


def normalize_stored_reference_doc(doc: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(doc, dict) or doc.get("doc_id") is None:
        return None
    try:
        doc_id = int(doc.get("doc_id"))
    except (TypeError, ValueError):
        return None

    image_urls = doc.get("image_urls") or doc.get("reference_images") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    raw_chunks = doc.get("chunks") or [
        {"preview": preview}
        for preview in (doc.get("chunk_previews") or [])
        if preview
    ]

    return {
        "doc_id": doc_id,
        "library_type": normalize_library_type(doc.get("library_type", "breakdown")),
        "title": doc.get("title", ""),
        "score": float(doc.get("score", 0.0) or 0.0),
        "chunks": [
            chunk
            for chunk in (
                normalize_stored_reference_chunk(chunk)
                for chunk in raw_chunks
            )
            if chunk
        ],
        "matched_image_urls": doc.get("matched_image_urls") or image_urls,
        "evidence_image_urls": doc.get("evidence_image_urls") or image_urls,
        "image_urls": image_urls,
        "evidence_section_ids": doc.get("evidence_section_ids") or [],
        "evidence_section_titles": doc.get("evidence_section_titles") or [],
        "reused_from_history": True,
    }


def parse_reference_documents_payload(raw_payload: Any) -> List[Dict[str, Any]]:
    payload = json_loads(raw_payload, [])
    if not isinstance(payload, list):
        return []
    reference_docs = []
    for item in payload:
        normalized = normalize_stored_reference_doc(item)
        if normalized:
            reference_docs.append(normalized)
    return reference_docs


def reference_docs_for_trace(reference_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    docs = []
    for doc in reference_docs or []:
        if doc.get("doc_id") is None:
            continue
        docs.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": normalize_library_type(doc.get("library_type", "breakdown")),
            "title": doc.get("title", ""),
            "score": float(doc.get("score", 0.0) or 0.0),
            "reused_from_history": bool(doc.get("reused_from_history")),
            "evidence_section_titles": doc.get("evidence_section_titles") or [],
            "chunk_previews": [
                str(chunk.get("content") or chunk.get("preview") or "")[:180]
                for chunk in (doc.get("chunks") or [])[:3]
                if isinstance(chunk, dict)
            ],
        })
    return docs


def reference_images_for_trace(reference_docs: List[Dict[str, Any]]) -> List[str]:
    images: List[str] = []
    for doc in reference_docs or []:
        for raw_value in (
            doc.get("evidence_image_urls"),
            doc.get("image_urls"),
            doc.get("matched_image_urls"),
        ):
            for image in normalize_reference_image_values(raw_value):
                if image and image not in images:
                    images.append(image)
        for chunk in (doc.get("chunks") or [])[:5]:
            if isinstance(chunk, dict):
                for image in normalize_reference_image_values(chunk.get("image_url")):
                    if image and image not in images:
                        images.append(image)
    return images[:10]


def route_value(decision: RouteDecision) -> str:
    return getattr(getattr(decision, "route", None), "value", str(getattr(decision, "route", "")))


def slots_to_dict(decision: RouteDecision) -> Dict[str, Any]:
    slots = getattr(decision, "slots", None)
    if not slots:
        return {}
    if hasattr(slots, "model_dump"):
        return slots.model_dump(exclude_none=True)
    if hasattr(slots, "dict"):
        return slots.dict(exclude_none=True)
    if isinstance(slots, dict):
        return {key: value for key, value in slots.items() if value is not None}
    return {}


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def safe_json_object_from_text(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        return {}
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            payload = json.loads(text[start:end + 1])
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def normalize_memory_text(text: Any, max_chars: int = 180) -> str:
    if isinstance(text, dict):
        text = memory_record_text(text)
    text = re.sub(r"\s+", " ", str(text or "").strip())
    text = re.sub(r"^[\-\*\d\.\)、）\s]+", "", text).strip()
    return text[:max_chars].rstrip()


def append_unique_memory_item(items: List[Any], value: Any, max_items: int = MEMORY_REFINEMENT_MAX_ITEMS) -> None:
    if isinstance(value, dict):
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        clean_value = value
    else:
        clean_text = normalize_memory_text(value)
        if not clean_text:
            return
        marker = clean_text
        clean_value = clean_text
    existing_markers = {
        json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        for item in items
    }
    if marker in existing_markers:
        return
    items.append(clean_value)
    if len(items) > max_items:
        del items[:-max_items]


def as_memory_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return [item for item in value if item]
    if value:
        return [value]
    return []


def clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def current_memory_time() -> str:
    return datetime.now().isoformat()


def memory_category_defaults(category: str) -> Dict[str, Any]:
    return dict(MEMORY_CATEGORY_DEFAULTS.get(category, {"confidence": 0.6, "status": "active", "ttl_turns": 12}))


def memory_record_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "title", "question", "value"):
            value = normalize_memory_text(item.get(key), 260)
            if value:
                return value
        if item.get("doc_id") is not None:
            return normalize_memory_text(f"{item.get('library_type') or 'breakdown'}:{item.get('doc_id')}", 260)
        return normalize_memory_text(json.dumps(item, ensure_ascii=False, sort_keys=True), 260)
    return normalize_memory_text(item, 260)


def memory_doc_ids(item: Any) -> List[int]:
    raw_values: List[Any] = []
    if isinstance(item, dict):
        raw_values.extend(as_memory_list(item.get("doc_ids")))
        if item.get("doc_id") is not None:
            raw_values.append(item.get("doc_id"))
    doc_ids: List[int] = []
    for value in raw_values:
        try:
            doc_id = int(value)
        except (TypeError, ValueError):
            continue
        if doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def memory_record_status(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("status") or "active")
    return "active"


def memory_record_confidence(item: Any, category: str = "") -> float:
    defaults = memory_category_defaults(category)
    if isinstance(item, dict):
        return clamp_float(item.get("confidence"), float(defaults.get("confidence", 0.6)))
    return float(defaults.get("confidence", 0.6))


def memory_record_turn(item: Any) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    for key in ("last_seen_turn_id", "source_turn_id", "created_turn_id"):
        try:
            value = int(item.get(key))
        except (TypeError, ValueError):
            continue
        return value
    return None


def memory_keywords(text: str) -> List[str]:
    values: List[str] = []
    for token in MEMORY_KEYWORD_RE.findall(str(text or "")):
        token = token.lower().strip()
        if not token:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            values.extend(token[index:index + 2] for index in range(0, len(token) - 1))
        else:
            values.append(token)
    stop_words = {
        "the", "and", "for", "with", "this", "that", "issue", "problem",
        "\u95ee\u9898", "\u6545\u969c", "\u600e\u4e48", "\u5904\u7406", "\u89e3\u51b3",
    }
    deduped: List[str] = []
    for value in values:
        if value in stop_words or value in deduped:
            continue
        deduped.append(value)
    return deduped[:80]


def memory_text_similarity(left: Any, right: Any) -> float:
    left_text = memory_record_text(left)
    right_text = memory_record_text(right)
    if not left_text or not right_text:
        return 0.0
    left_compact = compact_text(left_text).lower()
    right_compact = compact_text(right_text).lower()
    if left_compact == right_compact:
        return 1.0
    if len(left_compact) >= 6 and len(right_compact) >= 6 and (
        left_compact in right_compact or right_compact in left_compact
    ):
        return 0.86
    left_terms = set(memory_keywords(left_text))
    right_terms = set(memory_keywords(right_text))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms.intersection(right_terms)) / max(1, len(left_terms.union(right_terms)))


def is_low_value_memory_text(text: str) -> bool:
    compact = compact_text(text)
    if not compact:
        return True
    if MEMORY_LOW_SIGNAL_RE.fullmatch(compact):
        return True
    if len(compact) < 4 and not MEMORY_CODE_RE.search(compact):
        return True
    return False


def should_store_memory_record(category: str, record: Dict[str, Any]) -> bool:
    text = memory_record_text(record)
    if is_low_value_memory_text(text):
        return False
    confidence = memory_record_confidence(record, category)
    threshold = get_positive_float_env("MEMORY_WRITE_CONFIDENCE_THRESHOLD", 0.58)
    if category in {"pending_questions", "suggested_steps", "candidate_facts"}:
        threshold = min(threshold, 0.5)
    return confidence >= threshold


def make_memory_record(
    category: str,
    value: Any,
    *,
    confidence: Optional[float] = None,
    status: Optional[str] = None,
    source: str = "memory_refinement",
    source_turn_id: Optional[int] = None,
    source_ai_message_id: Optional[int] = None,
    source_trace_id: Optional[int] = None,
    doc_ids: Optional[List[int]] = None,
    now_iso: Optional[str] = None,
    ttl_turns: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    defaults = memory_category_defaults(category)
    now_iso = now_iso or current_memory_time()
    record: Dict[str, Any] = {}
    if category == "cited_docs" and isinstance(value, dict):
        text = normalize_memory_text(value.get("title") or value.get("text") or "", 220)
        record.update(
            {
                "doc_id": value.get("doc_id"),
                "library_type": value.get("library_type") or "breakdown",
                "title": text,
                "evidence_section_titles": value.get("evidence_section_titles") or [],
                "score": float(value.get("score", 0.0) or 0.0),
            }
        )
        if record.get("doc_id") is None:
            return None
    else:
        text = memory_record_text(value)
        if not text:
            return None
        record["text"] = text

    merged_doc_ids = memory_doc_ids(value)
    for doc_id in doc_ids or []:
        try:
            normalized_doc_id = int(doc_id)
        except (TypeError, ValueError):
            continue
        if normalized_doc_id not in merged_doc_ids:
            merged_doc_ids.append(normalized_doc_id)

    record.update(
        {
            "confidence": round(
                clamp_float(confidence, float(defaults.get("confidence", 0.6))),
                2,
            ),
            "status": status or str(defaults.get("status") or "active"),
            "source": source,
            "source_turn_id": source_turn_id,
            "source_ai_message_id": source_ai_message_id,
            "source_trace_id": source_trace_id,
            "doc_ids": merged_doc_ids,
            "created_time": now_iso,
            "last_seen": now_iso,
            "last_seen_turn_id": source_turn_id,
            "ttl_turns": int(ttl_turns or defaults.get("ttl_turns") or 12),
        }
    )
    return record if should_store_memory_record(category, record) else None


def normalize_memory_record(category: str, item: Any) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    defaults = memory_category_defaults(category)
    now_iso = current_memory_time()
    if isinstance(item, dict):
        record = dict(item)
        if category == "cited_docs":
            if record.get("doc_id") is None:
                return None
            record.setdefault("title", normalize_memory_text(record.get("text") or record.get("title"), 220))
            record.setdefault("library_type", "breakdown")
        else:
            text = memory_record_text(record)
            if not text:
                return None
            record["text"] = text
        record["confidence"] = round(
            clamp_float(record.get("confidence"), float(defaults.get("confidence", 0.6))),
            2,
        )
        record.setdefault("status", defaults.get("status") or "active")
        record.setdefault("source", "legacy")
        record.setdefault("source_turn_id", None)
        record.setdefault("source_ai_message_id", None)
        record.setdefault("source_trace_id", None)
        record["doc_ids"] = memory_doc_ids(record)
        record.setdefault("created_time", record.get("last_seen") or now_iso)
        record.setdefault("last_seen", record.get("created_time") or now_iso)
        record.setdefault("last_seen_turn_id", record.get("source_turn_id"))
        record.setdefault("ttl_turns", int(defaults.get("ttl_turns") or 12))
        return record if should_store_memory_record(category, record) else None
    return make_memory_record(
        category,
        item,
        confidence=float(defaults.get("confidence", 0.6)),
        status=str(defaults.get("status") or "active"),
        source="legacy",
        now_iso=now_iso,
    )


def normalize_memory_records(category: str, values: Any) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in as_memory_list(values):
        record = normalize_memory_record(category, item)
        if record:
            append_memory_record(records, record, category, max_items=MEMORY_RECORD_MAX_ITEMS)
    return records


def append_memory_record(
    records: List[Dict[str, Any]],
    record: Dict[str, Any],
    category: str,
    max_items: int = MEMORY_RECORD_MAX_ITEMS,
) -> Dict[str, Any]:
    if category == "cited_docs":
        marker = str(record.get("doc_id"))
    else:
        marker = compact_text(memory_record_text(record)).lower()
    for existing in records:
        existing_marker = (
            str(existing.get("doc_id"))
            if category == "cited_docs"
            else compact_text(memory_record_text(existing)).lower()
        )
        if existing_marker != marker:
            continue
        existing["confidence"] = round(
            max(memory_record_confidence(existing, category), memory_record_confidence(record, category)),
            2,
        )
        incoming_status = str(record.get("status") or "")
        if incoming_status in {"active", "open", "proposed", "candidate"}:
            existing["status"] = incoming_status
        existing["last_seen"] = record.get("last_seen") or existing.get("last_seen")
        existing["last_seen_turn_id"] = record.get("last_seen_turn_id") or existing.get("last_seen_turn_id")
        existing["source_turn_id"] = record.get("source_turn_id") or existing.get("source_turn_id")
        existing["source_ai_message_id"] = record.get("source_ai_message_id") or existing.get("source_ai_message_id")
        existing["source_trace_id"] = record.get("source_trace_id") or existing.get("source_trace_id")
        existing["doc_ids"] = sorted(set(memory_doc_ids(existing) + memory_doc_ids(record)))
        return existing
    records.append(record)
    if len(records) > max_items:
        del records[:-max_items]
    return record


def mark_stale_memory_records(records: List[Dict[str, Any]], current_turn_id: Optional[int]) -> None:
    if current_turn_id is None:
        return
    for record in records:
        if memory_record_status(record) not in MEMORY_ACTIVE_STATUSES:
            continue
        last_seen_turn = memory_record_turn(record)
        if last_seen_turn is None:
            continue
        ttl_turns = int(record.get("ttl_turns") or 0)
        if ttl_turns > 0 and current_turn_id - last_seen_turn > ttl_turns:
            record["status"] = "stale"


def append_memory_conflict(
    conflicts: List[Dict[str, Any]],
    *,
    new_category: str,
    new_record: Dict[str, Any],
    old_category: str,
    old_record: Dict[str, Any],
    similarity: float,
    now_iso: str,
) -> None:
    conflict = {
        "new_category": new_category,
        "new_text": memory_record_text(new_record),
        "old_category": old_category,
        "old_text": memory_record_text(old_record),
        "similarity": round(similarity, 3),
        "status": "superseded",
        "source_turn_id": new_record.get("source_turn_id"),
        "created_time": now_iso,
    }
    append_unique_memory_item(conflicts, conflict, max_items=get_positive_int_env("MEMORY_CONFLICT_MAX_ITEMS", 12))


def context_analysis_from_payload(payload: Dict[str, Any], fallback: ContextAnalysis) -> ContextAnalysis:
    if not isinstance(payload, dict):
        return fallback
    action = str(payload.get("action") or "").strip().lower()
    if action not in CONTEXT_CLASSIFIER_ACTIONS:
        return fallback
    try:
        score = float(payload.get("score", fallback.score))
    except (TypeError, ValueError):
        score = fallback.score
    slots_payload = payload.get("slots") if isinstance(payload.get("slots"), dict) else {}
    current_slots = dict(fallback.current_slots or {})
    slot_confidence: Dict[str, Dict[str, Any]] = {}
    for key, raw_value in slots_payload.items():
        if isinstance(raw_value, dict):
            value = str(raw_value.get("value") or "").strip()
            confidence = raw_value.get("confidence", 0.65)
        else:
            value = str(raw_value or "").strip()
            confidence = 0.65
        if not value:
            continue
        current_slots[key] = value
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.65
        slot_confidence[key] = {
            "value": value,
            "confidence": max(0.0, min(1.0, confidence_value)),
            "source": "llm_context_classifier",
        }
    signals = list(fallback.signals or [])
    if "llm_context_classifier" not in signals:
        signals.append("llm_context_classifier")
    reason = normalize_memory_text(payload.get("reason"), 260)
    return ContextAnalysis(
        action=action,
        score=max(0.0, min(1.0, score)),
        last_focus=fallback.last_focus,
        reason=reason,
        signals=signals,
        conflicts=list(fallback.conflicts or []),
        current_slots=current_slots,
        slot_confidence=slot_confidence,
        working_memory_delta=dict(fallback.working_memory_delta or {}),
    )


class MemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def analyze_context_transition(
        self,
        current_text: str,
        active_context: Optional[Dict[str, Any]] = None,
        decision_slots: Optional[Dict[str, str]] = None,
        retrieval_query: str = "",
    ) -> ContextAnalysis:
        heuristic = analyze_context_transition_heuristic(
            current_text=current_text,
            active_context=active_context,
            decision_slots=decision_slots,
            retrieval_query=retrieval_query,
        )
        if not get_bool_env("MEMORY_CONTEXT_CLASSIFIER_LLM_ENABLED", False):
            return heuristic
        if not active_context:
            return heuristic
        try:
            llm_payload = await self._classify_context_with_llm(
                current_text=current_text,
                active_context=active_context,
                decision_slots=decision_slots or {},
                retrieval_query=retrieval_query,
                heuristic=heuristic,
            )
            return context_analysis_from_payload(llm_payload, heuristic)
        except Exception as error:
            print(f"[MemoryService] context classifier fallback: {type(error).__name__}: {error}")
            return heuristic

    async def _classify_context_with_llm(
        self,
        current_text: str,
        active_context: Dict[str, Any],
        decision_slots: Dict[str, str],
        retrieval_query: str,
        heuristic: ContextAnalysis,
    ) -> Dict[str, Any]:
        from openai import AsyncOpenAI
        from utils.ai_endpoint import get_ai_base_url

        timeout = get_positive_float_env("MEMORY_CONTEXT_CLASSIFIER_TIMEOUT", 4.0)
        client = AsyncOpenAI(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY", "EMPTY"),
            timeout=timeout,
        )
        context_payload = {
            "active_issue": active_context.get("active_issue"),
            "active_device": active_context.get("active_device"),
            "active_component": active_context.get("active_component"),
            "active_symptom": active_context.get("active_symptom"),
            "active_error_code": active_context.get("active_error_code"),
            "active_query": active_context.get("active_query"),
            "working_memory": active_context.get("working_memory"),
        }
        response = await client.chat.completions.create(
            model=(
                os.getenv("MEMORY_CONTEXT_CLASSIFIER_MODEL")
                or os.getenv("INTENT_ROUTER_MODEL")
                or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
            ),
            messages=[
                {"role": "system", "content": CONTEXT_CLASSIFIER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "active_context": context_payload,
                            "current_text": current_text,
                            "retrieval_query": retrieval_query,
                            "decision_slots": decision_slots,
                            "heuristic": heuristic.to_dict(),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0,
            max_tokens=get_positive_int_env("MEMORY_CONTEXT_CLASSIFIER_MAX_TOKENS", 180),
        )
        return safe_json_object_from_text(response.choices[0].message.content or "")

    async def load_recent_messages(
        self,
        session_id: int,
        before_order: int,
        limit: int = 6,
    ) -> List[Message]:
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id, Message.message_order < before_order)
            .order_by(desc(Message.message_order))
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    async def load_previous_reference_documents(
        self,
        session_id: int,
        before_order: int,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        history = await self.load_recent_messages(session_id, before_order, limit=limit)
        for message in reversed(history or []):
            if int(getattr(message, "role", 0) or 0) != 0:
                continue
            reference_docs = parse_reference_documents_payload(
                getattr(message, "ai_reference_doc_ids", None)
            )
            if reference_docs:
                return reference_docs
        return []

    async def load_recent_ai_traces(
        self,
        session_id: int,
        limit: int = 6,
        exclude_routes: Optional[Iterable[str]] = None,
    ) -> List[AiMessageTrace]:
        result = await self.db.execute(
            select(AiMessageTrace)
            .where(AiMessageTrace.session_id == session_id)
            .order_by(desc(AiMessageTrace.created_time), desc(AiMessageTrace.id))
            .limit(limit)
        )
        traces = list(result.scalars().all())
        traces.reverse()
        excluded = set(exclude_routes or [])
        if excluded:
            traces = [trace for trace in traces if trace.route not in excluded]
        return traces

    async def get_active_context(self, session_id: int) -> Optional[ConversationContext]:
        result = await self.db.execute(
            select(ConversationContext).where(ConversationContext.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_active_context_dict(self, session_id: int) -> Optional[Dict[str, Any]]:
        context = await self.get_active_context(session_id)
        return self.context_to_dict(context) if context else None

    async def load_recent_context_events(
        self,
        session_id: int,
        limit: int = 5,
    ) -> List[ConversationContextEvent]:
        result = await self.db.execute(
            select(ConversationContextEvent)
            .where(ConversationContextEvent.session_id == session_id)
            .order_by(desc(ConversationContextEvent.created_time), desc(ConversationContextEvent.id))
            .limit(limit)
        )
        events = list(result.scalars().all())
        events.reverse()
        return events

    async def load_latest_summary(self, session_id: int) -> Optional[ConversationSummary]:
        result = await self.db.execute(
            select(ConversationSummary)
            .where(ConversationSummary.session_id == session_id)
            .order_by(desc(ConversationSummary.end_message_order), desc(ConversationSummary.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    def context_to_dict(self, context: ConversationContext) -> Dict[str, Any]:
        slots = json_loads(context.slots_json, {})
        return {
            "session_id": context.session_id,
            "status": context.status,
            "active_issue": context.active_issue,
            "active_device": context.active_device,
            "active_component": context.active_component,
            "active_symptom": context.active_symptom,
            "active_error_code": context.active_error_code,
            "active_question": context.active_question,
            "active_query": context.active_query,
            "active_route": context.active_route,
            "active_reason": context.active_reason,
            "active_reference_docs": json_loads(context.active_reference_docs_json, []),
            "active_reference_images": json_loads(context.active_reference_images_json, []),
            "slots": slots,
            "slot_confidence": slots.get("_slot_confidence", {}) if isinstance(slots, dict) else {},
            "working_memory": slots.get("_working_memory", {}) if isinstance(slots, dict) else {},
            "context_analysis": slots.get("_context_analysis", {}) if isinstance(slots, dict) else {},
            "summary_text": context.summary_text,
            "last_user_message_id": context.last_user_message_id,
            "last_ai_message_id": context.last_ai_message_id,
            "last_trace_id": context.last_trace_id,
            "turn_count": context.turn_count,
            "updated_time": context.updated_time.isoformat() if context.updated_time else None,
        }

    async def create_ai_trace(
        self,
        user_message: Message,
        ai_message: Optional[Message],
        decision: RouteDecision,
        retrieval_query: str,
        reference_docs: List[Dict[str, Any]],
        actions: List[str],
        used_previous_refs: bool = False,
        validation: Optional[Dict[str, Any]] = None,
        answer: str = "",
        status_value: str = "pending",
        error_message: Optional[str] = None,
        model_name: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
    ) -> Optional[int]:
        if not get_bool_env("TRACE_PERSIST_ENABLED", True):
            return None
        try:
            trace = AiMessageTrace(
                session_id=user_message.session_id,
                user_message_id=user_message.id,
                ai_message_id=ai_message.id if ai_message else None,
                route=route_value(decision),
                reason=decision.reason,
                original_question=user_message.content_text,
                query_rewrite=decision.query_rewrite,
                retrieval_query=retrieval_query,
                used_previous_refs=1 if used_previous_refs else 0,
                reference_docs_json=json_dumps(reference_docs_for_trace(reference_docs)),
                reference_images_json=json_dumps(reference_images_for_trace(reference_docs)),
                actions_json=json_dumps(actions or []),
                validation_json=json_dumps(validation or {}),
                answer_preview=answer_preview(answer),
                model_name=model_name or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                status=status_value,
                error_message=(error_message[:1000] if error_message else None),
                created_time=datetime.now(),
                updated_time=datetime.now(),
            )
            self.db.add(trace)
            await self.db.commit()
            await self.db.refresh(trace)
            return trace.id
        except Exception as error:
            await self.db.rollback()
            print(f"[MemoryService] create_ai_trace failed: {type(error).__name__}: {error}")
            return None

    async def update_ai_trace(
        self,
        trace_id: Optional[int],
        answer: str = "",
        status_value: Optional[str] = None,
        error_message: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
    ) -> None:
        if not trace_id or not get_bool_env("TRACE_PERSIST_ENABLED", True):
            return
        try:
            result = await self.db.execute(select(AiMessageTrace).where(AiMessageTrace.id == trace_id))
            trace = result.scalar_one_or_none()
            if not trace:
                return
            if answer:
                trace.answer_preview = answer_preview(answer)
            if status_value:
                trace.status = status_value
            if error_message is not None:
                trace.error_message = error_message[:1000]
            if input_tokens is not None:
                trace.input_tokens = input_tokens
            if output_tokens is not None:
                trace.output_tokens = output_tokens
            if latency_ms is not None:
                trace.latency_ms = latency_ms
            trace.updated_time = datetime.now()
            await self.db.commit()
        except Exception as error:
            await self.db.rollback()
            print(f"[MemoryService] update_ai_trace failed: {type(error).__name__}: {error}")

    async def delete_session_runtime_state(
        self,
        session_id: int,
        include_usage_logs: bool = True,
    ) -> None:
        if include_usage_logs:
            await self.db.execute(delete(AiUsageLog).where(AiUsageLog.session_id == session_id))
        await self.db.execute(delete(AiMessageTrace).where(AiMessageTrace.session_id == session_id))
        await self.db.execute(delete(ConversationContextEvent).where(ConversationContextEvent.session_id == session_id))
        await self.db.execute(delete(ConversationContext).where(ConversationContext.session_id == session_id))
        await self.db.execute(delete(ConversationSummary).where(ConversationSummary.session_id == session_id))

    def should_update_active_context(self, decision: RouteDecision, reference_docs: List[Dict[str, Any]]) -> bool:
        route = route_value(decision)
        if route in {ANSWER_AUDIT_ROUTE, "casual_chat", "emotional_feedback", "clarify"}:
            return False
        if route == "knowledge_search":
            return True
        return bool(reference_docs)

    async def update_active_context(
        self,
        user_message: Message,
        ai_message: Optional[Message],
        decision: RouteDecision,
        retrieval_query: str,
        reference_docs: List[Dict[str, Any]],
        trace_id: Optional[int] = None,
        source: str = "completion",
    ) -> Optional[ConversationContext]:
        if not get_bool_env("MEMORY_ACTIVE_CONTEXT_ENABLED", True):
            return None
        if not self.should_update_active_context(decision, reference_docs):
            return await self.get_active_context(user_message.session_id)

        now = datetime.now()
        slots = slots_to_dict(decision)
        reference_docs_trace = reference_docs_for_trace(reference_docs)
        reference_images = reference_images_for_trace(reference_docs)
        previous = await self.get_active_context(user_message.session_id)
        previous_snapshot = self.context_to_dict(previous) if previous else None
        previous_slots_payload = json_loads(previous.slots_json, {}) if previous else {}
        context_analysis = await self.analyze_context_transition(
            current_text=user_message.content_text,
            active_context=previous_snapshot,
            decision_slots=slots,
            retrieval_query=retrieval_query,
        )
        working_memory = self.merge_structured_working_memory(
            previous_slots_payload.get("_working_memory") or {},
            context_analysis.working_memory_delta,
            should_reset=context_analysis.should_switch,
            current_turn_id=getattr(user_message, "message_order", None) or getattr(user_message, "id", None),
            source_turn_id=getattr(user_message, "message_order", None) or getattr(user_message, "id", None),
            source="user_context_update",
        )
        slot_confidence = build_slot_confidence(slots, context_analysis)
        all_slots = {**context_analysis.current_slots, **slots}
        proposed_issue = retrieval_query or user_message.content_text
        active_issue = (
            previous.active_issue
            if previous and context_analysis.should_continue and previous.active_issue
            else proposed_issue
        )

        if previous:
            context = previous
            if context_analysis.action == "switch":
                event_type = "context_switched"
            elif context_analysis.action == "clarify":
                event_type = "context_ambiguous"
            else:
                event_type = "context_refreshed"
        else:
            context = ConversationContext(
                session_id=user_message.session_id,
                created_time=now,
            )
            self.db.add(context)
            event_type = "context_created"

        context.status = "active"
        context.active_issue = active_issue
        if previous and context_analysis.should_switch:
            context.active_device = all_slots.get("device")
            context.active_component = all_slots.get("component")
            context.active_symptom = all_slots.get("symptom") or active_issue
            context.active_error_code = all_slots.get("error_code")
        else:
            context.active_device = all_slots.get("device") or context.active_device
            context.active_component = all_slots.get("component") or context.active_component
            context.active_symptom = all_slots.get("symptom") or context.active_symptom or active_issue
            context.active_error_code = all_slots.get("error_code") or context.active_error_code
        context.active_question = user_message.content_text
        context.active_query = retrieval_query
        context.active_route = route_value(decision)
        context.active_reason = decision.reason
        context.active_reference_docs_json = json_dumps(reference_docs_trace)
        context.active_reference_images_json = json_dumps(reference_images)
        slots_payload = dict(slots)
        slots_payload["_slot_confidence"] = slot_confidence
        slots_payload["_working_memory"] = working_memory
        slots_payload["_context_analysis"] = context_analysis.to_dict()
        context.slots_json = json_dumps(slots_payload)
        context.last_user_message_id = user_message.id
        context.last_ai_message_id = ai_message.id if ai_message else None
        context.last_trace_id = trace_id
        context.turn_count = int(context.turn_count or 0) + 1
        context.updated_time = now

        await self.db.flush()
        new_snapshot = self.context_to_dict(context)
        event = ConversationContextEvent(
            session_id=user_message.session_id,
            user_message_id=user_message.id,
            ai_message_id=ai_message.id if ai_message else None,
            event_type=event_type,
            route=route_value(decision),
            reason=decision.reason,
            source=source,
            previous_context_json=json_dumps(previous_snapshot or {}),
            new_context_json=json_dumps(new_snapshot),
            created_time=now,
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(context)
        return context

    async def refine_after_answer(
        self,
        user_message: Message,
        ai_message: Optional[Message],
        decision: RouteDecision,
        retrieval_query: str,
        reference_docs: List[Dict[str, Any]],
        answer: str,
        trace_id: Optional[int] = None,
        source: str = "answer_refinement",
    ) -> Optional[ConversationContext]:
        if not get_bool_env("MEMORY_POST_ANSWER_REFINEMENT_ENABLED", True):
            return await self.get_active_context(user_message.session_id)
        if not self.should_update_active_context(decision, reference_docs):
            return await self.get_active_context(user_message.session_id)

        context = await self.get_active_context(user_message.session_id)
        if not context:
            context = await self.update_active_context(
                user_message,
                ai_message,
                decision,
                retrieval_query,
                reference_docs,
                trace_id=trace_id,
                source=source,
            )
            if not context:
                return None

        previous_snapshot = self.context_to_dict(context)
        slots_payload = json_loads(context.slots_json, {})
        if not isinstance(slots_payload, dict):
            slots_payload = {}

        refinement = self.build_answer_memory_refinement(
            user_text=user_message.content_text,
            answer=answer,
            reference_docs=reference_docs,
            source_turn_id=getattr(user_message, "message_order", None) or getattr(user_message, "id", None),
            source_ai_message_id=getattr(ai_message, "id", None) if ai_message else None,
            source_trace_id=trace_id,
            source=source,
        )
        working_memory = self.merge_structured_working_memory(
            slots_payload.get("_working_memory") or {},
            refinement,
            should_reset=False,
            current_turn_id=getattr(user_message, "message_order", None) or getattr(user_message, "id", None),
        )
        slots_payload["_working_memory"] = working_memory
        slots_payload["_post_answer_memory"] = refinement
        context.slots_json = json_dumps(slots_payload)
        context.summary_text = self._build_structured_summary_from_context(
            self.context_to_dict(context),
            max_chars=get_positive_int_env("MEMORY_CONTEXT_SUMMARY_MAX_CHARS", 1400),
        )
        context.last_ai_message_id = ai_message.id if ai_message else context.last_ai_message_id
        context.last_trace_id = trace_id or context.last_trace_id
        context.updated_time = datetime.now()

        await self.db.flush()
        event = ConversationContextEvent(
            session_id=user_message.session_id,
            user_message_id=user_message.id,
            ai_message_id=ai_message.id if ai_message else None,
            event_type="memory_refined",
            route=route_value(decision),
            reason=decision.reason,
            source=source,
            previous_context_json=json_dumps(previous_snapshot or {}),
            new_context_json=json_dumps(self.context_to_dict(context)),
            created_time=datetime.now(),
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(context)
        return context

    def build_answer_memory_refinement(
        self,
        user_text: str,
        answer: str,
        reference_docs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        user_delta = analyze_context_transition_heuristic(user_text).working_memory_delta
        confirmed_facts = list(user_delta.get("confirmed_facts") or [])
        excluded_causes = list(user_delta.get("excluded_causes") or [])
        suggested_steps: List[str] = []
        pending_questions: List[str] = list(user_delta.get("pending_questions") or [])

        for line in self._answer_lines(answer):
            if CONFIRMED_ANSWER_RE.search(line):
                append_unique_memory_item(confirmed_facts, line)
            if EXCLUDED_ANSWER_RE.search(line):
                append_unique_memory_item(excluded_causes, line)
            if SUGGESTED_STEP_RE.search(line):
                append_unique_memory_item(suggested_steps, line)
            if line.endswith(("?", "？")) or PENDING_QUESTION_RE.search(line):
                append_unique_memory_item(pending_questions, line)

        cited_docs = []
        for doc in reference_docs_for_trace(reference_docs):
            cited_doc = {
                "doc_id": doc.get("doc_id"),
                "library_type": doc.get("library_type"),
                "title": doc.get("title"),
                "evidence_section_titles": doc.get("evidence_section_titles") or [],
            }
            if cited_doc["doc_id"] is not None:
                append_unique_memory_item(cited_docs, cited_doc)

        return {
            "confirmed_facts": confirmed_facts[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "excluded_causes": excluded_causes[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "suggested_steps": suggested_steps[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "pending_questions": pending_questions[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "cited_docs": cited_docs[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "updated_time": datetime.now().isoformat(),
        }

    def merge_structured_working_memory(
        self,
        previous: Dict[str, Any],
        refinement: Dict[str, Any],
        should_reset: bool = False,
    ) -> Dict[str, Any]:
        previous = previous if isinstance(previous, dict) else {}
        merged = {
            "confirmed_facts": [] if should_reset else as_memory_list(previous.get("confirmed_facts")),
            "excluded_causes": [] if should_reset else as_memory_list(previous.get("excluded_causes")),
            "pending_questions": [] if should_reset else as_memory_list(previous.get("pending_questions")),
            "suggested_steps": [] if should_reset else as_memory_list(previous.get("suggested_steps")),
            "cited_docs": [] if should_reset else as_memory_list(previous.get("cited_docs")),
            "last_focus": previous.get("last_focus") or "",
        }
        for key in ("confirmed_facts", "excluded_causes", "pending_questions", "suggested_steps", "cited_docs"):
            for value in as_memory_list((refinement or {}).get(key)):
                append_unique_memory_item(merged[key], value)
        if refinement and refinement.get("last_focus"):
            merged["last_focus"] = refinement["last_focus"]
        return merged

    def build_answer_memory_refinement(
        self,
        user_text: str,
        answer: str,
        reference_docs: List[Dict[str, Any]],
        source_turn_id: Optional[int] = None,
        source_ai_message_id: Optional[int] = None,
        source_trace_id: Optional[int] = None,
        source: str = "answer_refinement",
    ) -> Dict[str, Any]:
        now_iso = current_memory_time()
        user_delta = analyze_context_transition_heuristic(user_text).working_memory_delta
        confirmed_facts: List[Dict[str, Any]] = []
        excluded_causes: List[Dict[str, Any]] = []
        candidate_facts: List[Dict[str, Any]] = []
        suggested_steps: List[Dict[str, Any]] = []
        pending_questions: List[Dict[str, Any]] = []

        cited_doc_ids: List[int] = []
        for doc in reference_docs_for_trace(reference_docs):
            try:
                doc_id = int(doc.get("doc_id"))
            except (TypeError, ValueError):
                continue
            if doc_id not in cited_doc_ids:
                cited_doc_ids.append(doc_id)

        for value in user_delta.get("confirmed_facts") or []:
            record = make_memory_record(
                "confirmed_facts",
                value,
                confidence=0.9,
                status="active",
                source="user_feedback",
                source_turn_id=source_turn_id,
                source_ai_message_id=source_ai_message_id,
                source_trace_id=source_trace_id,
                doc_ids=cited_doc_ids,
                now_iso=now_iso,
            )
            if record:
                append_memory_record(confirmed_facts, record, "confirmed_facts")

        for value in user_delta.get("excluded_causes") or []:
            record = make_memory_record(
                "excluded_causes",
                value,
                confidence=0.92,
                status="active",
                source="user_feedback",
                source_turn_id=source_turn_id,
                source_ai_message_id=source_ai_message_id,
                source_trace_id=source_trace_id,
                doc_ids=cited_doc_ids,
                now_iso=now_iso,
            )
            if record:
                append_memory_record(excluded_causes, record, "excluded_causes")

        for value in user_delta.get("pending_questions") or []:
            record = make_memory_record(
                "pending_questions",
                value,
                confidence=0.74,
                status="open",
                source="user_question",
                source_turn_id=source_turn_id,
                source_ai_message_id=source_ai_message_id,
                source_trace_id=source_trace_id,
                doc_ids=cited_doc_ids,
                now_iso=now_iso,
            )
            if record:
                append_memory_record(pending_questions, record, "pending_questions")

        for line in self._answer_lines(answer):
            doc_confidence = 0.64 if cited_doc_ids else 0.52
            if CONFIRMED_ANSWER_RE.search(line) or MEMORY_UNCERTAIN_RE.search(line):
                record = make_memory_record(
                    "candidate_facts",
                    line,
                    confidence=doc_confidence,
                    status="candidate",
                    source="assistant_answer",
                    source_turn_id=source_turn_id,
                    source_ai_message_id=source_ai_message_id,
                    source_trace_id=source_trace_id,
                    doc_ids=cited_doc_ids,
                    now_iso=now_iso,
                )
                if record:
                    append_memory_record(candidate_facts, record, "candidate_facts")
            if EXCLUDED_ANSWER_RE.search(line):
                record = make_memory_record(
                    "candidate_facts",
                    line,
                    confidence=doc_confidence,
                    status="candidate",
                    source="assistant_answer",
                    source_turn_id=source_turn_id,
                    source_ai_message_id=source_ai_message_id,
                    source_trace_id=source_trace_id,
                    doc_ids=cited_doc_ids,
                    now_iso=now_iso,
                )
                if record:
                    append_memory_record(candidate_facts, record, "candidate_facts")
            if SUGGESTED_STEP_RE.search(line):
                record = make_memory_record(
                    "suggested_steps",
                    line,
                    confidence=0.68 if cited_doc_ids else 0.56,
                    status="proposed",
                    source="assistant_answer",
                    source_turn_id=source_turn_id,
                    source_ai_message_id=source_ai_message_id,
                    source_trace_id=source_trace_id,
                    doc_ids=cited_doc_ids,
                    now_iso=now_iso,
                )
                if record:
                    append_memory_record(suggested_steps, record, "suggested_steps")
            if line.endswith("?") or line.endswith("\uff1f") or PENDING_QUESTION_RE.search(line):
                record = make_memory_record(
                    "pending_questions",
                    line,
                    confidence=0.76,
                    status="open",
                    source="assistant_question",
                    source_turn_id=source_turn_id,
                    source_ai_message_id=source_ai_message_id,
                    source_trace_id=source_trace_id,
                    doc_ids=cited_doc_ids,
                    now_iso=now_iso,
                )
                if record:
                    append_memory_record(pending_questions, record, "pending_questions")

        cited_docs: List[Dict[str, Any]] = []
        for doc in reference_docs_for_trace(reference_docs):
            cited_doc = {
                "doc_id": doc.get("doc_id"),
                "library_type": doc.get("library_type"),
                "title": doc.get("title"),
                "evidence_section_titles": doc.get("evidence_section_titles") or [],
                "score": doc.get("score", 0.0),
            }
            record = make_memory_record(
                "cited_docs",
                cited_doc,
                confidence=max(0.72, min(1.0, float(doc.get("score", 0.0) or 0.0) or 0.9)),
                status="active",
                source="reference_doc",
                source_turn_id=source_turn_id,
                source_ai_message_id=source_ai_message_id,
                source_trace_id=source_trace_id,
                now_iso=now_iso,
            )
            if record:
                append_memory_record(cited_docs, record, "cited_docs")

        return {
            "confirmed_facts": confirmed_facts[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "excluded_causes": excluded_causes[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "candidate_facts": candidate_facts[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "suggested_steps": suggested_steps[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "pending_questions": pending_questions[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "cited_docs": cited_docs[-MEMORY_REFINEMENT_MAX_ITEMS:],
            "updated_time": now_iso,
            "write_policy": {
                "confirmed_facts": "user-confirmed stable facts only",
                "candidate_facts": "assistant-derived or uncertain facts awaiting confirmation",
                "threshold": get_positive_float_env("MEMORY_WRITE_CONFIDENCE_THRESHOLD", 0.58),
                "source": source,
            },
        }

    def merge_structured_working_memory(
        self,
        previous: Dict[str, Any],
        refinement: Dict[str, Any],
        should_reset: bool = False,
        current_turn_id: Optional[int] = None,
        source_turn_id: Optional[int] = None,
        source: str = "memory_merge",
    ) -> Dict[str, Any]:
        previous = previous if isinstance(previous, dict) else {}
        refinement = refinement if isinstance(refinement, dict) else {}
        now_iso = current_memory_time()
        merged: Dict[str, Any] = {
            "last_focus": "" if should_reset else previous.get("last_focus") or "",
            "memory_conflicts": [] if should_reset else as_memory_list(previous.get("memory_conflicts")),
        }

        for key in MEMORY_RECORD_KEYS:
            merged[key] = [] if should_reset else normalize_memory_records(key, previous.get(key))
            mark_stale_memory_records(merged[key], current_turn_id)

        incoming_records: List[tuple[str, Dict[str, Any]]] = []
        for key in MEMORY_RECORD_KEYS:
            for value in as_memory_list(refinement.get(key)):
                if isinstance(value, dict):
                    record = normalize_memory_record(key, value)
                else:
                    record = make_memory_record(
                        key,
                        value,
                        source=source,
                        source_turn_id=source_turn_id,
                        now_iso=now_iso,
                    )
                if not record:
                    continue
                appended = append_memory_record(merged[key], record, key)
                incoming_records.append((key, appended))

        self._resolve_memory_conflicts(merged, incoming_records, now_iso)

        if refinement.get("last_focus"):
            merged["last_focus"] = refinement["last_focus"]
        if refinement.get("updated_time"):
            merged["updated_time"] = refinement["updated_time"]
        else:
            merged["updated_time"] = now_iso
        for key in MEMORY_RECORD_KEYS:
            if len(merged[key]) > MEMORY_RECORD_MAX_ITEMS:
                merged[key] = merged[key][-MEMORY_RECORD_MAX_ITEMS:]
        return merged

    def _resolve_memory_conflicts(
        self,
        merged: Dict[str, Any],
        incoming_records: List[tuple[str, Dict[str, Any]]],
        now_iso: str,
    ) -> None:
        threshold = get_positive_float_env("MEMORY_CONFLICT_SIMILARITY_THRESHOLD", 0.45)
        conflicts = merged.setdefault("memory_conflicts", [])
        for new_category, new_record in incoming_records:
            if memory_record_status(new_record) not in MEMORY_ACTIVE_STATUSES:
                continue
            for old_category in MEMORY_CONFLICT_PAIRS.get(new_category, ()):
                for old_record in merged.get(old_category) or []:
                    if old_record is new_record or memory_record_status(old_record) not in MEMORY_ACTIVE_STATUSES:
                        continue
                    similarity = memory_text_similarity(new_record, old_record)
                    if similarity < threshold:
                        continue
                    old_record["status"] = "superseded"
                    old_record["superseded_by"] = memory_record_text(new_record)
                    old_record["superseded_time"] = now_iso
                    append_memory_conflict(
                        conflicts,
                        new_category=new_category,
                        new_record=new_record,
                        old_category=old_category,
                        old_record=old_record,
                        similarity=similarity,
                        now_iso=now_iso,
                    )

    def retrieve_relevant_working_memory(
        self,
        working_memory: Dict[str, Any],
        query: str,
        active_context: Optional[Dict[str, Any]] = None,
        context_analysis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not get_bool_env("MEMORY_RETRIEVAL_ENABLED", True):
            return working_memory if isinstance(working_memory, dict) else {}
        working_memory = working_memory if isinstance(working_memory, dict) else {}
        active_context = active_context if isinstance(active_context, dict) else {}
        retrieval_query = " ".join(
            part
            for part in [
                query,
                active_context.get("active_issue"),
                active_context.get("active_device"),
                active_context.get("active_component"),
                active_context.get("active_error_code"),
                active_context.get("active_symptom"),
            ]
            if part
        )
        query_terms = set(memory_keywords(retrieval_query))
        selected: Dict[str, Any] = {
            "last_focus": working_memory.get("last_focus") or "",
            "memory_conflicts": self._rank_memory_records(
                normalize_memory_records("candidate_facts", working_memory.get("memory_conflicts")),
                query_terms,
                "candidate_facts",
                current_turn_id=None,
                limit=get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_CONFLICTS", 2),
            ),
        }
        current_turn_id = None
        if isinstance(context_analysis, dict):
            try:
                current_turn_id = int(context_analysis.get("current_turn_id"))
            except (TypeError, ValueError):
                current_turn_id = None

        limits = {
            "confirmed_facts": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_FACTS", 5),
            "excluded_causes": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_EXCLUDED", 4),
            "candidate_facts": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_CANDIDATES", 3),
            "pending_questions": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_PENDING", 3),
            "suggested_steps": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_STEPS", 4),
            "cited_docs": get_positive_int_env("MEMORY_RETRIEVAL_TOP_K_DOCS", 3),
        }
        for key in MEMORY_RECORD_KEYS:
            records = normalize_memory_records(key, working_memory.get(key))
            selected[key] = self._rank_memory_records(
                records,
                query_terms,
                key,
                current_turn_id=current_turn_id,
                limit=limits[key],
            )
        selected["_retrieval"] = {
            "enabled": True,
            "query": normalize_memory_text(retrieval_query, 220),
            "query_terms": sorted(query_terms)[:20],
            "source_counts": {
                key: len(as_memory_list(working_memory.get(key))) for key in MEMORY_RECORD_KEYS
            },
            "selected_counts": {
                key: len(as_memory_list(selected.get(key))) for key in MEMORY_RECORD_KEYS
            },
        }
        return selected

    def _rank_memory_records(
        self,
        records: List[Dict[str, Any]],
        query_terms: set[str],
        category: str,
        current_turn_id: Optional[int],
        limit: int,
    ) -> List[Dict[str, Any]]:
        scored: List[tuple[float, int, Dict[str, Any]]] = []
        min_score = get_positive_float_env("MEMORY_RETRIEVAL_MIN_SCORE", 0.05)
        for index, record in enumerate(records or []):
            if memory_record_status(record) not in MEMORY_RENDERABLE_STATUSES:
                continue
            score = self._score_memory_record(record, query_terms, category, current_turn_id)
            if score < min_score and query_terms:
                continue
            ranked_record = dict(record)
            ranked_record["retrieval_score"] = round(score, 3)
            scored.append((score, index, ranked_record))
        scored.sort(key=lambda item: (item[0], memory_record_turn(item[2]) or 0, item[1]), reverse=True)
        return [record for _, _, record in scored[:limit]]

    def _score_memory_record(
        self,
        record: Dict[str, Any],
        query_terms: set[str],
        category: str,
        current_turn_id: Optional[int],
    ) -> float:
        text_terms = set(memory_keywords(memory_record_text(record)))
        overlap = len(query_terms.intersection(text_terms)) / max(1, len(query_terms)) if query_terms else 0.25
        confidence = memory_record_confidence(record, category)
        status = memory_record_status(record)
        status_weight = {
            "active": 1.0,
            "open": 0.92,
            "proposed": 0.78,
            "candidate": 0.66,
        }.get(status, 0.2)
        recency = 0.2
        last_seen_turn = memory_record_turn(record)
        if current_turn_id is not None and last_seen_turn is not None:
            distance = max(0, current_turn_id - last_seen_turn)
            recency = 1.0 / (1.0 + min(distance, 30) / 8.0)
        elif last_seen_turn is not None:
            recency = 0.45
        category_weight = {
            "confirmed_facts": 1.0,
            "excluded_causes": 0.95,
            "pending_questions": 0.85,
            "suggested_steps": 0.76,
            "candidate_facts": 0.66,
            "cited_docs": 0.82,
        }.get(category, 0.7)
        return (overlap * 0.52 + confidence * 0.28 + recency * 0.2) * status_weight * category_weight

    def active_context_reference_documents(self, active_context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not active_context:
            return []
        context_images = active_context.get("active_reference_images") or []
        docs = []
        for item in active_context.get("active_reference_docs") or []:
            normalized = normalize_stored_reference_doc(item)
            if normalized:
                if context_images and not normalized.get("evidence_image_urls"):
                    normalized["evidence_image_urls"] = context_images
                    normalized["image_urls"] = context_images
                docs.append(normalized)
        return docs

    def build_active_context_prompt(self, active_context: Optional[Dict[str, Any]]) -> str:
        if not active_context:
            return ""
        lines = []
        if active_context.get("active_issue"):
            lines.append(f"Current main issue: {active_context['active_issue']}")
        if active_context.get("active_device"):
            lines.append(f"Device: {active_context['active_device']}")
        if active_context.get("active_component"):
            lines.append(f"Component: {active_context['active_component']}")
        if active_context.get("active_error_code"):
            lines.append(f"Error code: {active_context['active_error_code']}")
        if active_context.get("active_symptom"):
            lines.append(f"Symptom: {active_context['active_symptom']}")
        if active_context.get("active_query"):
            lines.append(f"Last retrieval query: {active_context['active_query']}")
        return "\n".join(lines)

    async def maybe_refresh_summary(
        self,
        session_id: int,
        keep_recent_messages: Optional[int] = None,
    ) -> Optional[ConversationSummary]:
        if not get_bool_env("MEMORY_SUMMARY_ENABLED", True):
            return None

        keep_recent = keep_recent_messages or get_positive_int_env("MEMORY_SUMMARY_KEEP_RECENT_MESSAGES", 8)
        min_messages = get_positive_int_env("MEMORY_SUMMARY_MIN_MESSAGES", 14)
        max_chars = get_positive_int_env("MEMORY_SUMMARY_MAX_CHARS", 1800)

        count_result = await self.db.execute(
            select(func.count(Message.id)).where(Message.session_id == session_id)
        )
        total_messages = int(count_result.scalar() or 0)
        if total_messages < min_messages:
            return await self.load_latest_summary(session_id)

        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(asc(Message.message_order))
        )
        messages = list(result.scalars().all())
        if len(messages) <= keep_recent:
            return await self.load_latest_summary(session_id)

        candidates = messages[:-keep_recent]
        if not candidates:
            return await self.load_latest_summary(session_id)

        latest = await self.load_latest_summary(session_id)
        end_order = int(candidates[-1].message_order)
        if latest and int(latest.end_message_order or 0) >= end_order:
            return latest

        active_context = await self.get_active_context(session_id)
        active_context_snapshot = self.context_to_dict(active_context) if active_context else None
        summary_text = self._build_structured_summary(
            candidates,
            max_chars=max_chars,
            active_context=active_context_snapshot,
        )
        if not summary_text:
            return latest

        token_estimate = max(1, len(summary_text) // 2)
        now = datetime.now()
        summary = ConversationSummary(
            session_id=session_id,
            start_message_order=int(candidates[0].message_order),
            end_message_order=end_order,
            message_count=len(candidates),
            token_count=token_estimate,
            summary_text=summary_text,
            created_time=now,
            updated_time=now,
        )
        self.db.add(summary)

        if active_context:
            active_context.summary_text = summary_text
            active_context.updated_time = now

        await self.db.commit()
        await self.db.refresh(summary)
        return summary

    def _answer_lines(self, text: str, max_lines: int = 40) -> List[str]:
        lines: List[str] = []
        for raw_line in str(text or "").splitlines():
            line = normalize_memory_text(raw_line, max_chars=220)
            if not line or line in {"---", "回答生成中，请稍后刷新。"}:
                continue
            lines.append(line)
            if len(lines) >= max_lines:
                break
        return lines

    def _build_structured_summary_from_context(
        self,
        active_context: Optional[Dict[str, Any]],
        max_chars: int,
    ) -> str:
        if not active_context:
            return ""
        working_memory = active_context.get("working_memory") or {}
        working_memory = self.retrieve_relevant_working_memory(
            working_memory,
            query=" ".join(
                part
                for part in [
                    active_context.get("active_issue"),
                    active_context.get("active_query"),
                    active_context.get("active_device"),
                    active_context.get("active_component"),
                    active_context.get("active_error_code"),
                ]
                if part
            ),
            active_context=active_context,
        )
        parts = ["Long conversation summary (structured):"]
        parts.append(f"当前故障：{active_context.get('active_issue') or active_context.get('active_query') or '未明确'}")
        details = []
        if active_context.get("active_device"):
            details.append(f"设备={active_context.get('active_device')}")
        if active_context.get("active_component"):
            details.append(f"部件={active_context.get('active_component')}")
        if active_context.get("active_error_code"):
            details.append(f"报错码={active_context.get('active_error_code')}")
        if active_context.get("active_symptom"):
            details.append(f"现象={active_context.get('active_symptom')}")
        if details:
            parts.append("关键槽位：" + "；".join(details))

        section_map = [
            ("已确认/已排查", working_memory.get("confirmed_facts")),
            ("已排除原因", working_memory.get("excluded_causes")),
            ("待确认/待补充", working_memory.get("pending_questions")),
            ("下一步建议", working_memory.get("suggested_steps")),
        ]
        for label, values in section_map:
            values = [normalize_memory_text(value, 160) for value in as_memory_list(values)]
            values = [value for value in values if value]
            if values:
                parts.append(f"{label}：" + "；".join(values[-5:]))

        candidate_values = [
            value
            for value in as_memory_list(working_memory.get("candidate_facts"))
            if not isinstance(value, dict) or memory_record_status(value) in {"candidate", "open", "proposed", "active"}
        ]
        candidate_lines = [
            memory_record_text(value) if isinstance(value, dict) else normalize_memory_text(value, 160)
            for value in candidate_values
        ]
        candidate_lines = [value for value in candidate_lines if value]
        if candidate_lines:
            parts.append("Candidate facts awaiting confirmation: " + " | ".join(candidate_lines[-5:]))

        cited_docs = working_memory.get("cited_docs") or active_context.get("active_reference_docs") or []
        doc_lines = []
        for doc in cited_docs[-5:] if isinstance(cited_docs, list) else []:
            if not isinstance(doc, dict) or doc.get("doc_id") is None:
                continue
            doc_lines.append(
                f"{doc.get('library_type') or 'breakdown'}:{doc.get('doc_id')}《"
                f"{normalize_memory_text(doc.get('title') or '未命名文档', 80)}》"
            )
        if doc_lines:
            parts.append("引用文档：" + "；".join(doc_lines))

        summary = "\n".join(parts)
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip() + "\n[摘要因长度限制已截断]"
        return summary

    def _build_structured_summary(
        self,
        messages: List[Message],
        max_chars: int,
        active_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        parts = []
        context_summary = self._build_structured_summary_from_context(
            active_context,
            max_chars=max_chars,
        )
        if context_summary:
            parts.append(context_summary)
        else:
            parts.append("Long conversation summary (structured):")
            parts.append("当前故障：未明确")

        history_lines = self._build_history_summary_lines(messages)
        if history_lines:
            parts.append("历史对话要点：")
            parts.extend(history_lines[-12:])

        summary = "\n".join(part for part in parts if part)
        if len(summary) > max_chars:
            summary = summary[:max_chars].rstrip() + "\n[摘要因长度限制已截断]"
        return summary

    def _build_history_summary_lines(self, messages: List[Message]) -> List[str]:
        lines = []
        for message in messages:
            text = re.sub(r"\s+", " ", str(message.content_text or "").strip())
            if not text or text == "回答生成中，请稍后刷新。":
                continue
            role = "User" if int(message.role or 0) == 1 else "Assistant"
            if len(text) > 220:
                text = text[:220].rstrip() + "..."
            lines.append(f"- {role}: {text}")
        return lines

    def _build_extractive_summary(self, messages: List[Message], max_chars: int) -> str:
        lines = self._build_history_summary_lines(messages)
        if not lines:
            return ""
        summary = "Long conversation summary (extractive):\n" + "\n".join(lines[-20:])
        if len(summary) > max_chars:
            summary = summary[-max_chars:].lstrip()
            summary = "Long conversation summary (extractive, truncated):\n" + summary
        return summary
