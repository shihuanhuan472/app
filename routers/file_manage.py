import os
import uuid
from datetime import datetime

import aiofiles
from fastapi import APIRouter, UploadFile, Depends, File
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from dependencies import get_current_active_user
from models import User
from schemas import UploadDocumentRequestNew, ResultNew, AnalyzeRequest, UploadDocumentResponse
from utils.PdfParser import pdf_parser
from utils.PPTParser import ppt_parser
from utils.VectorService import VectorService
from utils.WordParser import word_parser
from utils.HTMLParser import html_parser

router = APIRouter(prefix="/api/v1/file", tags=["对话"])
ALLOWED_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".html", ".mhtml", ".docx"}

@router.post("/upload")
async def upload_files(files: List[UploadFile] = File(...),
                       db: Session = Depends(get_db),
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
            return ResultNew.result(400, f"文件{file.filename}上传失败，请稍后重试", None)

    return ResultNew.result(0, None, final_result)


@router.post("/analyze", summary="解析文件")
async def analyze_files(file_list: AnalyzeRequest,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_active_user)):
    # file_list是后端相对路径
    success_file_url = []
    success_origin_filename = []
    error_origin_filename = []
    document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
    for file, file_name in zip(file_list.file_list, file_list.file_name):
        try:
            file_ext = file.split(".")[-1]
            file_ext = "." + file_ext
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
            if not document.title:
                if os.path.exists(url):
                    os.remove(url)
                error_origin_filename.append(file_name)
                continue
            document.contributor_id = current_user.id
            document.origin_file_name = file_name
            document.origin_file_dir = file
            document.first_edit_date = datetime.now()
            print(document.title)
            db.add(document)
            db.commit()
            db.refresh(document)
            vector_service = VectorService(db)
            vector_service.add_document_to_vector_store(document)

            success_file_url.append(file)
            success_origin_filename.append(file_name)

        except Exception as e:
            print(e)
            url = os.path.join(document_base_dir, file)
            if os.path.exists(url):
                os.remove(url)
            error_origin_filename.append(file_name)

    analyze_result = UploadDocumentResponse(
        success_file_url=success_file_url,
        success_origin_filename=success_origin_filename,
        error_origin_filename=error_origin_filename
    )
    print(success_file_url)
    print(error_origin_filename)
    return ResultNew.result(0, None, analyze_result)
