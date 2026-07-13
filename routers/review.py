import asyncio
import os
from datetime import datetime
from types import SimpleNamespace
from typing import Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import (
    Document,
    DocumentBreakdown,
    DocumentKnowledge,
    Document_review,
    KnowledgeDocumentReview,
    KnowledgeDocumentSection,
    SourceDocument,
    User,
)
from schemas import DocumentReviewRequest, DocumentReviewResponse, Result
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.file_cleanup import delete_file_if_exists, delete_image_with_variants
from utils.roles import UserRole, has_role
from utils.tag_service import normalize_tag_values, set_document_tag_names
from utils.upload_paths import normalize_upload_path
from utils.VectorService import VectorService
from knowledge_parsers import knowledge_parser
from knowledge_parsers.section_service import delete_section_images_for_document, replace_knowledge_document_sections

router = APIRouter(prefix="/review", tags=["document-review"])

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}
REVIEW_LIBRARY_MODELS = {"breakdown": Document_review, "knowledge": KnowledgeDocumentReview}
LEGACY_KNOWLEDGE_REVIEW_MIGRATED_COMMENT = "系统已迁移为知识库审核记录"


def _normalize_library_type(library_type: str) -> str:
    """把审核请求里的目标库类型固定为故障库或知识库，避免审核通过时写错表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """按审核记录里的库类型选择实际文档表模型。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _get_review_model(library_type: str):
    return REVIEW_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _review_library_type(review) -> str:
    if isinstance(review, KnowledgeDocumentReview):
        return "knowledge"
    return _normalize_library_type(getattr(review, "document_library_type", "breakdown"))


def _review_storage_type(review) -> str:
    return "knowledge" if isinstance(review, KnowledgeDocumentReview) else "breakdown"


def _normalize_tags(tag):
    """审核表保留 tag id/name 混合输入，最终写文档表时会转成 tag id 数组。"""
    return normalize_tag_values(tag)


def _section_to_review_payload(section, index: int) -> dict:
    if hasattr(section, "model_dump"):
        data = section.model_dump()
    elif isinstance(section, dict):
        data = dict(section)
    else:
        data = {
            "section_index": getattr(section, "section_index", None),
            "section_title": getattr(section, "section_title", None),
            "section_type": getattr(section, "section_type", None),
            "plain_text": getattr(section, "plain_text", None),
            "image_urls": getattr(section, "image_urls", None),
            "char_start": getattr(section, "char_start", None),
            "char_end": getattr(section, "char_end", None),
            "metadata": getattr(section, "metadata", None) or getattr(section, "section_metadata", None),
        }

    image_urls = data.get("image_urls") or []
    if isinstance(image_urls, str):
        image_urls = [url.strip() for url in image_urls.split(",") if url.strip()]

    metadata = data.get("metadata")
    if metadata is None:
        metadata = data.get("section_metadata") or {}

    plain_text = data.get("plain_text") or ""
    return {
        "section_index": data.get("section_index") if data.get("section_index") is not None else index,
        "section_title": data.get("section_title") or f"section-{index + 1}",
        "section_type": data.get("section_type") or str(index + 1),
        "plain_text": plain_text,
        "image_urls": image_urls,
        "char_start": data.get("char_start"),
        "char_end": data.get("char_end"),
        "metadata": metadata,
    }


def _serialize_review_sections(sections) -> Optional[List[dict]]:
    if sections is None:
        return None
    return [_section_to_review_payload(section, index) for index, section in enumerate(sections or [])]


def _review_section_objects(sections) -> List[SimpleNamespace]:
    payload = _serialize_review_sections(sections) or []
    return [SimpleNamespace(**section) for section in payload]


def _review_image_urls(review: Document_review) -> List[str]:
    image_urls = []
    for field in IMAGE_FIELDS:
        value = getattr(review, field, None)
        if isinstance(value, str):
            candidates = [url.strip() for url in value.split(",") if url.strip()]
        elif isinstance(value, list):
            candidates = [str(url).strip() for url in value if str(url).strip()]
        else:
            candidates = []
        for url in candidates:
            if url not in image_urls:
                image_urls.append(url)
    return image_urls


def _fallback_knowledge_sections(review: Document_review) -> List[SimpleNamespace]:
    text_parts = [
        getattr(review, "problem_intro", None),
        getattr(review, "causes", None),
        getattr(review, "evaluation", None),
        getattr(review, "inspection", None),
        getattr(review, "solutions", None),
        getattr(review, "key_points", None),
    ]
    plain_text = "\n\n".join(str(part).strip() for part in text_parts if str(part or "").strip())
    if not plain_text:
        return []
    return [
        SimpleNamespace(
            section_index=0,
            section_title=review.title or "section-1",
            section_type="1",
            plain_text=plain_text,
            image_urls=_review_image_urls(review),
            char_start=0,
            char_end=len(plain_text),
            metadata={},
        )
    ]


def _review_sections_for_create(review: Document_review) -> List[SimpleNamespace]:
    sections = _review_section_objects(getattr(review, "sections", None))
    if sections:
        return sections
    return _fallback_knowledge_sections(review)


def _filter_model_data(model, data: dict) -> dict:
    allowed_fields = set(model.__table__.columns.keys())
    return {key: value for key, value in data.items() if key in allowed_fields}


async def _cleanup_document_files(db: AsyncSession, document: Document):
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

    # 删除知识库文档的章节图片（KnowledgeDocumentSection.image_urls）
    library_type = getattr(document, "library_type", "breakdown")
    if library_type == "knowledge":
        await delete_section_images_for_document(db, document.id, base_url)

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
        source_document.review_library_type = "breakdown"
        source_document.parse_error = None
        source_document.parse_started_time = None


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
    review,
    contributor_name: Optional[str] = None,
    reviewer_name: Optional[str] = None,
) -> DocumentReviewResponse:
    library_type = _review_library_type(review)
    return DocumentReviewResponse(
        id=review.id,
        document_id=review.document_id,
        library_type=library_type,
        review_library_type=_review_storage_type(review),
        tag=_normalize_tags(review.tag),
        title=review.title,
        contributor_id=review.contributor_id,
        contributor_name=contributor_name,
        reviewer_id=review.reviewer_id,
        reviewer_name=reviewer_name,
        first_edit_date=review.first_edit_date,
        reviewed_time=review.reviewed_time,
        problem_intro=getattr(review, "problem_intro", None),
        image_urls=getattr(review, "image_urls", None),
        causes=getattr(review, "causes", None),
        evaluation=getattr(review, "evaluation", None),
        inspection=getattr(review, "inspection", None),
        solutions=getattr(review, "solutions", None),
        key_points=getattr(review, "key_points", None),
        origin_file_name=getattr(review, "origin_file_name", None),
        origin_file_dir=getattr(review, "origin_file_dir", None),
        sections=_serialize_review_sections(getattr(review, "sections", None)),
        image_urls_problem_intro=getattr(review, "image_urls_problem_intro", None),
        image_urls_causes=getattr(review, "image_urls_causes", None),
        image_urls_evaluation=getattr(review, "image_urls_evaluation", None),
        image_urls_inspection=getattr(review, "image_urls_inspection", None),
        image_urls_solutions=getattr(review, "image_urls_solutions", None),
        image_urls_key_points=getattr(review, "image_urls_key_points", None),
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


async def _get_review_or_404(db: AsyncSession, review_id: int, review_library_type: str = "breakdown"):
    review_model = _get_review_model(review_library_type)
    review_result = await db.execute(select(review_model).where(review_model.id == review_id))
    review = review_result.scalar_one_or_none()
    if not review:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "审核申请不存在")
    return review


async def _get_review_for_update_or_404(db: AsyncSession, review_id: int, review_library_type: str = "breakdown"):
    review_model = _get_review_model(review_library_type)
    review_result = await db.execute(
        select(review_model).where(review_model.id == review_id).with_for_update()
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


async def _get_source_document_for_review(db: AsyncSession, review):
    review_type = _review_library_type(review)
    result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.review_id == review.id,
            SourceDocument.review_library_type == review_type,
            SourceDocument.is_deleted == 0,
        )
    )
    source_document = result.scalar_one_or_none()
    if source_document:
        return source_document

    # Legacy rows created before review_library_type existed may still keep the review id
    # with the default review_library_type. If there is only one active source row for
    # this review id, use it so reject/withdraw can release it back to uploaded.
    fallback_result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.review_id == review.id,
            SourceDocument.is_deleted == 0,
        )
    )
    candidates = fallback_result.scalars().all()
    if len(candidates) == 1:
        return candidates[0]

    origin_file_dir = getattr(review, "origin_file_dir", None)
    origin_file_name = getattr(review, "origin_file_name", None)
    for candidate in candidates:
        if origin_file_dir and candidate.stored_file_path == origin_file_dir:
            return candidate
        if origin_file_name and candidate.origin_file_name == origin_file_name:
            return candidate
    return None


def _source_matches_review(source: SourceDocument, review) -> bool:
    origin_file_dir = normalize_upload_path(getattr(review, "origin_file_dir", None))
    stored_file_path = normalize_upload_path(getattr(source, "stored_file_path", None))
    if origin_file_dir and stored_file_path and origin_file_dir == stored_file_path:
        return True
    origin_file_name = getattr(review, "origin_file_name", None)
    if origin_file_name and origin_file_name == getattr(source, "origin_file_name", None):
        return True
    return False


async def _find_legacy_knowledge_source(db: AsyncSession, review: Document_review):
    result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.review_id == review.id,
            SourceDocument.is_deleted == 0,
        )
    )
    candidates = result.scalars().all()
    knowledge_candidates = [
        source for source in candidates
        if _normalize_library_type(source.review_library_type) == "knowledge"
        or _normalize_library_type(source.document_library_type) == "knowledge"
    ]
    if not knowledge_candidates:
        return None
    matched = [source for source in knowledge_candidates if _source_matches_review(source, review)]
    if matched:
        return matched[0]
    return knowledge_candidates[0] if len(knowledge_candidates) == 1 else None


async def _sections_from_source_file(source: SourceDocument, review: Document_review) -> Optional[List[dict]]:
    document_base_dir = os.getenv(
        "DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
    )
    stored_path = normalize_upload_path(source.stored_file_path) or source.stored_file_path
    absolute_path = os.path.join(document_base_dir, stored_path)
    if not absolute_path or not await asyncio.to_thread(os.path.exists, absolute_path):
        return None
    parsed = await asyncio.to_thread(knowledge_parser.parse, absolute_path)
    if not parsed:
        return None
    if parsed.title and not review.title:
        review.title = parsed.title
    if getattr(parsed, "image_urls", None) and not getattr(review, "image_urls", None):
        review.image_urls = ", ".join(str(url).strip() for url in parsed.image_urls if str(url).strip())
    sections = _serialize_review_sections(getattr(parsed, "sections", None))
    if sections:
        return sections
    content = str(getattr(parsed, "content", "") or "").strip()
    if not content:
        return None
    image_urls = [str(url).strip() for url in getattr(parsed, "image_urls", []) or [] if str(url).strip()]
    return [
        {
            "section_index": 0,
            "section_title": getattr(parsed, "title", None) or review.title or "section-1",
            "section_type": "1",
            "plain_text": content,
            "image_urls": image_urls,
            "char_start": 0,
            "char_end": len(content),
            "metadata": {},
        }
    ]


async def _migrate_legacy_knowledge_reviews(db: AsyncSession, reviews: List) -> List:
    migrated_reviews = []
    changed = False
    for review in reviews:
        if (
            isinstance(review, Document_review)
            and review.status == 3
            and review.review_comment == LEGACY_KNOWLEDGE_REVIEW_MIGRATED_COMMENT
        ):
            continue
        if not isinstance(review, Document_review) or review.status != 0:
            migrated_reviews.append(review)
            continue

        source = await _find_legacy_knowledge_source(db, review)
        if not source:
            migrated_reviews.append(review)
            continue

        existing_result = await db.execute(
            select(KnowledgeDocumentReview)
            .where(
                KnowledgeDocumentReview.status == 0,
                KnowledgeDocumentReview.contributor_id == review.contributor_id,
                KnowledgeDocumentReview.origin_file_name == review.origin_file_name,
                KnowledgeDocumentReview.origin_file_dir == normalize_upload_path(review.origin_file_dir),
            )
            .order_by(KnowledgeDocumentReview.first_edit_date.desc())
        )
        existing_review = existing_result.scalars().first()
        if existing_review:
            source.review_id = existing_review.id
            source.review_library_type = "knowledge"
            source.document_library_type = "knowledge"
            source.status = "review_pending"
            review.status = 3
            review.reviewed_time = datetime.now()
            review.review_comment = LEGACY_KNOWLEDGE_REVIEW_MIGRATED_COMMENT
            migrated_reviews.append(existing_review)
            changed = True
            continue

        sections = await _sections_from_source_file(source, review)
        new_review = KnowledgeDocumentReview(
            document_id=review.document_id,
            title=review.title,
            contributor_id=review.contributor_id,
            reviewer_id=None,
            first_edit_date=review.first_edit_date or datetime.now(),
            reviewed_time=None,
            status=0,
            image_urls=getattr(review, "image_urls", None),
            origin_file_name=getattr(review, "origin_file_name", None),
            origin_file_dir=normalize_upload_path(getattr(review, "origin_file_dir", None)),
            tag=_normalize_tags(getattr(review, "tag", [])),
            sections=sections or _serialize_review_sections(_fallback_knowledge_sections(review)),
            action_type=review.action_type,
            review_comment=None,
        )
        db.add(new_review)
        await db.flush()
        await db.refresh(new_review)

        source.review_id = new_review.id
        source.review_library_type = "knowledge"
        source.document_library_type = "knowledge"
        source.status = "review_pending"
        source.parse_error = None
        source.parse_started_time = None

        review.status = 3
        review.reviewed_time = datetime.now()
        review.review_comment = LEGACY_KNOWLEDGE_REVIEW_MIGRATED_COMMENT
        migrated_reviews.append(new_review)
        changed = True

    if changed:
        await db.commit()
        for review in migrated_reviews:
            await db.refresh(review)
    return migrated_reviews


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
    review_model = _get_review_model(document_library_type)
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

    review_kwargs = {
        "contributor_id": current_user.id,
        "first_edit_date": datetime.now(),
        "status": 0,
        "document_library_type": document_library_type,
        "sections": _serialize_review_sections(request.sections),
    }
    for field in REVIEW_COPY_FIELDS:
        value = getattr(request, field, None)
        if value is None and target_document is not None:
            value = getattr(target_document, field, None)
        if field == "tag":
            value = _normalize_tags(value)
        review_kwargs[field] = _normalize_path_value(value) if isinstance(value, str) else value

    # Upsert pending review for update/delete requests to avoid duplicate pending records
    if request.action_type in (2, 3) and request.document_id:
        pending_conditions = [
            review_model.status == 0,
            review_model.action_type == request.action_type,
            review_model.document_id == request.document_id,
            review_model.contributor_id == current_user.id,
        ]
        if hasattr(review_model, "document_library_type"):
            pending_conditions.append(review_model.document_library_type == document_library_type)

        pending_result = await db.execute(
            select(review_model)
            .where(*pending_conditions)
            .with_for_update()
            .order_by(review_model.first_edit_date.desc())
        )
        pending_review = pending_result.scalars().first()
        if pending_review:
            pending_review.first_edit_date = datetime.now()
            # Review comments are authored by reviewers only.
            pending_review.review_comment = None
            for field, value in _filter_model_data(review_model, review_kwargs).items():
                setattr(pending_review, field, value)
            await db.commit()
            await db.refresh(pending_review)
            contributor_name = current_user.full_name or current_user.username
            return Result.success_with_data(_review_to_response(pending_review, contributor_name=contributor_name))

    review = review_model(
        document_id=request.document_id if request.action_type in (2, 3) else None,
        action_type=request.action_type,
        # New submissions must start with empty review comments.
        review_comment=None,
        **_filter_model_data(review_model, review_kwargs),
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

    breakdown_result = await db.execute(
        select(Document_review).where(Document_review.contributor_id == target_user_id)
    )
    knowledge_result = await db.execute(
        select(KnowledgeDocumentReview).where(KnowledgeDocumentReview.contributor_id == target_user_id)
    )
    reviews = list(breakdown_result.scalars().all()) + list(knowledge_result.scalars().all())
    reviews = await _migrate_legacy_knowledge_reviews(db, reviews)
    reviews.sort(key=lambda review: review.first_edit_date or datetime.min, reverse=True)
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

    breakdown_result = await db.execute(select(Document_review).where(Document_review.status == 0))
    knowledge_result = await db.execute(select(KnowledgeDocumentReview).where(KnowledgeDocumentReview.status == 0))
    reviews = list(breakdown_result.scalars().all()) + list(knowledge_result.scalars().all())
    reviews = await _migrate_legacy_knowledge_reviews(db, reviews)
    reviews.sort(key=lambda review: review.first_edit_date or datetime.min, reverse=True)
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

    breakdown_result = await db.execute(select(Document_review))
    knowledge_result = await db.execute(select(KnowledgeDocumentReview))
    reviews = list(breakdown_result.scalars().all()) + list(knowledge_result.scalars().all())
    reviews = await _migrate_legacy_knowledge_reviews(db, reviews)
    reviews.sort(key=lambda review: review.first_edit_date or datetime.min, reverse=True)
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
    review_library_type: str = Query(default="breakdown"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    review = await _get_review_for_update_or_404(db, review_id, review_library_type)
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "该审核申请已处理")

    review_comment = _extract_review_comment(payload)
    review_document_library_type = _review_library_type(review)
    vector_service = VectorService(db)
    document_model = _get_document_model(review_document_library_type)

    try:
        if review.action_type == 1:
            new_document = document_model(**_filter_model_data(document_model, {
                "title": review.title,
                "contributor_id": review.contributor_id,
                "first_edit_date": review.first_edit_date or datetime.now(),
                "problem_intro": getattr(review, "problem_intro", None),
                "image_urls": getattr(review, "image_urls", None),
                "causes": getattr(review, "causes", None),
                "evaluation": getattr(review, "evaluation", None),
                "inspection": getattr(review, "inspection", None),
                "solutions": getattr(review, "solutions", None),
                "key_points": getattr(review, "key_points", None),
                "origin_file_name": getattr(review, "origin_file_name", None),
                "origin_file_dir": getattr(review, "origin_file_dir", None),
                "image_urls_problem_intro": getattr(review, "image_urls_problem_intro", None),
                "image_urls_causes": getattr(review, "image_urls_causes", None),
                "image_urls_evaluation": getattr(review, "image_urls_evaluation", None),
                "image_urls_inspection": getattr(review, "image_urls_inspection", None),
                "image_urls_solutions": getattr(review, "image_urls_solutions", None),
                "image_urls_key_points": getattr(review, "image_urls_key_points", None),
                "tag": _normalize_tags(review.tag),
                "is_vectorized": 0,
            }))
            db.add(new_document)
            await db.flush()
            await set_document_tag_names(db, new_document, review.tag, created_by=review.contributor_id)
            if _review_library_type(review) == "knowledge":
                await replace_knowledge_document_sections(db, new_document, _review_sections_for_create(review))
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
            if _review_library_type(review) == "knowledge" and getattr(review, "sections", None) is not None:
                await replace_knowledge_document_sections(db, document, _review_section_objects(review.sections))
            document.is_vectorized = 0
            await vector_service.delete_document_from_vector_store(document.id, getattr(document, "library_type", review_document_library_type))
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
            await _cleanup_document_files(db, document)
            await _reset_source_documents_for_document(db, document.id, getattr(document, "library_type", "breakdown"))
            document.is_deleted = 1
        else:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "action_type 无效")

        review.status = 1
        review.reviewer_id = current_user.id
        review.reviewed_time = datetime.now()
        review.review_comment = review_comment if review_comment is not None else review.review_comment
        source_document = await _get_source_document_for_review(db, review)
        if source_document:
            source_document.status = "vectorized" if review.action_type == 1 else "uploaded"
            source_document.document_id = review.document_id if review.action_type == 1 else source_document.document_id
            source_document.document_library_type = review_document_library_type
            source_document.review_id = None
            source_document.review_library_type = "breakdown"
            source_document.parse_error = None
            source_document.parse_started_time = None
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
    review_library_type: str = Query(default="breakdown"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not _can_review(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无审核权限")

    review = await _get_review_for_update_or_404(db, review_id, review_library_type)
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "该审核申请已处理")

    review.status = 2
    review.reviewer_id = current_user.id
    review.reviewed_time = datetime.now()
    review_comment = _extract_review_comment(payload)
    review.review_comment = review_comment if review_comment is not None else review.review_comment
    source_document = await _get_source_document_for_review(db, review)
    if source_document:
        source_document.status = "uploaded"
        source_document.review_id = None
        source_document.review_library_type = "breakdown"
        source_document.parse_error = review.review_comment
        source_document.parse_started_time = None
    else:
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
    review_library_type: str = Query(default="breakdown"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    review = await _get_review_for_update_or_404(db, review_id, review_library_type)
    if review.contributor_id != current_user.id and not _is_admin(current_user):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权限撤回该审核申请")
    if review.status != 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.REVIEW_ALREADY_PROCESSED, "仅待审核状态可撤回")

    review.status = 3
    review.reviewed_time = datetime.now()
    source_document = await _get_source_document_for_review(db, review)
    if source_document:
        source_document.status = "uploaded"
        source_document.review_id = None
        source_document.review_library_type = "breakdown"
        source_document.parse_error = None
        source_document.parse_started_time = None
    else:
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
