import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from dependencies import require_roles
from models import (
    Conversation,
    DocumentBreakdown,
    DocumentKnowledge,
    Document_review,
    Message,
    RoleGroup,
    RoleGroupPermission,
    SourceDocument,
    Tag,
    User,
)
from schemas import (
    Page,
    Result,
    RoleGroupCreate,
    RoleGroupUpdate,
    UserCreate,
    UserQueryByPage,
    UserResponse,
    UserUpdateByAdmin,
)
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.api_key import generate_api_key
from utils.pagination import build_pagination_payload
from utils.roles import (
    get_expected_perm_for_role,
    get_user_permissions,
    is_role_perm_consistent,
    legacy_role_perm_for_permissions,
    normalize_permission_codes,
    normalize_perm_value,
    normalize_role_value,
)

"""
管理员相关操作，即对用户的增删改查。
"""

router = APIRouter(prefix="/admin", tags=["管理员"])
logger = logging.getLogger(__name__)


def _normalize_and_validate_role_perm(role_value, perm_value):
    normalized_role = normalize_role_value(role_value)
    normalized_perm = normalize_perm_value(perm_value)

    if normalized_role is None or normalized_perm is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色或权限参数非法")

    if not is_role_perm_consistent(normalized_role, normalized_perm):
        expected_perm = get_expected_perm_for_role(normalized_role)
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            BizCode.BAD_REQUEST,
            f"角色与权限不匹配：该角色仅允许权限值 {expected_perm}",
        )

    return normalized_role, normalized_perm

def _serialize_role_group(role_group: RoleGroup) -> dict:
    return {
        "id": role_group.id,
        "code": role_group.code,
        "name": role_group.name,
        "description": role_group.description,
        "permissions": [
            permission.permission_code
            for permission in (role_group.permissions or [])
            if permission.permission_code
        ],
        "is_system": role_group.is_system,
        "is_deleted": role_group.is_deleted,
        "created_time": role_group.created_time,
        "updated_time": role_group.updated_time,
    }


def _serialize_user(user: User) -> dict:
    role_group = getattr(user, "role_group", None)
    return {
        "id": user.id,
        "username": user.username,
        "phone": user.phone,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "perm": getattr(user, "perm", None),
        "role_group_id": getattr(user, "role_group_id", None),
        "role_group_name": getattr(role_group, "name", None),
        "permissions": sorted(get_user_permissions(user)),
        "department": user.department,
        "api_key": getattr(user, "api_key", None),
        "created_time": user.created_time,
        "last_login": user.last_login,
    }


async def _get_role_group_or_400(db: AsyncSession, role_group_id: int) -> RoleGroup:
    result = await db.execute(
        select(RoleGroup)
        .options(selectinload(RoleGroup.permissions))
        .where(RoleGroup.id == role_group_id, RoleGroup.is_deleted == 0)
    )
    role_group = result.scalar_one_or_none()
    if role_group is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色组不存在或已停用")
    return role_group


async def _apply_role_group_to_user(db: AsyncSession, user: User, role_group_id: int):
    role_group = await _get_role_group_or_400(db, role_group_id)
    permissions = [permission.permission_code for permission in role_group.permissions]
    legacy_role, legacy_perm = legacy_role_perm_for_permissions(permissions)
    user.role_group_id = role_group.id
    user.role = legacy_role
    user.perm = legacy_perm


async def _generate_unique_api_key(db: AsyncSession) -> str:
    for _ in range(10):
        api_key = generate_api_key()
        result = await db.execute(select(User.id).where(User.api_key == api_key))
        if result.scalar_one_or_none() is None:
            return api_key
    raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "API Key 生成失败")


def _normalize_library_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "knowledge" if "knowledge" in text else "breakdown"


def _percent_change(current: int, previous: int) -> float:
    current = int(current or 0)
    previous = int(previous or 0)
    if previous <= 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _split_csv_values(raw_value: Any) -> List[str]:
    if not raw_value:
        return []
    return [item.strip() for item in str(raw_value).split(",") if item.strip()]


def _parse_json_or_csv_list(raw_value: Any) -> List[Any]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return list(raw_value)
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except Exception:
            return _split_csv_values(value)
    return [raw_value]


def _parse_reference_documents(raw_value: Any) -> List[Dict[str, Any]]:
    docs = []
    for item in _parse_json_or_csv_list(raw_value):
        if isinstance(item, dict):
            doc_id = item.get("doc_id") or item.get("id")
            if doc_id is None:
                continue
            try:
                doc_id = int(doc_id)
            except (TypeError, ValueError):
                continue
            docs.append(
                {
                    "doc_id": doc_id,
                    "library_type": _normalize_library_type(item.get("library_type", "breakdown")),
                    "title": item.get("title") or "",
                }
            )
            continue

        text_value = str(item or "").strip()
        if not text_value:
            continue
        library_type = "breakdown"
        doc_id_text = text_value
        if ":" in text_value:
            library_type_text, doc_id_text = text_value.split(":", 1)
            library_type = _normalize_library_type(library_type_text)
        doc_id_text = doc_id_text.strip()
        if not doc_id_text.isdigit():
            continue
        docs.append({"doc_id": int(doc_id_text), "library_type": library_type, "title": ""})
    return docs


def _count_uploaded_images(raw_value: Any) -> int:
    return len(_split_csv_values(raw_value))


async def _scalar_int(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return int(result.scalar_one_or_none() or 0)


async def _count_rows(db: AsyncSession, model, filters: Sequence[Any]) -> int:
    return await _scalar_int(db, select(func.count()).select_from(model).where(*filters))


async def _sum_message_tokens(db: AsyncSession, filters: Sequence[Any]) -> int:
    return await _scalar_int(
        db,
        select(func.coalesce(func.sum(Message.token_count), 0)).where(*filters),
    )


def _range_filters(column, start: Optional[datetime], end: Optional[datetime]) -> List[Any]:
    filters = []
    if start is not None:
        filters.append(column >= start)
    if end is not None:
        filters.append(column < end)
    return filters


async def _count_source_uploads(db: AsyncSession, start: datetime, end: datetime) -> int:
    return await _count_rows(
        db,
        SourceDocument,
        [SourceDocument.is_deleted == 0, *_range_filters(SourceDocument.upload_time, start, end)],
    )


async def _count_user_questions(db: AsyncSession, start: datetime, end: datetime) -> int:
    return await _count_rows(
        db,
        Message,
        [Message.role == 1, *_range_filters(Message.created_time, start, end)],
    )


def _weekday_label(value: datetime, today_start: datetime) -> str:
    if value.date() == today_start.date():
        return "今天"
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[value.weekday()]


async def _build_trend_series(db: AsyncSession, now: datetime, today_start: datetime) -> Dict[str, Any]:
    trend_ranges = {}

    today_end = now + timedelta(seconds=1)
    today_span = (today_end - today_start) / 7
    today_buckets = []
    today_labels = []
    for index in range(7):
        bucket_start = today_start + today_span * index
        bucket_end = today_start + today_span * (index + 1) if index < 6 else today_end
        today_buckets.append((bucket_start, bucket_end))
        today_labels.append("现在" if index == 6 else f"{bucket_start.hour:02d}:00")

    week_buckets = []
    week_labels = []
    for days_ago in range(6, -1, -1):
        bucket_start = today_start - timedelta(days=days_ago)
        bucket_end = bucket_start + timedelta(days=1)
        week_buckets.append((bucket_start, bucket_end))
        week_labels.append(_weekday_label(bucket_start, today_start))

    month_start = today_start - timedelta(days=29)
    month_end = now + timedelta(seconds=1)
    month_span = (month_end - month_start) / 7
    month_buckets = []
    month_labels = []
    for index in range(7):
        bucket_start = month_start + month_span * index
        bucket_end = month_start + month_span * (index + 1) if index < 6 else month_end
        month_buckets.append((bucket_start, bucket_end))
        month_labels.append(f"{bucket_start.month}/{bucket_start.day}")

    definitions = {
        "today": {
            "labels": today_labels,
            "buckets": today_buckets,
            "subtitle": "今天上传文件与问答次数变化",
            "aria_label": "今天上传文件与问答次数趋势图",
        },
        "week": {
            "labels": week_labels,
            "buckets": week_buckets,
            "subtitle": "最近 7 天上传文件与问答次数变化",
            "aria_label": "最近七天上传文件与问答次数趋势图",
        },
        "month": {
            "labels": month_labels,
            "buckets": month_buckets,
            "subtitle": "最近 30 天上传文件与问答次数变化",
            "aria_label": "最近三十天上传文件与问答次数趋势图",
        },
    }

    for key, definition in definitions.items():
        uploads = []
        questions = []
        for bucket_start, bucket_end in definition["buckets"]:
            uploads.append(await _count_source_uploads(db, bucket_start, bucket_end))
            questions.append(await _count_user_questions(db, bucket_start, bucket_end))
        trend_ranges[key] = {
            "labels": definition["labels"],
            "uploads": uploads,
            "questions": questions,
            "subtitle": definition["subtitle"],
            "aria_label": definition["aria_label"],
        }

    return trend_ranges


async def _collect_active_user_ids(db: AsyncSession, start: datetime, end: datetime) -> set[int]:
    user_ids: set[int] = set()

    login_result = await db.execute(
        select(User.id).where(
            User.status == 1,
            *_range_filters(User.last_login, start, end),
        )
    )
    user_ids.update(int(row[0]) for row in login_result.all() if row[0] is not None)

    conversation_result = await db.execute(
        select(Conversation.user_id)
        .where(*_range_filters(Conversation.updated_time, start, end))
        .distinct()
    )
    user_ids.update(int(row[0]) for row in conversation_result.all() if row[0] is not None)

    message_result = await db.execute(
        select(Conversation.user_id)
        .join(Message, Message.session_id == Conversation.id)
        .where(*_range_filters(Message.created_time, start, end))
        .distinct()
    )
    user_ids.update(int(row[0]) for row in message_result.all() if row[0] is not None)

    source_result = await db.execute(
        select(SourceDocument.uploader_id)
        .where(
            SourceDocument.is_deleted == 0,
            SourceDocument.uploader_id.is_not(None),
            *_range_filters(SourceDocument.upload_time, start, end),
        )
        .distinct()
    )
    user_ids.update(int(row[0]) for row in source_result.all() if row[0] is not None)

    review_result = await db.execute(
        select(Document_review.contributor_id)
        .where(
            Document_review.contributor_id.is_not(None),
            *_range_filters(Document_review.first_edit_date, start, end),
        )
        .distinct()
    )
    user_ids.update(int(row[0]) for row in review_result.all() if row[0] is not None)

    return user_ids


async def _build_tag_usage(db: AsyncSession, tags: Sequence[Tag]) -> Dict[str, int]:
    if not tags:
        return {"high_frequency": 0}

    tag_lookup: Dict[str, int] = {}
    for tag in tags:
        tag_lookup[str(tag.id)] = int(tag.id)
        if tag.name:
            tag_lookup[str(tag.name).strip()] = int(tag.id)

    tag_counts = Counter()
    for model in (DocumentBreakdown, DocumentKnowledge):
        result = await db.execute(select(model.tag).where(model.is_deleted == 0))
        for row in result.all():
            for raw_tag in _parse_json_or_csv_list(row[0]):
                key = str(raw_tag or "").strip()
                tag_id = tag_lookup.get(key)
                if tag_id is not None:
                    tag_counts[tag_id] += 1

    high_frequency = sum(1 for count in tag_counts.values() if count >= 2)
    if high_frequency == 0:
        high_frequency = sum(1 for count in tag_counts.values() if count > 0)
    return {"high_frequency": high_frequency}


async def _build_token_summary(
    db: AsyncSession,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    date_filters = _range_filters(Message.created_time, start, end)
    message_tokens = await _sum_message_tokens(db, [*date_filters])
    ai_tokens = await _sum_message_tokens(db, [Message.role == 0, *date_filters])
    user_tokens = await _sum_message_tokens(db, [Message.role == 1, *date_filters])
    call_count = await _count_rows(db, Message, [Message.role == 1, *date_filters])

    image_result = await db.execute(
        select(Message.user_uploaded_images).where(
            Message.role == 1,
            Message.user_uploaded_images.is_not(None),
            Message.user_uploaded_images != "",
            *date_filters,
        )
    )
    image_count = sum(_count_uploaded_images(row[0]) for row in image_result.all())
    image_tokens = image_count * 578

    reference_result = await db.execute(
        select(Message.ai_reference_doc_ids).where(
            Message.role == 0,
            Message.ai_reference_doc_ids.is_not(None),
            Message.ai_reference_doc_ids != "",
            *date_filters,
        )
    )
    reference_count = sum(len(_parse_reference_documents(row[0])) for row in reference_result.all())
    reference_tokens = reference_count * 258

    total_tokens = int(message_tokens + image_tokens + reference_tokens)
    return {
        "total": total_tokens,
        "message_tokens": message_tokens,
        "ai_tokens": ai_tokens,
        "user_tokens": user_tokens,
        "image_tokens": image_tokens,
        "reference_tokens": reference_tokens,
        "image_count": image_count,
        "reference_count": reference_count,
        "call_count": call_count,
        "average_per_call": int(round(total_tokens / call_count)) if call_count else 0,
        "breakdown": [
            {"label": "AI 回答", "value": ai_tokens, "color": "blue"},
            {"label": "用户提问", "value": user_tokens, "color": "orange"},
            {"label": "图片输入估算", "value": image_tokens, "color": "green"},
            {"label": "知识引用估算", "value": reference_tokens, "color": "gray"},
        ],
    }


async def _lookup_document_titles(
    db: AsyncSession,
    keys: Iterable[Tuple[str, int]],
) -> Dict[Tuple[str, int], str]:
    key_list = list(keys)
    title_map: Dict[Tuple[str, int], str] = {}
    for library_type, model in (("breakdown", DocumentBreakdown), ("knowledge", DocumentKnowledge)):
        ids = [doc_id for item_library_type, doc_id in key_list if item_library_type == library_type]
        if not ids:
            continue
        result = await db.execute(select(model.id, model.title).where(model.id.in_(ids)))
        for row in result.all():
            title_map[(library_type, int(row.id))] = row.title or f"文档 {row.id}"
    return title_map


async def _build_hot_documents(db: AsyncSession, start: datetime, limit: int = 5) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(Message.ai_reference_doc_ids).where(
            Message.role == 0,
            Message.ai_reference_doc_ids.is_not(None),
            Message.ai_reference_doc_ids != "",
            Message.created_time >= start,
        )
    )

    counts: Counter[Tuple[str, int]] = Counter()
    title_map: Dict[Tuple[str, int], str] = {}
    for row in result.all():
        for doc in _parse_reference_documents(row[0]):
            key = (doc["library_type"], int(doc["doc_id"]))
            counts[key] += 1
            if doc.get("title") and key not in title_map:
                title_map[key] = str(doc["title"])

    if not counts:
        return []

    missing_titles = [key for key in counts if key not in title_map]
    title_map.update(await _lookup_document_titles(db, missing_titles))

    rows = []
    for rank, ((library_type, doc_id), hit_count) in enumerate(counts.most_common(limit), start=1):
        title = title_map.get((library_type, doc_id)) or f"文档 {doc_id}"
        rows.append(
            {
                "rank": rank,
                "doc_id": doc_id,
                "library_type": library_type,
                "library_label": "知识库" if library_type == "knowledge" else "故障库",
                "title": title,
                "hit_count": int(hit_count),
            }
        )
    return rows


async def _build_recent_questions(db: AsyncSession, limit: int = 5) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(Message, User.full_name, User.username)
        .join(Conversation, Message.session_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(Message.role == 1)
        .order_by(desc(Message.created_time), desc(Message.id))
        .limit(limit)
    )
    rows = []
    for message, full_name, username in result.all():
        ai_result = await db.execute(
            select(Message)
            .where(
                Message.session_id == message.session_id,
                Message.role == 0,
                Message.created_time >= message.created_time,
            )
            .order_by(Message.created_time.asc(), Message.id.asc())
            .limit(1)
        )
        ai_message = ai_result.scalar_one_or_none()
        reference_count = len(_parse_reference_documents(ai_message.ai_reference_doc_ids)) if ai_message else 0
        is_success = bool(
            ai_message
            and ai_message.content_text
            and "回答生成中" not in ai_message.content_text
        )
        rows.append(
            {
                "id": message.id,
                "question": message.content_text or "",
                "user_name": full_name or username or "用户",
                "created_time": _iso_datetime(message.created_time),
                "reference_count": reference_count,
                "status": "success" if is_success else "pending",
                "status_label": "成功" if is_success else "处理中",
            }
        )
    return rows


@router.get("/dashboard", summary="管理员数据看板统计")
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)

    total_uploads = await _count_rows(db, SourceDocument, [SourceDocument.is_deleted == 0])
    today_uploads = await _count_source_uploads(db, today_start, tomorrow_start)
    yesterday_uploads = await _count_source_uploads(db, yesterday_start, today_start)
    parsing_uploads = await _count_rows(
        db,
        SourceDocument,
        [SourceDocument.is_deleted == 0, SourceDocument.status == "parsing"],
    )
    parse_failed_count = await _count_rows(
        db,
        SourceDocument,
        [SourceDocument.is_deleted == 0, SourceDocument.status == "parse_failed"],
    )

    knowledge_count = await _count_rows(db, DocumentKnowledge, [DocumentKnowledge.is_deleted == 0])
    breakdown_count = await _count_rows(db, DocumentBreakdown, [DocumentBreakdown.is_deleted == 0])
    pending_review_count = await _count_rows(db, Document_review, [Document_review.status == 0])
    today_docs = (
        await _count_rows(db, DocumentKnowledge, [DocumentKnowledge.is_deleted == 0, *_range_filters(DocumentKnowledge.first_edit_date, today_start, tomorrow_start)])
        + await _count_rows(db, DocumentBreakdown, [DocumentBreakdown.is_deleted == 0, *_range_filters(DocumentBreakdown.first_edit_date, today_start, tomorrow_start)])
        + await _count_rows(db, Document_review, [*_range_filters(Document_review.first_edit_date, today_start, tomorrow_start)])
    )
    yesterday_docs = (
        await _count_rows(db, DocumentKnowledge, [DocumentKnowledge.is_deleted == 0, *_range_filters(DocumentKnowledge.first_edit_date, yesterday_start, today_start)])
        + await _count_rows(db, DocumentBreakdown, [DocumentBreakdown.is_deleted == 0, *_range_filters(DocumentBreakdown.first_edit_date, yesterday_start, today_start)])
        + await _count_rows(db, Document_review, [*_range_filters(Document_review.first_edit_date, yesterday_start, today_start)])
    )

    tags_result = await db.execute(select(Tag).where(Tag.is_deleted == 0))
    tags = tags_result.scalars().all()
    total_tags = len(tags)
    today_tags = await _count_rows(db, Tag, [Tag.is_deleted == 0, *_range_filters(Tag.created_time, today_start, tomorrow_start)])
    yesterday_tags = await _count_rows(db, Tag, [Tag.is_deleted == 0, *_range_filters(Tag.created_time, yesterday_start, today_start)])
    tag_usage = await _build_tag_usage(db, tags)

    today_questions = await _count_user_questions(db, today_start, tomorrow_start)
    yesterday_questions = await _count_user_questions(db, yesterday_start, today_start)
    today_ai_answers = await _count_rows(db, Message, [Message.role == 0, *_range_filters(Message.created_time, today_start, tomorrow_start)])
    today_ai_hits = await _count_rows(
        db,
        Message,
        [
            Message.role == 0,
            Message.ai_reference_doc_ids.is_not(None),
            Message.ai_reference_doc_ids != "",
            *_range_filters(Message.created_time, today_start, tomorrow_start),
        ],
    )
    success_rate = round((today_ai_answers / today_questions) * 100, 1) if today_questions else 0
    hit_rate = round((today_ai_hits / today_ai_answers) * 100, 1) if today_ai_answers else 0

    today_active_users = await _collect_active_user_ids(db, today_start, tomorrow_start)
    yesterday_active_users = await _collect_active_user_ids(db, yesterday_start, today_start)

    today_token_summary = await _build_token_summary(db, today_start, tomorrow_start)
    total_token_summary = await _build_token_summary(db)

    data = {
        "updated_at": _iso_datetime(now),
        "metrics": {
            "uploads": {
                "total": total_uploads,
                "today": today_uploads,
                "parsing": parsing_uploads,
                "change_percent": _percent_change(today_uploads, yesterday_uploads),
            },
            "documents": {
                "total": knowledge_count + breakdown_count + pending_review_count,
                "knowledge": knowledge_count,
                "breakdown": breakdown_count,
                "pending_review": pending_review_count,
                "today": today_docs,
                "change_percent": _percent_change(today_docs, yesterday_docs),
            },
            "tags": {
                "total": total_tags,
                "today": today_tags,
                "high_frequency": tag_usage["high_frequency"],
                "change_percent": _percent_change(today_tags, yesterday_tags),
            },
            "questions": {
                "today": today_questions,
                "success_rate": min(success_rate, 100),
                "hit_rate": min(hit_rate, 100),
                "change_percent": _percent_change(today_questions, yesterday_questions),
            },
            "active_users": {
                "today": len(today_active_users),
                "change_percent": _percent_change(len(today_active_users), len(yesterday_active_users)),
            },
        },
        "trends": await _build_trend_series(db, now, today_start),
        "token_usage": {
            "today": today_token_summary,
            "total": total_token_summary,
        },
        "hot_documents": await _build_hot_documents(db, today_start),
        "recent_questions": await _build_recent_questions(db),
        "document_categories": [
            {
                "key": "knowledge",
                "title": "知识库文档",
                "description": "通用维修知识与技术资料",
                "count": knowledge_count,
                "level": "healthy",
                "icon": "fa-book",
            },
            {
                "key": "breakdown",
                "title": "故障库文档",
                "description": "故障案例、处理记录与维修经验",
                "count": breakdown_count,
                "level": "healthy",
                "icon": "fa-circle-plus",
            },
            {
                "key": "pending_review",
                "title": "审核中文档",
                "description": "需要管理员或审核人员处理",
                "count": pending_review_count,
                "level": "warning",
                "icon": "fa-clock",
            },
            {
                "key": "parse_failed",
                "title": "解析失败文档",
                "description": "建议优先排查文件格式和解析日志",
                "count": parse_failed_count,
                "level": "warning",
                "icon": "fa-triangle-exclamation",
            },
        ],
    }
    return Result.success_with_data(data)


@router.get("/role_groups", summary="管理员查询角色组")
async def get_role_groups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    result = await db.execute(
        select(RoleGroup)
        .options(selectinload(RoleGroup.permissions))
        .where(RoleGroup.is_deleted == 0)
        .order_by(RoleGroup.id.asc())
    )
    return Result.success_with_data([_serialize_role_group(role_group) for role_group in result.scalars().all()])


@router.post("/role_groups", summary="管理员创建角色组")
async def create_role_group(
    role_group_data: RoleGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    permissions = normalize_permission_codes(role_group_data.permissions)
    if not permissions:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请至少选择一个权限")

    code = (role_group_data.code or role_group_data.name).strip()
    if not code:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色编码不能为空")

    exists_result = await db.execute(
        select(RoleGroup).where(
            RoleGroup.is_deleted == 0,
            or_(RoleGroup.code == code, RoleGroup.name == role_group_data.name),
        )
    )
    if exists_result.scalar_one_or_none():
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色组名称或编码已存在")

    role_group = RoleGroup(
        code=code,
        name=role_group_data.name.strip(),
        description=role_group_data.description,
        is_system=0,
        is_deleted=0,
        created_time=datetime.now(),
        updated_time=datetime.now(),
    )
    db.add(role_group)
    await db.flush()
    for permission in permissions:
        db.add(RoleGroupPermission(role_group_id=role_group.id, permission_code=permission))
    await db.commit()
    await db.refresh(role_group)

    role_group = await _get_role_group_or_400(db, role_group.id)
    return Result.success_with_data(_serialize_role_group(role_group))


@router.patch("/role_groups", summary="管理员更新角色组")
async def update_role_group(
    role_group_data: RoleGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    role_group = await _get_role_group_or_400(db, role_group_data.id)

    if role_group_data.is_deleted == 1:
        if role_group.is_system:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "系统角色组不能删除")
        role_group.is_deleted = 1
        role_group.updated_time = datetime.now()
        await db.commit()
        return Result.success()

    if role_group_data.name is not None:
        role_group.name = role_group_data.name.strip()
    if role_group_data.code is not None:
        if role_group.is_system:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "系统角色组编码不能修改")
        role_group.code = role_group_data.code.strip()
    if role_group_data.description is not None:
        role_group.description = role_group_data.description

    if role_group_data.permissions is not None:
        permissions = normalize_permission_codes(role_group_data.permissions)
        if not permissions:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请至少选择一个权限")
        await db.execute(
            RoleGroupPermission.__table__.delete().where(RoleGroupPermission.role_group_id == role_group.id)
        )
        for permission in permissions:
            db.add(RoleGroupPermission(role_group_id=role_group.id, permission_code=permission))

        legacy_role, legacy_perm = legacy_role_perm_for_permissions(permissions)
        users_result = await db.execute(select(User).where(User.role_group_id == role_group.id))
        for user in users_result.scalars().all():
            user.role = legacy_role
            user.perm = legacy_perm

    role_group.updated_time = datetime.now()
    await db.commit()
    role_group = await _get_role_group_or_400(db, role_group.id)
    return Result.success_with_data(_serialize_role_group(role_group))


@router.post("/add_user", summary="管理员添加用户")
async def add_user(
    user: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        phone = user.phone
        email = user.email
        username = user.username
        normalized_role, normalized_perm = _normalize_and_validate_role_perm(user.role, user.perm)
        role_group_id = user.role_group_id

        phone_result = await db.execute(select(User).where(User.phone == phone, User.status == 1))
        user_phone_find = phone_result.scalar_one_or_none()
        if user_phone_find:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "手机号已被其他用户使用")

        if email is not None:
            email_result = await db.execute(select(User).where(User.email == email, User.status == 1))
            user_email_find = email_result.scalar_one_or_none()
            if user_email_find:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "邮箱已被其他用户使用")

        if username is not None:
            username_result = await db.execute(select(User).where(User.username == username, User.status == 1))
            user_username_find = username_result.scalar_one_or_none()
            if user_username_find:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "用户名已存在，添加失败")

        hashed_password = hashlib.md5("123456".encode()).hexdigest()

        deleted_user_result = await db.execute(select(User).where(User.username == username, User.status == 0))
        user_delete = deleted_user_result.scalar_one_or_none()

        if user_delete:
            user_delete.status = user.status if user.status is not None else 1
            user_delete.phone = phone
            user_delete.email = email
            if role_group_id:
                await _apply_role_group_to_user(db, user_delete, role_group_id)
            else:
                user_delete.role = normalized_role
                user_delete.perm = normalized_perm
            user_delete.password = hashed_password
            user_delete.full_name = user.full_name
            user_delete.department = user.department
            if not user_delete.api_key:
                user_delete.api_key = await _generate_unique_api_key(db)
            user_delete.created_time = datetime.now()
            user_delete.last_login = None
            await db.commit()
            await db.refresh(user_delete)
        else:
            user_dict = user.model_dump(exclude={"password", "status", "role_group_id"}, exclude_none=True)
            user_dict["role"] = normalized_role
            user_dict["perm"] = normalized_perm

            new_user = User(
                **user_dict,
                password=hashed_password,
                api_key=await _generate_unique_api_key(db),
                status=user.status if user.status is not None else 1,
                created_time=datetime.now(),
                last_login=None,
            )
            if role_group_id:
                await _apply_role_group_to_user(db, new_user, role_group_id)
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

        return Result.success()
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"创建用户失败: {str(e)}")


@router.patch("/update_user", summary="管理员更新用户信息")
async def update_user(
    new_user: UserUpdateByAdmin,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """
    更新用户信息，包括软删除（status=0）。
    """
    try:
        result = await db.execute(select(User).where(User.id == new_user.id))
        user = result.scalar_one_or_none()

        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "用户不存在")

        new_user_dict = new_user.model_dump(exclude_unset=True)
        if not new_user_dict:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请提供需要更新的字段")

        role_group_id = new_user_dict.pop("role_group_id", None)
        if role_group_id:
            await _apply_role_group_to_user(db, user, int(role_group_id))

        should_validate_role_perm = role_group_id is None and (("role" in new_user_dict) or ("perm" in new_user_dict))
        if should_validate_role_perm:
            target_role = new_user_dict.get("role", user.role)
            target_perm = new_user_dict.get("perm", user.perm)
            normalized_role, normalized_perm = _normalize_and_validate_role_perm(target_role, target_perm)
            new_user_dict["role"] = normalized_role
            new_user_dict["perm"] = normalized_perm

        if "phone" in new_user_dict and new_user_dict["phone"] != user.phone:
            phone_result = await db.execute(
                select(User).where(
                    User.phone == new_user_dict["phone"],
                    User.id != user.id,
                    User.status == 1,
                )
            )
            exist_phone = phone_result.scalar_one_or_none()
            if exist_phone:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "手机号已被其他用户使用")

        if "email" in new_user_dict and new_user_dict["email"] and new_user_dict["email"] != user.email:
            email_result = await db.execute(
                select(User).where(
                    User.email == new_user_dict["email"],
                    User.id != user.id,
                    User.status == 1,
                )
            )
            exist_email = email_result.scalar_one_or_none()
            if exist_email:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "邮箱已被其他用户使用")

        for field, value in new_user_dict.items():
            if value is not None and field != "id":
                setattr(user, field, value)

        await db.commit()
        await db.refresh(user)

        refreshed_result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.id == user.id)
        )
        data = _serialize_user(refreshed_result.scalar_one())
        return Result.success_with_data(data)

    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"用户更新异常: {str(e)}", exc_info=True)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试")


@router.get("/users", summary="管理员查询所有用户数据")
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1)
        )
        users = result.scalars().all()
        users_data = [_serialize_user(user) for user in users]
        return Result.success_with_data(users_data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.get("/user/{id}", summary="管理员查询某个用户信息")
async def get_user_by_id(
    id,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1, User.id == id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "资源未找到")
        user_data = _serialize_user(user)
        return Result.success_with_data(user_data)
    except AppException:
        raise
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.post("/users/page", summary="管理员分页查询用户信息")
async def get_user_page(
    page: Page,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        offset = (page.page - 1) * page.size

        total_count_result = await db.execute(select(func.count()).select_from(User).where(User.status == 1))
        total_count = total_count_result.scalar_one()

        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1)
            .offset(offset)
            .limit(page.size)
        )
        users = result.scalars().all()

        users_data = [_serialize_user(user) for user in users]
        data = build_pagination_payload(total_count, page.page, page.size, users_data, "users")
        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.post("/query", summary="查询用户信息")
async def query(
    query: UserQueryByPage,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        offset = (query.page - 1) * query.size

        filters = and_(
            User.status == 1,
            or_(
                User.username.like(f"%{query.data}%"),
                User.phone.like(f"%{query.data}%"),
                User.full_name.like(f"%{query.data}%"),
                User.department.like(f"%{query.data}%"),
            ),
        )

        total_count_result = await db.execute(select(func.count()).select_from(User).where(filters))
        total_count = total_count_result.scalar_one()

        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(filters)
            .offset(offset)
            .limit(query.size)
        )
        users = result.scalars().all()

        users_response = [_serialize_user(user) for user in users]

        data = build_pagination_payload(total_count, query.page, query.size, users_response, "users")

        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")
