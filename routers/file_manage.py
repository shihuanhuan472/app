import asyncio
import os
import uuid
import json
from datetime import datetime

import aiofiles
from fastapi import APIRouter, UploadFile, Depends, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List
from database import get_db
from dependencies import get_current_active_user
from models import User, Document, DocumentBreakdown, DocumentKnowledge, Document_review
from schemas import UploadDocumentRequestNew, ResultNew, AnalyzeRequest, UploadDocumentResponse, DeleteDocumentRequestNew
from utils.PdfParser import pdf_parser
from utils.PPTParser import ppt_parser
from utils.VectorService import VectorService
from utils.WordParser import word_parser
from utils.HTMLParser import html_parser
from utils.TXTParser import txt_parser
from utils.MarkdownParser import markdown_parser
from utils.ImageParser import image_parser
from utils.CsvExcelParser import csv_excel_parser
from utils.roles import UserRole, has_role
from sqlalchemy import or_, select, func, delete

router = APIRouter(prefix="/api/v1/datasets", tags=["对话"])
DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}


def _normalize_library_type(library_type: str) -> str:
    """把 dataset_id 或请求体里的库类型统一成固定值，避免批量接口写错文档表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """根据库类型选择批量接口要操作的 ORM 模型。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _normalize_tags(tag):
    """把标签统一成字符串数组，方便存入 JSON 字段。"""
    if not tag:
        return []
    return [str(item).strip() for item in tag if str(item).strip()]


DOCUMENT_COPY_FIELDS = [
    "title", "contributor_id", "first_edit_date", "problem_intro", "image_urls", "image_urls_problem_intro",
    "causes", "image_urls_causes", "evaluation", "image_urls_evaluation", "inspection", "image_urls_inspection",
    "solutions", "image_urls_solutions", "key_points", "image_urls_key_points", "origin_file_name", "origin_file_dir",
]


def _copy_document_to_library(document: Document, library_type: str, tag=None):
    """把解析器返回的默认文档对象复制到目标库表，支持知识库批量导入。"""
    document_model = _get_document_model(library_type)
    copied_data = {field: getattr(document, field, None) for field in DOCUMENT_COPY_FIELDS}
    copied_data["tag"] = _normalize_tags(tag if tag is not None else getattr(document, "tag", []))
    return document_model(**copied_data)
ALLOWED_EXTENSIONS = {
    ".pdf", ".pptx", ".ppt", ".html", ".mhtml", ".docx", ".txt", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp",
    ".csv", ".xlsx", ".xls", ".xlsm"
}


def _normalize_document_for_db(document: Document) -> None:
    text_fields = [
        "title", "problem_intro", "causes", "evaluation",
        "inspection", "solutions", "key_points", "origin_file_name", "origin_file_dir"
    ]
    image_fields = [
        "image_urls", "image_urls_problem_intro", "image_urls_causes",
        "image_urls_evaluation", "image_urls_inspection",
        "image_urls_solutions", "image_urls_key_points"
    ]

    for field in text_fields:
        value = getattr(document, field, None)
        if isinstance(value, (list, tuple)):
            setattr(document, field, "\n".join(str(item) for item in value if item is not None))
        elif isinstance(value, dict):
            setattr(document, field, json.dumps(value, ensure_ascii=False))
        elif value is not None and not isinstance(value, str):
            setattr(document, field, str(value))


def _build_create_review_from_document(document: Document, contributor_id: int) -> Document_review:
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
        origin_file_dir=document.origin_file_dir,
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

    for field in image_fields:
        value = getattr(document, field, None)
        if isinstance(value, (list, tuple)):
            text = ", ".join(str(item).strip() for item in value if item is not None and str(item).strip())
            setattr(document, field, text or None)
        elif isinstance(value, dict):
            setattr(document, field, json.dumps(value, ensure_ascii=False))
        elif value is not None and not isinstance(value, str):
            setattr(document, field, str(value))

@router.post("/upload_files")
@router.post("/{dataset_id}/documents")
async def upload_files(dataset_id: str = "",
                       files: List[UploadFile] = File(...),
                       db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    document_relative_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
    dir = os.path.join(document_base_dir, document_relative_dir)
    final_result = []
    success_file_url = []
    if not os.path.exists(dir):
        os.mkdir(dir)
        print(f"创建了{dir}")

    for file in files:
        file_ext = os.path.splitext(file.filename)[-1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            for file_tmp_url in success_file_url:
                url = os.path.join(document_base_dir, file_tmp_url)
                if os.path.exists(url):
                    os.remove(url)

            return ResultNew.result(400, f"{file.filename}格式不支持", None)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"

        try:
            contents = await file.read()
            url = os.path.join(dir, filename)
            async with aiofiles.open(url, "wb") as f:
                await f.write(contents)

            upload_document_tmp = UploadDocumentRequestNew(
                name=file.filename,
                size=file.size,
                type=file_ext[1:],
                location=document_relative_dir + "/" + filename,
                create=datetime.now().timestamp()
            )
            success_file_url.append(document_relative_dir + "/" + filename)
            final_result.append(upload_document_tmp)

        except Exception as e:
            for file_tmp_url in success_file_url:
                url = os.path.join(document_base_dir, file_tmp_url)
                if os.path.exists(url):
                    os.remove(url)
            return ResultNew.result(101, f"文件{file.filename}上传失败，请稍后重试", None)

    return ResultNew.result(1, None, final_result)


@router.post("/analyze_files", summary="解析文件")
@router.post("/analyze", summary="解析文件")
async def analyze_files(file_list: AnalyzeRequest,
                        db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_active_user)):
    # file_list是后端相对路径
    submit_for_review = bool(file_list.submit_for_review)
    if submit_for_review and not has_role(current_user, UserRole.TECHNICIAN):
        return ResultNew.result(403, "仅技术人员可提交审核", None)

    success_file_url = []
    success_origin_filename = []
    error_origin_filename = []
    document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    for file, file_name in zip(file_list.file_list, file_list.file_name):
        try:
            file_ext = os.path.splitext(file)[1].lower()
            # print(file_ext)
            if file_ext not in ALLOWED_EXTENSIONS:
                error_origin_filename.append(file_name)
                continue
            url = os.path.join(document_base_dir, file)
            # print(url)
            document = None

            # 根据不同的文件类型调用不同的解析器
            if file_ext == ".pdf":
                document = pdf_parser.parse(url)
            elif file_ext == ".pptx" or file_ext == ".ppt":
                document = ppt_parser.parse(url)
            elif file_ext == ".html" or file_ext == ".mhtml":
                # document = await asyncio.to_thread(html_parser.parse(url))
                document = html_parser.parse(url)
            elif file_ext == ".docx":
                document = word_parser.parse(url)
            elif file_ext == ".txt":
                document = txt_parser.parse(url)
            elif file_ext == ".md" or file_ext == ".markdown":
                document = markdown_parser.parse(url)
            elif file_ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                document = image_parser.parse(url)
            elif file_ext in {".csv", ".xlsx", ".xls", ".xlsm"}:
                document = csv_excel_parser.parse(url)
            if not document or not document.title:
                if os.path.exists(url):
                    # os.remove(url)
                    await asyncio.to_thread(os.remove, url)
                error_origin_filename.append(file_name)
                continue
            document.contributor_id = current_user.id
            document.origin_file_name = file_name
            document.origin_file_dir = file
            document.first_edit_date = datetime.now()
            document.tag = _normalize_tags(file_list.tag)
            document.library_type = _normalize_library_type(file_list.library_type)
            _normalize_document_for_db(document)
            print(document.title)
            if submit_for_review:
                review = _build_create_review_from_document(document, current_user.id)
                db.add(review)
                await db.commit()
                await db.refresh(review)
            else:
                document = _copy_document_to_library(document, file_list.library_type, file_list.tag)
                db.add(document)
                await db.commit()
                await db.refresh(document)
                vector_service = VectorService(db)
                # vector_service.add_document_to_vector_store(document)
                await vector_service.add_document_to_vector_store(document)

            success_file_url.append(file)
            success_origin_filename.append(file_name)

        except Exception as e:
            print(e)
            url = os.path.join(document_base_dir, file)
            if os.path.exists(url):
                # os.remove(url)
                await asyncio.to_thread(os.remove, url)
            error_origin_filename.append(file_name)

    analyze_result = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename
    )
    print(success_file_url)
    print(error_origin_filename)
    return ResultNew.result(1, None, analyze_result)


# @router.get("/{dataset_id}/documents")
# async def get_documents(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1),
#                         order_by: str = Query("create_time"), desc: bool = Query(True), keywords: str = Query(None),
#                         id: int = Query(None), create_time_from: int = Query(0), create_time_to = Query(0),
#                         ):

def get_image_config():
    MAX_IMAGE_SIZE: int = int(os.getenv("MAX_IMAGE_SIZE", 20 * 1024 * 1024))
    IMAGE_DIR: str = os.getenv("IMAGE_DIR", "upload/images")
    BASE_DIR: str = os.getenv("BASE_DIR", "/")
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    return {
        "MAX_IMAGE_SIZE": MAX_IMAGE_SIZE,
        "IMAGE_DIR": IMAGE_DIR,
        "BASE_DIR": BASE_DIR,
        "ALLOWED_EXTENSIONS": ALLOWED_EXTENSIONS
    }


@router.delete("/{dataset_id}/documents")
async def delete_documents(dataset_id: str, ids: DeleteDocumentRequestNew,
                          db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        error_document_ids = []
        document_model = _get_document_model(dataset_id)

        for id in ids.ids:
            result = await db.execute(
                select(document_model).where(
                    document_model.id == id,
                    document_model.is_deleted == 0,
                )
            )
            document = result.scalar_one_or_none()

            if not document:
                print(f"文档{id}不存在")
                continue

            if document.contributor_id != current_user.id and not has_role(current_user, UserRole.ADMIN):
                print(f"{current_user.id}用户无权删除文档{id}")
                error_document_ids.append(id)
                continue

            attrs = ["image_urls_problem_intro", "image_urls_causes", "image_urls_evaluation",
                     "image_urls_inspection", "image_urls_solutions", "image_urls_key_points", "image_urls"]
            config = get_image_config()
            base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))

            # 删除文档的时候，把文档里的图片都删掉
            for attr in attrs:
                value = getattr(document, attr)
                if value is not None:
                    image_urls = value.split(", ")
                    for image_url in image_urls:
                        filename = os.path.basename(image_url)
                        if filename.strip():
                            url = os.path.join(base_url, filename.lstrip("/").lstrip("\\"))

                            await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                               url) else None

                            name, ext = os.path.splitext(filename)
                            name = f"{name}_compressed{ext}"
                            url = os.path.join(base_url, name.lstrip("/").lstrip("\\"))
                            await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                               url) else None

                            name = f"{name}_compressed_512{ext}"
                            url = os.path.join(base_url, name.lstrip("/").lstrip("\\"))
                            await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                               url) else None
            if document.origin_file_dir:
                url = os.path.join(config["BASE_DIR"], document.origin_file_dir)
                await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists, url) else None
                print(f"已删除源文件{document.origin_file_dir}")

            vector_service = VectorService(db)
            await vector_service.delete_document_from_vector_store(id, getattr(document, "library_type", "breakdown"))
            print(f"成功删除文档{id}")

            review_refs_result = await db.execute(
                select(Document_review).where(
                    Document_review.document_id == id,
                    Document_review.document_library_type == getattr(document, "library_type", "breakdown"),
                )
            )
            review_refs = review_refs_result.scalars().all()
            for review_ref in review_refs:
                if review_ref.status == 0:
                    review_ref.status = 3
                    auto_msg = "源文档已被删除，系统自动撤回"
                    if review_ref.review_comment and review_ref.review_comment.strip():
                        review_ref.review_comment = f"{review_ref.review_comment}\n{auto_msg}"
                    else:
                        review_ref.review_comment = auto_msg
                    review_ref.reviewed_time = datetime.now()

            document.is_deleted = 1

        await db.commit()

        if len(error_document_ids) > 0:
            msg = ", ".join(map(str, error_document_ids))
            msg = "文档" + msg + "无删除权限"
        else:
            msg = ""

        return ResultNew.result(1, msg, None)
    except Exception as e:
        print(e)
        await db.rollback()
        return ResultNew.result(102, f"删除文档失败：{str(e)}", None)
