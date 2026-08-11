# routers/message.py
import asyncio
import json
import mimetypes
import os
import re
import time
import uuid
import base64
from functools import lru_cache

import aiofiles
from PIL import Image
from openai import OpenAI, AsyncOpenAI
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, status, UploadFile, Query, File
from sqlalchemy import func, and_
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, and_, select, desc, asc, delete
from typing import List, Dict, Any, Optional
from schemas import (
    Result,
    ResultNew,
    ConversationCreateNew,
    ConversationDeleteRequest,
    MessageCreateNew,
)
from models import (
    AiUsageLog,
    AiMessageTrace,
    ConversationContext,
    ConversationContextEvent,
    ConversationSummary,
    Message,
    User,
    Conversation,
    Document,
    DocumentBreakdown,
    DocumentKnowledge,
    KnowledgeDocumentSection,
)
from schemas import MessageCreate, MessageResponse
from database import get_db, AsyncSessionLocal
from dependencies import get_current_active_user
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.ai_endpoint import get_ai_base_url, get_ai_base_url_alt
from utils.desensitize import (
    desensitize_json_payload_string,
    desensitize_text,
    desensitize_value,
    max_sensitive_term_length,
)
from utils.token_counter import get_token_count
from utils.ai_usage import record_ai_usage
from agents.intent import IntentRouterAgent, RouteDecision
from agents.intent.taxonomy import IntentRoute
from agents.memory import MemoryPack, MemoryPackBuilder, MemoryService
from agents.skills import AgentSkill, SkillPromptBuilder, SkillRegistry
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/message", tags=["消息"])
chat_router = APIRouter(prefix="/api/v1/chats", tags=["对话与AI问答"])

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}
CONTEXTUAL_FOLLOWUP_REASON = "contextual_followup"
CONTEXTUAL_RETRY_REASON = "contextual_retry"
ANSWER_AUDIT_ROUTE = "answer_audit"
TRACE_PREVIEW_MAX_CHARS = 1200
CONTEXT_REFERENCE_RE = re.compile(
    r"上面|上边|上文|前面|之前|刚才|刚刚|上一(个|条|轮)|前一(个|条|轮)|"
    r"相关问题|这个问题|那个问题|该问题|此问题|这个故障|那个故障"
)
FOLLOWUP_TASK_RE = re.compile(
    r"解决方案|解决办法|处理方法|怎么解决|怎么处理|怎么办|"
    r"原因|为什么|排查|检查步骤|操作步骤|步骤|"
    r"再(给我)?(说|讲|解释|总结)(一下)?|重新(说|讲|解释)|详细(说|讲|解释)|展开(说|讲)?"
)
LOW_VALUE_HISTORY_RE = re.compile(
    r"^(你好|您好|谢谢|多谢|感谢|好的?|收到|明白|ok|okay|不对|你回答|我要投诉|投诉|再见|拜拜|"
    r"问题太多了|问题很多|太多了|随便|无所谓|不知道|没事).*$",
    re.IGNORECASE,
)
RETRY_BASE_DOMAIN_RE = re.compile(
    r"知识库|文档|手册|说明书|报错|异常|报警|排查|维修|维护|修复|解决|操作|步骤|参数|阈值|范围|"
    r"设备|仪器|试剂|测序|芯片|相机|温控|注射泵|barcode|"
    r"冰箱|洗衣机|洗碗机|烘干机|干衣机|笔记本|电脑|耳机|电视|电源|PLC|激光|切割机|CNC|"
    r"Q30|ECR|DNBSEQ|DL-T7|G50|FIT|NSB|SBC|DNQ|Cycle|PE|"
    r"(?:error|err)[\s:_-]*[a-z0-9_-]+|[A-Z]{1,5}[-_]?[0-9]{2,}",
    re.IGNORECASE,
)
QUERY_REWRITE_SYSTEM_PROMPT = (
    "你是维修知识库检索 Query 改写器。请根据历史对话和当前追问，"
    "把当前问题改写成一个自包含、适合知识库检索的中文问题。"
    "必须保留设备、部件、故障现象、报错码、指标和用户想要的答案类型。"
    "不要回答问题，不要解释，只输出改写后的单句。"
)
GENERIC_QUERY_TERMS = {
    "上面", "上边", "上文", "前面", "之前", "刚才", "刚刚", "相关", "问题", "故障",
    "解决", "方案", "办法", "处理", "方法", "怎么", "如何", "原因", "为什么",
    "排查", "检查", "步骤", "操作", "重新", "详细", "展开", "总结", "一下",
    "给我", "提到", "说一", "讲一", "这个", "那个", "该问", "此问",
}


def _normalize_library_type(library_type: str) -> str:
    """统一向量检索返回的库类型，确保回查正确的文档表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """根据库类型选择消息提示词要读取的文档表。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def image_to_base64(image: str, dir: str = None):
    """将图片读取并编码为 base64 字符串。"""
    if dir is not None:
        image = os.path.join(dir, image)
    with open(image, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
        return image_base64

def get_image_config():
    MESSAGE_MAX_IMAGE_SIZE: int = int(os.getenv("MESSAGE_MAX_IMAGE_SIZE", 20 * 1024 * 1024))
    MESSAGE_IMAGE_DIR: str = os.getenv("MESSAGE_IMAGE_DIR", "upload/images")
    MESSAGE_BASE_DIR: str = os.getenv("MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    return {
        "MESSAGE_MAX_IMAGE_SIZE": MESSAGE_MAX_IMAGE_SIZE,
        "MESSAGE_IMAGE_DIR": MESSAGE_IMAGE_DIR,
        "MESSAGE_BASE_DIR": MESSAGE_BASE_DIR,
        "ALLOWED_EXTENSIONS": ALLOWED_EXTENSIONS
    }


def _split_uploaded_images(uploaded_images: str) -> List[str]:
    if not uploaded_images:
        return []
    return [image.strip() for image in uploaded_images.split(",") if image.strip()]


async def _count_text_tokens(text: str) -> int:
    if not text:
        return 0
    return int(await asyncio.to_thread(_count_text_tokens_cached, text))


@lru_cache(maxsize=8192)
def _count_text_tokens_cached(text: str) -> int:
    return int(get_token_count(text or ""))


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    text = str(text or "").strip()
    if not text or token_budget <= 0:
        return ""
    if _count_text_tokens_cached(text) <= token_budget:
        return text
    suffix = "\n[内容因上下文长度限制已截断]"
    suffix_tokens = _count_text_tokens_cached(suffix)
    budget = max(1, token_budget - suffix_tokens)
    approx_chars = max(1, budget * 3)
    candidate = text[:approx_chars].rstrip()
    while candidate and _count_text_tokens_cached(candidate) > budget:
        candidate = candidate[: max(1, int(len(candidate) * 0.8))].rstrip()
    return f"{candidate}{suffix}" if candidate else ""


async def _get_or_update_text_token_count(db: AsyncSession, message: Message) -> int:
    token_count = int(getattr(message, "token_count", 0) or 0)
    if token_count > 0 or not message.content_text:
        return token_count

    token_count = await _count_text_tokens(message.content_text)
    message.token_count = token_count
    return token_count


def _estimate_image_tokens(uploaded_images: str, per_image_tokens: int) -> int:
    return len(_split_uploaded_images(uploaded_images)) * per_image_tokens


def _is_ai_service_unavailable_error(error: Exception) -> bool:
    error_type = type(error).__name__
    if error_type in {"APIConnectionError", "APITimeoutError"}:
        return True

    message = str(error).lower()
    keywords = [
        "connection error",
        "connection",
        "connect",
        "timed out",
        "timeout",
        "refused",
        "service unavailable",
        "temporarily unavailable",
        "name resolution",
        "max retries exceeded",
        "502",
        "503",
        "504",
    ]
    return any(keyword in message for keyword in keywords)


def _get_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _get_positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _filter_reference_documents_by_confidence(
    reference_docs: List[Dict[str, Any]],
    decision: RouteDecision,
) -> List[Dict[str, Any]]:
    """
    对检索结果做二次置信度过滤。

    领域问题使用向量服务已有阈值；普通问题要求更高分，避免非专业闲聊或泛问句误召回。
    """
    if not reference_docs:
        return []

    base_min_score = _get_positive_float_env(
        "RAG_REFERENCE_MIN_SCORE",
        _get_positive_float_env("SIMILARITY_LOWER_LIMIT", 0.5),
    )
    general_min_score = _get_positive_float_env("RAG_GENERAL_REFERENCE_MIN_SCORE", 0.62)
    min_score = base_min_score if decision.route.value == "knowledge_search" else general_min_score

    filtered_docs = [
        doc
        for doc in reference_docs
        if float(doc.get("score", 0.0)) >= min_score
    ]
    if len(filtered_docs) != len(reference_docs):
        top_score = max(float(doc.get("score", 0.0)) for doc in reference_docs)
        print(
            "[意图识别][reference_filter] "
            f"route={decision.route.value} min_score={min_score:.3f} "
            f"before={len(reference_docs)} after={len(filtered_docs)} top_score={top_score:.6f}"
        )
    return filtered_docs


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _compact_for_context(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _is_contextual_followup_text(text: str) -> bool:
    compact = _compact_for_context(text)
    return bool(CONTEXT_REFERENCE_RE.search(compact) and FOLLOWUP_TASK_RE.search(compact))


def _message_role_label(message: Any) -> str:
    return "用户" if int(getattr(message, "role", 0) or 0) == 1 else "AI助手"


def _message_plain_text(message: Any, max_chars: int = 500) -> str:
    text = str(getattr(message, "content_text", "") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def _is_low_value_history_text(text: str) -> bool:
    compact = _compact_for_context(text)
    return not compact or bool(LOW_VALUE_HISTORY_RE.fullmatch(compact))


def _is_valid_contextual_retry_base_question(text: str) -> bool:
    text = _strip_question_tail(text)
    if _is_low_value_history_text(text) or _is_contextual_followup_text(text):
        return False
    return bool(RETRY_BASE_DOMAIN_RE.search(text))


def _rag_query_passes_context_gate(query: str, uploaded_images: str = "") -> bool:
    if str(uploaded_images or "").strip():
        return True
    return _is_valid_contextual_retry_base_question(query)


async def _load_recent_rewrite_history(
    db: AsyncSession,
    session_id: int,
    before_order: int,
    limit: int = 6,
) -> List[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id, Message.message_order < before_order)
        .order_by(desc(Message.message_order))
        .limit(limit)
    )
    history = list(result.scalars().all())
    history.reverse()
    return history


def _extract_followup_focus(question: str) -> str:
    compact = _compact_for_context(question)
    focus_rules = (
        ("解决方案", r"解决方案|解决办法|怎么解决|处理方法|怎么处理|怎么办"),
        ("原因", r"原因|为什么"),
        ("排查步骤", r"排查|检查步骤|操作步骤|步骤"),
        ("总结", r"总结"),
    )
    for focus, pattern in focus_rules:
        if re.search(pattern, compact):
            return focus
    return ""


def _strip_question_tail(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[。！？!?,，.、~～；;：:]+$", "", text)
    return text.strip()


def _last_substantive_user_question(history: List[Any]) -> str:
    for message in reversed(history or []):
        if int(getattr(message, "role", 0) or 0) != 1:
            continue
        text = _strip_question_tail(_message_plain_text(message, max_chars=240))
        if not _is_valid_contextual_retry_base_question(text):
            continue
        return text
    return ""


def _contextual_retry_base_question(memory_pack: Optional[MemoryPack]) -> str:
    active_context = getattr(memory_pack, "active_context", None) if memory_pack else None
    for key in ("active_query", "active_issue", "active_question"):
        candidate = _strip_question_tail(str((active_context or {}).get(key) or ""))
        if _is_valid_contextual_retry_base_question(candidate):
            return candidate
    return _last_substantive_user_question(getattr(memory_pack, "recent_messages", None) or [])


def _contextual_retry_has_base_question(memory_pack: Optional[MemoryPack]) -> bool:
    return bool(_contextual_retry_base_question(memory_pack))


def _normalize_stored_reference_chunk(chunk: Any) -> Dict[str, Any]:
    if not isinstance(chunk, dict):
        return {}
    normalized = dict(chunk)
    if not normalized.get("content") and normalized.get("preview"):
        normalized["content"] = normalized.get("preview")
    return normalized


def _normalize_stored_reference_doc(doc: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(doc, dict) or doc.get("doc_id") is None:
        return None
    try:
        doc_id = int(doc.get("doc_id"))
    except (TypeError, ValueError):
        return None

    image_urls = doc.get("image_urls") or []
    if isinstance(image_urls, str):
        image_urls = [image_urls]

    normalized = {
        "doc_id": doc_id,
        "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
        "title": doc.get("title", ""),
        "score": float(doc.get("score", 0.0) or 0.0),
        "chunks": [
            chunk
            for chunk in (
                _normalize_stored_reference_chunk(chunk)
                for chunk in (doc.get("chunks") or [])
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
    return normalized


def _parse_reference_documents_payload(raw_payload: Any) -> List[Dict[str, Any]]:
    if not raw_payload:
        return []
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except Exception:
            return []
    if not isinstance(raw_payload, list):
        return []

    reference_docs = []
    for item in raw_payload:
        normalized = _normalize_stored_reference_doc(item)
        if normalized:
            reference_docs.append(normalized)
    return reference_docs


def _last_reference_documents_from_history(history: List[Any]) -> List[Dict[str, Any]]:
    for message in reversed(history or []):
        if int(getattr(message, "role", 0) or 0) != 0:
            continue
        reference_docs = _parse_reference_documents_payload(
            getattr(message, "ai_reference_doc_ids", None)
        )
        if reference_docs:
            return reference_docs
    return []


async def _load_previous_reference_documents(
    db: AsyncSession,
    session_id: int,
    before_order: int,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    history = await _load_recent_rewrite_history(db, session_id, before_order, limit=limit)
    return _last_reference_documents_from_history(history)


def _json_dumps_for_trace(value: Any) -> str:
    return json.dumps(desensitize_value(value), ensure_ascii=False)


def _json_loads_for_trace(value: Any, default: Any = None) -> Any:
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


def _answer_preview(answer: str) -> str:
    text = str(answer or "").strip()
    return text[:TRACE_PREVIEW_MAX_CHARS]


def _reference_docs_for_trace(reference_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    docs = []
    for doc in reference_docs or []:
        if doc.get("doc_id") is None:
            continue
        docs.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
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


def _reference_images_for_trace(reference_docs: List[Dict[str, Any]]) -> List[str]:
    images = []
    for doc in reference_docs or []:
        for raw_value in (
            doc.get("evidence_image_urls"),
            doc.get("image_urls"),
            doc.get("matched_image_urls"),
        ):
            for image in _normalize_reference_image_values(raw_value):
                if image and image not in images:
                    images.append(image)
        for chunk in (doc.get("chunks") or [])[:5]:
            if isinstance(chunk, dict) and chunk.get("image_url") and chunk.get("image_url") not in images:
                images.append(chunk.get("image_url"))
    return images[:10]


async def _create_ai_trace(
    db: AsyncSession,
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
) -> Optional[int]:
    if not _get_bool_env("TRACE_PERSIST_ENABLED", True):
        return None
    try:
        trace = AiMessageTrace(
            session_id=user_message.session_id,
            user_message_id=user_message.id,
            ai_message_id=ai_message.id if ai_message else None,
            route=decision.route.value,
            reason=decision.reason,
            original_question=user_message.content_text,
            query_rewrite=decision.query_rewrite,
            retrieval_query=retrieval_query,
            used_previous_refs=1 if used_previous_refs else 0,
            reference_docs_json=_json_dumps_for_trace(_reference_docs_for_trace(reference_docs)),
            reference_images_json=_json_dumps_for_trace(_reference_images_for_trace(reference_docs)),
            actions_json=_json_dumps_for_trace(actions or []),
            validation_json=_json_dumps_for_trace(validation or {}),
            answer_preview=_answer_preview(answer),
            model_name=os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
            status=status_value,
            error_message=error_message,
            created_time=datetime.now(),
            updated_time=datetime.now(),
        )
        if answer:
            trace.output_tokens = await _count_text_tokens(answer)
        db.add(trace)
        await db.commit()
        await db.refresh(trace)
        return trace.id
    except Exception as error:
        await db.rollback()
        print(f"[AI Trace] 创建失败: {type(error).__name__}: {error}")
        return None


async def _update_ai_trace(
    db: AsyncSession,
    trace_id: Optional[int],
    answer: str = "",
    status_value: Optional[str] = None,
    error_message: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
) -> None:
    if not trace_id or not _get_bool_env("TRACE_PERSIST_ENABLED", True):
        return
    try:
        result = await db.execute(select(AiMessageTrace).where(AiMessageTrace.id == trace_id))
        trace = result.scalar_one_or_none()
        if not trace:
            return
        if answer:
            trace.answer_preview = _answer_preview(answer)
            trace.output_tokens = output_tokens if output_tokens is not None else await _count_text_tokens(answer)
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
        await db.commit()
    except Exception as error:
        await db.rollback()
        print(f"[AI Trace] 更新失败: {type(error).__name__}: {error}")


async def _load_recent_ai_traces(
    db: AsyncSession,
    session_id: int,
    limit: int = 6,
) -> List[AiMessageTrace]:
    result = await db.execute(
        select(AiMessageTrace)
        .where(AiMessageTrace.session_id == session_id)
        .order_by(desc(AiMessageTrace.created_time), desc(AiMessageTrace.id))
        .limit(limit)
    )
    traces = list(result.scalars().all())
    traces.reverse()
    return traces


def _trace_doc_ids(trace: AiMessageTrace) -> List[str]:
    docs = _json_loads_for_trace(trace.reference_docs_json, [])
    ids = []
    for doc in docs or []:
        if isinstance(doc, dict) and doc.get("doc_id") is not None:
            ids.append(f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc['doc_id'])}")
    return ids


def _trace_doc_labels(trace: AiMessageTrace, limit: int = 3) -> List[str]:
    docs = _json_loads_for_trace(trace.reference_docs_json, [])
    labels = []
    for doc in (docs or [])[:limit]:
        if not isinstance(doc, dict):
            continue
        doc_id = doc.get("doc_id")
        library_type = _normalize_library_type(doc.get("library_type", "breakdown"))
        title = doc.get("title") or "未命名文档"
        score = doc.get("score")
        score_text = f"，score={float(score):.3f}" if isinstance(score, (int, float)) else ""
        labels.append(f"{library_type}:{doc_id}《{title}》{score_text}")
    return labels


def _trace_images(trace: AiMessageTrace) -> List[str]:
    images = _json_loads_for_trace(trace.reference_images_json, [])
    return [str(image) for image in images or [] if image]


def _trace_actions(trace: AiMessageTrace) -> List[str]:
    actions = _json_loads_for_trace(trace.actions_json, [])
    return [str(action) for action in actions or [] if action]


def _format_trace_brief(label: str, trace: AiMessageTrace) -> List[str]:
    docs = _trace_doc_labels(trace)
    images = _trace_images(trace)
    return [
        f"{label}回答的依据：",
        f"- 关注问题：{trace.original_question or '无记录'}",
        f"- 实际检索：{trace.retrieval_query or trace.query_rewrite or trace.original_question or '无'}",
        f"- 引用文档：{'；'.join(docs) if docs else '无'}",
        f"- 参考图片：{len(images)} 张" + (f"（{'; '.join(images[:3])}）" if images else ""),
    ]


def _is_substantive_knowledge_trace(trace: AiMessageTrace) -> bool:
    if trace.route != "knowledge_search":
        return False
    actions = _trace_actions(trace)
    return bool(
        _trace_doc_ids(trace)
        or _trace_images(trace)
        or any("rag_search" in action or "reuse_previous_references" in action for action in actions)
    )


def _active_context_doc_ids(active_context: Optional[Dict[str, Any]]) -> List[str]:
    if not active_context:
        return []
    doc_ids = []
    for doc in active_context.get("active_reference_docs") or []:
        if isinstance(doc, dict) and doc.get("doc_id") is not None:
            doc_ids.append(
                f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc['doc_id'])}"
            )
    return doc_ids


def _trace_matches_active_context(trace: AiMessageTrace, active_context: Optional[Dict[str, Any]]) -> bool:
    if not active_context:
        return True

    active_doc_ids = set(_active_context_doc_ids(active_context))
    if active_doc_ids and active_doc_ids.intersection(_trace_doc_ids(trace)):
        return True

    trace_query = _compact_for_context(trace.retrieval_query or trace.query_rewrite or trace.original_question or "")
    active_text = _compact_for_context(
        active_context.get("active_query")
        or active_context.get("active_issue")
        or active_context.get("active_question")
        or ""
    )
    if not active_text or not trace_query:
        return False
    if active_text in trace_query or trace_query in active_text:
        return True

    active_terms = set(_extract_retrieval_query_terms(active_text))
    trace_terms = set(_extract_retrieval_query_terms(trace_query))
    return bool(active_terms and trace_terms and len(active_terms.intersection(trace_terms)) >= 2)


def _select_answer_audit_traces(
    traces: List[AiMessageTrace],
    active_context: Optional[Dict[str, Any]],
) -> List[AiMessageTrace]:
    knowledge_traces = [trace for trace in traces if _is_substantive_knowledge_trace(trace)]
    related_traces = [
        trace for trace in knowledge_traces if _trace_matches_active_context(trace, active_context)
    ]
    return related_traces if len(related_traces) >= 2 else knowledge_traces


def _compare_trace_pair(first: AiMessageTrace, second: AiMessageTrace) -> Dict[str, Any]:
    first_docs = _trace_doc_ids(first)
    second_docs = _trace_doc_ids(second)
    first_images = _trace_images(first)
    second_images = _trace_images(second)
    first_query = (first.retrieval_query or first.query_rewrite or first.original_question or "").strip()
    second_query = (second.retrieval_query or second.query_rewrite or second.original_question or "").strip()
    return {
        "query_changed": first_query != second_query,
        "docs_changed": set(first_docs) != set(second_docs),
        "images_changed": set(first_images) != set(second_images),
        "first_doc_ids": first_docs,
        "second_doc_ids": second_docs,
        "first_images": first_images,
        "second_images": second_images,
    }


async def _build_answer_audit_response(
    db: AsyncSession,
    message_now: Message,
    memory_pack: Optional[MemoryPack] = None,
    decision: Optional[RouteDecision] = None,
    skill: Optional[AgentSkill] = None,
) -> tuple[str, List[str], Dict[str, Any]]:
    actions = ["load_memory_pack", "compare_last_answers"] if memory_pack else ["load_recent_traces", "compare_last_answers"]
    selected_skill = skill or SkillRegistry().select_for_memory(memory_pack)
    actions.extend([f"select_skill:{selected_skill.name}", *selected_skill.actions])
    traces = list(getattr(memory_pack, "recent_traces", None) or [])
    if not traces:
        traces = [
            trace
            for trace in await MemoryService(db).load_recent_ai_traces(
                message_now.session_id,
                limit=_get_positive_int_env("ANSWER_AUDIT_HISTORY_LIMIT", 12),
                exclude_routes={ANSWER_AUDIT_ROUTE},
            )
        ]
    active_context = getattr(memory_pack, "active_context", None) if memory_pack else None
    raw_trace_count = len(traces)
    traces = _select_answer_audit_traces(traces, active_context)
    validation: Dict[str, Any] = {
        "raw_trace_count": raw_trace_count,
        "trace_count": len(traces),
        "filtered_non_knowledge_traces": raw_trace_count - len(traces),
    }
    if memory_pack:
        validation.update(memory_pack.to_trace_validation())
    validation.update(selected_skill.to_trace_validation())

    async def finalize(audit_facts: str) -> tuple[str, List[str], Dict[str, Any]]:
        if not _get_bool_env("ANSWER_AUDIT_LLM_ENABLED", True):
            return audit_facts, actions, validation
        try:
            prompt_text = SkillPromptBuilder().build_audit_prompt(
                question=message_now.content_text,
                audit_context=audit_facts,
                skill=selected_skill,
                memory_pack=memory_pack,
                memory_prompt=_build_memory_prompt(memory_pack),
            )
            actions.extend(["build_skill_prompt", "generate_skill_answer"])
            answer = await _generate_skill_text_answer(
                prompt_text,
                max_tokens=_get_positive_int_env("ANSWER_AUDIT_MAX_TOKENS", 1200),
                timeout=_get_positive_float_env("ANSWER_AUDIT_TIMEOUT", 45.0),
            )
            if answer:
                return answer, actions, validation
        except Exception as error:
            actions.append("skill_generation_fallback")
            validation["skill_generation_error"] = f"{type(error).__name__}: {str(error)[:300]}"
            print(f"[AnswerAuditSkill] 生成失败，使用审计事实兜底: {type(error).__name__}: {error}")
        return audit_facts, actions, validation

    if len(traces) < 2:
        actions.append("fallback_to_message_references")
        history_refs = await MemoryService(db).load_previous_reference_documents(
            message_now.session_id,
            message_now.message_order,
            limit=_get_positive_int_env("ANSWER_AUDIT_MESSAGE_HISTORY_LIMIT", 8),
        )
        validation["fallback_reference_count"] = len(history_refs)
        if history_refs:
            docs = _reference_docs_for_trace(history_refs)
            images = _reference_images_for_trace(history_refs)
            answer = (
                "我重新检查了上下文，但最近只有一轮和当前主问题相关的知识回答，"
                "不能把问候、确认这类闲聊当成第二次回答来比较。\n\n"
                f"这轮相关回答引用了 {len(docs)} 个文档、{len(images)} 张参考图片。"
                "如果你是想让我重新回答当前主问题，我会基于当前会话主题和上一轮引用重新组织答案；"
                "如果你是想比较两次回答差异，需要先有两轮相关知识回答记录。"
            )
            return await finalize(answer)
        answer = (
            "我重新检查了上下文，但没有找到两轮和当前主问题相关的知识回答。"
            "我不会拿闲聊或无引用回答来强行比较；后续会只基于相关知识回答记录做复盘。"
        )
        return await finalize(answer)

    first, second = traces[-2], traces[-1]
    comparison = _compare_trace_pair(first, second)
    validation.update(comparison)

    reasons = []
    if comparison["query_changed"]:
        reasons.append("两次用于检索的 query 不同，所以召回结果可能不同。")
    if comparison["docs_changed"]:
        reasons.append("两次引用的文档不同，答案依据发生了变化。")
    if comparison["images_changed"]:
        reasons.append("两次参考图片列表不同，所以前端展示的图片会不一样。")
    if int(getattr(second, "used_previous_refs", 0) or 0):
        reasons.append("最近一次回答复用了上一轮引用，目的是减少上下文追问时的检索漂移。")
    if not reasons:
        reasons.append("从执行记录看，两次检索问题、引用文档和图片基本一致；差异更可能来自模型生成措辞不同。")

    lines = ["我重新检查了最近两轮相关知识回答。"]
    if active_context:
        lines.append(
            "当前会话主问题："
            f"{active_context.get('active_issue') or active_context.get('active_query') or '无明确记录'}"
        )
        if active_context.get("active_reference_docs"):
            lines.append(f"当前会话主题记录了 {len(active_context.get('active_reference_docs') or [])} 个引用文档。")
        lines.append("")
    lines.extend(_format_trace_brief("第一次", first))
    lines.append("")
    lines.extend(_format_trace_brief("第二次", second))
    lines.append("")
    lines.append("差异判断：")
    for reason in reasons:
        lines.append(f"- {reason}")
    recent_events = list(getattr(memory_pack, "recent_context_events", None) or []) if memory_pack else []
    if recent_events:
        event_labels = [
            f"{event.event_type}({event.reason or '无reason'})"
            for event in recent_events[-3:]
        ]
        lines.append(f"- 最近上下文事件：{'；'.join(event_labels)}。")
    lines.append("")
    lines.append(
        "改进动作：后续遇到“上边/刚才的问题再说一下”这类上下文追问时，"
        "系统会优先复用上一轮参考文档和图片；如果需要重新检索，会使用结合历史改写后的完整问题。"
    )
    return await finalize("\n".join(lines))


def _rewrite_query_heuristically(current_question: str, history: List[Any]) -> str:
    current_question = _strip_question_tail(current_question)
    if not _is_contextual_followup_text(current_question):
        return current_question

    base_question = _last_substantive_user_question(history)
    if not base_question:
        return current_question

    focus = _extract_followup_focus(current_question)
    if focus and focus not in base_question:
        rewritten = f"{base_question}的{focus}"
    else:
        rewritten = base_question
    return _strip_question_tail(rewritten)


def _clean_rewritten_query(text: str, fallback: str) -> str:
    candidate = str(text or "").strip()
    candidate = re.sub(r"^```(?:text|json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip().strip("\"'“”‘’")
    candidate = re.sub(r"^(改写后问题|改写问题|检索问题|query|问题)\s*[:：]\s*", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.splitlines()[0].strip() if candidate else ""
    candidate = _strip_question_tail(candidate)
    if not candidate or len(candidate) < 4:
        return fallback
    if len(candidate) > 240:
        candidate = candidate[:240].rstrip()
    return candidate


def _format_history_for_query_rewrite(history: List[Any]) -> str:
    lines = []
    for message in history or []:
        text = _message_plain_text(message)
        if not text or text == "回答生成中，请稍后刷新。":
            continue
        lines.append(f"{_message_role_label(message)}：{text}")
    return "\n".join(lines[-6:])


async def _rewrite_query_with_llm(
    current_question: str,
    history: List[Any],
    fallback_query: str,
) -> str:
    history_text = _format_history_for_query_rewrite(history)
    if not history_text:
        return fallback_query

    request_timeout = _get_positive_float_env("CONTEXT_QUERY_REWRITE_TIMEOUT", 6.0)
    client = AsyncOpenAI(
        base_url=get_ai_base_url(),
        api_key=os.getenv("API_KEY", "EMPTY"),
        timeout=request_timeout,
    )
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=(
                os.getenv("CONTEXT_QUERY_REWRITE_MODEL")
                or os.getenv("INTENT_ROUTER_MODEL")
                or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
            ),
            messages=[
                {"role": "system", "content": QUERY_REWRITE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"历史对话：\n{history_text}\n\n"
                        f"当前追问：{current_question}\n\n"
                        f"若无法从历史中确定指代对象，请原样输出当前追问。"
                    ),
                },
            ],
            temperature=0,
            max_tokens=_get_positive_int_env("CONTEXT_QUERY_REWRITE_MAX_TOKENS", 120),
        ),
        timeout=request_timeout,
    )
    return _clean_rewritten_query(response.choices[0].message.content or "", fallback_query)


def _should_rewrite_retrieval_query(question: str, decision: RouteDecision) -> bool:
    return (
        decision.reason in {CONTEXTUAL_FOLLOWUP_REASON, CONTEXTUAL_RETRY_REASON}
        or _is_contextual_followup_text(question)
    )


async def _resolve_retrieval_query(
    db: AsyncSession,
    message_now: Message,
    decision: RouteDecision,
    memory_pack: Optional[MemoryPack] = None,
) -> str:
    raw_query = str(decision.query_rewrite or message_now.content_text or "").strip()
    current_question = str(message_now.content_text or "").strip()
    if not decision.use_rag or not _should_rewrite_retrieval_query(current_question, decision):
        return raw_query

    history = list(getattr(memory_pack, "recent_messages", None) or [])
    if not history:
        history = await _load_recent_rewrite_history(
            db,
            message_now.session_id,
            message_now.message_order,
            limit=_get_positive_int_env("CONTEXT_QUERY_REWRITE_HISTORY_LIMIT", 6),
        )
    if decision.reason == CONTEXTUAL_RETRY_REASON:
        if memory_pack is not None and not getattr(memory_pack, "recent_messages", None):
            memory_pack.recent_messages = history
        base_question = _contextual_retry_base_question(memory_pack)
        if base_question:
            print(f"[上下文重答] 使用有效上下文恢复检索问题: {base_question}")
            return base_question
        print("[上下文重答] 未找到有效主问题，停止上下文检索")
        return ""

    rewritten_query = _rewrite_query_heuristically(current_question, history)

    heuristic_resolved = (
        rewritten_query
        and rewritten_query != current_question
        and not _is_contextual_followup_text(rewritten_query)
    )
    force_llm = _get_bool_env("CONTEXT_QUERY_REWRITE_FORCE_LLM", False)
    if _get_bool_env("CONTEXT_QUERY_REWRITE_LLM_ENABLED", True) and (force_llm or not heuristic_resolved):
        try:
            llm_query = await _rewrite_query_with_llm(current_question, history, rewritten_query)
            if llm_query and not _is_contextual_followup_text(llm_query):
                rewritten_query = llm_query
        except Exception as error:
            print(f"[上下文改写] LLM改写失败，使用启发式结果: {type(error).__name__}")

    rewritten_query = rewritten_query or raw_query
    if rewritten_query != raw_query:
        print(f"[上下文改写] raw={raw_query} rewritten={rewritten_query}")
    return rewritten_query


def _extract_retrieval_query_terms(query: str) -> List[str]:
    text = str(query or "").strip()
    if not text:
        return []

    normalized = re.sub(r"[，。！？、；：,.!?;:()\[\]【】\"'“”‘’]", " ", text)
    normalized = re.sub(r"\s+", " ", normalized)
    terms: List[str] = []

    for match in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[A-Z]{1,5}[-_]?\d{2,}", normalized):
        term = match.strip()
        if len(term) >= 2 and term.lower() not in GENERIC_QUERY_TERMS:
            terms.append(term.lower())

    chinese_text = re.sub(
        r"上面|上边|上文|前面|之前|刚才|刚刚|相关问题|这个问题|那个问题|该问题|此问题|"
        r"解决方案|解决办法|处理方法|怎么解决|怎么处理|怎么办|原因|为什么|"
        r"排查|检查步骤|操作步骤|步骤|再给我|再说|再讲|重新说|详细说|一下|的",
        " ",
        normalized,
    )
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", chinese_text):
        if sequence in GENERIC_QUERY_TERMS:
            continue
        if len(sequence) <= 6:
            terms.append(sequence)
            continue
        for size in (4, 3, 2):
            for index in range(0, len(sequence) - size + 1):
                gram = sequence[index:index + size]
                if gram not in GENERIC_QUERY_TERMS:
                    terms.append(gram)

    unique_terms = []
    for term in terms:
        if term and term not in unique_terms:
            unique_terms.append(term)
    return unique_terms[:40]


def _reference_doc_match_text(doc: Dict[str, Any]) -> str:
    pieces = [str(doc.get("title") or ""), str(doc.get("content") or "")]
    for chunk in (doc.get("chunks") or [])[:5]:
        if isinstance(chunk, dict):
            pieces.append(str(chunk.get("title") or ""))
            pieces.append(str(chunk.get("content") or ""))
    return "\n".join(pieces).lower()


def _filter_reference_documents_by_query_terms(
    reference_docs: List[Dict[str, Any]],
    retrieval_query: str,
    decision: RouteDecision,
) -> List[Dict[str, Any]]:
    if not reference_docs or decision.reason != CONTEXTUAL_FOLLOWUP_REASON:
        return reference_docs

    terms = _extract_retrieval_query_terms(retrieval_query)
    if len(terms) < 2:
        return reference_docs

    scored_docs = []
    for doc in reference_docs:
        match_text = _reference_doc_match_text(doc)
        hit_count = sum(1 for term in terms if term.lower() in match_text)
        scored_docs.append((hit_count, doc))

    max_hits = max(hit_count for hit_count, _doc in scored_docs)
    if max_hits < 2:
        return reference_docs

    min_hits = max(2, max_hits // 2)
    filtered_docs = [doc for hit_count, doc in scored_docs if hit_count >= min_hits]
    if filtered_docs and len(filtered_docs) != len(reference_docs):
        print(
            "[上下文改写][reference_filter] "
            f"query_terms={terms[:12]} min_hits={min_hits} "
            f"before={len(reference_docs)} after={len(filtered_docs)}"
        )
        return filtered_docs
    return reference_docs


def _build_memory_prompt(memory_pack: Optional[MemoryPack]) -> str:
    if not memory_pack:
        return ""
    return memory_pack.to_prompt(
        include_recent_messages=_get_bool_env("MEMORY_PROMPT_INCLUDE_RECENT_MESSAGES", False),
        include_context_events=_get_bool_env("MEMORY_PROMPT_INCLUDE_CONTEXT_EVENTS", False),
        max_recent_messages=_get_positive_int_env("MEMORY_PROMPT_RECENT_MESSAGES", 4),
        max_message_chars=_get_positive_int_env("MEMORY_PROMPT_MESSAGE_MAX_CHARS", 260),
        max_reference_docs=_get_positive_int_env("MEMORY_PROMPT_REFERENCE_DOCS", 3),
    )


async def _generate_skill_text_answer(
    prompt_text: str,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
) -> str:
    request_timeout = timeout or _get_positive_float_env("SKILL_GENERATION_TIMEOUT", 45.0)
    client = AsyncOpenAI(
        base_url=get_ai_base_url(),
        api_key=os.getenv("API_KEY", "EMPTY"),
        timeout=request_timeout,
    )
    response = await asyncio.wait_for(
        client.chat.completions.create(
            model=os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct"),
            messages=[{"role": "user", "content": desensitize_text(prompt_text)}],
            temperature=_get_positive_float_env("SKILL_GENERATION_TEMPERATURE", 0.2),
            max_tokens=max_tokens or _get_positive_int_env("SKILL_GENERATION_MAX_TOKENS", 1200),
        ),
        timeout=request_timeout,
    )
    answer = response.choices[0].message.content or ""
    answer = answer.replace("\n---\n", "---").replace("\n\n", "\n").strip()
    return desensitize_text(answer)


def _merge_reference_documents(
    primary_docs: List[Dict[str, Any]],
    secondary_docs: List[Dict[str, Any]],
    max_docs: int = -1,
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for doc in (primary_docs or []) + (secondary_docs or []):
        if doc.get("doc_id") is None:
            continue
        key = f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc['doc_id'])}"
        if key not in merged:
            merged[key] = dict(doc)
            merged[key]["chunks"] = list(doc.get("chunks") or [])
            order.append(key)
            continue
        existing = merged[key]
        existing["score"] = max(float(existing.get("score", 0.0) or 0.0), float(doc.get("score", 0.0) or 0.0))
        existing_chunks = existing.get("chunks") or []
        seen_chunks = {
            str(chunk.get("content") or chunk.get("preview") or "")[:240]
            for chunk in existing_chunks
            if isinstance(chunk, dict)
        }
        for chunk in doc.get("chunks") or []:
            if not isinstance(chunk, dict):
                continue
            marker = str(chunk.get("content") or chunk.get("preview") or "")[:240]
            if marker and marker not in seen_chunks:
                existing_chunks.append(chunk)
                seen_chunks.add(marker)
        existing["chunks"] = existing_chunks
        for image_field in ("matched_image_urls", "evidence_image_urls", "image_urls"):
            images = _normalize_reference_image_values(existing.get(image_field))
            for image in _normalize_reference_image_values(doc.get(image_field)):
                if image and image not in images:
                    images.append(image)
            if images:
                existing[image_field] = images

    docs = [merged[key] for key in order]
    docs.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return docs[:max_docs] if max_docs and max_docs > 0 else docs


async def _load_reusable_reference_documents_from_memory(
    memory_service: MemoryService,
    memory_pack: Optional[MemoryPack],
    message_now: Message,
) -> List[Dict[str, Any]]:
    reusable_reference_documents = await memory_service.load_previous_reference_documents(
        message_now.session_id,
        message_now.message_order,
        limit=_get_positive_int_env("CONTEXT_REFERENCE_REUSE_HISTORY_LIMIT", 8),
    )
    if reusable_reference_documents:
        return reusable_reference_documents

    if memory_pack and memory_pack.active_context:
        return memory_service.active_context_reference_documents(memory_pack.active_context)
    return []


async def _adaptive_retrieve_reference_documents(
    db: AsyncSession,
    retrieval_query: str,
    user_uploaded_images: Optional[str],
    route_decision: RouteDecision,
    memory_pack: Optional[MemoryPack],
) -> tuple[List[Dict[str, Any]], List[str]]:
    plan = memory_pack.adaptive_rag if memory_pack else None
    top_k = plan.top_k if plan else -1
    top_k_documents = plan.top_k_documents if plan else -1
    actions = [f"rag_search:{plan.strategy if plan else 'default'}"]

    reference_docs = await get_reference_documents(
        db,
        retrieval_query,
        user_uploaded_images,
        top_k=top_k,
        top_k_documents=top_k_documents,
    )

    if not plan or not plan.iterative_retrieval or not memory_pack or not memory_pack.active_context:
        return reference_docs, actions

    active_query = str(
        memory_pack.active_context.get("active_query")
        or memory_pack.active_context.get("active_issue")
        or ""
    ).strip()
    if not active_query or _compact_for_context(active_query) == _compact_for_context(retrieval_query):
        return reference_docs, actions

    actions.append("react_context_retrieve")
    second_query = f"{retrieval_query}\n当前会话主问题：{active_query}"
    secondary_docs = await get_reference_documents(
        db,
        second_query,
        user_uploaded_images,
        top_k=top_k,
        top_k_documents=top_k_documents,
    )
    max_docs = top_k_documents if top_k_documents and top_k_documents > 0 else -1
    return _merge_reference_documents(reference_docs, secondary_docs, max_docs=max_docs), actions


def _api_timestamp(value: Optional[datetime]) -> Optional[float]:
    """
    功能说明：
        将数据库中的 datetime 转换为 api.md 约定的 Unix 时间戳。
    参数说明：
        value：数据库时间字段，可为空。
    返回值说明：
        有效时间返回 float 秒级时间戳；空值返回 None。
    关键处理流程：
        仅做格式转换，不改变时区和原始业务含义。
    """
    return value.timestamp() if value else None


def _api_http_date(value: Optional[datetime]) -> Optional[str]:
    """
    功能说明：
        将 datetime 转换为对话接口兼容的 HTTP 日期字符串。
    参数说明：
        value：数据库时间字段，可为空。
    返回值说明：
        返回形如 Tue, 10 Jun 2026 10:00:00 GMT 的字符串；空值返回 None。
    关键处理流程：
        保持 conversation_v1 已有返回字段，便于前端兼容历史格式。
    """
    return value.strftime("%a, %d %b %Y %H:%M:%S GMT") if value else None


def _sanitize_answer_images_for_display(text: Optional[str]) -> Optional[str]:
    """
    功能说明：
        清理历史 AI 消息中的图片引用，确保返回给前端的本地图片均可访问。
    参数说明：
        text：数据库中保存的 AI 回答文本。
    返回值说明：
        返回清理后的文本；存在的本地图片会转换成 /upload/...，不存在的本地图片会被移除。
    关键处理流程：
        该函数主要处理历史消息，避免旧回答中已失效的 Markdown 图片在页面加载时触发 404。
    """
    if not text:
        return text
    import re

    def convert_markdown(match: re.Match) -> str:
        alt_text = match.group(1)
        image_url = match.group(2)
        if image_url.startswith(("data:", "blob:", "http://", "https://", "//")):
            return match.group(0)
        web_url = _image_to_web_url_if_exists(image_url)
        return f"![{alt_text}]({web_url})" if web_url else ""

    sanitized = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", convert_markdown, text)
    sanitized = re.sub(r"(?im)^.*(?:img_url|image_url|配图路径|本文配图路径).*$(?:\r?\n)?", "", sanitized)
    sanitized = re.sub(r"[A-Za-z]:[/\\][^\s)\]]+", "", sanitized)
    return sanitized


def _message_to_chat_message_dict(message: Message) -> Dict[str, Any]:
    """
    功能说明：
        将 Message ORM 对象转换为 api.md 中会话消息列表使用的结构。
    参数说明：
        message：数据库消息对象，role=1 表示用户，role=0 表示 AI。
    返回值说明：
        返回包含 id、role、content、content_text、图片、引用和创建时间的字典。
    关键处理流程：
        同时保留 api.md 的 content 字段和旧前端使用的 content_text 字段，
        避免前端一次性切换时丢失历史消息展示能力。
    """
    created_time = message.created_time.isoformat() if message.created_time else None
    content_text = (
        desensitize_text(_sanitize_answer_images_for_display(message.content_text))
        if message.role == 0
        else message.content_text
    )
    ai_reference_doc_ids = (
        desensitize_json_payload_string(message.ai_reference_doc_ids)
        if message.role == 0
        else message.ai_reference_doc_ids
    )
    return {
        "id": message.id,
        "session_id": message.session_id,
        "message_order": message.message_order,
        "role": "user" if message.role == 1 else "assistant",
        "role_value": message.role,
        "content": content_text,
        "content_text": content_text,
        "user_uploaded_images": message.user_uploaded_images,
        "ai_reference_doc_ids": ai_reference_doc_ids,
        "created_time": created_time,
    }


def _conversation_to_chat_session(
    chat_id: str,
    conversation: Conversation,
    messages: Optional[List[Message]] = None,
) -> Dict[str, Any]:
    """
    功能说明：
        将 Conversation ORM 对象转换为 api.md 中 Chat Session 的返回结构。
    参数说明：
        chat_id：路径中的聊天助手 ID。
        conversation：数据库会话对象。
        messages：可选的消息列表；未传入时 messages 返回空数组。
    返回值说明：
        返回包含 chat/chat_id、id、name、messages、create_time、update_time 等字段的字典。
    关键处理流程：
        统一把内部 title 映射为接口规范中的 name，同时保留 title 供旧页面兼容。
    """
    message_payload = [_message_to_chat_message_dict(item) for item in (messages or [])]
    return {
        "chat": chat_id,
        "chat_id": chat_id,
        "id": conversation.id,
        "name": conversation.title,
        "title": conversation.title,
        "messages": message_payload,
        "create_date": _api_http_date(conversation.created_time),
        "create_time": _api_timestamp(conversation.created_time),
        "created_time": conversation.created_time.isoformat() if conversation.created_time else None,
        "update_date": _api_http_date(conversation.updated_time),
        "update_time": _api_timestamp(conversation.updated_time),
        "updated_time": conversation.updated_time.isoformat() if conversation.updated_time else None,
    }


def _normalize_uploaded_images(uploaded_images: Optional[str]) -> Optional[str]:
    """
    功能说明：
        规范化用户问答图片字段，适配 api.md 中多个路径以英文逗号分隔的格式。
    参数说明：
        uploaded_images：前端传入的图片路径字符串，可为空。
    返回值说明：
        规范化后的相对路径字符串；无有效图片时返回 None。
    关键处理流程：
        将反斜杠统一为斜杠，移除路径前导斜杠，并用 ', ' 重新拼接。
    """
    if not uploaded_images:
        return None
    images = [
        image.strip().replace("\\", "/").lstrip("/")
        for image in str(uploaded_images).split(",")
        if image and image.strip()
    ]
    return ", ".join(images) if images else None


async def _validate_uploaded_images_exist(uploaded_images: Optional[str], config: dict) -> Optional[str]:
    """
    功能说明：
        校验 completion 请求中声明的用户上传图片是否已通过附件接口上传。
    参数说明：
        uploaded_images：请求体中的 user_uploaded_images 字符串。
        config：get_image_config() 返回的图片目录配置。
    返回值说明：
        返回规范化后的图片路径字符串；无图片时返回 None。
    关键处理流程：
        1. 先规范化路径分隔符和前导斜杠；
        2. 仅取文件名落到 MESSAGE_IMAGE_DIR 下校验，避免用户构造目录穿越；
        3. 任一图片不存在则抛出“图片未上传”业务异常。
    """
    normalized = _normalize_uploaded_images(uploaded_images)
    if not normalized:
        return None

    base_url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))
    for image in _split_uploaded_images(normalized):
        filename = os.path.basename(image)
        image_path = os.path.join(base_url, filename.lstrip("/").lstrip("\\"))
        if not await asyncio.to_thread(os.path.exists, image_path):
            print(image_path)
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传")
    return normalized


def _debug_reference_doc_image_summary(reference_docs: List[Dict[str, Any]], label: str):
    """打印参考文档图片字段摘要，便于排查图片未展示问题。"""
    try:
        docs = reference_docs or []
        print(f"[图片排查][{label}] docs={len(docs)}")
        for index, doc in enumerate(docs, start=1):
            chunks = doc.get("chunks") or []
            matched_image_urls = doc.get("matched_image_urls") or []
            evidence_image_urls = doc.get("evidence_image_urls") or []
            chunk_images = [
                chunk.get("image_url")
                for chunk in chunks
                if isinstance(chunk, dict) and chunk.get("image_url")
            ]
            print(
                f"[图片排查][{label}] doc#{index} "
                f"id={doc.get('doc_id')} library={doc.get('library_type')} "
                f"title={doc.get('title')} chunks={len(chunks)} "
                f"evidence_image_urls={evidence_image_urls} "
                f"matched_image_urls={matched_image_urls} chunk_images={chunk_images}"
            )
    except Exception as e:
        print(f"[图片排查][{label}] 打印失败: {e}")


def _normalize_reference_image_values(raw_value: Any) -> List[str]:
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


def _extend_unique_images(images: List[str], raw_value: Any):
    for image in _normalize_reference_image_values(raw_value):
        if image not in images:
            images.append(image)


def _section_image_urls(section: KnowledgeDocumentSection) -> List[str]:
    return _normalize_reference_image_values(getattr(section, "image_urls", None))


def _breakdown_document_image_urls(document: DocumentBreakdown) -> List[str]:
    images: List[str] = []
    for attr in (
        "image_urls",
        "image_urls_problem_intro",
        "image_urls_causes",
        "image_urls_evaluation",
        "image_urls_inspection",
        "image_urls_solutions",
        "image_urls_key_points",
    ):
        _extend_unique_images(images, getattr(document, attr, None))
    return images


def _collect_reference_image_paths(reference_docs: List[Dict[str, Any]], max_images: Optional[int] = None) -> List[str]:
    """
    功能说明：
        从向量检索命中的文档/Chunk 中提取可展示的参考图片路径。
    参数说明：
        reference_docs：get_reference_documents 返回的参考文档列表。
        max_images：最多返回图片数量；None 表示不限制。
    返回值说明：
        去重后的“本地文件真实存在”的图片路径列表，顺序与检索结果顺序一致。
    关键处理流程：
        优先读取 evidence_image_urls，也就是本轮实际写入 prompt 的证据片段图片；
        若尚未构建证据片段，则兼容旧逻辑读取 matched_image_urls 和 chunk.image_url。
        收集时会做本地文件存在性校验，避免把历史脏数据或已丢失文件返回给前端造成 404。
    """
    images: List[str] = []
    print(f"[图片排查][collect_start] max_images={max_images}")
    _debug_reference_doc_image_summary(reference_docs, "collect_input")

    def add(image: Optional[str], source: str):
        if not image:
            return
        normalized = str(image).strip().replace("\\", "/")
        if not normalized or normalized in images:
            if normalized in images:
                print(f"[图片排查][collect_skip_duplicate] source={source} image={normalized}")
            return
        web_url = _image_to_web_url_if_exists(normalized)
        if web_url is None:
            print(f"[图片过滤] source={source} 文件不存在，跳过引用: {normalized}")
            return
        print(f"[图片排查][collect_ok] source={source} image={normalized} web_url={web_url}")
        images.append(normalized)

    for doc_index, doc in enumerate(reference_docs or [], start=1):
        evidence_images = doc.get("evidence_image_urls", []) or []
        if evidence_images:
            for image in evidence_images:
                add(image, f"doc#{doc_index}.evidence_image_urls")
            if max_images is not None and len(images) >= max_images:
                break
            continue

        for image in doc.get("image_urls", []) or []:
            add(image, f"doc#{doc_index}.image_urls")
        for image in doc.get("matched_image_urls", []) or []:
            add(image, f"doc#{doc_index}.matched_image_urls")
        for chunk_index, chunk in enumerate(doc.get("chunks", []) or [], start=1):
            add(chunk.get("image_url"), f"doc#{doc_index}.chunks#{chunk_index}.image_url")
        if max_images is not None and len(images) >= max_images:
            break

    result = images[:max_images] if max_images is not None else images
    print(f"[图片排查][collect_result] count={len(result)} images={result}")
    return result


def _image_to_web_url_if_exists(image_path: str) -> Optional[str]:
    """
    功能说明：
        将本地图片路径解析为可访问 Web URL，并在返回前确认本地文件真实存在。
    参数说明：
        image_path：数据库、向量检索或模型输出中的图片路径，可为绝对路径、upload 相对路径或 /upload URL。
    返回值说明：
        文件存在时返回 /upload/... URL；外部 http(s)/data/blob URL 原样返回；本地文件不存在返回 None。
    关键处理流程：
        1. 统一 Windows/Unix 路径分隔符；
        2. 识别 /upload 或 upload 路径并映射到 MESSAGE_BASE_DIR/upload；
        3. 识别 Windows 绝对路径中包含的 /upload 片段；
        4. 最终用 os.path.exists 校验，避免前端收到必然 404 的图片 URL。
    """
    if not image_path:
        return None

    normalized = str(image_path).strip().replace("\\", "/")
    if not normalized:
        return None
    if normalized.startswith(("data:", "blob:", "http://", "https://", "//")):
        return normalized

    config = get_image_config()
    base_dir = config["MESSAGE_BASE_DIR"]

    import re
    upload_match = re.search(r"(?:^|/)upload/(.+)$", normalized, re.IGNORECASE)
    if upload_match:
        relative_upload = f"upload/{upload_match.group(1).lstrip('/')}"
        absolute_path = os.path.join(base_dir, relative_upload)
        return f"/{relative_upload}" if os.path.exists(absolute_path) else None

    if re.match(r"^[A-Za-z]:/", normalized):
        return _path_to_web_url(normalized) if os.path.exists(normalized) else None

    filename = os.path.basename(normalized)
    fallback_relative = f"upload/images/{filename}"
    fallback_absolute = os.path.join(base_dir, fallback_relative)
    return f"/{fallback_relative}" if filename and os.path.exists(fallback_absolute) else None


async def _append_reference_images_to_answer(answer: str, reference_docs: List[Dict[str, Any]]) -> str:
    """
    功能说明：
        为 AI 最终回答稳定补充可渲染的参考图片 Markdown。
    参数说明：
        answer：大模型生成的原始回答文本。
        reference_docs：本轮 RAG 检索命中的参考文档列表。
    返回值说明：
        返回已替换本地路径并在必要时追加参考图片的最终回答。
    关键处理流程：
        1. 收集命中 Chunk 中的图片路径；
        2. 先把回答里已有的本地路径转换为 /upload/... Web URL；
        3. 若回答未包含这些图片，则在末尾追加“参考图片”Markdown，避免依赖模型
           一定按提示词输出图片路径。
    """
    final_answer = answer or ""
    config = get_image_config()
    max_images = int(os.getenv("MAX_DOC_IMAGES", 5))
    _debug_reference_doc_image_summary(reference_docs, "append_answer_input")
    image_paths = _collect_reference_image_paths(reference_docs, max_images=max_images)
    print(f"[图片排查][append_answer] image_paths={image_paths}")

    if image_paths:
        final_answer = await _replace_image_urls_in_text(final_answer, config, image_paths)
        appended_lines = []
        for index, image_path in enumerate(image_paths, start=1):
            web_url = _image_to_web_url_if_exists(image_path)
            if not web_url:
                print(f"[图片排查][append_skip_no_web_url] image={image_path}")
                continue
            if image_path in final_answer or web_url in final_answer:
                print(f"[图片排查][append_skip_exists] image={image_path} web_url={web_url}")
                continue
            print(f"[图片排查][append_markdown] image={image_path} web_url={web_url}")
            appended_lines.append(f"![参考图片{index}]({web_url})")
        if appended_lines:
            final_answer = final_answer.rstrip() + "\n\n参考图片：\n" + "\n".join(appended_lines)
            print(f"[图片排查][append_done] appended={len(appended_lines)}")
        else:
            print("[图片排查][append_done] 没有新增图片 Markdown")
    else:
        print("[图片排查][append_answer] 未收集到可用参考图片")
        final_answer = await _replace_image_urls_in_text(final_answer, config)
        final_answer = _remove_reference_image_hint_without_images(final_answer)

    return final_answer


def _remove_reference_image_hint_without_images(text: str) -> str:
    """无可展示参考图片时，清理模型误生成的图片提示语。"""
    if not text:
        return text
    import re
    cleaned = re.sub(
        r"[（(]?(?:见下方参考图片|参考图片见下方|见下方配图|见下方相关图片)[）)]?[。！？!?,，；;：:]*",
        "",
        text,
    )
    cleaned = re.sub(r"(?im)^\s*(?:参考图片|相关参考图片)\s*[：:]\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _reference_doc_ids_to_api_reference(reference_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    功能说明：
        将后端参考文档匹配结果转换为 Completion 流式响应中的 reference 结构。
    参数说明：
        reference_docs：get_reference_documents 返回的参考文档列表。
    返回值说明：
        返回 {"total": n, "doc_aggs": [...]}，每项包含文档 ID、标题、库类型、分数和图片。
    关键处理流程：
        保留 api.md 的 reference.doc_aggs 结构，同时增加 image_urls 供前端后续扩展展示。
    """
    doc_aggs = []
    for doc in reference_docs or []:
        doc_id = doc.get("doc_id")
        if doc_id is None:
            continue
        images = _collect_reference_image_paths([doc], max_images=3)
        item = {
            "doc_id": f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc_id)}",
            "doc_name": desensitize_text(doc.get("title", "")),
            "title": desensitize_text(doc.get("title", "")),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
            "score": doc.get("score", 0.0),
        }
        if images:
            item["image_urls"] = [
                web_url
                for image in images
                for web_url in [_image_to_web_url_if_exists(image)]
                if web_url
            ]
        doc_aggs.append(item)
    return {"total": len(doc_aggs), "doc_aggs": doc_aggs}

async def upload_images(images: List[UploadFile] = File(...)):
    """
    功能说明：
        聊天图片附件保存逻辑，供 /api/v1/chats/{chat_id}/images 复用。
    参数说明：
        images：multipart/form-data 中的图片文件数组，字段名为 images。
    返回值说明：
        返回 Result(code=1)；data 为上传成功图片的 url、filename、original_name 列表。
    关键处理流程：
        校验图片大小和扩展名，生成唯一文件名保存到 MESSAGE_IMAGE_DIR，并返回相对 URL。
    """
    # 聊天消息图片统一存放在 upload/images
    config = get_image_config()
    url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

    uploaded_images = []
    if not os.path.exists(url):
        os.makedirs(url)
        print(f"创建路径 {url}")
    for image in images:
        try:
            if image.size is not None and image.size > config["MESSAGE_MAX_IMAGE_SIZE"]:
                continue
            file_ext = Path(image.filename).suffix.lower()
            if file_ext not in config["ALLOWED_EXTENSIONS"]:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"
            save_path = Path(url) / unique_filename
            print("save_path: ", save_path)
            contents = await image.read()
            if len(contents) > config["MESSAGE_MAX_IMAGE_SIZE"]:
                continue

            async with aiofiles.open(save_path, "wb") as buffer:
                await buffer.write(contents)

            relative_url = str((Path(config["MESSAGE_IMAGE_DIR"]) / unique_filename).as_posix())
            uploaded_images.append({
                "url": relative_url,
                "filename": unique_filename,
                "original_name": image.filename
            })
            print("上传图片成功")
            print(uploaded_images[0])
        except Exception as e:
            # 单个文件失败不影响其他文件继续上传
            print(f"文件 {image.filename} 上传失败: {str(e)}")

    return Result.success_with_data(uploaded_images)


@chat_router.post("/{chat_id}/images", summary="上传问答图片")
async def upload_chat_images(chat_id: str, images: List[UploadFile] = File(...)):
    """
    功能说明：
        api.md 规范下的对话附件上传接口：POST /api/v1/chats/{chat_id}/images。
    参数说明：
        chat_id：聊天助手 ID，当前项目不区分助手实例，保留用于符合接口规范。
        images：multipart/form-data 中的图片文件数组，字段名为 images。
    返回值说明：
        返回与 api.md 一致的 Result(code=1,msg='success')，data 为图片路径列表。
    关键处理流程：
        复用旧版 /message/upload_images 的存储与校验逻辑，避免维护两套附件实现。
    """
    return await upload_images(images)

async def compress_image(image_path: str, max_size=512, pad_color=(0, 0, 0)):

    def _compress():
        if not os.path.exists(image_path):
            raise FileNotFoundError()

        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = f"{name}_compressed_{max_size}{ext}"
        new_path = os.path.join(dir_name, new_path)

        if os.path.exists(new_path):
            return new_path

        # return image_path
        image = Image.open(image_path).convert("RGB")
        # new_size = (448, 448)
        max_length = max(image.width, image.height)
        rate = max_size / max_length
        new_size = (int(image.width * rate), int(image.height * rate))
        resized_image = image.resize(new_size)

        new_image = Image.new("RGB", (max_size, max_size), pad_color)

        x = (max_size - new_size[0]) // 2
        y = (max_size - new_size[1]) // 2

        new_image.paste(resized_image, (x, y))

        new_image.save(new_path)
        return new_path

    return await asyncio.to_thread(_compress)

def _path_to_web_url(image_path: str) -> str:
    """
    功能说明：
        将图片路径转换为 Web URL 字符串，不负责文件存在性校验。
    参数说明：
        image_path：绝对路径、upload 相对路径或文件名。
    返回值说明：
        返回 /upload/... 格式 URL；若无法识别目录则默认落到 /upload/images/{filename}。
    关键处理流程：
        先提取路径中的 upload 片段，避免把 Windows 绝对路径直接暴露给前端。
        调用方若要避免 404，应优先使用 _image_to_web_url_if_exists()。
    """
    import re
    normalized = str(image_path).replace('\\', '/')
    match = re.search(r'(?:^|/)upload/(.+)$', normalized, re.IGNORECASE)
    if match:
        return f'/upload/{match.group(1)}'
    filename = os.path.basename(normalized)
    return f'/upload/images/{filename}'


async def _replace_image_urls_in_text(text: str, config: dict, known_image_paths: list = None) -> str:
    """
    功能说明：
        将 AI 回答中的本地图片路径替换为 Web URL，并移除不存在的本地图片引用。
    参数说明：
        text：AI 回答文本。
        config：图片配置，保留参数兼容旧调用。
        known_image_paths：本轮检索命中的已知图片路径列表。
    返回值说明：
        返回处理后的回答文本；可访问图片会变成 /upload/...，不可访问图片 Markdown 会被移除。
    关键处理流程：
        1. 对已知裸路径做替换或移除，避免暴露 Windows 本地路径；
        2. 对 Markdown 图片语法中的本地路径做存在性校验；
        3. 本地文件不存在时删除该图片 Markdown，防止页面请求 404。
    """
    import re

    # 预扫描：处理 AI 可能直接输出的裸路径 → Web URL
    if known_image_paths:
        for img_path in known_image_paths:
            if not img_path:
                continue
            escaped = re.escape(img_path)
            bare_pattern = r'(?<!\]\()' + escaped
            if not re.search(bare_pattern, text):
                continue
            web_url = _image_to_web_url_if_exists(img_path)
            replacement = web_url or ""
            text = re.sub(bare_pattern, replacement, text)
            print(f"[图片替换-裸路径] ✓ {img_path} → {replacement or '[已移除不存在图片]'}")

    # 正式替换：Markdown ![]() 语法中的本地路径 → Web URL
    markdown_pattern = r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)'
    matches = list(re.finditer(markdown_pattern, text))
    if not matches:
        return text

    print(f"[图片替换] 找到 {len(matches)} 个 Markdown 图片引用")

    def _convert_match(match: re.Match) -> str:
        alt_text = match.group(1)
        image_url = match.group(2)
        # 外部 URL 和 data/blob URL 不做本地存在性校验。
        if image_url.startswith("data:") or image_url.startswith("http://") or image_url.startswith("https://"):
            return match.group(0)
        web_url = _image_to_web_url_if_exists(image_url)
        if not web_url:
            print(f"[图片替换] 移除不存在图片引用: {image_url[:120]}")
            return ""
        print(f"[图片替换] {image_url[:80]} → {web_url}")
        return f'![{alt_text}]({web_url})'

    result_parts = []
    last_end = 0
    for match in matches:
        result_parts.append(text[last_end:match.start()])
        replacement = _convert_match(match)
        result_parts.append(replacement)
        last_end = match.end()
    result_parts.append(text[last_end:])
    return ''.join(result_parts)


def _strip_image_references_for_stream(text: str) -> str:
    """
    功能说明：
        生成流式展示文本，移除图片 Markdown、/upload 路径和 Windows 本地路径。
    参数说明：
        text：模型截至当前 chunk 的累计原始输出。
    返回值说明：
        返回适合流式展示的文本，不包含图片路径和 Markdown 图片语法。
    关键处理流程：
        1. 移除完整 Markdown 图片 `![...](...)`；
        2. 移除尚未闭合的 Markdown 图片片段，防止逐字输出时短暂暴露路径；
        3. 移除裸露的 Windows 绝对路径、/upload 路径和 upload/images 相对路径；
        4. 最终图片由流结束后的 _append_reference_images_to_answer() 统一补充。
    """
    if not text:
        return ""

    import re
    sanitized = text
    sanitized = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", sanitized)

    # 若最后一个图片 Markdown 还未闭合，只展示其之前的内容，避免出现 `![图片](E:/...`。
    last_image_start = sanitized.rfind("![")
    if last_image_start != -1:
        tail = sanitized[last_image_start:]
        if ")" not in tail:
            sanitized = sanitized[:last_image_start]

    # 删除模型可能直接输出的路径或 img_url 行。
    sanitized = re.sub(r"(?im)^.*(?:img_url|image_url|配图路径|本文配图路径).*$(?:\r?\n)?", "", sanitized)
    sanitized = re.sub(r"[A-Za-z]:[/\\][^\s)\]]+", "", sanitized)
    sanitized = re.sub(r"/upload/[^\s)\]]+", "", sanitized)
    sanitized = re.sub(r"\bupload/(?:images|ask)/[^\s)\]]+", "", sanitized)
    return sanitized.rstrip()


async def stream_ai_response(
    id,
    messages: list,
    session_id: int,
    reference_docs,
    reference_ids_str: str,
    api_v1: bool = False,
    trace_id: Optional[int] = None,
    user_message_id: Optional[int] = None,
    decision: Optional[RouteDecision] = None,
    retrieval_query: str = "",
):
    """
    功能说明：
        流式调用大模型生成 AI 回答，并持续回写数据库消息内容。
    参数说明：
        id：预创建的 AI 消息 ID。
        messages：发送给大模型的上下文消息列表。
        session_id：当前对话 ID。
        reference_docs：本轮检索命中的文档结构，用于返回引用和补充图片。
        reference_ids_str：兼容旧字段的文档 ID 字符串。
        api_v1：True 时按 api.md 的 /chats/{chat_id}/completions SSE 结构返回；
                False 时使用内部兼容根字段结构，不再对外注册旧 HTTP 接口。
    返回值说明：
        异步生成 text/event-stream 数据块；结束时发送 {"code":1,"data":"true"}。
    关键处理流程：
        1. 边接收模型 token 边生成“隐藏图片路径”的展示文本发送给前端；
        2. 流结束后统一替换/追加参考图片 Markdown，并持久化最终回答；
        3. 根据 api_v1 切换新接口或内部兼容结构的 SSE JSON 包装格式。
    """
    print("stream ai answer")
    api_key = os.getenv("API_KEY", "EMPTY")
    base_url = get_ai_base_url()
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))
    request_timeout = _get_positive_int_env("AI_REQUEST_TIMEOUT", 60)
    first_chunk_timeout = _get_positive_int_env("AI_STREAM_FIRST_CHUNK_TIMEOUT", 120)
    idle_timeout = _get_positive_int_env("AI_STREAM_IDLE_TIMEOUT", 120)
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=request_timeout)

    data = {}
    data["id"] = id
    data["session_id"] = session_id
    safe_reference_docs = desensitize_value(reference_docs or [])
    data["reference"] = (
        _reference_doc_ids_to_api_reference(safe_reference_docs)
        if api_v1
        else (None if not reference_ids_str else reference_ids_str)
    )
    data["reference_docs"] = safe_reference_docs

    def _format_stream_event(payload: Dict[str, Any], is_error: bool = False) -> str:
        if api_v1:
            envelope = {"code": 102 if is_error else 0, "data": payload}
            if is_error:
                envelope["message"] = payload.get("message", "回答失败")
            return f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def persist_ai_content(content_text: str, with_token_count: bool = False, status_value: str = "success"):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Message).where(Message.id == id))
            ai_msg = result.scalar_one_or_none()
            user_msg = None
            if user_message_id:
                user_result = await db.execute(select(Message).where(Message.id == user_message_id))
                user_msg = user_result.scalar_one_or_none()
            token_count = None
            if ai_msg:
                ai_msg.content_text = content_text
                await db.commit()
                if with_token_count:
                    token_count = await _count_text_tokens(content_text)
                    ai_msg.token_count = token_count
                    await db.commit()
            memory_service = MemoryService(db)
            await memory_service.update_ai_trace(
                trace_id,
                answer=content_text,
                status_value=status_value,
                output_tokens=token_count,
            )
            if with_token_count and status_value == "success":
                if user_msg and ai_msg and decision:
                    await memory_service.refine_after_answer(
                        user_msg,
                        ai_msg,
                        decision,
                        retrieval_query,
                        reference_docs or [],
                        content_text,
                        trace_id=trace_id,
                        source="stream_answer_refinement",
                    )
                await memory_service.maybe_refresh_summary(session_id)

    try:
        stream_start = time.perf_counter()
        yield ": stream-start\n\n"
        print(
            "[AI流式] 已建立SSE连接，开始请求模型 "
            f"base_url={base_url} model={model} max_tokens={max_token} "
            f"request_timeout={request_timeout}s first_chunk_timeout={first_chunk_timeout}s "
            f"idle_timeout={idle_timeout}s"
        )
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_token,
                    stream=True,
                    stream_options={"include_usage": True},
                ),
                timeout=request_timeout,
            )
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"AI模型请求超时：{request_timeout}秒内未创建流式响应，base_url={base_url}, model={model}"
            ) from e
        print(f"[AI流式] 模型流响应已创建，耗时: {time.perf_counter() - stream_start:.3f}s")

        full_content = ""
        usage_payload = None
        last_stream_content = None
        hold_chars = max(0, max_sensitive_term_length() - 1)
        first_chunk_logged = False
        stream_iterator = response.__aiter__()
        first_event_logged = False

        while True:
            next_timeout = first_chunk_timeout if not first_event_logged else idle_timeout
            try:
                chunk = await asyncio.wait_for(stream_iterator.__anext__(), timeout=next_timeout)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError as e:
                phase = "首个chunk" if not first_event_logged else "后续chunk"
                raise TimeoutError(
                    f"AI流式响应{phase}超时：{next_timeout}秒未收到模型数据，base_url={base_url}, model={model}"
                ) from e

            if not first_event_logged:
                first_event_logged = True
                print(f"[AI流式] 首个事件耗时: {time.perf_counter() - stream_start:.3f}s")
            if getattr(chunk, "usage", None) is not None:
                usage_payload = {
                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or getattr(chunk.usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or getattr(chunk.usage, "output_tokens", 0) or 0,
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                }
            if chunk.choices and chunk.choices[0].delta.content:
                if not first_chunk_logged:
                    first_chunk_logged = True
                    print(f"[AI流式] 首个chunk耗时: {time.perf_counter() - stream_start:.3f}s")
                content = chunk.choices[0].delta.content
                full_content += content
                stream_content = _strip_image_references_for_stream(full_content)

                if hold_chars and len(stream_content) > hold_chars:
                    display_source = stream_content[:-hold_chars]
                elif hold_chars:
                    display_source = ""
                else:
                    display_source = stream_content

                stream_content = desensitize_text(display_source)
                if not stream_content or stream_content == last_stream_content:
                    continue
                last_stream_content = stream_content
                data["answer"] = stream_content
                data["final"] = False
                if not api_v1:
                    data["code"] = 1
                yield _format_stream_event(data)

        final_content = full_content.strip()
        already_persisted = False

        # 将 AI 回答中的本地图片路径替换为 Web URL，并在必要时追加结构化参考图片
        if final_content:
            print(f"[AI流式原始回答-前500字]: {final_content[:500]}")
            processed = await _append_reference_images_to_answer(final_content, reference_docs or [])
            if final_content.strip() and not processed.strip():
                processed = "已检索到相关文档，但模型仅返回了不可访问的图片引用，未生成有效文字回答。"
            final_content = desensitize_text(processed)
            if final_content:
                data["answer"] = final_content
                data["final"] = True
                if not api_v1:
                    data["code"] = 1
                await persist_ai_content(final_content, with_token_count=True)
                yield _format_stream_event(data)
                already_persisted = True

        if not final_content:
            final_content = "已检索到相关文档，但回答生成为空，请稍后重试。"
            data["answer"] = final_content
            if not api_v1:
                data["code"] = 1
            await persist_ai_content(final_content, with_token_count=True)
            yield _format_stream_event(data)
        elif not already_persisted:
            final_content = desensitize_text(final_content)
            await persist_ai_content(final_content, with_token_count=True)

        async with AsyncSessionLocal() as usage_db:
            await record_ai_usage(
                usage_db,
                session_id=session_id,
                message_id=id,
                model=model,
                request_type="chat_stream",
                usage=usage_payload,
            )
            await usage_db.commit()

        final_data = {"code": 1, "data": "true"}
        yield f"data: {json.dumps(final_data)}\n\n"
    except Exception as e:
        print(f"[AI流式] 请求失败: {type(e).__name__}: {e}")
        try:
            async with AsyncSessionLocal() as usage_db:
                await record_ai_usage(
                    usage_db,
                    session_id=session_id,
                    message_id=id,
                    model=model,
                    request_type="chat_stream",
                    status="error",
                    error_message=str(e)[:1000],
                )
                await usage_db.commit()
        except Exception as usage_error:
            print(usage_error)
        fallback_content = (
            "AI服务不可用或响应超时，请检查模型服务地址、模型名称和服务状态。"
            if _is_ai_service_unavailable_error(e)
            else "回答生成失败，请稍后重试。"
        )
        try:
            await persist_ai_content(fallback_content, with_token_count=True, status_value="error")
        except Exception as persist_error:
            print(persist_error)
        error_data = {
            "code": 0,
            "message": fallback_content
        }
        yield _format_stream_event(error_data, is_error=True)


async def stream_static_ai_response(
    id: int,
    session_id: int,
    answer: str,
    api_v1: bool = False,
):
    data = {
        "id": id,
        "session_id": session_id,
        "answer": answer,
        "reference": {"total": 0, "doc_aggs": []} if api_v1 else None,
        "reference_docs": "",
        "final": True,
    }
    if not api_v1:
        data["code"] = 1

    def format_event(payload: Dict[str, Any]) -> str:
        if api_v1:
            return f"data: {json.dumps({'code': 0, 'data': payload}, ensure_ascii=False)}\n\n"
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    yield ": stream-start\n\n"
    yield format_event(data)
    yield f"data: {json.dumps({'code': 1, 'data': 'true'}, ensure_ascii=False)}\n\n"


async def _create_completion(
    message: MessageCreateNew,
    db: AsyncSession,
    current_user: User,
    api_v1: bool = False,
):
    """
    功能说明：
        创建用户消息、检索参考文档、构造大模型上下文并生成 AI completion。
    参数说明：
        message：api.md Completion 请求体，包含 question、session_id、stream、user_uploaded_images。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
        api_v1：True 时返回 /api/v1/chats/{chat_id}/completions 规范结构；
                False 为内部兼容包装结构，不再对外注册旧 HTTP 接口。
    返回值说明：
        stream=True 返回 StreamingResponse；stream=False 返回 ResultNew 或内部兼容 Result 数据。
    关键处理流程：
        1. 校验会话归属和上传图片；
        2. 保存用户消息，并基于问题/图片做向量检索；
        3. 生成 RAG prompt 和多模态上下文；
        4. 预创建 AI 消息，流式或非流式生成最终回答。
    """
    trace_id: Optional[int] = None
    try:
        # 校验会话归属（Conversation 表）
        config = get_image_config()

        conv_result = await db.execute(
            select(Conversation).where(
                and_(Conversation.id == message.session_id, Conversation.user_id == current_user.id)
            )
        )
        conversation = conv_result.scalar_one_or_none()

        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "请先新建对话")

        max_order_result = await db.execute(
            select(func.max(Message.message_order)).where(Message.session_id == message.session_id)
        )
        max_order = max_order_result.scalar() or 0

        # 检查消息里引用的图片是否都已存在
        message.user_uploaded_images = await _validate_uploaded_images_exist(message.user_uploaded_images, config)

        user_text_token_count = await _count_text_tokens(message.question)

        # db.commit() → 刷新拿到 message.id
        db_message = Message(
            session_id=message.session_id,
            role=1,
            message_order=max_order + 1,
            content_text=message.question,
            user_uploaded_images=message.user_uploaded_images,
            token_count=user_text_token_count,
            created_time=datetime.now()
        )

        db.add(db_message)
        await db.commit()
        await db.refresh(db_message)

        # 意图识别：寒暄、感谢、确认等直接对话不进入 RAG，避免短文本误召回知识库。
        route_decision = await IntentRouterAgent().route(
            db_message.content_text,
            db_message.user_uploaded_images,
        )
        print(
            "[意图识别] "
            f"route={route_decision.route.value} "
            f"use_rag={route_decision.use_rag} "
            f"confidence={route_decision.confidence} "
            f"source={route_decision.source} "
            f"reason={route_decision.reason}"
        )
        memory_service = MemoryService(db)
        memory_pack = await MemoryPackBuilder(memory_service).build(db_message, route_decision)
        if (
            route_decision.reason == CONTEXTUAL_RETRY_REASON
            and not _contextual_retry_has_base_question(memory_pack)
        ):
            print("[上下文重答] 当前会话没有可恢复的主问题，改为澄清并跳过RAG")
            route_decision = RouteDecision(
                route=IntentRoute.CLARIFY,
                use_rag=False,
                confidence=route_decision.confidence,
                reason="contextual_retry_without_context",
                need_clarification=True,
                clarification_question="当前对话里还没有可重新回答的问题，请先描述具体设备、故障现象或报错信息。",
                source=route_decision.source,
            )
            memory_pack = await MemoryPackBuilder(memory_service).build(db_message, route_decision)
        print(
            "[MemoryPack] "
            f"strategy={memory_pack.strategy} "
            f"complexity={memory_pack.complexity} "
            f"actions={memory_pack.actions}"
        )
        selected_skill = SkillRegistry().select(route_decision, memory_pack)
        print(
            "[SkillRegistry] "
            f"skill={selected_skill.name} "
            f"prompt={selected_skill.prompt_file} "
            f"description={selected_skill.description}"
        )

        if route_decision.route.value == ANSWER_AUDIT_ROUTE and _get_bool_env("ANSWER_AUDIT_ENABLED", True):
            answer, audit_actions, audit_validation = await _build_answer_audit_response(
                db,
                db_message,
                memory_pack=memory_pack,
                decision=route_decision,
                skill=selected_skill,
            )
            answer_token_count = await _count_text_tokens(answer)
            conversation.updated_time = datetime.now()
            if max_order == 0 or conversation.title == "新对话":
                conversation.title = await get_new_title_by_ai(message.question)

            ai_msg = Message(
                session_id=message.session_id,
                role=0,
                message_order=max_order + 2,
                content_text=answer,
                ai_reference_doc_ids="",
                token_count=answer_token_count,
                created_time=datetime.now(),
            )
            db.add(ai_msg)
            await db.commit()
            await db.refresh(ai_msg)

            await memory_service.create_ai_trace(
                db_message,
                ai_msg,
                route_decision,
                retrieval_query=db_message.content_text,
                reference_docs=[],
                actions=audit_actions,
                used_previous_refs=False,
                validation={**audit_validation, **memory_pack.to_trace_validation()},
                answer=answer,
                status_value="success",
                output_tokens=answer_token_count,
            )
            await memory_service.maybe_refresh_summary(message.session_id)

            data = {
                "answer": answer,
                "reference": {"total": 0, "doc_aggs": []} if api_v1 else "",
                "reference_docs": "",
                "id": ai_msg.id,
                "session_id": message.session_id,
            }
            if message.stream:
                return StreamingResponse(
                    stream_static_ai_response(
                        ai_msg.id,
                        message.session_id,
                        answer,
                        api_v1=api_v1,
                    ),
                    media_type="text/event-stream",
                )
            return ResultNew.result(0, None, data) if api_v1 else Result.success_with_data(data)

        # 向量检索
        retrieval_query = route_decision.query_rewrite or db_message.content_text
        used_previous_refs = False
        retrieval_actions: List[str] = []
        if route_decision.use_rag:
            retrieval_query = await _resolve_retrieval_query(
                db,
                db_message,
                route_decision,
                memory_pack=memory_pack,
            )
            if not _rag_query_passes_context_gate(retrieval_query, db_message.user_uploaded_images):
                print(
                    "[RAG Gate] 检索问题缺少有效维修领域锚点，改为澄清并跳过RAG "
                    f"query={retrieval_query or db_message.content_text}"
                )
                route_decision = RouteDecision(
                    route=IntentRoute.CLARIFY,
                    use_rag=False,
                    confidence=route_decision.confidence,
                    reason="rag_query_without_domain_anchor",
                    need_clarification=True,
                    clarification_question="当前问题缺少可检索的设备、故障现象、报错码或参数信息，请补充后我再帮您排查。",
                    source=route_decision.source,
                )
                memory_pack = await MemoryPackBuilder(memory_service).build(db_message, route_decision)
                selected_skill = SkillRegistry().select(route_decision, memory_pack)
                retrieval_query = db_message.content_text
                ai_reference_documents = []
                retrieval_actions.append("rag_gate_clarify")
            else:
                reusable_reference_documents = []
                if route_decision.reason in {CONTEXTUAL_FOLLOWUP_REASON, CONTEXTUAL_RETRY_REASON}:
                    reusable_reference_documents = await _load_reusable_reference_documents_from_memory(
                        memory_service,
                        memory_pack,
                        db_message,
                    )
                if reusable_reference_documents:
                    print(
                        "[上下文改写] 复用上一轮参考文档 "
                        f"count={len(reusable_reference_documents)}"
                    )
                    ai_reference_documents = reusable_reference_documents
                    used_previous_refs = True
                    retrieval_actions.append("reuse_previous_references")
                else:
                    ai_reference_documents, retrieval_actions = await _adaptive_retrieve_reference_documents(
                        db,
                        retrieval_query,
                        db_message.user_uploaded_images,
                        route_decision,
                        memory_pack,
                    )
                    ai_reference_documents = _filter_reference_documents_by_confidence(
                        ai_reference_documents,
                        route_decision,
                    )
                    ai_reference_documents = _filter_reference_documents_by_query_terms(
                        ai_reference_documents,
                        retrieval_query,
                        route_decision,
                    )
        else:
            ai_reference_documents = []
        ai_reference_document_ids = get_ai_reference_document_ids(ai_reference_documents)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)

        # 传给 generate_messages() 用于构建 prompt
        messages = await generate_messages(
            db,
            db_message.session_id,
            db_message,
            ai_reference_documents,
            retrieval_query=retrieval_query,
            memory_pack=memory_pack,
            skill=selected_skill,
        )
        ai_reference_document_payload = get_ai_reference_documents_payload(ai_reference_documents)

        conversation.updated_time = datetime.now()

        if max_order == 0 or conversation.title == "新对话":
            new_title = await get_new_title_by_ai(message.question)
            conversation.title = new_title

        ai_msg = Message(
            session_id=message.session_id,
            role=0,
            message_order=max_order + 2,
            content_text="回答生成中，请稍后刷新。",
            ai_reference_doc_ids=ai_reference_document_payload,
            token_count=0,
            created_time=datetime.now()
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        workflow_actions = ["intent_route", f"select_skill:{selected_skill.name}"]
        workflow_actions.extend(selected_skill.actions)
        workflow_actions.extend(memory_pack.actions)
        if route_decision.use_rag:
            if route_decision.reason in {CONTEXTUAL_FOLLOWUP_REASON, CONTEXTUAL_RETRY_REASON}:
                workflow_actions.append("resolve_contextual_query")
            workflow_actions.extend(retrieval_actions or ["reuse_previous_references" if used_previous_refs else "rag_search"])
            workflow_actions.extend(["build_prompt", "generate_answer"])
        else:
            workflow_actions.append("direct_generate_answer")

        trace_validation = memory_pack.to_trace_validation()
        trace_validation.update(selected_skill.to_trace_validation())
        trace_validation.update({
            "reference_count": len(ai_reference_documents or []),
            "reference_ids": ai_reference_document_ids,
        })

        trace_id = await memory_service.create_ai_trace(
            db_message,
            ai_msg,
            route_decision,
            retrieval_query=retrieval_query,
            reference_docs=ai_reference_documents,
            actions=workflow_actions,
            used_previous_refs=used_previous_refs,
            validation=trace_validation,
            status_value="pending" if message.stream else "generating",
        )
        await memory_service.update_active_context(
            db_message,
            ai_msg,
            route_decision,
            retrieval_query,
            ai_reference_documents,
            trace_id=trace_id,
        )

        # 传给 stream_ai_response() 用于构建 reference.doc_aggs 和图片
        if message.stream:
            return StreamingResponse(
                stream_ai_response(
                    ai_msg.id,
                    messages,
                    message.session_id,
                    ai_reference_documents,
                    ai_reference_document_ids_str,
                    api_v1=api_v1,
                    trace_id=trace_id,
                    user_message_id=db_message.id,
                    decision=route_decision,
                    retrieval_query=retrieval_query,
                ),
                media_type="text/event-stream"
            )

        ai_msg = await get_ai_answer(db, messages, ai_msg.id, ai_reference_documents)
        answer = ai_msg.content_text if ai_msg else ""
        await memory_service.update_ai_trace(
            trace_id,
            answer=answer,
            status_value="success",
            output_tokens=ai_msg.token_count if ai_msg else None,
        )
        if ai_msg:
            await memory_service.refine_after_answer(
                db_message,
                ai_msg,
                route_decision,
                retrieval_query,
                ai_reference_documents,
                answer,
                trace_id=trace_id,
            )
        await memory_service.maybe_refresh_summary(message.session_id)
        data = {
            "answer": answer,
            "reference": _reference_doc_ids_to_api_reference(ai_reference_documents) if api_v1 else ai_reference_document_ids_str,
            "reference_docs": ai_reference_document_payload,
            "id": ai_msg.id if ai_msg else None,
            "session_id": message.session_id,
        }
        return ResultNew.result(0, None, data) if api_v1 else Result.success_with_data(data)


    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        print(e)
        if trace_id:
            try:
                async with AsyncSessionLocal() as trace_db:
                    await MemoryService(trace_db).update_ai_trace(
                        trace_id,
                        status_value="error",
                        error_message=str(e),
                    )
            except Exception as trace_error:
                print(trace_error)
        if _is_ai_service_unavailable_error(e):
            raise AppException(
                status.HTTP_502_BAD_GATEWAY,
                BizCode.AI_SERVICE_UNAVAILABLE,
                "AI服务不可用，请稍后重试"
            )
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试")


async def ask(message: MessageCreate,
              db: AsyncSession = Depends(get_db),
              current_user: User = Depends(get_current_active_user)):
    """
    功能说明：
        旧版 AI 提问函数，仅保留为内部兼容包装，不再注册为 HTTP 路由。
    参数说明：
        message：旧版请求体，content_text 为用户问题。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        默认返回 text/event-stream；SSE 结构保持旧版根字段 code/answer/reference_docs。
    关键处理流程：
        将旧字段 content_text 映射为 api.md 的 question 后复用统一 completion 实现。
    """
    completion = MessageCreateNew(
        question=message.content_text or "",
        session_id=message.session_id,
        stream=message.stream,
        user_uploaded_images=message.user_uploaded_images,
    )
    return await _create_completion(completion, db, current_user, api_v1=False)


@chat_router.post("/{chat_id}/session", summary="创建聊天助手对话")
async def create_chat_session(
    chat_id: str,
    conversation_create: ConversationCreateNew,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    功能说明：
        按 api.md 创建一个 Chat Session。
    参数说明：
        chat_id：聊天助手 ID，测试环境可传 test。
        conversation_create：请求体，name 为会话名称。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        返回 ResultNew(code=0)，data 为新建会话信息。
    关键处理流程：
        在现有 conversation 表中创建记录，并把内部 title 映射到接口规范的 name。
    """
    try:
        now = datetime.now()
        conversation = Conversation(
            title=conversation_create.name or "新对话",
            user_id=current_user.id,
            created_time=now,
            updated_time=now,
        )
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return ResultNew.result(0, None, _conversation_to_chat_session(chat_id, conversation, []))
    except Exception as e:
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "创建对话失败")


@chat_router.put("/{chat_id}/session/{session_id}", summary="更新对话名称")
async def update_chat_session(
    chat_id: str,
    session_id: int,
    conversation_create: ConversationCreateNew,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    功能说明：
        按 api.md 更新指定 Chat Session 的名称。
    参数说明：
        chat_id：聊天助手 ID，仅用于符合路径规范。
        session_id：会话 ID。
        conversation_create：请求体，name 为新的会话名称。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        成功返回 ResultNew(code=0,data=None)。
    关键处理流程：
        校验会话存在且属于当前用户后更新 conversation.title。
    """
    try:
        result = await db.execute(
            select(Conversation).where(
                and_(Conversation.id == session_id, Conversation.user_id == current_user.id)
            )
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "对话不存在")
        conversation.title = conversation_create.name
        conversation.updated_time = datetime.now()
        await db.commit()
        return ResultNew.result(0, None, None)
    except AppException:
        raise
    except Exception as e:
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "更新对话失败")


@chat_router.get("/{chat_id}/sessions", summary="获取对话列表或对话消息")
async def get_chat_sessions(
    chat_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1),
    order_by: str = Query("create_time"),
    desc_order: bool = Query(True, alias="desc"),
    name: Optional[str] = Query(None),
    id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    功能说明：
        按 api.md 获取当前用户的会话列表；传入 id 时返回该会话及其消息。
    参数说明：
        chat_id：聊天助手 ID。
        page/page_size：分页参数。
        order_by：排序字段，支持 create_time、update_time。
        desc_order：是否倒序，对应 Query 参数 desc。
        name：按会话名称模糊过滤。
        id：指定会话 ID，用于“获取对话消息”场景。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        返回 ResultNew(code=0)，data 包含 total、page、page_size、sessions。
    关键处理流程：
        查询 conversation 表并按需加载 message 表，统一转换为 Chat Session 结构。
    """
    try:
        if order_by not in {"create_time", "update_time"}:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "排序方式有误")

        conditions = [Conversation.user_id == current_user.id]
        if name:
            conditions.append(Conversation.title.like(f"%{name}%"))
        if id is not None:
            conditions.append(Conversation.id == id)

        order_field = Conversation.created_time if order_by == "create_time" else Conversation.updated_time
        order_clause = desc(order_field) if desc_order else asc(order_field)
        offset = (page - 1) * page_size

        total_count_result = await db.execute(select(func.count()).select_from(Conversation).where(*conditions))
        total_count = total_count_result.scalar_one()

        result = await db.execute(
            select(Conversation).where(*conditions).order_by(order_clause).offset(offset).limit(page_size)
        )
        conversations = result.scalars().all()

        sessions = []
        for conversation in conversations:
            msg_result = await db.execute(
                select(Message).where(Message.session_id == conversation.id).order_by(Message.created_time.asc(), Message.id.asc())
            )
            messages = list(msg_result.scalars().all()) if id is not None else []
            sessions.append(_conversation_to_chat_session(chat_id, conversation, messages))

        return ResultNew.result(0, None, {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "sessions": sessions,
        })
    except AppException:
        raise
    except Exception as e:
        print(e)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "查询对话失败")


@chat_router.delete("/{chat_id}/sessions", summary="删除对话")
async def delete_chat_sessions(
    chat_id: str,
    ids: ConversationDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    功能说明：
        按 api.md 批量删除 Chat Session。
    参数说明：
        chat_id：聊天助手 ID，仅用于符合路径规范。
        ids：请求体，ids 为会话 ID 数组。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        成功返回 ResultNew(code=0,data=None)；无权限删除的 ID 会在 message 中提示。
    关键处理流程：
        逐个校验会话归属，先删除 message，再删除 conversation，避免孤儿消息。
    """
    try:
        forbidden_ids = []
        for session_id in ids.ids:
            result = await db.execute(select(Conversation).where(Conversation.id == session_id))
            conversation = result.scalar_one_or_none()
            if not conversation:
                continue
            if conversation.user_id != current_user.id:
                forbidden_ids.append(session_id)
                continue
            await MemoryService(db).delete_session_runtime_state(session_id)
            await db.execute(delete(Message).where(Message.session_id == session_id))
            await db.execute(delete(Conversation).where(Conversation.id == session_id))
        await db.commit()
        msg = None
        if forbidden_ids:
            msg = "对话" + ", ".join(map(str, forbidden_ids)) + "无权限删除"
        return ResultNew.result(0, msg, None)
    except Exception as e:
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "删除对话失败")


@chat_router.post("/{chat_id}/completions", summary="AI 问答")
async def create_chat_completion(
    chat_id: str,
    message: MessageCreateNew,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    功能说明：
        按 api.md 创建 AI Completion，替代废弃的旧问答入口。
    参数说明：
        chat_id：聊天助手 ID，当前项目不区分助手实例，保留用于符合接口规范。
        message：请求体，question/session_id/stream/user_uploaded_images。
        db：当前异步数据库会话。
        current_user：认证后的当前用户。
    返回值说明：
        stream=true 返回 text/event-stream；stream=false 返回 ResultNew(code=0,data={answer,...})。
    关键处理流程：
        复用 _create_completion 的正式 AI 对话逻辑，并启用 api_v1 响应包装。
    """
    return await _create_completion(message, db, current_user, api_v1=True)

async def generate_messages(
    db,
    id,
    message_now,
    documents_id,
    retrieval_query: Optional[str] = None,
    memory_pack: Optional[MemoryPack] = None,
    skill: Optional[AgentSkill] = None,
):
    """
    功能说明：
        构建发送给大模型的上下文消息列表，包含最近历史、RAG 提示词和多模态图片。
    参数说明：
        db：当前异步数据库会话。
        id：会话 ID。
        message_now：本轮用户消息 ORM 对象。
        documents_id：本轮检索命中的文档引用，包含 doc_id/library_type/chunks。
        retrieval_query：结合上下文改写后的检索问题；为空时使用当前用户原问题。
        memory_pack：按意图组装的短期记忆包，包含 active_context 和长对话摘要。
        skill：本轮选中的业务 Skill，用于决定回答 prompt 的组织方式。
    返回值说明：
        返回 OpenAI Chat Completions 兼容的 messages 数组。
    关键处理流程：
        1. 按 token 预算截取最近历史消息；
        2. 根据检索文档生成受 token 限制的 RAG prompt；
        3. 将命中文档图片和用户上传图片编码为 data URL，供多模态模型理解；
        4. 末尾追加当前用户问题。
    """
    print("generate_messages")
    message_order = max(message_now.message_order - 6, 0)

    result = await db.execute(
        select(Message)
        .where(Message.session_id == id, Message.message_order > message_order)
        .order_by(desc(Message.created_time))
    )
    messages_db = result.scalars().all()

    messages = []
    config = get_image_config()
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    print("get_config")
    tokens = 0

    user_question_tokens = await _get_or_update_text_token_count(db, message_now)

    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        user_question_tokens += _estimate_image_tokens(message_now.user_uploaded_images, 578)

    tokens_max -= user_question_tokens

    if tokens_max < 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.MESSAGE_CONTEXT_TOO_LONG, "消息长度过长")

    flag = 0
    cached_tokens_updated = False

    if messages_db:
        print("messages_db")
        for message in messages_db:
            if flag == 0:
                flag = 1
                continue
            data = {}
            token_tmp = 0
            role = "user" if message.role == 1 else "assistant"
            msg_text = []
            msg_text.append({"type": "text", "text": desensitize_text(message.content_text)})

            had_cached_token = int(getattr(message, "token_count", 0) or 0) > 0 or not message.content_text
            token_tmp += await _get_or_update_text_token_count(db, message)
            cached_tokens_updated = cached_tokens_updated or not had_cached_token

            if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
                images = _split_uploaded_images(message.user_uploaded_images)
                for image in images:

                    image_compressed = await compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image))

                    mime_type, _ = mimetypes.guess_type(image_compressed)
                    if mime_type is None:
                        ext = os.path.splitext(image_compressed)[1].lower()
                        mime_type = {
                            '.png': 'image/png',
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.webp': 'image/webp',
                            '.bmp': 'image/bmp'
                        }.get(ext, 'image/jpeg')
                    image_base64 = image_to_base64(image_compressed)
                    msg_text.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                    })
                    token_tmp += 258
            data["role"] = role
            data["content"] = msg_text

            if tokens + token_tmp >= tokens_max:
                break
            messages.append(data)
            tokens += token_tmp
            print(f"token: {tokens}")
            if tokens > tokens_max:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.MESSAGE_CONTEXT_TOO_LONG, "对话内容达到上限，请重新创建对话")
    if cached_tokens_updated:
        await db.commit()
    data = {}
    messages.reverse()
    print(f"tokens: {tokens}")

    tokens_tmp = tokens_max - tokens
    prompt = await get_prompt(db, documents_id, tokens_tmp)
    memory_prompt = _build_memory_prompt(memory_pack)
    selected_skill = skill or SkillRegistry().select_for_memory(memory_pack)
    prompt_text = SkillPromptBuilder().build_completion_prompt(
        question=message_now.content_text,
        skill=selected_skill,
        memory_pack=memory_pack,
        memory_prompt=memory_prompt,
        rag_prompt=prompt,
        retrieval_query=retrieval_query,
    )

    msg_content = [{
        "type": "text",
        "text": desensitize_text(prompt_text),
    }]

    # 添加文档中命中的参考图片，让多模态模型可结合图片内容回答；最终展示仍由后端追加 Markdown 图片保证稳定。
    doc_image_urls = _collect_reference_image_paths(
        documents_id if prompt and isinstance(documents_id, list) else [],
        max_images=int(os.getenv("MAX_DOC_IMAGES", 5)),
    )
    print(f"[图片排查][generate_messages] doc_image_urls={doc_image_urls}")
    for image in doc_image_urls:
        image_path = os.path.join(config["MESSAGE_BASE_DIR"], image.lstrip("/"))
        print(f"[图片排查][generate_messages] 准备加入模型图片 image={image} resolved_path={image_path}")
        if not await asyncio.to_thread(os.path.exists, image_path):
            print(f"[图片排查][generate_messages] 文件不存在，跳过模型图片: {image_path}")
            continue
        try:
            image_compressed = await compress_image(image_path, max_size=512)
            mime_type, _ = mimetypes.guess_type(image_compressed)
            if mime_type is None:
                ext = os.path.splitext(image_compressed)[1].lower()
                mime_type = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp'
                }.get(ext, 'image/jpeg')
            image_base64 = image_to_base64(image_compressed)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
            print(f"[图片排查][generate_messages] 已加入模型图片: {image_compressed}")
        except Exception as e:
            print(f"添加文档图片失败 {image}: {e}")

    print(f"[图片排查][generate_messages] msg_content_types={[item.get('type') for item in msg_content]}")
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        images = _split_uploaded_images(message_now.user_uploaded_images)
        for image in images:
            image_compressed = await compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image), max_size=768)
            print(image_compressed)
            mime_type, _ = mimetypes.guess_type(image_compressed)
            if mime_type is None:
                ext = os.path.splitext(image_compressed)[1].lower()
                mime_type = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp'
                }.get(ext, 'image/jpeg')
            image_base64 = image_to_base64(image_compressed)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
    data["role"] = "user"
    data["content"] = msg_content

    messages.append(data)
    print(len(messages))

    return messages

async def get_new_title_by_ai(content):
    """使用大模型为新会话生成简短标题。"""

    content = desensitize_text(content)

    messages = [{
        "role": "user",
        "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求正式简洁，并且只给出标题，不要有任何多余内容。\n内容：{content}"
    }]

    api_key = os.getenv("API_KEY", "EMPTY")
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))

    def _call_openai():
        client = OpenAI(base_url=get_ai_base_url_alt(), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token
        )
        return response.choices[0].message.content

    new_title = await asyncio.to_thread(_call_openai)
    new_title = desensitize_text(new_title)
    print("new_title: ", new_title)
    if len(new_title) > 15 or len(new_title) == 0:
        new_title = "新对话"

    return new_title

async def get_reference_documents(
    db,
    question: str,
    image: str = None,
    top_k: int = -1,
    top_k_documents: int = -1,
):
    """向量检索相关文档，并返回可展示的文档匹配信息。"""
    from utils.VectorService import VectorService

    vector_service = VectorService(db)
    documents = await vector_service.search_similar_documents(
        question,
        image,
        top_k=top_k,
        top_k_documents=top_k_documents,
    )
    print(f"[图片排查][search_raw] docs={len(documents or [])}")
    _debug_reference_doc_image_summary(documents, "search_raw")
    normalized_docs = []
    for doc in documents:
        doc_id = doc.get("doc_id")
        if doc_id is None:
            continue
        score = float(doc.get("score", 0.0))
        library_type = _normalize_library_type(doc.get("library_type", "breakdown"))
        normalized_docs.append({
            "doc_id": int(doc_id),
            "library_type": library_type,
            "title": doc.get("title", ""),
            "score": round(score, 6),
            "chunks": doc.get("chunks") or [],
            "matched_image_urls": doc.get("matched_image_urls") or [],
        })

    if not normalized_docs:
        print("[图片排查][search_normalized] 无可用检索结果")
        return []
    _debug_reference_doc_image_summary(normalized_docs, "search_normalized")

    # 遍历结果，按库类型回查 MySQL 确认文档未被软删除  
    active_map = {}
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        candidate_ids = [doc["doc_id"] for doc in normalized_docs if doc["library_type"] == library_type]
        if not candidate_ids:
            continue
        active_docs_result = await db.execute(
            select(document_model.id, document_model.title).where(
                document_model.id.in_(candidate_ids),
                document_model.is_deleted == 0,
            )
        )
        active_map.update({(library_type, row.id): row.title for row in active_docs_result.all()})

    filtered_docs = []
    for doc in normalized_docs:
        doc_id = int(doc["doc_id"])
        library_type = _normalize_library_type(doc.get("library_type", "breakdown"))
        if (library_type, doc_id) not in active_map:
            continue
        filtered_docs.append({
            "doc_id": doc_id,
            "library_type": library_type,
            "title": active_map.get((library_type, doc_id)) or doc.get("title", ""),
            "score": doc.get("score", 0.0),
            "chunks": doc.get("chunks") or [],
            "matched_image_urls": doc.get("matched_image_urls") or [],
        })
    _debug_reference_doc_image_summary(filtered_docs, "search_filtered")
    return filtered_docs


def get_ai_reference_document_ids(reference_docs: List[Dict[str, Any]]) -> List[str]:
    """从参考文档中提取文档 id 列表（用于拼接提示词）。"""
    return [f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc['doc_id'])}" for doc in reference_docs if doc.get("doc_id") is not None]

def get_ai_reference_prompt_refs(reference_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保留命中的 chunk/section 信息，用于组装更精确的 RAG prompt。"""
    refs = []
    for doc in reference_docs:
        if doc.get("doc_id") is None:
            continue
        refs.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
            "chunks": doc.get("chunks") or [],
            "matched_image_urls": doc.get("matched_image_urls") or [],
        })
    return refs

def _chunk_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
    metadata = chunk.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}

def _section_has_body(section: KnowledgeDocumentSection) -> bool:
    return bool(str(section.plain_text or "").strip())

def _section_marker(section: KnowledgeDocumentSection) -> str:
    return str(section.section_type or "").strip()

def _is_child_or_self_section(candidate: KnowledgeDocumentSection, parent: KnowledgeDocumentSection) -> bool:
    parent_marker = _section_marker(parent)
    candidate_marker = _section_marker(candidate)
    if not parent_marker or not candidate_marker:
        return False
    return candidate_marker == parent_marker or candidate_marker.startswith(parent_marker + ".")

def _select_prompt_sections(
    sections: List[KnowledgeDocumentSection],
    matched_chunks: List[Dict[str, Any]],
    max_sections: int = 10,
) -> List[KnowledgeDocumentSection]:
    if not sections:
        return []

    by_id = {section.id: section for section in sections if section.id is not None}
    by_index = {section.section_index: section for section in sections}
    selected: List[KnowledgeDocumentSection] = []
    selected_ids = set()

    def add(section: Optional[KnowledgeDocumentSection]):
        if not section or section.id in selected_ids:
            return
        if not _section_has_body(section):
            return
        selected.append(section)
        selected_ids.add(section.id)

    matched_sections: List[KnowledgeDocumentSection] = []
    for chunk in matched_chunks or []:
        metadata = _chunk_metadata(chunk)
        section = None
        section_id = metadata.get("section_id")
        if section_id is not None:
            try:
                section = by_id.get(int(section_id))
            except Exception:
                section = None
        if section is None and metadata.get("section_index") is not None:
            try:
                section = by_index.get(int(metadata.get("section_index")))
            except Exception:
                section = None
        if section and section not in matched_sections:
            matched_sections.append(section)

    for section in matched_sections:
        add(section)
        current_pos = sections.index(section)
        if current_pos > 0:
            add(sections[current_pos - 1])
        if current_pos + 1 < len(sections):
            add(sections[current_pos + 1])
        for candidate in sections:
            if len(selected) >= max_sections:
                break
            if _is_child_or_self_section(candidate, section):
                add(candidate)

    if not selected:
        for section in sections:
            add(section)
            if len(selected) >= max_sections:
                break

    return selected[:max_sections]


def _matched_chunk_texts(matched_chunks: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    """
    功能说明：
        从检索命中的 Chunk 中抽取用于 RAG Prompt 的文本，并附带图片路径提示。
    参数说明：
        matched_chunks：向量检索返回的 Chunk 列表。
        limit：最多抽取的 Chunk 数量。
    返回值说明：
        返回去重后的文本片段列表；若 Chunk 带 image_url，会追加“本文配图路径”。
    关键处理流程：
        按检索顺序去重，保留图片路径给模型引用，后端最终会将路径转为可渲染 URL。
    """
    texts = []
    seen = set()
    for chunk in matched_chunks or []:
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        marker = content[:300]
        if marker in seen:
            continue
        seen.add(marker)
        img_url = chunk.get("image_url", "")
        if img_url:
            content += "\n[本文包含配图，图片由系统在回答结束后展示；请勿输出图片路径或 Markdown 图片语法。]"
        texts.append(content)
        if len(texts) >= limit:
            break
    return texts


def _matched_chunk_image_urls(matched_chunks: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    """提取实际写入 prompt 的命中 chunk 图片，和 _matched_chunk_texts 保持同样的去重/截断规则。"""
    images: List[str] = []
    seen = set()
    included = 0
    for chunk in matched_chunks or []:
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        marker = content[:300]
        if marker in seen:
            continue
        seen.add(marker)
        _extend_unique_images(images, chunk.get("image_url"))
        included += 1
        if included >= limit:
            break
    return images


def _append_prompt_piece(parts: List[str], text: str, remaining_tokens: int) -> int:
    text = str(text or "").strip()
    if not text or remaining_tokens <= 0:
        return 0
    token_count = _count_text_tokens_cached(text)
    if token_count <= remaining_tokens:
        parts.append(text)
        return token_count
    truncated = _truncate_text_to_token_budget(text, remaining_tokens)
    if truncated:
        parts.append(truncated)
        return _count_text_tokens_cached(truncated)
    return 0

async def get_prompt(db, document_ids, max_tokens):
    """
    功能说明：
        根据检索文档组装 RAG 提示词，并严格受 token 上限约束。
    参数说明：
        db：当前异步数据库会话。
        document_ids：参考文档列表，元素包含 doc_id、library_type、chunks。
        max_tokens：提示词最大 token 预算。
    返回值说明：
        返回最终提示词字符串；无可用文档时返回空字符串。
    关键处理流程：
        1. 按库类型回查有效文档；
        2. 知识库文档优先选择命中 Chunk 附近章节；
        3. Chunk 仅写入“存在配图”的提示，不暴露真实路径；
        4. 超过 token 预算时进行截断，避免上下文超限。
    """
    if not document_ids:
        return ""
    tokens = 0
    prompts = []

    document_refs = []
    reference_doc_by_ref = {}
    seen_refs = set()
    for value in document_ids:
        reference_doc = value if isinstance(value, dict) else None
        if isinstance(value, dict):
            library_type = _normalize_library_type(value.get("library_type", "breakdown"))
            doc_id = int(value.get("doc_id"))
            chunks = value.get("chunks") or []
        else:
            library_type, _, raw_doc_id = str(value).partition(":")
            library_type = _normalize_library_type(library_type)
            doc_id = int(raw_doc_id or library_type)
            chunks = []

        ref_key = (library_type, doc_id)
        if ref_key in seen_refs:
            continue
        seen_refs.add(ref_key)
        if reference_doc is not None:
            reference_doc_by_ref[ref_key] = reference_doc
        document_refs.append((library_type, doc_id, chunks))

    documents_by_ref = {}
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        ids = [doc_id for ref_library_type, doc_id, _chunks in document_refs if ref_library_type == library_type]
        if not ids:
            continue
        result = await db.execute(
            select(document_model).where(
                document_model.id.in_(ids),
                document_model.is_deleted == 0,
            )
        )
        for document in result.scalars().all():
            documents_by_ref[(library_type, int(document.id))] = document

    documents = [
        documents_by_ref[(library_type, doc_id)]
        for library_type, doc_id, _chunks in document_refs
        if (library_type, doc_id) in documents_by_ref
    ]

    for i, document in enumerate(documents):
        if not document:
            continue
        document_library_type = _normalize_library_type(getattr(document, "library_type", "breakdown"))
        reference_doc = reference_doc_by_ref.get((document_library_type, int(document.id)))
        if document_library_type == "knowledge":
            matched_chunks = []
            for ref_library_type, ref_doc_id, chunks in document_refs:
                if ref_library_type == "knowledge" and ref_doc_id == document.id:
                    matched_chunks.extend(chunks or [])
            print(f"RAG prompt doc={document.id} matched_chunks={len(matched_chunks)} max_tokens={max_tokens}")
            section_result = await db.execute(
                select(KnowledgeDocumentSection)
                .where(KnowledgeDocumentSection.document_id == document.id)
                .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
            )
            all_sections = list(section_result.scalars().all())
            selected_sections = _select_prompt_sections(all_sections, matched_chunks, max_sections=10)
            doc_parts = [f"【知识库文档{i + 1}】：{document.title}"]
            remaining = max_tokens - tokens - _count_text_tokens_cached(doc_parts[0])
            prompt_sections: List[KnowledgeDocumentSection] = []

            matched_texts = _matched_chunk_texts(matched_chunks, limit=8)
            if matched_texts and remaining > 0:
                used = _append_prompt_piece(
                    doc_parts,
                    "命中的相关片段：\n" + "\n\n".join(
                        f"片段{index + 1}：\n{text}" for index, text in enumerate(matched_texts)
                    ),
                    remaining,
                )
                remaining -= used

            if selected_sections and remaining > 0:
                section_parts = []
                for section in selected_sections:
                    section_piece = f"{section.section_title or '未命名章节'}：{section.plain_text or ''}"
                    used = _append_prompt_piece(section_parts, section_piece, remaining)
                    remaining -= used
                    if used > 0:
                        prompt_sections.append(section)
                    if remaining <= 0:
                        break
                if section_parts:
                    doc_parts.append("相关章节内容：\n" + "\n\n".join(section_parts))

            if reference_doc is not None:
                evidence_images: List[str] = []
                for section in prompt_sections:
                    _extend_unique_images(evidence_images, _section_image_urls(section))
                _extend_unique_images(evidence_images, _matched_chunk_image_urls(matched_chunks, limit=8))
                if not (
                    reference_doc.get("reused_from_history")
                    and reference_doc.get("evidence_image_urls")
                ):
                    reference_doc["evidence_section_ids"] = [
                        int(section.id) for section in prompt_sections if section.id is not None
                    ]
                    reference_doc["evidence_section_titles"] = [
                        section.section_title or "未命名章节" for section in prompt_sections
                    ]
                    reference_doc["evidence_image_urls"] = evidence_images
                print(
                    f"[图片排查][evidence] doc=knowledge:{document.id} "
                    f"sections={reference_doc['evidence_section_titles']} "
                    f"images={reference_doc.get('evidence_image_urls') or evidence_images}"
                )

            doc_prompt = "\n".join(part for part in doc_parts if part).strip()
        else:
            if reference_doc is not None:
                evidence_images = _breakdown_document_image_urls(document)
                if not (
                    reference_doc.get("reused_from_history")
                    and reference_doc.get("evidence_image_urls")
                ):
                    reference_doc["evidence_section_ids"] = []
                    reference_doc["evidence_section_titles"] = ["完整故障文档"]
                    reference_doc["evidence_image_urls"] = evidence_images
                print(
                    f"[图片排查][evidence] doc=breakdown:{document.id} "
                    f"sections={reference_doc['evidence_section_titles']} "
                    f"images={reference_doc.get('evidence_image_urls') or evidence_images}"
                )
            doc_prompt = f"""【文档{i + 1}】：{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
        doc_prompt = desensitize_text(doc_prompt)
        token_tmp = _count_text_tokens_cached(doc_prompt)

        if tokens + token_tmp >= max_tokens:
            remaining = max_tokens - tokens
            doc_prompt = _truncate_text_to_token_budget(doc_prompt, remaining)
            token_tmp = _count_text_tokens_cached(doc_prompt)
            if not doc_prompt or token_tmp <= 0:
                break
        tokens += token_tmp
        prompts.append(doc_prompt)
        print(f"RAG prompt appended doc={getattr(document, 'id', '')} tokens={token_tmp} total={tokens}")

    # 增加统一指令
    if prompts:
        final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
        final_prompt += "\n---\n".join(prompts)
        reference_image_docs = (
            document_ids
            if isinstance(document_ids, list) and all(isinstance(item, dict) for item in document_ids)
            else []
        )
        has_reference_images = bool(_collect_reference_image_paths(reference_image_docs, max_images=1))
        if has_reference_images:
            final_prompt += "\n如需结合文档配图，请在文字中说明“见下方参考图片”，不要输出任何图片路径、服务器路径或 Markdown 图片语法；图片由系统在回答结束后统一展示。"
        return desensitize_text(final_prompt)

    return ""

def get_ai_reference_document_ids_str(ai_reference_document_ids):
    """将文档 id 列表转为逗号分隔字符串，便于存储。"""
    if len(ai_reference_document_ids) == 0:
        return ""
    result = ", ".join(map(str, ai_reference_document_ids))
    return result


def get_ai_reference_documents_payload(reference_docs: List[Dict[str, Any]]) -> str:
    """将参考文档匹配信息序列化为 JSON 字符串，便于持久化。"""
    if not reference_docs:
        return ""
    payload = []
    for doc in reference_docs:
        if doc.get("doc_id") is None:
            continue
        chunks = []
        for chunk in (doc.get("chunks") or [])[:3]:
            metadata = _chunk_metadata(chunk)
            chunks.append({
                "score": float(chunk.get("score", 0.0)),
                "content_type": metadata.get("content_type"),
                "section_title": metadata.get("section_title"),
                "table_index": metadata.get("table_index"),
                "row_start": metadata.get("row_start"),
                "row_end": metadata.get("row_end"),
                "preview": str(chunk.get("content") or "")[:300],
            })
        images = _collect_reference_image_paths([doc], max_images=3)
        payload.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
            "title": doc.get("title", ""),
            "score": float(doc.get("score", 0.0)),
            "chunks": chunks,
            "image_urls": [
                web_url
                for image in images
                for web_url in [_image_to_web_url_if_exists(image)]
                if web_url
            ],
            "evidence_section_ids": doc.get("evidence_section_ids") or [],
            "evidence_section_titles": doc.get("evidence_section_titles") or [],
        })
    if not payload:
        return ""
    return json.dumps(desensitize_value(payload), ensure_ascii=False)

async def get_ai_answer(db, messages, id, reference_docs: Optional[List[Dict[str, Any]]] = None):
    """
    功能说明：
        非流式调用大模型生成 AI 回答，并回写到预创建的 AI 消息记录。
    参数说明：
        db：当前异步数据库会话。
        messages：发送给大模型的上下文消息列表。
        id：预创建的 AI 消息 ID。
        reference_docs：本轮检索命中的参考文档，用于替换/追加图片 Markdown。
    返回值说明：
        返回已刷新后的 AI Message ORM 对象；若消息不存在则返回 None。
    关键处理流程：
        1. 调用兼容 OpenAI Chat Completions 的模型服务；
        2. 清理模型输出中的冗余换行；
        3. 将本地图片路径转为 /upload/... 并稳定追加参考图片；
        4. 更新 content_text 和 token_count。
    """
    api_key = os.getenv("API_KEY", "EMPTY")
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))
    def _call_openai():
        client = OpenAI(base_url=get_ai_base_url(), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token
        )
        return response.choices[0].message.content

    final_ans = await asyncio.to_thread(_call_openai)
    final_ans = final_ans.replace("\n---\n", "---").replace("\n\n", "\n")

    # 图片路径替换为 Web URL，并在必要时追加参考图片 Markdown。
    final_ans = await _append_reference_images_to_answer(final_ans, reference_docs or [])
    final_ans = desensitize_text(final_ans)

    result = await db.execute(select(Message).where(Message.id == id))
    ai_msg = result.scalar_one_or_none()

    if ai_msg:
        ai_msg.content_text = final_ans
        ai_msg.token_count = await _count_text_tokens(final_ans)
        await db.commit()
        await db.refresh(ai_msg)

    return ai_msg

async def get_by_conversation(id: int,
                              db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_active_user)):
    try:
        conv_result = await db.execute(
            select(Conversation).where(
                and_(Conversation.id == id, Conversation.user_id == current_user.id))
        )
        conversation = conv_result.scalar_one_or_none()
        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "无该对话或无权限访问该对话")

        msg_result = await db.execute(
            select(Message).where(Message.session_id == id).order_by(Message.created_time)
        )
        messages = msg_result.scalars().all()

        message_response = []
        for message in messages:
            item = MessageResponse.from_orm(message).dict()
            if message.role == 0:
                item["content_text"] = desensitize_text(
                    _sanitize_answer_images_for_display(message.content_text)
                )
                item["ai_reference_doc_ids"] = desensitize_json_payload_string(message.ai_reference_doc_ids)
            message_response.append(item)
        return Result.success_with_data(message_response)
    except Exception as e:
        if isinstance(e, AppException):
            raise
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "获取对话消息失败")



