"""用户对产品的问题和需求反馈，与诊断反馈学习模块独立。"""
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import User, UserFeedback
from schemas import Result
from utils.roles import UserRole, has_role

router = APIRouter(prefix="/user-feedback", tags=["用户问题反馈"])
UPLOAD_ROOT = Path("runtime/user_feedback")
MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".log"}


def validate_fields(software, feedback_type, description, details, department, person):
    values = [value.strip() for value in (software, feedback_type, description, details, department, person)]
    software, feedback_type, description, details, department, person = values
    if software not in {"智能诊断知识库", "智能诊断系统", "实验室管理云平台"}:
        raise HTTPException(400, "请选择有效的软件名称")
    if feedback_type not in {"问题", "需求"}:
        raise HTTPException(400, "请选择问题或需求")
    if department not in {"研发", "生产/工程/质量", "FSE/FAS"}:
        raise HTTPException(400, "请选择有效的部门")
    if not description or len(description) > 200:
        raise HTTPException(400, "问题/需求描述须为 1～200 字")
    if len(details) > 2000:
        raise HTTPException(400, "详细描述不能超过 2000 字")
    if not person or len(person) > 100:
        raise HTTPException(400, "人员须为 1～100 字")
    return dict(zip(("software", "feedback_type", "description", "details", "department", "person"), values))


@router.post("", summary="提交问题或需求反馈")
async def submit_feedback(
    software: str = Form(...), feedback_type: str = Form(...),
    description: str = Form(...), details: str = Form(""),
    department: str = Form(...), person: str = Form(...),
    files: Optional[List[UploadFile]] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    fields = validate_fields(software, feedback_type, description, details, department, person)
    files = files or []
    if len(files) > 6:
        raise HTTPException(400, "图片和附件最多 6 个")
    for upload in files:
        if Path(upload.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, "附件格式不支持，请上传图片、文档、日志或 ZIP 文件")
    written = []
    attachments = []
    try:
        if files:
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
        for upload in files:
            storage_name = uuid4().hex
            target = UPLOAD_ROOT / storage_name
            written.append(target)
            size = 0
            with target.open("wb") as output:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(400, "单个附件不能超过 10 MB")
                    output.write(chunk)
            if not size:
                raise HTTPException(400, "不能上传空附件")
            attachments.append({"name": (upload.filename or "附件").replace("\\", "/").split("/")[-1][:255], "storage_name": storage_name, "size": size})
        record = UserFeedback(**fields, user_id=current_user.id, attachments=attachments, created_time=datetime.now())
        db.add(record)
        await db.flush()
        record_id = record.id
        await db.commit()
    except Exception:
        await db.rollback()
        for target in written:
            target.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()
    return Result.success_with_data({"id": record_id})


@router.get("", summary="查看反馈（管理员可查看全部）")
async def list_feedback(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user),
):
    conditions = [] if has_role(current_user, UserRole.ADMIN) else [UserFeedback.user_id == current_user.id]
    total = (await db.execute(select(func.count()).select_from(UserFeedback).where(*conditions))).scalar_one()
    records = (await db.execute(select(UserFeedback).where(*conditions).order_by(UserFeedback.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    items = []
    for record in records:
        item = {key: getattr(record, key) for key in ("id", "user_id", "software", "feedback_type", "description", "details", "department", "person")}
        item["created_time"] = record.created_time.isoformat()
        item["attachments"] = [{"name": a["name"], "size": a["size"], "index": i} for i, a in enumerate(record.attachments)]
        items.append(item)
    return Result.success_with_data({"items": items, "total": total, "page": page, "page_size": page_size, "scope": "all" if has_role(current_user, UserRole.ADMIN) else "mine"})


@router.get("/{feedback_id}/attachments/{index}", summary="下载反馈附件")
async def download_attachment(
    feedback_id: int, index: int,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user),
):
    record = await db.get(UserFeedback, feedback_id)
    if record is None or (record.user_id != current_user.id and not has_role(current_user, UserRole.ADMIN)):
        raise HTTPException(404, "反馈不存在")
    if index < 0 or index >= len(record.attachments):
        raise HTTPException(404, "附件不存在")
    attachment = record.attachments[index]
    path = UPLOAD_ROOT / attachment["storage_name"]
    if not path.is_file():
        raise HTTPException(404, "附件文件不存在")
    return FileResponse(path, filename=attachment["name"], media_type="application/octet-stream")
