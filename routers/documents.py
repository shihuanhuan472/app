# routers/documents.py
import uuid
from datetime import datetime
import os
from pathlib import Path
import logging
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, Body
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List
from utils.VectorService import VectorService
from dependencies import get_current_active_user
from models import Document, User
from schemas import DocumentCreate, DocumentResponse, Result, DeleteImageRequest, Page, DocumentQuery
from database import get_db

router = APIRouter(prefix="/document", tags=["文档"])
load_dotenv()
logger = logging.getLogger(__name__)

def document_convert_documentResponse(document: Document, contributor_name: str) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
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
        key_points=document.key_points
    )


def documents_to_responses(
        db: Session,
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
        users = db.query(
            User.id,
            User.full_name,
            User.username
        ).filter(
            User.id.in_(user_ids)
        ).all()

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

@router.post("/add", summary="添加文档")
async def create_document(document: DocumentCreate,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_active_user)):
    config = get_image_config()
    try:
        if document.image_urls:
            urls = [url.strip() for url in document.image_urls.split(", ") if url.strip()]
            base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
            print("base_dir" + config["BASE_DIR"])
            print("image_dir" + config["IMAGE_DIR"])
            print("base_url" + base_url)

            for url in urls:
                url_check = os.path.basename(url)
                url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
                # print(url_check)
                if not os.path.exists(url_check):
                    print(url_check)
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                        detail="图片未上传")

        contributor_id = current_user.id

        document.image_urls = (document.image_urls.replace("\\", "/")
                               .replace(", /", ", ")
                               .removeprefix("/")
                               .removesuffix(", "))

        document_data = Document(**document.dict(),
                                 contributor_id=contributor_id,
                                 is_vectorized=0,
                                 first_edit_date=datetime.now())
        db.add(document_data)
        db.commit()
        db.refresh(document_data)

        vector_service = VectorService(db)
        vector_service.add_document_to_vector_store(document_data)

        data = document_convert_documentResponse(document_data, current_user.full_name)
        return Result.success_with_data(data)

    except HTTPException:
        # 重新抛出已知的HTTP异常
        raise

    except Exception as e:
        # 其他异常回滚
        db.rollback()
        return Result.error("添加文档失败")

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
            with open(save_path, "wb") as buffer:
                buffer.write(contents)

            # 构建文件信息
            file_url = f"{url}/{unique_filename}"
            relative_url = Path(config["IMAGE_DIR"]) / unique_filename
            uploaded_images.append({
                "url": relative_url,
                # "relative_url": relative_url,
                "filename": unique_filename,
                "original_name": image.filename
            })
            print(uploaded_images)
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
        if not os.path.exists(url):
            raise HTTPException(status_code=404, detail="未找到图片")
        os.remove(url)
        print(f"图片{url}删除成功")
        return Result.success()
    except FileNotFoundError:
        return Result.error(f"文件 {image_url} 不存在")
    except Exception as e:
        return Result.error(f"删除文件时出错: {str(e)}")


@router.put("/update", summary="更新文档")
async def update_document(id: int,
                          document: DocumentCreate,
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
        document_now = db.query(Document).filter(Document.id == id).first()
        if not document_now:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文档不存在"
            )
        if current_user.id != document_now.contributor_id and current_user.role != 0:
            raise HTTPException(status_code=403, detail="无权编辑该文档")

        image_urls_str = ""

        if document.image_urls:
            image_urls = [url.strip() for url in document.image_urls.split(", ") if url.strip()]
            for image_url in image_urls:
                image_name = os.path.basename(image_url)
                url_check = os.path.join(base_url, image_name.lstrip("/").lstrip("\\"))
                if not os.path.exists(url_check):
                    return Result.error(f"图片未上传，更新失败，请重新上传图片")

                # url_check_str = url_check.lstrip("/").lstrip("\\")
                url_check_str = os.path.join(config["IMAGE_DIR"].lstrip("/").lstrip("\\"), image_name.lstrip("/").lstrip("\\"))
                url_check_str = url_check_str.lstrip("/").lstrip("\\")
                url_check_str = url_check_str.replace("\\", "/")

                image_urls_str += url_check_str + ", "

        document_data = document.dict(exclude_unset=True)
        for key, value in document_data.items():
            # print(key, value)
            if key == "id" or key == "image_urls":
                continue
            setattr(document_now, key, value)

        if len(image_urls_str) > 0:
            image_urls_str = image_urls_str.removesuffix(", ")

        setattr(document_now, "image_urls", image_urls_str)

        document_now.is_vectorized = 0
        vector_service = VectorService(db)
        vector_service.delete_document_from_vector_store(id)

        db.commit()
        db.refresh(document_now)

        vector_service.add_document_to_vector_store(document_now)

        full_name = db.query(User.full_name).filter(User.id == document_now.contributor_id).scalar()

        document_response = document_convert_documentResponse(document_now, full_name)
        return Result.success_with_data(document_response)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        return Result.error(f"更新文档错误：{str(e)}")

@router.delete("/dele/{id}", summary="删除文档")
async def delete(id: int,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_active_user)):
    try:
        document = db.query(Document).filter(Document.id == id).first()
        if not document:
            return Result.error(f"文档不存在")
        if document.contributor_id != current_user.id and current_user.role != 0:
            return Result.error("无删除权限")
        if document.image_urls:
            config = get_image_config()
            base_url = os.path.join(config["BASE_DIR"], config["IMAGE_DIR"].lstrip("/").lstrip("\\"))
            image_urls = document.image_urls.split(", ")
            print("删除的image_urls: ", image_urls)
            for image_url in image_urls:
                filename = os.path.basename(image_url)
                url = os.path.join(base_url, filename.lstrip("/").lstrip("\\"))
                if os.path.exists(url):
                    os.remove(url)
                    print(f"删除了{url}")

        vector_service = VectorService(db)
        vector_service.delete_document_from_vector_store(id)

        print("成功删除文档")

        db.delete(document)
        db.commit()
        return Result.success()
    except HTTPException:
        raise
    except Exception as e:
        # 其他异常回滚
        db.rollback()
        return Result.error(f"删除文档失败：{str(e)}")

@router.get("/", summary="获取所有文档")
async def get_documents(current_user: User = Depends(get_current_active_user),
                        db: Session = Depends(get_db)):
    documents = db.query(Document)
    responses = documents_to_responses(db, documents)
    # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
    return Result.success_with_data(responses)

@router.get("/get_by_id/{id}", summary="根据id获得文档内容")
async def get_document(id: int,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    try:
        document = db.query(Document).filter(Document.id == id).first()
        if document:
            full_name = db.query(User.full_name).filter(User.id == document.contributor_id).scalar()
            document_data = document_convert_documentResponse(document, full_name)
            return Result.success_with_data(document_data)
        return Result.success()
    except Exception as e:
        return Result.error(f"根据id获取文档内容失败：{str(e)}")

@router.post("/page", summary="分页查询文档内容")
async def get_page(page: Page,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_active_user)):
    logger.info("分页查询文档内容")
    try:
        offset = (page.page - 1) * page.size
        total_count = db.query(Document).count()
        documents = db.query(Document).offset(offset).limit(page.size).all()
        total_pages = (total_count + page.size - 1) // page.size
        responses = documents_to_responses(db, documents)
        # documents_data = [document_convert_documentResponse(document, current_user.full_name) for document in documents]
        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "documents": responses
        }
        return Result.success_with_data(data)
    except Exception as e:
        return Result.error(f"分页查询文档内容失败：{str(e)}")

@router.post("/query", summary="查询文档信息")
async def query(query: DocumentQuery,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_active_user)):
    try:
        offset = (query.page - 1) * query.size


        total_count = db.query(Document).join(
            User, Document.contributor_id == User.id
        ).filter(
            or_(
                Document.title.like(f"%{query.data}%"),
                User.full_name.like(f"%{query.data}%"),  # 从 User 表查询姓名
                User.username.like(f"%{query.data}%"),  # 也可以查询用户名
                Document.problem_intro.like(f"%{query.data}%")
            )
        ).count()


        documents = db.query(Document).join(
            User, Document.contributor_id == User.id
        ).filter(
            or_(
                Document.title.like(f"%{query.data}%"),
                User.full_name.like(f"%{query.data}%"),  # 从 User 表查询姓名
                User.username.like(f"%{query.data}%"),  # 也可以查询用户名
                Document.problem_intro.like(f"%{query.data}%")
            )
        ).order_by(
            Document.first_edit_date.desc()
        ).offset(offset).limit(query.size).all()
        total_pages = (total_count + query.size - 1) // query.size
        documents_response = documents_to_responses(db, documents)

        # documents_response = [document_convert_documentResponse(document, current_user.full_name) for document in documents]

        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "documents": documents_response
        }

        return Result.success_with_data(data)
    except Exception as e:
        return Result.error(f"查询文档信息失败：{str(e)}")