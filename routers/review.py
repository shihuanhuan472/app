import asyncio
import os
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import Document, DocumentBreakdown, DocumentKnowledge, Document_review, SourceDocument, User
from schemas import DocumentReviewRequest, DocumentReviewResponse, Result
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.file_cleanup import delete_file_if_exists, delete_image_with_variants
from utils.roles import UserRole, has_role
from utils.tag_service import normalize_tag_values, set_document_tag_names
from utils.upload_paths import normalize_upload_path
from utils.VectorService import VectorService

router = APIRouter(prefix="/review", tags=["document-review"])

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}


def _normalize_library_type(library_type: str) -> str:
    """把审核请求里的目标库类型固定为故障库或知识库，避免审核通过时写错表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """按审核记录里的库类型选择实际文档表模型。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _normalize_tags(tag):
    """审核表保留 tag id/name 混合输入，最终写文档表时会转成 tag id 数组。"""
    return normalize_tag_values(tag)


def _filter_model_data(model, data: dict) -> dict:
    allowed_fields = set(model.__table__.columns.keys())
    return {key: value for key, value in data.items() if key in allowed_fields}


async def _cleanup_document_files(document: Document):
    config_base_dir = os.getenv("BASE_DIR", "/")
    image_dir = os.getenv("IMAGE_DIR", "/upload/images")
    base_url = os.path.join(config_base_dir, image_dir.lstrip("/").lstrip("\\"))
    image_attrs = [
        "image_urls_problem_intro",
        "image_urls_causes",
        "image_urls_evaluation",
        "image_urls_inspection",
        "image_urls_solutions",
        "image_urls_key_points",
        "image_urls",
    ]

    for attr in image_attrs:
        value = getattr(document, attr, None)
        if not value:
            continue
        for image_url in str(value).split(", "):
            filename = os.path.basename(image_url)
            if filename.strip():
                await asyncio.to_thread(delete_image_with_variants, os.path.join(base_url, filename.lstrip("/").lstrip("\\")))

    if getattr(document, "origin_file_dir", None):
        origin_file_dir = normalize_upload_path(document.origin_file_dir) or document.origin_file_dir
        await asyncio.to_thread(delete_file_if_exists, os.path.join(config_base_dir, origin_file_dir))


async def _reset_source_documents_for_document(db: AsyncSession, document_id: int, library_type: str):
    result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.document_id == document_id,
            SourceDocument.document_library_type == _normalize_library_type(library_type),
            SourceDocument.is_deleted == 0,
        )
    )
    for source_document in result.scalars().all():
        source_document.status = "uploaded"
        source_document.document_id = None
        source_document.document_library_type = "breakdown"
        source_document.review_id = None
        source_document.parse_error = None


async def _cleanup_review_origin_file(review: Document_review):
    if review.action_type != 1 or not getattr(review, "origin_file_dir", None):
        return
    config_base_dir = os.getenv("BASE_DIR", "/")
    origin_file_dir = normalize_upload_path(review.origin_file_dir) or review.origin_file_dir
    await asyncio.to_thread(delete_file_if_exists, os.path.join(config_base_dir, origin_file_dir))

IMAGE_FIELDS = [
    "image_urls",
    "image_urls_problem_intro",
    "image_urls_causes",
    "image_urls_evaluation",
    "image_urls_inspection",
    "image_urls_solutions",
    "image_urls_key_points",
]

REVIEW_COPY_FIELDS = [
    "title",
    "problem_intro",
    "image_urls",
    "causes",
    "evaluation",
    "inspection",
    "solutions",
    "key_points",
    "origin_file_name",
    "origin_file_dir",
    "tag",
    "image_urls_problem_intro",
    "image_urls_causes",
    "image_urls_evaluation",
    "image_urls_inspection",
    "image_urls_solutions",
    "image_urls_key_points",
]


def _is_admin(user: User) -> bool:
    return has_role(user, UserRole.ADMIN)


def _is_reviewer(user: User) -> bool:
    return has_role(user, UserRole.REVIEWER)


def _is_technician(user: User) -> bool:
    return has_role(user, UserRole.TECHNICIAN)


def _can_review(user: User) -> bool:
    return _is_admin(user) or _is_reviewer(user)


def _normalize_path_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return (
        value.replace("\\", "/")
        .replace(", /", ", ")
        .removeprefix("/")
        .removesuffix(", ")
        .strip()
    )


def _get_image_config() -> Dict[str, str]:
    image_dir = os.getenv("IMAGE_DIR", "upload/images")
    base_dir = os.getenv("BASE_DIR", "/")
    return {
        "IMAGE_DIR": image_dir,
        "BASE_DIR": base_dir,
    }


async def _check_image_url(image_urls: Optional[str]) -> bool:
    if not image_urls:
        return True
    config = _get_image_config()
    base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
    urls = [url.strip() for url in image_urls.split(", ") if url.strip()]
    for url in urls:
        image_name = os.path.basename(url)
        url_check = os.path.join(base_url, image_name.lstrip("/").lstrip("\\"))
        if not await asyncio.to_thread(os.path.exists, url_check):
            return False
    return True


async def _validate_review_images(payload: DocumentReviewRequest):
    for field in IMAGE_FIELDS:
        value = getattr(payload, field, None)
        if value is not None:
            setattr(payload, field, _normalize_path_value(value))
        if not await _check_image_url(getattr(payload, field, None)):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传")


def _review_to_response(
    review: Document_review,
    contributor_name: Optional[str] = None,
    reviewer_name: Optional[str] = None,
) -> DocumentReviewResponse:
    return DocumentReviewResponse(
        id=review.id,
        document_id=review.document_id,
        library_type=review.document_library_type,
        tag=_normalize_tags(review.tag),
        title=review.title,
        contributor_id=review.contributor_id,
        contributor_name=contributor_name,
        reviewer_id=review.reviewer_id,
        reviewer_name=reviewer_name,
        first_edit_date=review.first_edit_date,
        reviewed_time=review.reviewed_time,
        problem_intro=review.problem_intro,
        image_urls=review.image_urls,
        causes=review.causes,
        evaluation=review.evaluation,
        inspection=review.inspection,
        solutions=review.solutions,
        key_points=review.key_points,
        origin_file_name=review.origin_file_name,
        origin_file_dir=review.origin_file_dir,
        image_urls_problem_intro=review.image_urls_problem_intro,
        image_urls_causes=review.image_urls_causes,
        image_urls_evaluation=review.image_urls_evaluation,
        image_urls_inspection=review.image_urls_inspection,
        image_urls_solutions=review.image_urls_solutions,
        image_urls_key_points=review.image_urls_key_points,
        status=review.status,
        action_type=review.action_type,
        review_comment=review.review_comment,
    )


async def _get_user_names(db: AsyncSession, reviews: List[Document_review]) -> dict:
    user_ids = list(
        {
            review.contributor_id for review in reviews if review.contributor_id is not None
        }
        | {
            review.reviewer_id for review in reviews if review.reviewer_id is not None
        }
    )
    if not user_ids:
        return {}

    result = await db.execute(select(User.id, User.full_name, User.username).where(User.id.in_(user_ids)))
    user_map = {}
    for row in result.all():
        user_map[row.id] = row.full_name or row.username
    return user_map


async def _get_review_or_404(db: AsyncSession, review_id: int) -> Document_review:
    review_result = await db.execute(select(Document_review).where(Document_review.id == review_id))
    review = review_result.scalar_one_or_none()
    if not review:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "审核申请不存在")
    return review


async def _get_review_for_update_or_404(db: AsyncSession, review_id: int) -> Document_review:
    review_result = await db.execute(
        select(Document_review).where(Document_review.id == review_id).with_for_update()
    )
    review = review_result.scalar_one_or_none()
    if not review:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "审核申请不存在")
    return review


def _extract_review_comment(payload: Optional[dict]) -> Optional[str]:
    if not payload:
        return None
    review_comment = payload.get("review_comment")
    if review_comment is None:
        return None
    review_comment = str(review_comment).strip()
    return review_comment if review_comment else None


@router.post("/create", summary="submit a review request")
async def create_review(
    request: DocumentReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not (_is_technician(current_user) or _is_admin(current_user)):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "仅技术人员可提交审核")

    if request.action_type not in (1, 2, 3):
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "action_type 无效")

    target_document = None
    document_library_type = _normalize_library_type(request.document_library_type)
    document_model = _get_document_model(document_library_type)
    if request.action_type in (2, 3):
        if not request.document_id:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "document_id 不能为空")
        doc_result = await db.execute(
            select(document_model).where(
                document_model.id == request.document_id,
                document_model.is_deleted == 0,
            )
        )
        target_document = doc_result.scalar_one_or_none()
        if not target_document:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "文档不存在")
        if target_document.contributor_id != current_user.id and not _is_admin(current_user):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权限提交该文档审核")

    if request.action_type == 1 and not request.title:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "标题不能为空")

    if request.action_type == 3 and not request.title and target_document:
        request.title = target_document.title

    await _validate_review_images(request)

    review_kwargs = {"contributor_id": current_user.id, "first_edit_date": datetime.now(), "status": 0, "document_library_type": document_library_type}
    for field in REVIEW_COPY_FIELDS:
        value = getattr(request, field, None)
        if value is None and target_document is not None:
            value = getattr(target_document, field, None)
        if field == "tag":
            value = _normalize_tags(value)
        review_kwargs[field] = _normalize_path_value(value) if isinstance(value, str) else value

    # Upsert pending review for update/delete requests to avoid duplicate pending records
    if request.action_type in (2, 3) and request.document_id:
        pending_result = await db.execute(
            select(Document_review)
            .where(
                Document_review.status == 0,
                Document_review.action_type == request.action_type,
                Document_review.document_id == request.document_id,
                Document_review.document_library_type == document_library_type,
                Document_review.contributor_id == current_user.id,
            )
            .with_for_update()
            .order_by(Document_review.first_edit_date.desc())
        )
        pending_review = pending_result.scalars().first()
        if pending_review:
            pending_review.first_edit_date = datetime.now()
            # Review comments are authored by reviewers only.
            pending_review.review_comment = None
            for field, value in review_kwargs.items():
                setattr(pending_review, field, value)
            await db.commit()
            await db.refresh(pending_review)
            contributor_name = current_user.full_name or current_user.username
            return Result.success_with_data(_review_to_response(pending_review, contributor_name=contributor_name))

    review = Document_review(
        document_id=request.document_id if request.action_type in (2, 3) else None,
        action_type=request.action_type,
        # New submissions must start with empty review comments.
        review_comment=None,
        **review_kwargs,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    contributor_name = current_user.full_name or current_user.username
    return Result.success_with_data(_review_to_response(review, contributor_name=contributor_name))


@router.get("/get_by_id", summary="get review requests by contributor id")
async def get_reviews(
    id: Optional[int] = Query(default=None, description="contributor user id"),
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    target_user_id = id if id is not None else x_user_id
    if target_user_id is None:
        target_user_id = current_user.id

    if target_user_id != current_user.id and not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权限查看该用户审核记录")

    result = await db.execute(
        select(Document_review)
        .where(Document_review.contributor_id == target_user_id)
        .order_by(Document_review.first_edit_date.desc())
    )
    reviews = result.scalars().all()
    user_map = await _get_user_names(db, reviews)
    responses = [
        _review_to_response(
            review,
            contributor_name=user_map.get(review.contributor_id),
            reviewer_name=user_map.get(review.reviewer_id),
        )
        for review in reviews
    ]
    return Result.success_with_data(responses)


@router.get("/pending", summary="get pending review list")
async def get_pending_reviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    result = await db.execute(
        select(Document_review)
        .where(Document_review.status == 0)
        .order_by(Document_review.first_edit_date.desc())
    )
    reviews = result.scalars().all()
    user_map = await _get_user_names(db, reviews)
    responses = [
        _review_to_response(
            review,
            contributor_name=user_map.get(review.contributor_id),
            reviewer_name=user_map.get(review.reviewer_id),
        )
        for review in reviews
    ]
    return Result.success_with_data(responses)


@router.get("/all", summary="get all review list")
async def get_all_reviews(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    result = await db.execute(select(Document_review).order_by(Document_review.first_edit_date.desc()))
    reviews = result.scalars().all()
    user_map = await _get_user_names(db, reviews)
    responses = [
        _review_to_response(
            review,
            contributor_name=user_map.get(review.contributor_id),
            reviewer_name=user_map.get(review.reviewer_id),
        )
        for review in reviews
    ]
    return Result.success_with_data(responses)


@router.post("/approve/{review_id}", summary="approve review request")
async def approve_review(
    review_id: int,
    payload: Optional[dict] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    review = await _get_review_for_update_or_404(db, review_id)
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "该审核申请已处理")

    review_comment = _extract_review_comment(payload)
    vector_service = VectorService(db)
    document_model = _get_document_model(review.document_library_type)

    try:
        if review.action_type == 1:
            new_document = document_model(**_filter_model_data(document_model, {
                "title": review.title,
                "contributor_id": review.contributor_id,
                "first_edit_date": review.first_edit_date or datetime.now(),
                "problem_intro": review.problem_intro,
                "image_urls": review.image_urls,
                "causes": review.causes,
                "evaluation": review.evaluation,
                "inspection": review.inspection,
                "solutions": review.solutions,
                "key_points": review.key_points,
                "origin_file_name": review.origin_file_name,
                "origin_file_dir": review.origin_file_dir,
                "image_urls_problem_intro": review.image_urls_problem_intro,
                "image_urls_causes": review.image_urls_causes,
                "image_urls_evaluation": review.image_urls_evaluation,
                "image_urls_inspection": review.image_urls_inspection,
                "image_urls_solutions": review.image_urls_solutions,
                "image_urls_key_points": review.image_urls_key_points,
                "tag": _normalize_tags(review.tag),
                "is_vectorized": 0,
            }))
            db.add(new_document)
            await db.flush()
            await set_document_tag_names(db, new_document, review.tag, created_by=review.contributor_id)
            review.document_id = new_document.id
            await vector_service.add_document_to_vector_store(new_document, commit=False)

        elif review.action_type == 2:
            if not review.document_id:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "缺少待更新文档ID")
            doc_result = await db.execute(
                select(document_model)
                .where(
                    document_model.id == review.document_id,
                    document_model.is_deleted == 0,
                )
                .with_for_update()
            )
            document = doc_result.scalar_one_or_none()
            if not document:
                raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "待更新文档不存在")

            for field in REVIEW_COPY_FIELDS:
                if field == "title" and not review.title:
                    continue
                if field == "tag":
                    await set_document_tag_names(db, document, getattr(review, field), created_by=review.contributor_id)
                    continue
                if hasattr(document, field):
                    setattr(document, field, getattr(review, field))
            document.is_vectorized = 0
            await vector_service.delete_document_from_vector_store(document.id, getattr(document, "library_type", "breakdown"))
            await vector_service.add_document_to_vector_store(document, commit=False)

        elif review.action_type == 3:
            if not review.document_id:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "缺少待删除文档ID")
            target_document_id = review.document_id
            doc_result = await db.execute(
                select(document_model)
                .where(
                    document_model.id == target_document_id,
                    document_model.is_deleted == 0,
                )
                .with_for_update()
            )
            document = doc_result.scalar_one_or_none()
            if not document:
                raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "待删除文档不存在")

            await vector_service.delete_document_from_vector_store(document.id, getattr(document, "library_type", "breakdown"))
            await _cleanup_document_files(document)
            await _reset_source_documents_for_document(db, document.id, getattr(document, "library_type", "breakdown"))
            document.is_deleted = 1
        else:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "action_type 无效")

        review.status = 1
        review.reviewer_id = current_user.id
        review.reviewed_time = datetime.now()
        review.review_comment = review_comment if review_comment is not None else review.review_comment
        source_result = await db.execute(
            select(SourceDocument).where(
                SourceDocument.review_id == review.id,
                SourceDocument.is_deleted == 0,
            )
        )
        source_document = source_result.scalar_one_or_none()
        if source_document:
            source_document.status = "vectorized" if review.action_type == 1 else "uploaded"
            source_document.document_id = review.document_id if review.action_type == 1 else source_document.document_id
            source_document.document_library_type = review.document_library_type
            source_document.review_id = None
            source_document.parse_error = None
        await db.commit()
        await db.refresh(review)
    except AppException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"审核通过失败: {str(e)}")

    names = await _get_user_names(db, [review])
    return Result.success_with_data(
        _review_to_response(
            review,
            contributor_name=names.get(review.contributor_id),
            reviewer_name=names.get(review.reviewer_id),
        )
    )


@router.post("/reject/{review_id}", summary="reject review request")
async def reject_review(
    review_id: int,
    payload: Optional[dict] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    review = await _get_review_for_update_or_404(db, review_id)
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "该审核申请已处理")

    review.status = 2
    review.reviewer_id = current_user.id
    review.reviewed_time = datetime.now()
    review_comment = _extract_review_comment(payload)
    review.review_comment = review_comment if review_comment is not None else review.review_comment
    source_result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.review_id == review.id,
            SourceDocument.is_deleted == 0,
        )
    )
    source_document = source_result.scalar_one_or_none()
    if source_document:
        source_document.status = "uploaded"
        source_document.review_id = None
        source_document.parse_error = review.review_comment
    await _cleanup_review_origin_file(review)
    await db.commit()
    await db.refresh(review)

    names = await _get_user_names(db, [review])
    return Result.success_with_data(
        _review_to_response(
            review,
            contributor_name=names.get(review.contributor_id),
            reviewer_name=names.get(review.reviewer_id),
        )
    )


@router.post("/withdraw/{review_id}", summary="withdraw review request")
async def withdraw_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    review = await _get_review_for_update_or_404(db, review_id)
    if review.contributor_id != current_user.id and not _is_admin(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权限撤回该审核申请")
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "仅待审核状态可撤回")

    review.status = 3
    review.reviewed_time = datetime.now()
    source_result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.review_id == review.id,
            SourceDocument.is_deleted == 0,
        )
    )
    source_document = source_result.scalar_one_or_none()
    if source_document:
        source_document.status = "uploaded"
        source_document.review_id = None
        source_document.parse_error = None
    await _cleanup_review_origin_file(review)
    await db.commit()
    await db.refresh(review)

    names = await _get_user_names(db, [review])
    return Result.success_with_data(
        _review_to_response(
            review,
            contributor_name=names.get(review.contributor_id),
            reviewer_name=names.get(review.reviewer_id),
        )
    )
