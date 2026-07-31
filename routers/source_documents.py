import asyncio
import asyncio
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import DocumentBreakdown, DocumentKnowledge, Document_review, KnowledgeDocumentReview, SourceDocument, User
from schemas import Result, SourceDocumentResponse
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.file_cleanup import delete_file_if_exists
from utils.pagination import build_pagination_payload
from utils.roles import UserRole, has_role
from utils.upload_paths import normalize_upload_path

router = APIRouter(prefix="/source-documents", tags=["源文档"])


DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}
REVIEW_LIBRARY_MODELS = {"breakdown": Document_review, "knowledge": KnowledgeDocumentReview}


def _normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _get_review_model(library_type: str):
    return REVIEW_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _review_library_type(review) -> str:
    if isinstance(review, KnowledgeDocumentReview):
        return "knowledge"
    return _normalize_library_type(getattr(review, "document_library_type", "breakdown"))


def source_document_to_response(source: SourceDocument, uploader_name: Optional[str]) -> SourceDocumentResponse:
    return SourceDocumentResponse(
        id=source.id,
        origin_file_name=source.origin_file_name,
        stored_file_path=normalize_upload_path(source.stored_file_path),
        file_ext=source.file_ext,
        file_category=source.file_category,
        file_size=source.file_size,
        uploader_id=source.uploader_id,
        uploader_name=uploader_name,
        upload_time=source.upload_time,
        status=source.status,
        parse_error=source.parse_error,
        parse_started_time=source.parse_started_time,
        document_id=source.document_id,
        document_library_type=source.document_library_type,
        review_library_type=source.review_library_type,
        review_id=source.review_id,
    )


async def _repair_stale_source_link(db: AsyncSession, source: SourceDocument) -> bool:
    if not source.document_id:
        return False
    document_model = _get_document_model(source.document_library_type)
    doc_result = await db.execute(
        select(document_model.id).where(
            document_model.id == source.document_id,
            document_model.is_deleted == 0,
        )
    )
    if doc_result.scalar_one_or_none() is not None:
        return False
    source.status = "uploaded"
    source.document_id = None
    source.document_library_type = "breakdown"
    source.parse_error = None
    source.parse_started_time = None
    return True


def _review_matches_source(review, source: SourceDocument) -> bool:
    origin_file_dir = getattr(review, "origin_file_dir", None)
    origin_file_name = getattr(review, "origin_file_name", None)
    if origin_file_dir and normalize_upload_path(origin_file_dir) == normalize_upload_path(source.stored_file_path):
        return True
    if origin_file_name and origin_file_name == source.origin_file_name:
        return True
    return False


async def _get_review_for_source(db: AsyncSession, source: SourceDocument):
    if not source.review_id:
        return None

    preferred_model = _get_review_model(source.review_library_type)
    result = await db.execute(select(preferred_model).where(preferred_model.id == source.review_id))
    preferred_review = result.scalar_one_or_none()
    if preferred_review and _review_matches_source(preferred_review, source):
        return preferred_review

    for review_model in REVIEW_LIBRARY_MODELS.values():
        if review_model is preferred_model:
            continue
        result = await db.execute(select(review_model).where(review_model.id == source.review_id))
        review = result.scalar_one_or_none()
        if review and _review_matches_source(review, source):
            source.review_library_type = _review_library_type(review)
            return review
    if preferred_review:
        return preferred_review
    return None


async def _repair_stale_review_link(db: AsyncSession, source: SourceDocument) -> bool:
    if source.review_id is None and source.status == "review_pending":
        source.status = "uploaded"
        source.review_library_type = "breakdown"
        source.parse_error = None
        source.parse_started_time = None
        return True
    if source.review_id is None:
        return False

    review = await _get_review_for_source(db, source)
    if not review:
        source.status = "uploaded"
        source.review_id = None
        source.review_library_type = "breakdown"
        source.parse_error = None
        source.parse_started_time = None
        return True

    if review.status == 0:
        source.status = "review_pending"
        source.review_library_type = _review_library_type(review)
        return False

    if review.status == 1 and getattr(review, "action_type", None) == 1 and getattr(review, "document_id", None):
        source.status = "vectorized"
        source.document_id = review.document_id
        source.document_library_type = _review_library_type(review)
        source.parse_error = None
    else:
        source.status = "uploaded"
        source.parse_error = getattr(review, "review_comment", None) if review.status == 2 else None

    source.review_id = None
    source.review_library_type = "breakdown"
    source.parse_started_time = None
    return True


@router.get("/page", summary="分页查询源文档")
async def get_source_documents_page(
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source_status: Optional[str] = Query(None),
    pending_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    conditions = [SourceDocument.is_deleted == 0]

    if not has_role(current_user, UserRole.ADMIN):
        conditions.append(SourceDocument.uploader_id == current_user.id)

    if keyword and keyword.strip():
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                SourceDocument.origin_file_name.like(like),
                SourceDocument.stored_file_path.like(like),
                SourceDocument.parse_error.like(like),
            )
        )

    if category and category.strip():
        conditions.append(SourceDocument.file_category == category.strip())

    if source_status and source_status.strip():
        conditions.append(SourceDocument.status == source_status.strip())
    if pending_only:
        conditions.extend(
            [
                SourceDocument.review_id.is_(None),
                SourceDocument.document_id.is_(None),
                SourceDocument.status.in_(["uploaded", "parse_failed"]),
            ]
        )

    repair_candidates_result = await db.execute(
        select(SourceDocument)
        .where(SourceDocument.is_deleted == 0, SourceDocument.document_id.is_not(None))
        .order_by(SourceDocument.id.desc())
    )
    repair_candidates = repair_candidates_result.scalars().all()
    repaired = False
    for source in repair_candidates:
        repaired = await _repair_stale_source_link(db, source) or repaired

    review_repair_candidates_result = await db.execute(
        select(SourceDocument)
        .where(
            SourceDocument.is_deleted == 0,
            or_(SourceDocument.review_id.is_not(None), SourceDocument.status == "review_pending"),
        )
        .order_by(SourceDocument.id.desc())
    )
    review_repair_candidates = review_repair_candidates_result.scalars().all()
    for source in review_repair_candidates:
        repaired = await _repair_stale_review_link(db, source) or repaired
    if repaired:
        await db.commit()

    total_result = await db.execute(select(func.count()).select_from(SourceDocument).where(*conditions))
    total_count = int(total_result.scalar_one() or 0)

    offset = (page - 1) * size
    result = await db.execute(
        select(SourceDocument)
        .where(*conditions)
        .order_by(SourceDocument.upload_time.desc(), SourceDocument.id.desc())
        .offset(offset)
        .limit(size)
    )
    sources = result.scalars().all()

    uploader_ids = {source.uploader_id for source in sources if source.uploader_id is not None}
    uploader_map = {}
    if uploader_ids:
        users_result = await db.execute(select(User.id, User.full_name, User.username).where(User.id.in_(uploader_ids)))
        for user_id, full_name, username in users_result.all():
            uploader_map[user_id] = full_name or username

    items = [
        source_document_to_response(
            source,
            "我" if source.uploader_id == current_user.id else uploader_map.get(source.uploader_id),
        )
        for source in sources
    ]

    return Result.success_with_data(
        build_pagination_payload(total_count, page, size, items, "source_documents")
    )


@router.delete("/{source_id}", summary="删除源文档")
async def delete_source_document(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.id == source_id,
            SourceDocument.is_deleted == 0,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.DOC_RESOURCE_NOT_FOUND, "源文档不存在")

    if source.uploader_id != current_user.id and not has_role(current_user, UserRole.ADMIN):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权删除该源文档")

    if await _repair_stale_source_link(db, source):
        await db.flush()
    if await _repair_stale_review_link(db, source):
        await db.flush()

    if source.document_id:
        document_model = _get_document_model(source.document_library_type)
        doc_result = await db.execute(
            select(document_model.id).where(document_model.id == source.document_id, document_model.is_deleted == 0)
        )
        if doc_result.scalar_one_or_none() is not None:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                BizCode.BAD_REQUEST,
                "该源文档已生成知识文档，请先在知识库删除关联文档",
            )

    if source.review_id:
        review_model = KnowledgeDocumentReview if source.review_library_type == "knowledge" else Document_review
        review_result = await db.execute(
            select(review_model.id).where(
                review_model.id == source.review_id,
                review_model.status == 0,
            )
        )
        if review_result.scalar_one_or_none() is not None:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                BizCode.BAD_REQUEST,
                "该源文档有关联的待审核记录，请先处理审核记录",
            )

    base_dir = os.getenv("DOCUMENT_BASE_DIR", ".")
    absolute_path = os.path.join(base_dir, normalize_upload_path(source.stored_file_path) or source.stored_file_path)
    await asyncio.to_thread(delete_file_if_exists, absolute_path)

    source.is_deleted = 1
    source.status = "deleted"
    source.deleted_time = datetime.now()
    source.parse_started_time = None
    await db.commit()

    return Result.success_with_data({"id": source_id})
