import asyncio
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import Document, Document_review, SourceDocument, User
from schemas import Result, SourceDocumentResponse
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.file_cleanup import delete_file_if_exists
from utils.roles import UserRole, has_role

router = APIRouter(prefix="/source-documents", tags=["源文档"])


def source_document_to_response(source: SourceDocument, uploader_name: Optional[str]) -> SourceDocumentResponse:
    return SourceDocumentResponse(
        id=source.id,
        origin_file_name=source.origin_file_name,
        stored_file_path=source.stored_file_path,
        file_ext=source.file_ext,
        file_category=source.file_category,
        file_size=source.file_size,
        uploader_id=source.uploader_id,
        uploader_name=uploader_name,
        upload_time=source.upload_time,
        status=source.status,
        parse_error=source.parse_error,
        document_id=source.document_id,
        review_id=source.review_id,
    )


@router.get("/page", summary="分页查询源文档")
async def get_source_documents_page(
    page: int = Query(1, ge=1),
    size: int = Query(12, ge=1, le=50),
    keyword: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    source_status: Optional[str] = Query(None),
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

    total_pages = (total_count + size - 1) // size if total_count else 0
    return Result.success_with_data(
        {
            "total_count": total_count,
            "total_pages": total_pages,
            "source_documents": items,
        }
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

    if source.document_id:
        doc_result = await db.execute(
            select(Document.id).where(Document.id == source.document_id, Document.is_deleted == 0)
        )
        if doc_result.scalar_one_or_none() is not None:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                BizCode.BAD_REQUEST,
                "该源文档已生成知识文档，请先在知识库删除关联文档",
            )

    if source.review_id:
        review_result = await db.execute(
            select(Document_review.id).where(
                Document_review.id == source.review_id,
                Document_review.status == 0,
            )
        )
        if review_result.scalar_one_or_none() is not None:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                BizCode.BAD_REQUEST,
                "该源文档有关联的待审核记录，请先处理审核记录",
            )

    base_dir = os.getenv("DOCUMENT_BASE_DIR", ".")
    absolute_path = os.path.join(base_dir, source.stored_file_path)
    await asyncio.to_thread(delete_file_if_exists, absolute_path)

    source.is_deleted = 1
    source.status = "deleted"
    source.deleted_time = datetime.now()
    await db.commit()

    return Result.success_with_data({"id": source_id})
