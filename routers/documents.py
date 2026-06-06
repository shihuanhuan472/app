# routers/documents.py
import asyncio
import json
import uuid
from datetime import datetime
import os
from pathlib import Path
import logging
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, status, UploadFile, Body, File
from sqlalchemy import or_
# from sqlalchemy.orm import Session
from typing import List
from utils.VectorService import VectorService
from dependencies import get_current_active_user
from models import Document, DocumentBreakdown, DocumentKnowledge, Document_review, User
from schemas import (DocumentCreate, DocumentResponse, Result, DeleteImageRequest, Page,
                     DocumentQuery, UploadDocumentResponse, AnalyzeRequest)
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
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.roles import UserRole, has_role
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select, func, delete

router = APIRouter(prefix="/document", tags=["文档"])
load_dotenv()
logger = logging.getLogger(__name__)

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}


def _normalize_library_type(library_type: str) -> str:
    """把前端传入的库类型收敛为两个固定值，避免出现拼写不同导致写错表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """根据库类型选择对应 ORM 模型，使同一套接口可以读写故障库或知识库。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]


def _is_all_library_type(library_type: str) -> bool:
    """判断是否需要同时查询故障库和知识库，列表页使用 all 才能展示两个库的数据。"""
    return str(library_type or "").strip().lower() == "all"


def _sort_documents_by_edit_time(documents):
    """按编辑时间倒序合并两张表的数据，避免知识库和故障库混排时顺序不稳定。"""
    return sorted(documents, key=lambda document: getattr(document, "first_edit_date", None) or datetime.min, reverse=True)


def _normalize_tags(tag):
    """把标签统一为去空白的字符串数组，便于用 MySQL JSON 字段保存。"""
    if not tag:
        return []
    return [str(item).strip() for item in tag if str(item).strip()]


def _tag_filter(model, tags):
    """按标签做包含任一标签的过滤，用于设备语义标签的交叉检索。"""
    normalized_tags = _normalize_tags(tags)
    if not normalized_tags:
        return None
    return or_(*[func.JSON_CONTAINS(model.tag, json.dumps(tag, ensure_ascii=False)) == 1 for tag in normalized_tags])


DOCUMENT_COPY_FIELDS = [
    "title", "contributor_id", "first_edit_date", "problem_intro", "image_urls", "image_urls_problem_intro",
    "causes", "image_urls_causes", "evaluation", "image_urls_evaluation", "inspection", "image_urls_inspection",
    "solutions", "image_urls_solutions", "key_points", "image_urls_key_points", "origin_file_name", "origin_file_dir",
]


def _copy_document_to_library(document: Document, library_type: str, tag=None):
    """把解析器产出的文档对象转换成目标库表对象，避免知识库导入时仍写入故障库表。"""
    document_model = _get_document_model(library_type)
    copied_data = {field: getattr(document, field, None) for field in DOCUMENT_COPY_FIELDS}
    copied_data["tag"] = _normalize_tags(tag if tag is not None else getattr(document, "tag", []))
    return document_model(**copied_data)

# 目前支持的文档类型
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

def document_convert_documentResponse(document: Document, contributor_name: str) -> DocumentResponse:
    """
    document类型转为documentResponse类型
    （其实是因为document类型没有作者姓名，所以不能直接用from_orm）
    当然也可以用循环，但是最开始的document没有这么多字段，就直接手写了
    """
    return DocumentResponse(
        id=document.id,
        library_type=getattr(document, "library_type", "breakdown"),
        tag=_normalize_tags(getattr(document, "tag", [])),
        title=document.title,
        contributor_id=document.contributor_id,
        contributor_name=contributor_name,
        first_edit_date=document.first_edit_date,
        problem_intro=document.problem_intro,
        image_urls=document.image_urls,
        causes=document.causes,
        evaluation=document.evaluation,
        inspection=document.inspection,
        solutions=document.solutions,
        key_points=document.key_points,

        origin_file_name=document.origin_file_name,
        origin_file_dir=document.origin_file_dir,

        image_urls_problem_intro=document.image_urls_problem_intro,
        image_urls_causes=document.image_urls_causes,
        image_urls_evaluation=document.image_urls_evaluation,
        image_urls_inspection=document.image_urls_inspection,
        image_urls_solutions=document.image_urls_solutions,
        image_urls_key_points=document.image_urls_key_points
    )


async def documents_to_responses(
        db: AsyncSession,
        documents: List[Document]
) -> List[DocumentResponse]:
    """
    将文档列表转换为响应列表（批量查询用户信息）
    """
    if not documents:
        return []

    # 1. 收集所有用户ID
    user_ids = list(set(
        doc.contributor_id for doc in documents
        if doc.contributor_id is not None
    ))

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

        responses.append(document_convert_documentResponse(
            document=doc,
            contributor_name=contributor_name
        ))

    return responses

async def check_image_url(image_urls: str):
    """
    如果有图片不存在，返回False，用来判断图片是不是都上传服务器了
    """
    if image_urls:
        config = get_image_config()
        base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
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
async def create_document(document: DocumentCreate,
                          db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(get_current_active_user)):
    print("添加文档")
    config = get_image_config()
    try:
        attrs = ["image_urls_problem_intro", "image_urls_causes", "image_urls_evaluation",
                "image_urls_inspection", "image_urls_solutions", "image_urls_key_points", "image_urls"]
        for attr in attrs:
            value = getattr(document, attr)
            if not await check_image_url(value):
                raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传")
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
                value = value.replace("\\", "/").replace(", /", ", ").removeprefix("/").removesuffix(", ")
            setattr(document, attr, value)

        document_model = _get_document_model(document.library_type)
        document_data = document_model(**document.dict(exclude={"library_type", "tag"}),
                                       tag=_normalize_tags(document.tag),
                                       contributor_id=contributor_id,
                                       is_vectorized=0,
                                       is_deleted=0,
                                       first_edit_date=datetime.now())
        db.add(document_data)
        await db.commit()
        await db.refresh(document_data)
        print("数据库插入成功")
        vector_service = VectorService(db)
        await vector_service.add_document_to_vector_store(document_data)
        print("向量化完成")
        data = document_convert_documentResponse(document_data, current_user.full_name)
        return Result.success_with_data(data)

    except AppException:
        # 重新抛出已知的HTTP异常
        raise

    except Exception as e:
        # 其他异常回滚
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "添加文档失败")

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
            uploaded_images.append({
                "url": relative_url,
                # "relative_url": relative_url,
                "filename": unique_filename,
                "original_name": image.filename
            })
            # print(uploaded_images)
        except Exception as e:
            # 记录错误但继续处理其他文件
            print(f"文件 {image.filename} 上传失败: {str(e)}")

    return Result.success_with_data(uploaded_images)

@router.delete("/delete_image", summary="删除图片")
async def delete_image(request: DeleteImageRequest = Body(...),
                       current_user: User = Depends(get_current_active_user)):
    image_url = request.image_url
    try:
        filename = os.path.basename(image_url)
        config = get_image_config()
        url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"), filename.lstrip("/").lstrip("\\"))
        # if not os.path.exists(url):
        #     raise HTTPException(status_code=404, detail="未找到图片")

        if not await asyncio.to_thread(os.path.exists, url):
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "资源未找到")

        # os.remove(url)
        await asyncio.to_thread(os.remove, url)

        print(f"图片{url}删除成功")
        return Result.success()
    except AppException:
        raise
    except FileNotFoundError:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, f"文件 {image_url} 不存在")
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"删除文件时出错: {str(e)}")


@router.put("/update", summary="更新文档")
async def update_document(id: int,
                          document: DocumentCreate,
                          library_type: str = "breakdown",
                          db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
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
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "文档不存在")
        if current_user.id != document_now.contributor_id and not has_role(current_user, UserRole.ADMIN):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "无权编辑该文档")

        image_urls_str = ""
        attrs = ["image_urls_problem_intro", "image_urls_causes", "image_urls_evaluation",
                 "image_urls_inspection", "image_urls_solutions", "image_urls_key_points", "image_urls"]

        for attr in attrs:
            urls_str = ""
            image_url = getattr(document, attr)
            if image_url:
                image_urls = [url.strip() for url in image_url.split(", ") if url.strip()]
                for image_url in image_urls:
                    image_name = os.path.basename(image_url)
                    url_check = os.path.join(base_url, image_name.lstrip("/").lstrip("\\"))
                    # if not os.path.exists(url_check):
                    #     return Result.error(f"图片未上传，更新失败，请重新上传图片")

                    if not await asyncio.to_thread(os.path.exists, url_check):
                        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "图片未上传，更新失败，请重新上传图片")

                    url_check_str = os.path.join(config["IMAGE_DIR"].lstrip("/").lstrip("\\"),
                                                 image_name.lstrip("/").lstrip("\\"))
                    url_check_str = url_check_str.lstrip("/").lstrip("\\")
                    url_check_str = url_check_str.replace("\\", "/")

                    urls_str += url_check_str + ", "
                if len(urls_str) > 0:
                    urls_str = urls_str.removesuffix(", ")
            setattr(document_now, attr, urls_str)


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

        document_data = document.dict(exclude_unset=True, exclude={"library_type"})
        for key, value in document_data.items():
            # print(key, value)
            if key == "id" or key in attrs:
                continue
            if key == "tag":
                value = _normalize_tags(value)
            setattr(document_now, key, value)

        if len(image_urls_str) > 0:
            image_urls_str = image_urls_str.removesuffix(", ")

        # setattr(document_now, "image_urls", image_urls_str)

        document_now.is_vectorized = 0

        # 更新文档内容的时候，向量需要重新生成
        vector_service = VectorService(db)
        await vector_service.delete_document_from_vector_store(id, getattr(document_now, "library_type", "breakdown"))

        await db.commit()
        await db.refresh(document_now)

        # vector_service.add_document_to_vector_store(document_now)
        await vector_service.add_document_to_vector_store(document_now)

        # full_name = db.query(User.full_name).filter(User.id == document_now.contributor_id).scalar()

        full_name_result = await db.execute(select(User.full_name).where(User.id == document_now.contributor_id))
        full_name = full_name_result.scalar_one_or_none()

        document_response = document_convert_documentResponse(document_now, full_name)
        return Result.success_with_data(document_response)
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"更新文档错误：{str(e)}")

@router.delete("/dele/{id}", summary="删除文档")
async def delete(id: int,
                 library_type: str = "breakdown",
                 db: AsyncSession = Depends(get_db),
                 current_user: User = Depends(get_current_active_user)):
    try:
        # document = db.query(Document).filter(Document.id == id).first()

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
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "文档不存在")
        # 文档表直删仅允许管理员；技术人员必须走删除审核流程
        if not has_role(current_user, UserRole.ADMIN):
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "技术人员需提交删除审核，审核通过后才会删除文档")
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
                        # if os.path.exists(url):
                        #     os.remove(url)
                        #     print(f"删除了{url}")

                        await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                           url) else None

                        name, ext = os.path.splitext(filename)
                        name = f"{name}_compressed{ext}"
                        url = os.path.join(base_url, name.lstrip("/").lstrip("\\"))
                        # if os.path.exists(url):
                        #     os.remove(url)
                        #     print(f"删除了{url}")

                        await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                           url) else None

                        name = f"{name}_compressed_512{ext}"
                        url = os.path.join(base_url, name.lstrip("/").lstrip("\\"))
                        await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists,
                                                                                           url) else None

        # if document.image_urls:
        #     config = get_image_config()
        #     base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
        #     image_urls = document.image_urls.split(", ")
        #     print("删除的image_urls: ", image_urls)
        #     for image_url in image_urls:
        #         filename = os.path.basename(image_url)
        #         url = os.path.join(base_url, filename.lstrip("/").lstrip("\\"))
        #         if os.path.exists(url):
        #             os.remove(url)
        #             print(f"删除了{url}")

        # 把原始文件也删掉
        if document.origin_file_dir:
            url = os.path.join(config["BASE_DIR"], document.origin_file_dir)
            # if os.path.exists(url):
            #     os.remove(url)
            #     print(f"已删除源文件{document.origin_file_dir}")
            await asyncio.to_thread(os.remove, url) if await asyncio.to_thread(os.path.exists, url) else None
            print(f"已删除源文件{document.origin_file_dir}")

        # 软删除场景下，保留审核记录与 document_id 的关联，仅对待审核记录自动撤回
        review_refs_result = await db.execute(
            select(Document_review).where(
                Document_review.document_id == id,
                Document_review.document_library_type == getattr(document, "library_type", "breakdown"),
            )
        )
        review_refs = review_refs_result.scalars().all()
        for review_ref in review_refs:
            # 待审核记录自动撤回，并补充系统备注
            if review_ref.status == 0:
                review_ref.status = 3
                auto_msg = "源文档已被管理员删除，系统自动撤回"
                if review_ref.review_comment and review_ref.review_comment.strip():
                    review_ref.review_comment = f"{review_ref.review_comment}\n{auto_msg}"
                else:
                    review_ref.review_comment = auto_msg
                review_ref.reviewed_time = datetime.now()

        await db.flush()

        # 删掉向量
        vector_service = VectorService(db)
        # vector_service.delete_document_from_vector_store(id)
        await vector_service.delete_document_from_vector_store(id, getattr(document, "library_type", "breakdown"))

        print("成功删除文档")
        document.is_deleted = 1
        await db.commit()
        return Result.success()
    except AppException:
        raise
    except Exception as e:
        # 其他异常回滚
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"删除文档失败：{str(e)}")

@router.get("/", summary="获取所有文档")
async def get_documents(current_user: User = Depends(get_current_active_user),
                        db: AsyncSession = Depends(get_db),
                        library_type: str = "breakdown"):
    document_model = _get_document_model(library_type)
    result = await db.execute(select(document_model).where(document_model.is_deleted == 0))
    documents = result.scalars().all()

    # documents = db.query(Document)
    responses = await documents_to_responses(db, documents)
    # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
    return Result.success_with_data(responses)

@router.get("/get_by_id/{id}", summary="根据id获得文档内容")
async def get_document(id: int,
                       library_type: str = "breakdown",
                       db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
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

            full_name_result = await db.execute(select(User.full_name).where(User.id == document.contributor_id))
            full_name = full_name_result.scalar_one_or_none()

            document_data = document_convert_documentResponse(document, full_name)
            return Result.success_with_data(document_data)
        return Result.success()
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"根据id获取文档内容失败：{str(e)}")

@router.post("/page", summary="分页查询文档内容")
async def get_page(page: Page,
                   db: AsyncSession = Depends(get_db),
                   current_user: User = Depends(get_current_active_user)):
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
                    select(func.count()).select_from(document_model).where(*where_conditions)
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
            documents = _sort_documents_by_edit_time(documents)[offset:offset + page.size]
            # 两张表总数除以每页数量，得到跨库分页总页数。
            total_pages = (total_count + page.size - 1) // page.size
            # 复用原来的响应转换逻辑，保留每条文档自身的 library_type。
            responses = await documents_to_responses(db, documents)
            data = {
                "total_count": total_count,
                "total_pages": total_pages,
                "documents": responses
            }
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

        total_pages = (total_count + page.size - 1) // page.size
        responses = await documents_to_responses(db, documents)
        # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "documents": responses
        }
        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"分页查询文档内容失败：{str(e)}")

@router.post("/query", summary="查询文档信息")
async def query(query: DocumentQuery,
                db: AsyncSession = Depends(get_db),
                current_user: User = Depends(get_current_active_user)):
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
                # 关键词搜索条件保持原逻辑：标题、作者姓名、用户名、问题描述都可以匹配。
                filter_condition = or_(
                    document_model.title.like(f"%{query.data}%"),
                    User.full_name.like(f"%{query.data}%"),
                    User.username.like(f"%{query.data}%"),
                    document_model.problem_intro.like(f"%{query.data}%")
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
                    select(document_model).join(User, document_model.contributor_id == User.id)
                    .where(*where_conditions)
                    .order_by(document_model.first_edit_date.desc())
                    .limit(max_needed)
                )
                # 合并两个库的搜索候选结果，统一排序后再分页。
                documents.extend(result.scalars().all())

            # 按编辑时间跨库排序，并只返回当前页需要的数据。
            documents = _sort_documents_by_edit_time(documents)[offset:offset + query.size]
            # 根据跨库总数计算页数，让前端分页按钮正确。
            total_pages = (total_count + query.size - 1) // query.size
            # 转成统一响应结构，同时保留 library_type 用于前端徽标和详情跳转。
            documents_response = await documents_to_responses(db, documents)
            data = {
                "total_count": total_count,
                "total_pages": total_pages,
                "documents": documents_response
            }
            return Result.success_with_data(data)

        document_model = _get_document_model(query.library_type)
        tag_condition = _tag_filter(document_model, query.tag)

        filter_condition = or_(
            document_model.title.like(f"%{query.data}%"),
            User.full_name.like(f"%{query.data}%"),
            User.username.like(f"%{query.data}%"),
            document_model.problem_intro.like(f"%{query.data}%")
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
            select(document_model).join(User, document_model.contributor_id == User.id)
            .where(*where_conditions)
            .order_by(document_model.first_edit_date.desc())
            .offset(offset)
            .limit(query.size)
        )
        documents = result.scalars().all()

        total_pages = (total_count + query.size - 1) // query.size
        documents_response = await documents_to_responses(db, documents)

        # documents_response = [document_convert_documentResponse(document, current_user.full_name) for document in documents]

        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "documents": documents_response
        }

        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询文档信息失败：{str(e)}")

@router.post("/upload_files", summary="上传文件")
async def upload_files(files: List[UploadFile] = File(...),
                       db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    """
    该aoi仅将文档保存至后端服务器，不做任何其他处理
    其实是为了把文档上传和分析分开，让分析过程看起来短一点
    """
    success_origin_filename = []
    success_file_url = []
    error_origin_filename = []
    has_server_error = False
    document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    document_relative_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
    dir = os.path.join(document_base_dir, document_relative_dir)

    if not os.path.exists(dir):
        os.mkdir(dir)
        print(f"创建了{dir}")

    for file in files:
        file_ext = os.path.splitext(file.filename)[-1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            error_origin_filename.append(file.filename)
            continue

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"

        try:
            contents = await file.read()
            url = os.path.join(dir, filename)
            async with aiofiles.open(url, "wb") as f:
                await f.write(contents)

            success_origin_filename.append(file.filename)
            success_file_url.append(document_relative_dir + "/" + filename)
        except Exception as e:
            print(e)
            error_origin_filename.append(file.filename)
            has_server_error = True
            # return Result.error(f"文件{file.filename}上传失败，请稍后重试")

    if len(success_file_url) == 0:
        if has_server_error:
            raise AppException(
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                biz_code=BizCode.INTERNAL_ERROR,
                message="服务器内部错误"
            )
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="请求核心参数无效"
        )

    upload_document_request = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename
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
            message="请求核心参数无效"
        )
    if len(file_list.file_list) != len(file_list.file_name):
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_REQUEST_INVALID,
            message="请求核心参数无效"
        )
    submit_for_review = bool(file_list.submit_for_review)
    if submit_for_review and not has_role(current_user, UserRole.TECHNICIAN):
        raise AppException(
            http_status=status.HTTP_403_FORBIDDEN,
            biz_code=BizCode.FORBIDDEN,
            message="仅技术人员可提交审核"
        )

    success_file_url = []
    success_origin_filename = []
    error_origin_filename = []
    has_invalid_request_error = False
    has_not_found_error = False
    has_server_error = False
    parse_failed_details = []
    has_token_limit_error = False
    has_ai_service_unavailable_error = False
    document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    for file, file_name in zip(file_list.file_list, file_list.file_name):
        try:
            file_ext = os.path.splitext(file)[1].lower()
            # print(file_ext)
            if file_ext not in ALLOWED_EXTENSIONS:
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                continue
            url = os.path.join(document_base_dir, file)
            if not os.path.exists(url):
                error_origin_filename.append(file_name)
                has_not_found_error = True
                continue
            # print(url)
            document = None
            parser_for_error = None

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

            if document is None:
                if os.path.exists(url):
                    await asyncio.to_thread(os.remove, url)
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                parser_code = getattr(parser_for_error, "last_error_code", None)
                parser_detail = getattr(parser_for_error, "last_error_detail", None)
                parse_failed_details.append(
                    {
                        "file_name": file_name,
                        "file_path": file,
                        "code": int(parser_code) if parser_code is not None else int(BizCode.DOC_PARSE_FAILED),
                        "detail": parser_detail or "解析器未返回文档对象",
                    }
                )
                logger.warning(
                    "document parse returned None, file=%s, code=%s, detail=%s",
                    file_name,
                    parser_code,
                    parser_detail,
                )
                if parser_code == int(BizCode.DOC_TOKEN_LIMIT_EXCEEDED):
                    has_token_limit_error = True
                if parser_code == int(BizCode.AI_SERVICE_UNAVAILABLE):
                    has_ai_service_unavailable_error = True
                continue

            if _is_ai_result_effectively_empty(document):
                if os.path.exists(url):
                    await asyncio.to_thread(os.remove, url)
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                parse_failed_details.append(
                    {
                        "file_name": file_name,
                        "file_path": file,
                        "code": int(BizCode.DOC_PARSE_FAILED),
                        "detail": "AI解析结果为空，可能该文档不是故障知识，或信息不足以形成故障分析结论。请补充故障现象、原因、排查和处理信息后重试。",
                    }
                )
                continue

            if not _has_meaningful_title(document):
                if os.path.exists(url):
                    # os.remove(url)
                    await asyncio.to_thread(os.remove, url)
                error_origin_filename.append(file_name)
                has_invalid_request_error = True
                parse_failed_details.append(
                    {
                        "file_name": file_name,
                        "file_path": file,
                        "code": int(BizCode.DOC_PARSE_FAILED),
                        "detail": "AI解析失败：未能生成有效标题。可能该文档不是故障知识，或内容不完整/不清晰。",
                    }
                )
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
            # if os.path.exists(url):
            #     os.remove(url)
            if os.path.exists(url):
                await asyncio.to_thread(os.remove, url)
            error_origin_filename.append(file_name)
            has_server_error = True

    if len(success_file_url) == 0 and len(error_origin_filename) > 0:
        if has_server_error:
            raise AppException(
                http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                biz_code=BizCode.INTERNAL_ERROR,
                message="服务器内部错误"
            )
        if has_not_found_error and not has_invalid_request_error:
            raise AppException(
                http_status=status.HTTP_404_NOT_FOUND,
                biz_code=BizCode.DOC_RESOURCE_NOT_FOUND,
                message="资源未找到"
            )
        if has_ai_service_unavailable_error:
            raise AppException(
                http_status=status.HTTP_502_BAD_GATEWAY,
                biz_code=BizCode.AI_SERVICE_UNAVAILABLE,
                message="AI服务不可用，请稍后重试"
            )
        if has_token_limit_error:
            raise AppException(
                http_status=status.HTTP_400_BAD_REQUEST,
                biz_code=BizCode.DOC_TOKEN_LIMIT_EXCEEDED,
                message="文件内容超出可解析长度，请缩短内容后重试",
                detail={"files": parse_failed_details}
            )
        raise AppException(
            http_status=status.HTTP_400_BAD_REQUEST,
            biz_code=BizCode.DOC_PARSE_FAILED,
            message="文件解析失败",
            detail={"files": parse_failed_details}
        )

    analyze_result = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename
    )
    print(success_file_url)
    print(error_origin_filename)
    return Result.success_with_data(analyze_result)
