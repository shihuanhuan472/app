# routers/documents.py
import asyncio
import json
import time
import uuid
from datetime import datetime
import os
import re
import shutil
from pathlib import Path
import logging
from types import SimpleNamespace
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, status, UploadFile, Body, File
from sqlalchemy import or_

# from sqlalchemy.orm import Session
from typing import List
from utils.VectorService import VectorService
from dependencies import get_current_active_user
from models import (
    Document,
    DocumentBreakdown,
    DocumentKnowledge,
    Document_review,
    KnowledgeDocumentSection,
    SourceDocument,
    User,
)
from schemas import (
    DocumentCreate,
    DocumentResponse,
    Result,
    DeleteImageRequest,
    Page,
    DocumentQuery,
    KnowledgeSectionResponse,
    UploadDocumentResponse,
    AnalyzeRequest,
    BatchDeleteRequest,
)
from database import get_db
import aiofiles
from utils.PdfParser import pdf_parser
from utils.PPTParser import ppt_parser
from utils.WordParser import word_parser
from utils.HTMLParser import html_parser
from utils.TXTParser import txt_parser
from utils.MarkdownParser import markdown_parser
from utils.ImageParser import image_parser
from utils.CsvExcelParser import csv_excel_parser
from knowledge_parsers import knowledge_parser
from knowledge_parsers.section_service import (
    get_knowledge_document_sections,
    replace_knowledge_document_sections,
    delete_section_images_for_document,
)
from utils.file_classifier import (
    ALLOWED_DOCUMENT_EXTENSIONS,
    build_document_storage_path,
    get_document_category,
    get_file_extension,
    is_upload_content_valid,
    normalize_uploaded_relative_filename,
)
from utils.file_cleanup import delete_file_if_exists, delete_image_with_variants
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.pagination import build_pagination_payload
from utils.roles import UserRole, has_role
from utils.upload_paths import normalize_upload_path
from utils.tag_service import (
    get_document_tag_names,
    normalize_tag_values,
    normalize_tag_names,
    set_document_tag_names,
    tag_filter_for_model,
    tag_keyword_filter_for_model,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select, func, delete

router = APIRouter(prefix="/document", tags=["文档"])
load_dotenv()
logger = logging.getLogger(__name__)

DOCUMENT_LIBRARY_MODELS = {
    "breakdown": DocumentBreakdown,
    "knowledge": DocumentKnowledge,
}


def _normalize_library_type(library_type: str) -> str:
    """把前端传入的库类型收敛为两个固定值，避免出现拼写不同导致写错表。"""
    return (
        "knowledge"
        if str(library_type or "").strip().lower() == "knowledge"
        else "breakdown"
    )


def _title_from_source_filename(file_name: str) -> str:
    """导入标题优先使用源文件名，去掉目录和扩展名。"""
    filename = Path(str(file_name or "").replace("\\", "/")).name
    title = os.path.splitext(filename)[0].strip()
    return title or filename.strip() or "未命名文档"


def _get_document_model(library_type: str):
    """根据库类型选择对应 ORM 模型，使同一套接口可以读写故障库或知识库。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _is_all_library_type(library_type: str) -> bool:
    """判断是否需要同时查询故障库和知识库，列表页使用 all 才能展示两个库的数据。"""
    return str(library_type or "").strip().lower() == "all"


def _sort_documents_by_edit_time(documents):
    """按编辑时间倒序合并两张表的数据，避免知识库和故障库混排时顺序不稳定。"""
    return sorted(
        documents,
        key=lambda document: getattr(document, "first_edit_date", None) or datetime.min,
        reverse=True,
    )


def _normalize_tags(tag):
    """把标签统一为去空白的字符串数组。实际关系保存在标签关联表中。"""
    return normalize_tag_values(tag)


def _require_admin_document_write(
    current_user: User, message: str = "技术人员需提交审核，审核通过后才会写入文档库"
):
    """文档库的直接写入只允许管理员，避免绕过审核流程。"""
    if not has_role(current_user, UserRole.ADMIN):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, message)


def _tag_filter(model, tags):
    """按标签做包含任一标签的过滤，用于设备语义标签的交叉检索。"""
    return tag_filter_for_model(model, tags)


def _tag_keyword_filter(model, keyword: str):
    """让搜索框关键词也能命中文档标签。"""
    return tag_keyword_filter_for_model(model, keyword)


def _document_text_keyword_conditions(model, keyword: str):
    conditions = [model.title.like(f"%{keyword}%")]
    for field in ("problem_intro", "summary", "content"):
        column = getattr(model, field, None)
        if column is not None:
            conditions.append(column.like(f"%{keyword}%"))
    return conditions


def _model_column_names(model) -> set:
    return set(model.__table__.columns.keys())


def _filter_model_data(model, data: dict) -> dict:
    allowed_fields = _model_column_names(model)
    return {key: value for key, value in data.items() if key in allowed_fields}


DOCUMENT_COPY_FIELDS = [
    "title",
    "contributor_id",
    "first_edit_date",
    "problem_intro",
    "image_urls",
    "image_urls_problem_intro",
    "causes",
    "image_urls_causes",
    "evaluation",
    "image_urls_evaluation",
    "inspection",
    "image_urls_inspection",
    "solutions",
    "image_urls_solutions",
    "key_points",
    "image_urls_key_points",
    "origin_file_name",
    "origin_file_dir",
]


def _is_knowledge_library(library_type: str) -> bool:
    return _normalize_library_type(library_type) == "knowledge"


def _parse_image_urls_to_set(value) -> set:
    """将逗号分隔的图片 URL 字符串解析为去重集合。"""
    if not value:
        return set()
    return set(url.strip() for url in str(value).split(", ") if url.strip())


def _join_image_urls(image_urls) -> str:
    if not image_urls:
        return None
    if isinstance(image_urls, str):
        return image_urls
    return (
        ", ".join(
            str(image_url).strip() for image_url in image_urls if str(image_url).strip()
        )
        or None
    )


def _normalize_section_marker(value) -> str:
    value = str(value or "").strip().lower()
    if value in {"title", "directory"}:
        return value
    if re.fullmatch(r"\d+(?:\.\d+)*", value):
        return value
    match = re.fullmatch(r"level_([1-6])", value)
    if match:
        return f"level_{match.group(1)}"
    return "1"


def _fill_request_section_markers(sections):
    counters = [0, 0, 0, 0, 0, 0]
    for index, section in enumerate(sections):
        raw = str(section.section_type or "").strip().lower()
        if raw in {"title", "directory"}:
            section.section_type = raw
            continue
        if re.fullmatch(r"\d+(?:\.\d+)*", raw):
            parts = [int(part) for part in raw.split(".") if part.isdigit()]
            for idx, part in enumerate(parts[:6]):
                counters[idx] = part
            for idx in range(len(parts), len(counters)):
                counters[idx] = 0
            section.section_type = raw
            continue

        level_match = re.fullmatch(r"level_([1-6])", raw)
        level = int(level_match.group(1)) if level_match else 1
        if level > 1 and counters[0] == 0:
            counters[0] = 1
        counters[level - 1] += 1
        for idx in range(level, len(counters)):
            counters[idx] = 0
        section.section_type = ".".join(
            str(part) for part in counters[:level] if part > 0
        ) or str(index + 1)


def _copy_document_to_library(document: Document, library_type: str, tag=None):
    """把解析器产出的文档对象转换成目标库表对象，避免知识库导入时仍写入故障库表。"""
    document_model = _get_document_model(library_type)
    copied_data = {
        field: getattr(document, field, None) for field in DOCUMENT_COPY_FIELDS
    }
    copied_data["tag"] = _normalize_tags(
        tag if tag is not None else getattr(document, "tag", [])
    )
    return document_model(**_filter_model_data(document_model, copied_data))


def _knowledge_sections_from_request(sections):
    result = []
    for index, section in enumerate(sections or []):
        data = section.model_dump() if hasattr(section, "model_dump") else dict(section)
        result.append(
            SimpleNamespace(
                section_index=(
                    data.get("section_index")
                    if data.get("section_index") is not None
                    else index
                ),
                section_title=data.get("section_title") or f"章节{index + 1}",
                section_type=_normalize_section_marker(
                    data.get("section_type") or str(index + 1)
                ),
                plain_text=data.get("plain_text") or "",
                image_urls=data.get("image_urls") or [],
                char_start=data.get("char_start"),
                char_end=data.get("char_end"),
                metadata=data.get("metadata") or {},
            )
        )
    _fill_request_section_markers(result)
    return result


def _knowledge_document_from_parsed(
    parsed, contributor_id: int, file_name: str, origin_file_dir: str, tags
):
    return DocumentKnowledge(
        title=parsed.title or file_name,
        contributor_id=contributor_id,
        first_edit_date=datetime.now(),
        image_urls=_join_image_urls(parsed.image_urls),
        origin_file_name=file_name,
        origin_file_dir=origin_file_dir,
        tag=_normalize_tags(tags),
        is_vectorized=0,
        is_deleted=0,
    )


async def _knowledge_sections_to_response(db: AsyncSession, document_id: int):
    sections = await get_knowledge_document_sections(db, document_id)
    return [
        KnowledgeSectionResponse(
            id=section.id,
            section_index=section.section_index,
            section_title=section.section_title,
            section_type=section.section_type,
            plain_text=section.plain_text,
            image_urls=section.image_urls or [],
            char_start=section.char_start,
            char_end=section.char_end,
            metadata=section.section_metadata or {},
        )
        for section in sections
    ]


# 目前支持的文档类型
ALLOWED_EXTENSIONS = ALLOWED_DOCUMENT_EXTENSIONS


def _normalize_document_for_db(document: Document) -> None:
    text_fields = [
        "title",
        "problem_intro",
        "causes",
        "evaluation",
        "inspection",
        "solutions",
        "key_points",
        "origin_file_name",
        "origin_file_dir",
    ]
    image_fields = [
        "image_urls",
        "image_urls_problem_intro",
        "image_urls_causes",
        "image_urls_evaluation",
        "image_urls_inspection",
        "image_urls_solutions",
        "image_urls_key_points",
    ]

    for field in text_fields:
        value = getattr(document, field, None)
        if isinstance(value, (list, tuple)):
            setattr(
                document,
                field,
                "\n".join(str(item) for item in value if item is not None),
            )
        elif isinstance(value, dict):
            setattr(document, field, json.dumps(value, ensure_ascii=False))
        elif value is not None and not isinstance(value, str):
            setattr(document, field, str(value))

    for field in image_fields:
        value = getattr(document, field, None)
        if isinstance(value, (list, tuple)):
            text = ", ".join(
                str(item).strip()
                for item in value
                if item is not None and str(item).strip()
            )
            setattr(document, field, text or None)
        elif isinstance(value, dict):
            setattr(document, field, json.dumps(value, ensure_ascii=False))
        elif value is not None and not isinstance(value, str):
            setattr(document, field, str(value))


def _is_empty_text(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.strip()) == 0
    return len(str(value).strip()) == 0


def _has_meaningful_title(document: Document) -> bool:
    return not _is_empty_text(getattr(document, "title", None))


def _is_ai_result_effectively_empty(document: Document) -> bool:
    """
    判断AI解析结果是否“基本为空”：
    标题、问题简介、原因、评估、检查、解决方案、总结全部为空时判定为空结果。
    """
    core_fields = [
        "title",
        "problem_intro",
        "causes",
        "evaluation",
        "inspection",
        "solutions",
        "key_points",
    ]
    return all(_is_empty_text(getattr(document, field, None)) for field in core_fields)


def _build_create_review_from_document(
    document: Document, contributor_id: int
) -> Document_review:
    return Document_review(
        document_id=None,
        document_library_type=getattr(document, "library_type", "breakdown"),
        title=document.title,
        contributor_id=contributor_id,
        reviewer_id=None,
        first_edit_date=document.first_edit_date or datetime.now(),
        reviewed_time=None,
        status=0,
        problem_intro=document.problem_intro,
        image_urls=document.image_urls,
        causes=document.causes,
        evaluation=document.evaluation,
        inspection=document.inspection,
        solutions=document.solutions,
        key_points=document.key_points,
        origin_file_name=document.origin_file_name,
        origin_file_dir=normalize_upload_path(document.origin_file_dir),
        tag=_normalize_tags(getattr(document, "tag", [])),
        image_urls_problem_intro=document.image_urls_problem_intro,
        image_urls_causes=document.image_urls_causes,
        image_urls_evaluation=document.image_urls_evaluation,
        image_urls_inspection=document.image_urls_inspection,
        image_urls_solutions=document.image_urls_solutions,
        image_urls_key_points=document.image_urls_key_points,
        action_type=1,
        review_comment=None,
    )


async def _get_source_document_by_path(db: AsyncSession, stored_file_path: str):
    result = await db.execute(
        select(SourceDocument).where(
            SourceDocument.stored_file_path == stored_file_path,
            SourceDocument.is_deleted == 0,
        )
    )
    return result.scalar_one_or_none()


async def _source_filename_exists(db: AsyncSession, origin_file_name: str) -> bool:
    result = await db.execute(
        select(SourceDocument.id)
        .where(
            SourceDocument.origin_file_name == origin_file_name,
            SourceDocument.is_deleted == 0,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _source_document_filter_for_document(document_id: int, library_type: str):
    return (
        SourceDocument.document_id == document_id,
        SourceDocument.document_library_type == _normalize_library_type(library_type),
        SourceDocument.is_deleted == 0,
    )


async def _delete_source_documents_for_document(
    db: AsyncSession, document_base_dir: str, document_id: int, library_type: str
):
    """删除源文档记录及其物理文件（兼容旧调用，内部拆分为 DB + 文件两步）。"""
    # 先删文件
    await _delete_source_document_files(
        db, document_base_dir, document_id, library_type
    )
    # 再标记 DB
    await _mark_source_documents_deleted(db, document_id, library_type)


async def _delete_source_document_files(
    db: AsyncSession, document_base_dir: str, document_id: int, library_type: str
):
    """仅删除 SourceDocument 关联的物理文件，不修改数据库。"""
    result = await db.execute(
        select(SourceDocument).where(
            *_source_document_filter_for_document(document_id, library_type),
        )
    )
    source_documents = result.scalars().all()
    for source_document in source_documents:
        if source_document.stored_file_path:
            absolute_path = os.path.join(
                document_base_dir,
                normalize_upload_path(source_document.stored_file_path)
                or source_document.stored_file_path,
            )
            await asyncio.to_thread(delete_file_if_exists, absolute_path)


async def _mark_source_documents_deleted(
    db: AsyncSession, document_id: int, library_type: str
):
    """仅标记 SourceDocument 为已删除，不删除物理文件。"""
    result = await db.execute(
        select(SourceDocument).where(
            *_source_document_filter_for_document(document_id, library_type),
        )
    )
    source_documents = result.scalars().all()
    for source_document in source_documents:
        source_document.is_deleted = 1
        source_document.status = "deleted"
        source_document.deleted_time = datetime.now()
        source_document.document_id = None
        source_document.review_id = None
        source_document.parse_error = None


async def _mark_source_parse_failed(
    db: AsyncSession,
    stored_file_path: str,
    error_message: str,
    library_type: str = "breakdown",
):
    source = await _get_source_document_by_path(db, stored_file_path)
    if source:
        source.status = "parse_failed"
        source.parse_error = error_message
        source.review_id = None
        source.document_id = None
        source.document_library_type = _normalize_library_type(library_type)
        await db.commit()


async def _copy_source_to_knowledge_storage(
    document_base_dir: str, source_relative_path: str, origin_file_name: str
) -> str:
    return source_relative_path


async def document_convert_documentResponse(
    db: AsyncSession, document: Document, contributor_name: str
) -> DocumentResponse:
    """
    document类型转为documentResponse类型
    （其实是因为document类型没有作者姓名，所以不能直接用from_orm）
    当然也可以用循环，但是最开始的document没有这么多字段，就直接手写了
    """
    return DocumentResponse(
        id=document.id,
        library_type=getattr(document, "library_type", "breakdown"),
        tag=await get_document_tag_names(db, document),
        title=document.title,
        section_ids=(
            getattr(document, "section_ids", None)
            if getattr(document, "library_type", "breakdown") == "knowledge"
            else None
        ),
        sections=(
            await _knowledge_sections_to_response(db, document.id)
            if getattr(document, "library_type", "breakdown") == "knowledge"
            else None
        ),
        contributor_id=document.contributor_id,
        contributor_name=contributor_name,
        first_edit_date=document.first_edit_date,
        problem_intro=getattr(document, "problem_intro", None),
        image_urls=getattr(document, "image_urls", None),
        causes=getattr(document, "causes", None),
        evaluation=getattr(document, "evaluation", None),
        inspection=getattr(document, "inspection", None),
        solutions=getattr(document, "solutions", None),
        key_points=getattr(document, "key_points", None),
        origin_file_name=getattr(document, "origin_file_name", None),
        origin_file_dir=normalize_upload_path(
            getattr(document, "origin_file_dir", None)
        ),
        image_urls_problem_intro=getattr(document, "image_urls_problem_intro", None),
        image_urls_causes=getattr(document, "image_urls_causes", None),
        image_urls_evaluation=getattr(document, "image_urls_evaluation", None),
        image_urls_inspection=getattr(document, "image_urls_inspection", None),
        image_urls_solutions=getattr(document, "image_urls_solutions", None),
        image_urls_key_points=getattr(document, "image_urls_key_points", None),
    )


async def documents_to_responses(
    db: AsyncSession, documents: List[Document]
) -> List[DocumentResponse]:
    """
    将文档列表转换为响应列表（批量查询用户信息）
    """
    if not documents:
        return []

    # 1. 收集所有用户ID
    user_ids = list(
        set(doc.contributor_id for doc in documents if doc.contributor_id is not None)
    )

    # 2. 批量查询用户信息
    user_map = {}
    if user_ids:
        # 只查询需要的字段
        # users = db.query(
        #     User.id,
        #     User.full_name,
        #     User.username
        # ).filter(
        #     User.id.in_(user_ids)
        # ).all()

        result = await db.execute(
            select(User.id, User.full_name, User.username).where(User.id.in_(user_ids))
        )
        users = result.all()

        user_map = {user.id: user for user in users}

    # 3. 转换文档
    responses = []
    for doc in documents:
        contributor_name = None

        # 获取用户名
        if doc.contributor_id:
            user = user_map.get(doc.contributor_id)
            if user:
                contributor_name = user.full_name or user.username
            else:
                contributor_name = f"用户{doc.contributor_id}"

        responses.append(
            await document_convert_documentResponse(
                db=db, document=doc, contributor_name=contributor_name
            )
        )

    return responses


async def check_image_url(image_urls: str):
    """
    如果有图片不存在，返回False，用来判断图片是不是都上传服务器了
    """
    if image_urls:
        config = get_image_config()
        base_url = os.path.join(
            config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\")
        )
        urls = [url.strip() for url in image_urls.split(", ") if url.strip()]
        for url in urls:
            url_check = os.path.basename(url)
            url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
            # print(url_check)
            if not await asyncio.to_thread(os.path.exists, url_check):
                return False
        return True
    return True


@router.post("/add", summary="添加文档")
async def create_document(
    document: DocumentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    print("添加文档")
    _require_admin_document_write(current_user)
    config = get_image_config()
    try:
        attrs = [
            "image_urls_problem_intro",
            "image_urls_causes",
            "image_urls_evaluation",
            "image_urls_inspection",
            "image_urls_solutions",
            "image_urls_key_points",
            "image_urls",
        ]
        for attr in attrs:
            value = getattr(document, attr)
            if not await check_image_url(value):
                raise AppException(
                    status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传"
                )
        print("图片校验完成")
        # if document.image_urls:
        #     urls = [url.strip() for url in document.image_urls.split(", ") if url.strip()]
        #     base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
        #
        #     for url in urls:
        #         url_check = os.path.basename(url)
        #         url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
        #         # print(url_check)
        #         if not os.path.exists(url_check):
        #             print(url_check)
        #             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
        #                                 detail="图片未上传")

        contributor_id = current_user.id

        # document.image_urls = (document.image_urls.replace("\\", "/")
        #                        .replace(", /", ", ")
        #                        .removeprefix("/")
        #                        .removesuffix(", "))

        """
        解释一下为什么这里我这么复杂地处理字符串
        我最后在数据库存的路径都是相对路径，方便前端预览图片或者文档
        如：upload/images/xxxxx.jpg
        但是在os.path.join，会使用反斜杠，反斜杠又可能出现转义，导致出错
        所以我在存储进数据库前同意处理成斜杠的格式，并且多个地址，中间以', '间隔
        """
        for attr in attrs:
            value = getattr(document, attr)
            if value is not None:
                value = (
                    value.replace("\\", "/")
                    .replace(", /", ", ")
                    .removeprefix("/")
                    .removesuffix(", ")
                )
            setattr(document, attr, value)

        document_model = _get_document_model(document.library_type)
        exclude_fields = {"library_type", "tag", "sections"}
        exclude_fields.update({"summary", "content"})
        document_payload = document.dict(exclude=exclude_fields)
        document_payload.update(
            tag=_normalize_tags(document.tag),
            contributor_id=contributor_id,
            is_vectorized=0,
            is_deleted=0,
            first_edit_date=datetime.now(),
        )
        document_data = document_model(
            **_filter_model_data(document_model, document_payload)
        )
        db.add(document_data)
        await db.flush()
        await db.refresh(document_data)
        if _is_knowledge_library(document.library_type):
            await replace_knowledge_document_sections(
                db,
                document_data,
                _knowledge_sections_from_request(document.sections),
            )
        await set_document_tag_names(
            db, document_data, document.tag, created_by=current_user.id
        )
        print("数据库插入成功")
        vector_service = VectorService(db)
        await vector_service.add_document_to_vector_store(document_data, commit=False)
        await db.commit()
        print("向量化完成")
        data = await document_convert_documentResponse(
            db, document_data, current_user.full_name
        )
        return Result.success_with_data(data)

    except AppException:
        # 重新抛出已知的HTTP异常
        raise

    except Exception as e:
        # 其他异常回滚
        print(e)
        await db.rollback()
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            "添加文档失败",
        )


def get_image_config():
    MAX_IMAGE_SIZE: int = int(os.getenv("MAX_IMAGE_SIZE", 20 * 1024 * 1024))
    IMAGE_DIR: str = os.getenv("IMAGE_DIR", "upload/images")
    BASE_DIR: str = os.getenv("BASE_DIR", "/")
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    return {
        "MAX_IMAGE_SIZE": MAX_IMAGE_SIZE,
        "IMAGE_DIR": IMAGE_DIR,
        "BASE_DIR": BASE_DIR,
        "ALLOWED_EXTENSIONS": ALLOWED_EXTENSIONS,
    }


@router.post("/upload_images", summary="上传文档图片")
async def upload_images(images: List[UploadFile]):
    """
    图片上传是单独的api，当前端用户一上传图片就会调用该api，避免全部堆到添加文档的时候
    """
    config = get_image_config()
    url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
    uploaded_images = []
    if not os.path.exists(url):
        os.makedirs(url)
        print(f"创建路径{url}")
    for image in images:
        try:
            if image.size > config["MAX_IMAGE_SIZE"]:
                continue
            file_ext = Path(image.filename).suffix.lower()
            if file_ext not in config["ALLOWED_EXTENSIONS"]:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"
            save_path = Path(url) / unique_filename

            # 保存文件
            contents = await image.read()
            # with open(save_path, "wb") as buffer:
            #     buffer.write(contents)

            async with aiofiles.open(save_path, "wb") as buffer:
                await buffer.write(contents)

            # 构建文件信息
            file_url = f"{url}/{unique_filename}"
            relative_url = Path(config["IMAGE_DIR"]) / unique_filename
            uploaded_images.append(
                {
                    "url": relative_url,
                    # "relative_url": relative_url,
                    "filename": unique_filename,
                    "original_name": image.filename,
                }
            )
            # print(uploaded_images)
        except Exception as e:
            # 记录错误但继续处理其他文件
            print(f"文件 {image.filename} 上传失败: {str(e)}")

    return Result.success_with_data(uploaded_images)


@router.delete("/delete_image", summary="删除图片")
async def delete_image(
    request: DeleteImageRequest = Body(...),
    current_user: User = Depends(get_current_active_user),
):
    image_url = request.image_url
    try:
        filename = os.path.basename(image_url)
        config = get_image_config()
        url = os.path.join(
            config["BASE_DIR"],
            config["IMAGE_DIR"].lstrip("/").lstrip("\\"),
            filename.lstrip("/").lstrip("\\"),
        )
        # if not os.path.exists(url):
        #     raise HTTPException(status_code=404, detail="未找到图片")

        if not await asyncio.to_thread(os.path.exists, url):
            raise AppException(
                status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "资源未找到"
            )

        # os.remove(url)
        await asyncio.to_thread(delete_file_if_exists, url)

        print(f"图片{url}删除成功")
        return Result.success()
    except AppException:
        raise
    except FileNotFoundError:
        raise AppException(
            status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, f"文件 {image_url} 不存在"
        )
    except Exception as e:
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"删除文件时出错: {str(e)}",
        )


@router.put("/update", summary="更新文档")
async def update_document(
    id: int,
    document: DocumentCreate,
    library_type: str = "breakdown",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        config = get_image_config()
        base_url = os.path.join(
            config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\")
        )
        # document_now = db.query(Document).filter(Document.id == id).first()

        document_model = _get_document_model(library_type or document.library_type)
        result = await db.execute(
            select(document_model)
            .where(
                document_model.id == id,
                document_model.is_deleted == 0,
            )
            .with_for_update()
        )
        document_now = result.scalar_one_or_none()

        if not document_now:
            raise AppException(
                status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "文档不存在"
            )
        _require_admin_document_write(
            current_user, "技术人员需提交修改审核，审核通过后才会更新文档"
        )

        image_urls_str = ""
        attrs = [
            attr
            for attr in [
                "image_urls_problem_intro",
                "image_urls_causes",
                "image_urls_evaluation",
                "image_urls_inspection",
                "image_urls_solutions",
                "image_urls_key_points",
                "image_urls",
            ]
            if hasattr(document_now, attr)
        ]

        # 更新前：记录旧图片 URL 集合，用于差异删除
        old_urls_by_attr = {}
        for attr in attrs:
            old_urls_by_attr[attr] = _parse_image_urls_to_set(
                getattr(document_now, attr, None)
            )

        for attr in attrs:
            urls_str = ""
            image_url = getattr(document, attr)
            if image_url:
                image_urls = [
                    url.strip() for url in image_url.split(", ") if url.strip()
                ]
                for image_url in image_urls:
                    image_name = os.path.basename(image_url)
                    url_check = os.path.join(
                        base_url, image_name.lstrip("/").lstrip("\\")
                    )
                    # if not os.path.exists(url_check):
                    #     return Result.error(f"图片未上传，更新失败，请重新上传图片")

                    if not await asyncio.to_thread(os.path.exists, url_check):
                        raise AppException(
                            status.HTTP_400_BAD_REQUEST,
                            BizCode.DOC_REQUEST_INVALID,
                            "图片未上传，更新失败，请重新上传图片",
                        )

                    url_check_str = os.path.join(
                        config["IMAGE_DIR"].lstrip("/").lstrip("\\"),
                        image_name.lstrip("/").lstrip("\\"),
                    )
                    url_check_str = url_check_str.lstrip("/").lstrip("\\")
                    url_check_str = url_check_str.replace("\\", "/")

                    urls_str += url_check_str + ", "
                if len(urls_str) > 0:
                    urls_str = urls_str.removesuffix(", ")
            setattr(document_now, attr, urls_str)

        # 更新后：删除旧版本中有、新版本中没有的图片
        for attr in attrs:
            new_urls = _parse_image_urls_to_set(
                getattr(document_now, attr, None)
            )
            old_urls = old_urls_by_attr.get(attr, set())
            removed = old_urls - new_urls
            for url in removed:
                filename = os.path.basename(url)
                if filename.strip():
                    file_path = os.path.join(
                        base_url, filename.lstrip("/").lstrip("\\")
                    )
                    await asyncio.to_thread(delete_image_with_variants, file_path)

        # if document.image_urls:
        #     image_urls = [url.strip() for url in document.image_urls.split(", ") if url.strip()]
        #     for image_url in image_urls:
        #         image_name = os.path.basename(image_url)
        #         url_check = os.path.join(base_url, image_name.lstrip("/").lstrip("\\"))
        #         if not os.path.exists(url_check):
        #             return Result.error(f"图片未上传，更新失败，请重新上传图片")
        #
        #         # url_check_str = url_check.lstrip("/").lstrip("\\")
        #         url_check_str = os.path.join(config["IMAGE_DIR"].lstrip("/").lstrip("\\"), image_name.lstrip("/").lstrip("\\"))
        #         url_check_str = url_check_str.lstrip("/").lstrip("\\")
        #         url_check_str = url_check_str.replace("\\", "/")
        #
        #         image_urls_str += url_check_str + ", "

        document_data = _filter_model_data(
            document_model,
            document.dict(
                exclude_unset=True,
                exclude={"library_type", "sections", "summary", "content"},
            ),
        )
        for key, value in document_data.items():
            # print(key, value)
            if key == "id" or key in attrs:
                continue
            if key == "tag":
                await set_document_tag_names(
                    db, document_now, value, created_by=current_user.id
                )
                continue
            setattr(document_now, key, value)

        if (
            _is_knowledge_library(getattr(document_now, "library_type", library_type))
            and document.sections is not None
        ):
            await replace_knowledge_document_sections(
                db,
                document_now,
                _knowledge_sections_from_request(document.sections),
            )

        if len(image_urls_str) > 0:
            image_urls_str = image_urls_str.removesuffix(", ")

        # setattr(document_now, "image_urls", image_urls_str)

        document_now.is_vectorized = 0

        # 更新文档内容的时候，向量需要重新生成
        vector_service = VectorService(db)
        await vector_service.delete_document_from_vector_store(
            id, getattr(document_now, "library_type", "breakdown")
        )

        await db.flush()

        # vector_service.add_document_to_vector_store(document_now)
        await vector_service.add_document_to_vector_store(document_now, commit=False)
        await db.commit()
        await db.refresh(document_now)

        # full_name = db.query(User.full_name).filter(User.id == document_now.contributor_id).scalar()

        full_name_result = await db.execute(
            select(User.full_name).where(User.id == document_now.contributor_id)
        )
        full_name = full_name_result.scalar_one_or_none()

        document_response = await document_convert_documentResponse(
            db, document_now, full_name
        )
        return Result.success_with_data(document_response)
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"更新文档错误：{str(e)}",
        )


@router.delete("/dele/{id}", summary="删除文档")
async def delete(
    id: int,
    library_type: str = "breakdown",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        document_model = _get_document_model(library_type)
        result = await db.execute(
            select(document_model)
            .where(
                document_model.id == id,
                document_model.is_deleted == 0,
            )
            .with_for_update()
        )
        document = result.scalar_one_or_none()

        if not document:
            raise AppException(
                status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "文档不存在"
            )
        if not has_role(current_user, UserRole.ADMIN):
            raise AppException(
                status.HTTP_403_FORBIDDEN,
                BizCode.FORBIDDEN,
                "技术人员需提交删除审核，审核通过后才会删除文档",
            )

        doc_library_type = getattr(document, "library_type", library_type)
        config = get_image_config()
        base_url = os.path.join(
            config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\")
        )
        document_base_dir = os.getenv(
            "DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
        )

        # === 阶段一：收集所有待删除的文件路径 ===
        image_paths = []
        attrs = [
            attr
            for attr in [
                "image_urls_problem_intro",
                "image_urls_causes",
                "image_urls_evaluation",
                "image_urls_inspection",
                "image_urls_solutions",
                "image_urls_key_points",
                "image_urls",
            ]
            if hasattr(document, attr)
        ]
        for attr in attrs:
            value = getattr(document, attr, None)
            if value:
                for image_url in value.split(", "):
                    filename = os.path.basename(image_url)
                    if filename.strip():
                        image_paths.append(
                            os.path.join(base_url, filename.lstrip("/").lstrip("\\"))
                        )

        origin_file_path = None
        if document.origin_file_dir:
            origin_file_path = os.path.join(
                config["BASE_DIR"],
                normalize_upload_path(document.origin_file_dir)
                or document.origin_file_dir,
            )

        # 收集章节图片路径（知识库）
        section_image_paths = []
        if _is_knowledge_library(doc_library_type):
            old_sections = await get_knowledge_document_sections(db, id)
            for section in old_sections:
                for url in (section.image_urls or []):
                    url_str = str(url).strip()
                    if url_str:
                        filename = os.path.basename(url_str)
                        if filename.strip():
                            section_image_paths.append(
                                os.path.join(
                                    base_url, filename.lstrip("/").lstrip("\\")
                                )
                            )

        # === 阶段二：数据库 + 向量库操作（事务内） ===
        # 处理审核记录
        review_refs_result = await db.execute(
            select(Document_review).where(
                Document_review.document_id == id,
                Document_review.document_library_type == doc_library_type,
            )
        )
        review_refs = review_refs_result.scalars().all()
        for review_ref in review_refs:
            if review_ref.status == 0:
                review_ref.status = 3
                auto_msg = "源文档已被管理员删除，系统自动撤回"
                if review_ref.review_comment and review_ref.review_comment.strip():
                    review_ref.review_comment = (
                        f"{review_ref.review_comment}\n{auto_msg}"
                    )
                else:
                    review_ref.review_comment = auto_msg
                review_ref.reviewed_time = datetime.now()

        await db.flush()

        # 标记源文档为已删除（仅 DB）
        await _mark_source_documents_deleted(db, id, doc_library_type)

        # 删除向量
        vector_service = VectorService(db)
        await vector_service.delete_document_from_vector_store(id, doc_library_type)

        # 标记文档为已删除
        document.is_deleted = 1
        await db.commit()
        print("成功删除文档（DB + Milvus）")

        # === 阶段三：删除磁盘文件（commit 之后） ===
        for path in image_paths:
            await asyncio.to_thread(delete_image_with_variants, path)
        for path in section_image_paths:
            await asyncio.to_thread(delete_image_with_variants, path)
        if origin_file_path:
            await asyncio.to_thread(delete_file_if_exists, origin_file_path)
            print(f"已删除源文件{document.origin_file_dir}")
        await _delete_source_document_files(
            db, document_base_dir, id, doc_library_type
        )

        return Result.success()
    except AppException:
        raise
    except Exception as e:
        print(e)
        await db.rollback()
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"删除文档失败：{str(e)}",
        )


async def _delete_single_document(db: AsyncSession, document, library_type: str):
    """
    内部辅助函数：执行单个文档的数据库 + 向量库清理。
    磁盘文件清理应在 commit 之后由调用方执行。
    """
    # 1. 处理关联的审核记录（自动撤回待审核项）
    review_refs_result = await db.execute(
        select(Document_review).where(
            Document_review.document_id == document.id,
            Document_review.document_library_type == library_type,
        )
    )
    review_refs = review_refs_result.scalars().all()
    for review_ref in review_refs:
        if review_ref.status == 0:
            review_ref.status = 3
            auto_msg = "源文档已被管理员批量删除，系统自动撤回"
            review_ref.review_comment = (
                f"{review_ref.review_comment}\n{auto_msg}"
                if review_ref.review_comment
                else auto_msg
            )
            review_ref.reviewed_time = datetime.now()

    # 2. 标记源文档为已删除（仅 DB）
    await _mark_source_documents_deleted(db, document.id, library_type)

    # 3. 删除向量库索引
    vector_service = VectorService(db)
    await vector_service.delete_document_from_vector_store(document.id, library_type)

    # 4. 标记数据库记录为已删除
    document.is_deleted = 1


async def _cleanup_document_disk_files(
    db: AsyncSession, document, library_type: str
):
    """删除文档关联的磁盘文件（commit 之后调用）。"""
    config = get_image_config()
    base_url = os.path.join(
        config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\")
    )

    # 删除文档级图片
    attrs = [
        attr
        for attr in [
            "image_urls_problem_intro",
            "image_urls_causes",
            "image_urls_evaluation",
            "image_urls_inspection",
            "image_urls_solutions",
            "image_urls_key_points",
            "image_urls",
        ]
        if hasattr(document, attr)
    ]
    for attr in attrs:
        value = getattr(document, attr, None)
        if value:
            for image_url in value.split(", "):
                filename = os.path.basename(image_url)
                if filename.strip():
                    url = os.path.join(base_url, filename.lstrip("/").lstrip("\\"))
                    await asyncio.to_thread(delete_image_with_variants, url)

    # 删除章节图片（知识库）
    if library_type == "knowledge":
        await delete_section_images_for_document(db, document.id, base_url)

    # 删除原始上传文件
    if document.origin_file_dir:
        url = os.path.join(
            config["BASE_DIR"],
            normalize_upload_path(document.origin_file_dir)
            or document.origin_file_dir,
        )
        await asyncio.to_thread(delete_file_if_exists, url)

    # 删除源文档物理文件
    document_base_dir = os.getenv(
        "DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
    )
    await _delete_source_document_files(
        db, document_base_dir, document.id, library_type
    )


@router.post("/deletes", summary="批量删除文档")
async def delete_documents(
    request: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    批量删除文档接口
    1. 权限校验：仅管理员可用
    2. 数据分组：根据 library_type 将请求拆分为故障库和知识库两组
    3. 并行处理：分别查询并执行删除逻辑
    """
    try:
        # 1. 权限校验
        if not has_role(current_user, UserRole.ADMIN):
            raise AppException(
                status.HTTP_403_FORBIDDEN,
                BizCode.FORBIDDEN,
                "只有系统管理员可以批量删除文档",
            )

        # 2. 验证请求数据
        if not request.documents or len(request.documents) == 0:
            raise AppException(
                status.HTTP_400_BAD_REQUEST,
                BizCode.DOC_REQUEST_INVALID,
                "请选择要删除的文档",
            )

        # 3. 按库类型分组 ID
        breakdown_ids = []
        knowledge_ids = []

        for doc_item in request.documents:
            normalized_type = _normalize_library_type(doc_item.library_type)
            if normalized_type == "knowledge":
                knowledge_ids.append(doc_item.id)
            else:
                breakdown_ids.append(doc_item.id)

        deleted_count = 0
        failed_items = []
        deleted_documents = []  # 记录成功删除的文档，用于 commit 后清理文件

        # 4. 处理故障库文档
        if breakdown_ids:
            result = await db.execute(
                select(DocumentBreakdown).where(
                    DocumentBreakdown.id.in_(breakdown_ids),
                    DocumentBreakdown.is_deleted == 0,
                )
            )
            documents = result.scalars().all()

            for document in documents:
                try:
                    await _delete_single_document(db, document, "breakdown")
                    deleted_count += 1
                    deleted_documents.append((document, "breakdown"))
                except Exception as e:
                    logger.exception(f"批量删除故障库文档失败, id={document.id}")
                    failed_items.append(
                        {"id": document.id, "library_type": "breakdown"}
                    )

        # 5. 处理知识库文档
        if knowledge_ids:
            result = await db.execute(
                select(DocumentKnowledge).where(
                    DocumentKnowledge.id.in_(knowledge_ids),
                    DocumentKnowledge.is_deleted == 0,
                )
            )
            documents = result.scalars().all()

            for document in documents:
                try:
                    await _delete_single_document(db, document, "knowledge")
                    deleted_count += 1
                    deleted_documents.append((document, "knowledge"))
                except Exception as e:
                    logger.exception(f"批量删除知识库文档失败, id={document.id}")
                    failed_items.append(
                        {"id": document.id, "library_type": "knowledge"}
                    )

        # 6. 提交事务
        await db.commit()

        # 6.1 事务提交成功后，清理磁盘文件（单个文件删除失败不影响整体结果）
        for document, lib_type in deleted_documents:
            try:
                await _cleanup_document_disk_files(db, document, lib_type)
            except Exception as e:
                logger.warning(
                    f"文档 {document.id} 磁盘文件清理失败（DB 已标记删除）: {e}"
                )

        # 7. 返回结果
        if failed_items:
            return Result.success_with_data(
                {
                    "deleted_count": deleted_count,
                    "failed_items": failed_items,
                    "message": f"成功删除 {deleted_count} 篇文档，{len(failed_items)} 篇删除失败",
                }
            )
        else:
            return Result.success_with_data(
                {
                    "deleted_count": deleted_count,
                    "message": f"成功删除 {deleted_count} 篇文档",
                }
            )

    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("批量删除文档异常")
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"批量删除文档失败：{str(e)}",
        )


@router.get("/", summary="获取所有文档")
async def get_documents(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    library_type: str = "breakdown",
):
    document_model = _get_document_model(library_type)
    result = await db.execute(
        select(document_model).where(document_model.is_deleted == 0)
    )
    documents = result.scalars().all()

    # documents = db.query(Document)
    responses = await documents_to_responses(db, documents)
    # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
    return Result.success_with_data(responses)


@router.get("/get_by_id/{id}", summary="根据id获得文档内容")
async def get_document(
    id: int,
    library_type: str = "breakdown",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        document_model = _get_document_model(library_type)
        result = await db.execute(
            select(document_model).where(
                document_model.id == id,
                document_model.is_deleted == 0,
            )
        )
        document = result.scalar_one_or_none()

        # document = db.query(Document).filter(Document.id == id).first()
        if document:
            # full_name = db.query(User.full_name).filter(User.id == document.contributor_id).scalar()

            full_name_result = await db.execute(
                select(User.full_name).where(User.id == document.contributor_id)
            )
            full_name = full_name_result.scalar_one_or_none()

            document_data = await document_convert_documentResponse(
                db, document, full_name
            )
            return Result.success_with_data(document_data)
        return Result.success()
    except Exception as e:
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"根据id获取文档内容失败：{str(e)}",
        )


@router.post("/page", summary="分页查询文档内容")
async def get_page(
    page: Page,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    logger.info("分页查询文档内容")
    try:
        offset = (page.page - 1) * page.size
        if _is_all_library_type(page.library_type):
            # 同时查询两个库时，每张表先取到当前页末尾需要的数据量，再统一排序分页，避免知识库数据被默认故障库过滤掉。
            max_needed = offset + page.size
            # total_count 需要累加两张表，前端分页才能显示完整记录数。
            total_count = 0
            # documents 保存两张表候选数据，后面统一按编辑时间倒序排列。
            documents = []
            for document_model in DOCUMENT_LIBRARY_MODELS.values():
                # 每张表都要使用自己的 JSON tag 字段构造过滤条件。
                tag_condition = _tag_filter(document_model, page.tag)
                # 默认只展示未删除文档，和原来单库查询行为保持一致。
                where_conditions = [document_model.is_deleted == 0]
                if tag_condition is not None:
                    # 如果前端选择了标签，就在两个库里都按标签过滤。
                    where_conditions.append(tag_condition)

                count_result = await db.execute(
                    select(func.count())
                    .select_from(document_model)
                    .where(*where_conditions)
                )
                # 累加每个库的数量，得到列表总数量。
                total_count += count_result.scalar_one()

                result = await db.execute(
                    select(document_model)
                    .where(*where_conditions)
                    .order_by(document_model.first_edit_date.desc())
                    .limit(max_needed)
                )
                # 合并两个库的候选文档，稍后再做跨库排序和分页。
                documents.extend(result.scalars().all())

            # 跨库排序后再切片，保证第一页能同时出现最新的故障库和知识库文档。
            documents = _sort_documents_by_edit_time(documents)[
                offset : offset + page.size
            ]
            # 复用原来的响应转换逻辑，保留每条文档自身的 library_type。
            responses = await documents_to_responses(db, documents)
            data = build_pagination_payload(
                total_count, page.page, page.size, responses, "documents"
            )
            return Result.success_with_data(data)

        document_model = _get_document_model(page.library_type)
        tag_condition = _tag_filter(document_model, page.tag)
        # total_count = db.query(Document).count()

        where_conditions = [document_model.is_deleted == 0]
        if tag_condition is not None:
            where_conditions.append(tag_condition)

        total_count_result = await db.execute(
            select(func.count()).select_from(document_model).where(*where_conditions)
        )
        total_count = total_count_result.scalar_one()

        result = await db.execute(
            select(document_model)
            .where(*where_conditions)
            .offset(offset)
            .limit(page.size)
        )
        documents = result.scalars().all()
        # documents = db.query(Document).offset(offset).limit(page.size).all()

        responses = await documents_to_responses(db, documents)
        # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
        data = build_pagination_payload(
            total_count, page.page, page.size, responses, "documents"
        )
        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"分页查询文档内容失败：{str(e)}",
        )


@router.post("/query", summary="查询文档信息")
async def query(
    query: DocumentQuery,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        # 根据文档的标题或问题简介，作者姓名或用户名查询
        offset = (query.page - 1) * query.size
        if _is_all_library_type(query.library_type):
            # 搜索页和普通列表一样需要跨库查询，否则知识库文档新增后搜索也找不到。
            max_needed = offset + query.size
            # total_count 记录两个库里符合关键词的总数量。
            total_count = 0
            # documents 保存两个库的搜索候选结果，后面统一排序分页。
            documents = []
            for document_model in DOCUMENT_LIBRARY_MODELS.values():
                # 每张表都要用对应 ORM 模型生成标签过滤条件。
                tag_condition = _tag_filter(document_model, query.tag)
                # 关键词搜索条件：标题、作者姓名、用户名、问题简介、标签都可以匹配。
                tag_keyword_condition = _tag_keyword_filter(document_model, query.data)
                filter_condition = or_(
                    *_document_text_keyword_conditions(document_model, query.data),
                    User.full_name.like(f"%{query.data}%"),
                    User.username.like(f"%{query.data}%"),
                    tag_keyword_condition,
                )
                # 默认排除已删除文档，并叠加关键词过滤。
                where_conditions = [document_model.is_deleted == 0, filter_condition]
                if tag_condition is not None:
                    # 如果前端传入标签，搜索时也要限制对应标签。
                    where_conditions.append(tag_condition)

                count_result = await db.execute(
                    select(func.count())
                    .select_from(document_model)
                    .join(User, document_model.contributor_id == User.id)
                    .where(*where_conditions)
                )
                # 累加两个库的搜索命中数量，保证分页总数准确。
                total_count += count_result.scalar_one()

                result = await db.execute(
                    select(document_model)
                    .join(User, document_model.contributor_id == User.id)
                    .where(*where_conditions)
                    .order_by(document_model.first_edit_date.desc())
                    .limit(max_needed)
                )
                # 合并两个库的搜索候选结果，统一排序后再分页。
                documents.extend(result.scalars().all())

            # 按编辑时间跨库排序，并只返回当前页需要的数据。
            documents = _sort_documents_by_edit_time(documents)[
                offset : offset + query.size
            ]
            # 转成统一响应结构，同时保留 library_type 用于前端徽标和详情跳转。
            documents_response = await documents_to_responses(db, documents)
            data = build_pagination_payload(
                total_count, query.page, query.size, documents_response, "documents"
            )
            return Result.success_with_data(data)

        document_model = _get_document_model(query.library_type)
        tag_condition = _tag_filter(document_model, query.tag)
        tag_keyword_condition = _tag_keyword_filter(document_model, query.data)

        filter_condition = or_(
            *_document_text_keyword_conditions(document_model, query.data),
            User.full_name.like(f"%{query.data}%"),
            User.username.like(f"%{query.data}%"),
            tag_keyword_condition,
        )
        where_conditions = [document_model.is_deleted == 0, filter_condition]
        if tag_condition is not None:
            where_conditions.append(tag_condition)

        total_count_result = await db.execute(
            select(func.count())
            .select_from(document_model)
            .join(User, document_model.contributor_id == User.id)
            .where(*where_conditions)
        )
        total_count = total_count_result.scalar_one()

        # total_count = db.query(Document).join(
        #     User, Document.contributor_id == User.id
        # ).filter(
        #     or_(
        #         Document.title.like(f"%{query.data}%"),
        #         User.full_name.like(f"%{query.data}%"),  # 从 User 表查询姓名
        #         User.username.like(f"%{query.data}%"),  # 也可以查询用户名
        #         Document.problem_intro.like(f"%{query.data}%")
        #     )
        # ).count()

        # documents = db.query(Document).join(
        #     User, Document.contributor_id == User.id
        # ).filter(
        #     or_(
        #         Document.title.like(f"%{query.data}%"),
        #         User.full_name.like(f"%{query.data}%"),  # 从 User 表查询姓名
        #         User.username.like(f"%{query.data}%"),  # 也可以查询用户名
        #         Document.problem_intro.like(f"%{query.data}%")
        #     )
        # ).order_by(
        #     Document.first_edit_date.desc()
        # ).offset(offset).limit(query.size).all()

        result = await db.execute(
            select(document_model)
            .join(User, document_model.contributor_id == User.id)
            .where(*where_conditions)
            .order_by(document_model.first_edit_date.desc())
            .offset(offset)
            .limit(query.size)
        )
        documents = result.scalars().all()

        documents_response = await documents_to_responses(db, documents)

        # documents_response = [document_convert_documentResponse(document, current_user.full_name) for document in documents]

        data = build_pagination_payload(
            total_count, query.page, query.size, documents_response, "documents"
        )

        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            BizCode.INTERNAL_ERROR,
            f"查询文档信息失败：{str(e)}",
        )


@router.post("/upload_files", summary="上传文件")
async def upload_files(
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    该aoi仅将文档保存至后端服务器，不做任何其他处理
    其实是为了把文档上传和分析分开，让分析过程看起来短一点
    """
    success_origin_filename = []
    success_file_url = []
    error_origin_filename = []
    has_server_error = False
    has_duplicate_file_error = False
    duplicate_origin_filename = []
    document_base_dir = os.getenv(
        "DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
    )
    source_relative_dir = os.getenv("SOURCE_DOCUMENT_DIR", "upload/source_documents")
    upload_file_names = [file.filename for file in files]
    duplicate_file_names = sorted(
        {name for name in upload_file_names if upload_file_names.count(name) > 1}
    )
    if duplicate_file_names:
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message=f"源文件名称重复：{', '.join(duplicate_file_names)}",
        )

    for file in files:
        normalized_filename = normalize_uploaded_relative_filename(file.filename)
        file_ext = get_file_extension(normalized_filename)
        if file_ext not in ALLOWED_EXTENSIONS:
            error_origin_filename.append(normalized_filename)
            continue

        try:
            if await _source_filename_exists(db, normalized_filename):
                error_origin_filename.append(normalized_filename)
                duplicate_origin_filename.append(normalized_filename)
                has_duplicate_file_error = True
                continue

            contents = await file.read()
            if not is_upload_content_valid(file_ext, contents):
                error_origin_filename.append(normalized_filename)
                has_server_error = False
                continue

            url, relative_path, category = build_document_storage_path(
                document_base_dir,
                source_relative_dir,
                normalized_filename,
            )
            async with aiofiles.open(url, "wb") as f:
                await f.write(contents)

            success_origin_filename.append(normalized_filename)
            success_file_url.append(relative_path)
            source_document = SourceDocument(
                origin_file_name=normalized_filename,
                stored_file_path=relative_path,
                file_ext=file_ext,
                file_category=get_document_category(file_ext),
                file_size=len(contents),
                uploader_id=current_user.id,
                upload_time=datetime.now(),
                status="uploaded",
                is_deleted=0,
            )
            db.add(source_document)
            await db.commit()
            logger.info(
                "uploaded document file classified, filename=%s, category=%s",
                file.filename,
                category,
            )
        except Exception as e:
            print(e)
            await db.rollback()
            error_origin_filename.append(normalized_filename)
            has_server_error = True
            # return Result.error(f"文件{file.filename}上传失败，请稍后重试")

    if len(success_file_url) == 0:
        if has_duplicate_file_error:
            raise AppException(
                http_status=status.HTTP_400_BAD_REQUEST,
                biz_code=BizCode.DOC_REQUEST_INVALID,
                message=f"源文件已存在，请勿重复上传：{', '.join(duplicate_origin_filename)}",
            )
        if has_server_error:
            raise AppException(
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                biz_code=BizCode.INTERNAL_ERROR,
                message="服务器内部错误",
            )
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="请求核心参数无效",
        )

    upload_document_request = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename,
    )
    return Result.success_with_data(upload_document_request)


@router.post("/analyze_files", summary="解析文件")
async def analyze_files(file_list: AnalyzeRequest,
                        db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_active_user)):
    # file_list是后端相对路径
    if not file_list.file_list or not file_list.file_name:
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="请求核心参数无效",
        )
    if len(file_list.file_list) != len(file_list.file_name):
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="请求核心参数无效",
        )
    submit_for_review = bool(file_list.submit_for_review)
    if _is_knowledge_library(file_list.library_type) and submit_for_review:
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="知识库导入暂不支持审核流程，请由管理员直接导入",
        )
    if submit_for_review and not has_role(current_user, UserRole.TECHNICIAN):
        raise AppException(
            http_status=status.HTTP_403_FORBIDDEN,
            biz_code=BizCode.FORBIDDEN,
            message="仅技术人员可提交审核",
        )
    if not submit_for_review:
        _require_admin_document_write(
            current_user, "技术人员需提交解析审核，审核通过后才会写入文档库"
        )

    success_file_url = []
    success_origin_filename = []
    error_origin_filename = []
    has_invalid_request_error = False
    has_not_found_error = False
    has_server_error = False
    parse_error_details = []
    has_token_limit_error = False
    has_ai_service_unavailable_error = False
    document_base_dir = os.getenv(
        "DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
    )
    current_user_id = current_user.id
    request_started = time.perf_counter()
    # AsyncSession.rollback() 会使当前 Session 中已加载的 ORM 对象属性过期。
    # 后面解析流程为了释放连接会先 rollback，如果继续访问 current_user.id，
    # SQLAlchemy 可能尝试在同步属性访问中重新 SELECT users，从而触发 MissingGreenlet。
    # 因此必须在 rollback 前把后续只读的标量值缓存下来。
    # 当前请求在鉴权时已经执行过 SELECT，会开启一个隐式事务。
    # PDF 解析/向量化可能耗时较长，不能让这个事务一直空闲占着连接和锁。
    await db.rollback()
    for file, file_name in zip(file_list.file_list, file_list.file_name):
        file_started = time.perf_counter()
        created_document_id = None
        created_library_type = _normalize_library_type(file_list.library_type)
        try:
            file_ext = os.path.splitext(file)[1].lower()
            # print(file_ext)
            if file_ext not in ALLOWED_EXTENSIONS:
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                await _mark_source_parse_failed(
                    db, file, "文件格式不支持", file_list.library_type
                )
                continue
            url = os.path.join(document_base_dir, file)
            if not os.path.exists(url):
                error_origin_filename.append(file_name)
                has_not_found_error = True
                await _mark_source_parse_failed(
                    db, file, "源文件不存在", file_list.library_type
                )
                continue

            if _is_knowledge_library(file_list.library_type):
                parsed = await asyncio.to_thread(knowledge_parser.parse, url)
                if parsed is None:
                    error_origin_filename.append(file_name)
                    has_invalid_request_error = True
                    parser_code = getattr(knowledge_parser, "last_error_code", None)
                    parser_detail = getattr(knowledge_parser, "last_error_detail", None)
                    parse_error_details.append(
                        {
                            "file_name": file_name,
                            "file_path": file,
                            "code": (
                                int(parser_code)
                                if parser_code is not None
                                else int(BizCode.DOC_PARSE_FAILED)
                            ),
                            "detail": parser_detail or "知识库解析器未返回文档对象",
                        }
                    )
                    await _mark_source_parse_failed(
                        db,
                        file,
                        parser_detail or "知识库解析器未返回文档对象",
                        file_list.library_type,
                    )
                    continue

                if _is_empty_text(parsed.title) and _is_empty_text(parsed.content):
                    error_origin_filename.append(file_name)
                    has_invalid_request_error = True
                    parse_error_details.append(
                        {
                            "file_name": file_name,
                            "file_path": file,
                            "code": int(BizCode.DOC_PARSE_FAILED),
                            "detail": "知识库解析结果为空，未能提取有效标题或正文。",
                        }
                    )
                    await _mark_source_parse_failed(
                        db, file, "知识库解析结果为空。", file_list.library_type
                    )
                    continue

                knowledge_file_path = await _copy_source_to_knowledge_storage(
                    document_base_dir, file, file_name
                )
                document = _knowledge_document_from_parsed(
                    parsed=parsed,
                    contributor_id=current_user_id,
                    file_name=file_name,
                    origin_file_dir=knowledge_file_path,
                    tags=file_list.tag,
                )
                db.add(document)
                await db.flush()
                await db.refresh(document)
                created_document_id = document.id
                created_library_type = "knowledge"
                await replace_knowledge_document_sections(db, document, parsed.sections)
                await set_document_tag_names(db, document, file_list.tag, created_by=current_user_id)

                # 先提交文档和章节，释放 MySQL 锁。
                # 后续向量化/AI 摘要/Milvus 写入较慢，不能放在同一个数据库事务中。
                await db.commit()

                vector_service = VectorService(db)
                vector_started = time.perf_counter()
                await vector_service.add_document_to_vector_store(document, commit=False)
                logger.info(
                    "knowledge document analyze vectorize done, file=%s, document_id=%s, elapsed=%.2fs",
                    file_name,
                    document.id,
                    time.perf_counter() - vector_started,
                )
                source = await _get_source_document_by_path(db, file)
                if source:
                    source.status = "vectorized"
                    source.document_id = document.id
                    source.document_library_type = "knowledge"
                    source.parse_error = None
                await db.commit()
                success_file_url.append(file)
                success_origin_filename.append(file_name)
                continue

            # print(url)
            document = None
            parser_for_error = None
            parse_started = time.perf_counter()

            # 根据不同的文件类型调用不同的解析器
            if file_ext == ".pdf":
                parser_for_error = pdf_parser
                # document = pdf_parser.parse(url)
                document = await asyncio.to_thread(pdf_parser.parse, url)

            elif file_ext == ".pptx" or file_ext == ".ppt":
                parser_for_error = ppt_parser
                # document = ppt_parser.parse(url)
                document = await asyncio.to_thread(ppt_parser.parse, url)

            elif file_ext == ".html" or file_ext == ".mhtml":
                parser_for_error = html_parser
                # document = await asyncio.to_thread(html_parser.parse(url))
                # document = html_parser.parse(url)

                document = await asyncio.to_thread(html_parser.parse, url)

            elif file_ext == ".docx":
                parser_for_error = word_parser
                # document = word_parser.parse(url)
                document = await asyncio.to_thread(word_parser.parse, url)
            elif file_ext == ".txt":
                parser_for_error = txt_parser
                document = await asyncio.to_thread(txt_parser.parse, url)
            elif file_ext == ".md" or file_ext == ".markdown":
                parser_for_error = markdown_parser
                document = await asyncio.to_thread(markdown_parser.parse, url)
            elif file_ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                parser_for_error = image_parser
                document = await asyncio.to_thread(image_parser.parse, url)
            elif file_ext in {".csv", ".xlsx", ".xls", ".xlsm"}:
                parser_for_error = csv_excel_parser
                document = await asyncio.to_thread(csv_excel_parser.parse, url)

            logger.info(
                "document analyze parser done, file=%s, ext=%s, elapsed=%.2fs",
                file_name,
                file_ext,
                time.perf_counter() - parse_started,
            )

            if document is None:
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                parser_code = getattr(parser_for_error, "last_error_code", None)
                parser_detail = getattr(parser_for_error, "last_error_detail", None)
                parse_error_details.append(
                    {
                        "file_name": file_name,
                        "file_path": file,
                        "code": (
                            int(parser_code)
                            if parser_code is not None
                            else int(BizCode.DOC_PARSE_FAILED)
                        ),
                        "detail": parser_detail or "解析器未返回文档对象",
                    }
                )
                logger.warning(
                    "document parse returned None, file=%s, code=%s, detail=%s",
                    file_name,
                    parser_code,
                    parser_detail,
                )
                await _mark_source_parse_failed(
                    db,
                    file,
                    parser_detail or "解析器未返回文档对象",
                    file_list.library_type,
                )
                if parser_code == int(BizCode.DOC_TOKEN_LIMIT_EXCEEDED):
                    has_token_limit_error = True
                if parser_code == int(BizCode.AI_SERVICE_UNAVAILABLE):
                    has_ai_service_unavailable_error = True
                continue

            if _is_ai_result_effectively_empty(document):
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                parse_error_details.append(
                    {
                        "file_name": file_name,
                        "file_path": file,
                        "code": int(BizCode.DOC_PARSE_FAILED),
                        "detail": "AI解析结果为空，可能该文档不是故障知识，或信息不足以形成故障分析结论。请补充故障现象、原因、排查和处理信息后重试。",
                    }
                )
                await _mark_source_parse_failed(
                    db,
                    file,
                    "AI解析结果为空，可能该文档不是故障知识，或信息不足以形成故障分析结论。",
                    file_list.library_type,
                )
                continue

            document.contributor_id = current_user_id
            document.origin_file_name = file_name
            knowledge_file_path = await _copy_source_to_knowledge_storage(
                document_base_dir, file, file_name
            )
            document.origin_file_dir = knowledge_file_path
            document.first_edit_date = datetime.now()
            document.tag = _normalize_tags(file_list.tag)
            document.library_type = _normalize_library_type(file_list.library_type)
            _normalize_document_for_db(document)
            print(document.title)
            if submit_for_review:
                review = _build_create_review_from_document(document, current_user_id)
                db.add(review)
                await db.commit()
                await db.refresh(review)
                source = await _get_source_document_by_path(db, file)
                if source:
                    source.status = "review_pending"
                    source.review_id = review.id
                    source.document_library_type = document.library_type
                    source.parse_error = None
                    await db.commit()
            else:
                document = _copy_document_to_library(
                    document, file_list.library_type, file_list.tag
                )
                db.add(document)
                await db.flush()
                await db.refresh(document)
                created_document_id = document.id
                created_library_type = getattr(document, "library_type", "breakdown")
                await set_document_tag_names(db, document, file_list.tag, created_by=current_user_id)
                vector_service = VectorService(db)
                vector_started = time.perf_counter()
                await vector_service.add_document_to_vector_store(
                    document, commit=False
                )
                logger.info(
                    "document analyze vectorize done, file=%s, document_id=%s, elapsed=%.2fs",
                    file_name,
                    document.id,
                    time.perf_counter() - vector_started,
                )
                source = await _get_source_document_by_path(db, file)
                if source:
                    source.status = "vectorized"
                    source.document_id = document.id
                    source.document_library_type = getattr(
                        document, "library_type", "breakdown"
                    )
                    source.parse_error = None
                await db.commit()

            success_file_url.append(file)
            success_origin_filename.append(file_name)
            logger.info(
                "document analyze file done, file=%s, status=success, elapsed=%.2fs",
                file_name,
                time.perf_counter() - file_started,
            )

        except Exception as e:
            print(e)
            await db.rollback()
            if created_document_id is not None:
                try:
                    vector_service = VectorService(db)
                    await vector_service.delete_document_from_vector_store(created_document_id, created_library_type)
                except Exception:
                    logger.exception(
                        "document vector cleanup failed after analyze error, file=%s, document_id=%s",
                        file_name,
                        created_document_id,
                    )
            await _mark_source_parse_failed(db, file, str(e), file_list.library_type)
            error_origin_filename.append(file_name)
            has_server_error = True
            logger.exception(
                "document analyze file failed, file=%s, elapsed=%.2fs",
                file_name,
                time.perf_counter() - file_started,
            )

    logger.info(
        "document analyze request done, success=%s, failed=%s, elapsed=%.2fs",
        len(success_file_url),
        len(error_origin_filename),
        time.perf_counter() - request_started,
    )

    if len(success_file_url) == 0 and len(error_origin_filename) > 0:
        if has_server_error:
            raise AppException(
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                biz_code=BizCode.INTERNAL_ERROR,
                message="服务器内部错误",
            )
        if has_not_found_error and not has_invalid_request_error:
            raise AppException(
                http_status=status.HTTP_404_NOT_FOUND,
                biz_code=BizCode.DOC_RESOURCE_NOT_FOUND,
                message="资源未找到",
            )
        if has_ai_service_unavailable_error:
            raise AppException(
                http_status=status.HTTP_502_BAD_GATEWAY,
                biz_code=BizCode.AI_SERVICE_UNAVAILABLE,
                message="AI服务不可用，请稍后重试",
            )
        if has_token_limit_error:
            raise AppException(
                http_status=status.HTTP_400_BAD_REQUEST,
                biz_code=BizCode.DOC_TOKEN_LIMIT_EXCEEDED,
                message="文件内容超出可解析长度，请缩短内容后重试",
                detail={"files": parse_error_details},
            )
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_PARSE_FAILED,
            message="文件解析失败",
            detail={"files": parse_error_details},
        )

    analyze_result = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename,
    )
    print(success_file_url)
    print(error_origin_filename)
    return Result.success_with_data(analyze_result)
