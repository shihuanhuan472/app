from datetime import datetime

import hashlib
import mimetypes
import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import (
    DocumentKnowledge,
    KnowledgeCategory,
    KnowledgeDocumentType,
    KnowledgeVersionAuditLog,
    KnowledgeVersionRelation,
    MachineModel,
    MachineModelAlias,
    MachineRevision,
    KnowledgeDocumentVersionFile,
    User,
)
from schemas import (
    KnowledgeDocumentMetadataUpdate,
    KnowledgeCategoryCreate,
    KnowledgeCategoryUpdate,
    KnowledgeVersionCreate,
    KnowledgeVersionDecision,
    KnowledgeVersionInvalidate,
    KnowledgeVersionPublish,
    KnowledgeVersionRollback,
    KnowledgeVersionUpdate,
    MachineModelInput,
    MachineRevisionInput,
    Result,
)
from services.knowledge_version_service import KnowledgeVersionError, KnowledgeVersionService
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.roles import PermissionCode, has_permission


router = APIRouter(prefix="/knowledge", tags=["知识库版本管理"])


def _require(user: User, *permissions: str):
    if not has_permission(user, *permissions):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "没有执行此版本操作的权限")


def _version_dict(item):
    return {
        "id": item.id,
        "document_id": item.document_id,
        "version": item.version_label,
        "hardware_version": item.hardware_version,
        "planned_publish_mode": item.planned_publish_mode,
        "planned_supersedes_version_ids": item.planned_supersedes_version_ids or [],
        "version_major": item.version_major,
        "version_minor": item.version_minor,
        "based_on_version_id": item.based_on_version_id,
        "workflow_status": item.workflow_status,
        "validity_status": item.validity_status,
        "change_category": item.change_category,
        "change_summary": item.change_summary,
        "change_reason": item.change_reason,
        "effective_from": item.effective_from,
        "effective_to": item.effective_to,
        "title": item.title_snapshot,
        "tag": item.tag_snapshot or [],
        "origin_file_name": item.origin_file_name,
        "origin_file_dir": item.origin_file_dir,
        "content_hash": item.content_hash,
        "index_status": item.index_status,
        "index_error": item.index_error,
        "indexed_at": item.indexed_at,
        "revision": item.revision,
        "created_by": item.created_by,
        "reviewed_by": item.reviewed_by,
        "published_by": item.published_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "reviewed_at": item.reviewed_at,
        "published_at": item.published_at,
    }


def _section_dict(item):
    return {
        "id": item.id, "stable_section_key": item.stable_section_key,
        "parent_section_key": item.parent_section_key, "section_index": item.section_index,
        "section_title": item.section_title, "section_type": item.section_type,
        "plain_text": item.plain_text, "image_urls": item.image_urls or [],
        "metadata": item.section_metadata or {}, "content_hash": item.content_hash,
    }


def _file_dict(item):
    return {
        "id": item.id, "file_role": item.file_role, "original_name": item.original_name,
        "storage_path": item.storage_path, "mime_type": item.mime_type,
        "file_size": item.file_size, "content_hash": item.content_hash,
    }


def _legacy_file_dict(version):
    if not version.origin_file_name or not version.origin_file_dir:
        return None
    return {
        "id": None, "file_role": "primary", "original_name": version.origin_file_name,
        "storage_path": version.origin_file_dir, "mime_type": mimetypes.guess_type(version.origin_file_name)[0],
        "file_size": None, "content_hash": version.content_hash, "legacy_fallback": True,
    }


def _scope_dict(item):
    return {column.name: getattr(item, column.name) for column in item.__table__.columns if column.name != "version_id"}


async def _call(operation):
    try:
        return await operation
    except KnowledgeVersionError as exc:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, str(exc))


@router.get("/document-types", summary="获取知识文档类型")
async def list_document_types(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    rows = (await db.execute(select(KnowledgeDocumentType).where(KnowledgeDocumentType.is_active.is_(True)).order_by(KnowledgeDocumentType.id))).scalars().all()
    return Result.success_with_data([{
        "id": row.id, "type_code": row.type_code, "type_name": row.type_name,
        "description": row.description, "requires_review": row.requires_review,
        "requires_applicability": row.requires_applicability,
    } for row in rows])


def _category_dict(row, document_count=0):
    return {
        "id": row.id, "name": row.name, "parent_id": row.parent_id,
        "full_path": row.full_path, "sort_order": row.sort_order,
        "is_active": row.is_active, "document_count": document_count,
    }


async def _category_path(db: AsyncSession, name: str, parent_id: int | None, exclude_id: int | None = None):
    clean_name = name.strip()
    if not clean_name or "/" in clean_name:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "分类名称不能为空且不能包含 / 字符")
    query = select(KnowledgeCategory).where(
        KnowledgeCategory.parent_id == parent_id,
        KnowledgeCategory.name == clean_name,
    )
    if exclude_id is not None:
        query = query.where(KnowledgeCategory.id != exclude_id)
    if (await db.execute(query)).scalar_one_or_none():
        raise AppException(status.HTTP_409_CONFLICT, BizCode.DOC_REQUEST_INVALID, "同级分类名称已存在")
    if parent_id is None:
        return clean_name
    parent = (await db.execute(select(KnowledgeCategory).where(KnowledgeCategory.id == parent_id))).scalar_one_or_none()
    if not parent:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "父分类不存在")
    if not parent.is_active:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "不能在已停用分类下新增分类")
    return f"{parent.full_path} / {clean_name}"


@router.get("/categories", summary="获取知识分类树数据")
async def list_categories(include_inactive: bool = Query(False), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    query = select(KnowledgeCategory).order_by(KnowledgeCategory.sort_order, KnowledgeCategory.name)
    if not include_inactive:
        query = query.where(KnowledgeCategory.is_active.is_(True))
    rows = (await db.execute(query)).scalars().all()
    counts = dict((await db.execute(
        select(DocumentKnowledge.category_id, func.count(DocumentKnowledge.id))
        .where(DocumentKnowledge.category_id.is_not(None), DocumentKnowledge.is_deleted == 0)
        .group_by(DocumentKnowledge.category_id)
    )).all())
    return Result.success_with_data([_category_dict(row, counts.get(row.id, 0)) for row in rows])


@router.post("/categories", summary="新增知识分类")
async def create_category(request: KnowledgeCategoryCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    full_path = await _category_path(db, request.name, request.parent_id)
    row = KnowledgeCategory(
        name=request.name.strip(), parent_id=request.parent_id, full_path=full_path,
        sort_order=request.sort_order, is_active=True, created_time=datetime.now(), updated_time=datetime.now(),
    )
    db.add(row)
    await db.flush()
    return Result.success_with_data(_category_dict(row))


@router.put("/categories/{category_id}", summary="更新知识分类")
async def update_category(category_id: int, request: KnowledgeCategoryUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = (await db.execute(select(KnowledgeCategory).where(KnowledgeCategory.id == category_id).with_for_update())).scalar_one_or_none()
    if not row:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "分类不存在")
    values = request.model_dump(exclude_unset=True)
    next_parent_id = values.get("parent_id", row.parent_id)
    if next_parent_id == category_id:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "分类不能作为自己的父分类")
    all_rows = (await db.execute(select(KnowledgeCategory))).scalars().all()
    by_parent = {}
    for item in all_rows:
        by_parent.setdefault(item.parent_id, []).append(item)
    descendants, stack = set(), list(by_parent.get(category_id, []))
    while stack:
        item = stack.pop()
        descendants.add(item.id)
        stack.extend(by_parent.get(item.id, []))
    if next_parent_id in descendants:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "不能将分类移动到自己的子分类下")
    next_name = values.get("name", row.name).strip()
    old_path = row.full_path
    new_path = await _category_path(db, next_name, next_parent_id, exclude_id=category_id)
    row.name, row.parent_id, row.full_path = next_name, next_parent_id, new_path
    if "sort_order" in values:
        row.sort_order = values["sort_order"]
    if "is_active" in values:
        row.is_active = values["is_active"]
    row.updated_time = datetime.now()
    for item in all_rows:
        if item.id in descendants:
            item.full_path = new_path + item.full_path[len(old_path):]
            item.updated_time = datetime.now()
    documents = (await db.execute(select(DocumentKnowledge).where(
        (DocumentKnowledge.category_id == category_id) | (DocumentKnowledge.category_id.in_(descendants))
    ))).scalars().all()
    category_paths = {item.id: item.full_path for item in all_rows}
    category_paths[row.id] = row.full_path
    for document in documents:
        document.category_path = category_paths.get(document.category_id)
        document.updated_time = datetime.now()
    await db.flush()
    return Result.success_with_data(_category_dict(row))


@router.get("/documents/{document_id}/metadata", summary="获取知识文档稳定元数据")
async def get_metadata(document_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    document = await _call(KnowledgeVersionService(db).get_document(document_id))
    return Result.success_with_data({
        "id": document.id, "title": document.title, "document_code": document.document_code,
        "document_type": document.document_type, "category_id": document.category_id, "category_path": document.category_path,
        "lifecycle_status": document.lifecycle_status, "applicability_mode": document.applicability_mode,
        "default_version_id": document.default_version_id, "updated_time": document.updated_time,
    })


@router.put("/documents/{document_id}/metadata", summary="更新知识文档稳定元数据")
async def update_metadata(document_id: int, request: KnowledgeDocumentMetadataUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    document = await _call(KnowledgeVersionService(db).get_document(document_id, lock=True))
    values = request.model_dump(exclude_unset=True)
    if "category_id" in values:
        category = (await db.execute(select(KnowledgeCategory).where(
            KnowledgeCategory.id == values["category_id"], KnowledgeCategory.is_active.is_(True)
        ))).scalar_one_or_none()
        if not category:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID, "请选择有效的分类目录")
        values["category_path"] = category.full_path
    for name, value in values.items():
        setattr(document, name, value)
    document.updated_time = datetime.now()
    await db.flush()
    return Result.success_with_data({"id": document.id, **values})


@router.get("/documents/{document_id}/versions", summary="获取知识文档版本历史")
async def list_versions(document_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    rows = await _call(KnowledgeVersionService(db).list_versions(document_id))
    ids = [row.id for row in rows]
    from models import KnowledgeVersionApplicability
    scopes = (await db.execute(select(KnowledgeVersionApplicability).where(KnowledgeVersionApplicability.version_id.in_(ids)))).scalars().all()
    relations = (await db.execute(select(KnowledgeVersionRelation).where(
        KnowledgeVersionRelation.source_version_id.in_(ids), KnowledgeVersionRelation.relation_type == "supersedes"
    ))).scalars().all()
    return Result.success_with_data([{
        **_version_dict(row),
        "applicability": [_scope_dict(scope) for scope in scopes if scope.version_id == row.id],
        "supersedes_version_ids": [relation.source_version_id for relation in relations if relation.target_version_id == row.id],
        "superseded_by_version_ids": [relation.target_version_id for relation in relations if relation.source_version_id == row.id],
    } for row in rows])


@router.post("/documents/{document_id}/versions", summary="创建知识文档版本草稿")
async def create_version(document_id: int, request: KnowledgeVersionCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = await _call(KnowledgeVersionService(db).create_draft(document_id, request, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.get("/versions/{version_id}", summary="获取知识文档版本详情")
async def get_version(version_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    detail = await _call(KnowledgeVersionService(db).detail(version_id))
    files = [_file_dict(row) for row in detail["files"]]
    if not files:
        legacy_file = _legacy_file_dict(detail["version"])
        if legacy_file:
            files.append(legacy_file)
    return Result.success_with_data({
        **_version_dict(detail["version"]),
        "sections": [_section_dict(row) for row in detail["sections"]],
        "files": files,
        "applicability": [_scope_dict(row) for row in detail["applicability"]],
    })


@router.put("/versions/{version_id}", summary="编辑知识文档版本草稿")
async def update_version(version_id: int, request: KnowledgeVersionUpdate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = await _call(KnowledgeVersionService(db).update_draft(version_id, request, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/files", summary="上传版本文件快照")
async def upload_version_files(version_id: int, files: list[UploadFile] = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    service = KnowledgeVersionService(db)
    version = await _call(service.get_version(version_id, lock=True))
    if version.workflow_status not in {"draft", "rejected"}:
        raise AppException(status.HTTP_409_CONFLICT, BizCode.DOC_REQUEST_INVALID, "只有草稿或已驳回版本可以上传文件")
    root = Path(os.getenv("BASE_DIR", ".")) / "upload" / "knowledge" / str(version.document_id) / str(version.id)
    source_dir = root / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    result = []
    if files:
        # A newly uploaded batch represents the new snapshot. Its first file replaces
        # the primary inherited from the base version; historical files remain intact.
        await db.execute(delete(KnowledgeDocumentVersionFile).where(
            KnowledgeDocumentVersionFile.version_id == version.id,
            KnowledgeDocumentVersionFile.file_role == "primary",
        ))
    for index, upload in enumerate(files):
        original = Path(upload.filename or "file").name
        target = source_dir / f"{uuid.uuid4().hex}{Path(original).suffix.lower()}"
        digest = hashlib.sha256()
        size = 0
        async with aiofiles.open(target, "wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                await handle.write(chunk)
        storage_path = target.as_posix()
        row = KnowledgeDocumentVersionFile(
            version_id=version.id, file_role="primary" if index == 0 else "attachment",
            original_name=original, storage_path=storage_path,
            mime_type=upload.content_type or mimetypes.guess_type(original)[0], file_size=size,
            content_hash=digest.hexdigest(), created_at=datetime.now(),
        )
        db.add(row)
        await db.flush()
        result.append(_file_dict(row))
        if index == 0:
            from knowledge_parsers import knowledge_parser
            from utils.document_usage import document_model_to_thread
            from services.knowledge_version_service import _sha256
            parsed = await document_model_to_thread(knowledge_parser.parse, str(target),
                request_type="parse_knowledge", user_id=current_user.id)
            if not parsed or not parsed.sections:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.DOC_REQUEST_INVALID,
                    "主文件解析失败或未提取到章节，请重新上传")
            await service._replace_children(version, sections=parsed.sections)
            await db.flush()
            version.title_snapshot = parsed.title or version.title_snapshot
            parsed_sections = await service._source_sections(version.id)
            version.image_urls_snapshot = ', '.join(dict.fromkeys(
                url for section in parsed_sections for url in (section.image_urls or [])))
            version.content_hash = _sha256({"title": version.title_snapshot,
                "tags": version.tag_snapshot, "sections": [s.content_hash for s in parsed_sections]})
            version.index_status = "pending"
            version.origin_file_name = original
            version.origin_file_dir = storage_path
    version.revision += 1
    version.updated_at = datetime.now()
    await service._audit(version.document_id, version.id, "upload_files", current_user.id, {"files": [item["original_name"] for item in result]})
    return Result.success_with_data({"revision": version.revision, "files": result})


@router.post("/versions/{version_id}/submit", summary="提交知识文档版本审核")
async def submit_version(version_id: int, request: KnowledgeVersionDecision = KnowledgeVersionDecision(), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = await _call(KnowledgeVersionService(db).transition(version_id, "submit", current_user.id, request.comment))
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/approve", summary="审核通过知识文档版本")
async def approve_version(version_id: int, request: KnowledgeVersionDecision = KnowledgeVersionDecision(), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.REVIEW)
    row = await _call(KnowledgeVersionService(db).transition(version_id, "approve", current_user.id, request.comment))
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/reject", summary="驳回知识文档版本")
async def reject_version(version_id: int, request: KnowledgeVersionDecision, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.REVIEW)
    row = await _call(KnowledgeVersionService(db).transition(version_id, "reject", current_user.id, request.comment))
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/withdraw", summary="撤回知识文档版本")
async def withdraw_version(version_id: int, request: KnowledgeVersionDecision = KnowledgeVersionDecision(), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = await _call(KnowledgeVersionService(db).transition(version_id, "withdraw", current_user.id, request.comment))
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/publish", summary="发布知识文档版本")
async def publish_version(version_id: int, request: KnowledgeVersionPublish, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.ADMIN)
    row = await _call(KnowledgeVersionService(db).publish(version_id, request, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/invalidate", summary="作废知识文档版本")
async def invalidate_version(version_id: int, request: KnowledgeVersionInvalidate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.ADMIN)
    row = await _call(KnowledgeVersionService(db).invalidate(version_id, request.reason, current_user.id, request.new_default_version_id))
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/set-default", summary="设为知识文档默认展示版本")
async def set_default_version(version_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.ADMIN)
    row = await _call(KnowledgeVersionService(db).set_default(version_id, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.post("/versions/{version_id}/reindex", summary="重建知识文档版本索引")
async def reindex_version(version_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.ADMIN)
    row = await _call(KnowledgeVersionService(db).reindex(version_id, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.post("/documents/{document_id}/rollback", summary="基于历史内容创建回滚草稿")
async def rollback_version(document_id: int, request: KnowledgeVersionRollback, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = await _call(KnowledgeVersionService(db).rollback(document_id, request, current_user.id))
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.get("/documents/{document_id}/audit-logs", summary="获取知识文档版本审计日志")
async def audit_logs(document_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    rows = (await db.execute(select(KnowledgeVersionAuditLog).where(KnowledgeVersionAuditLog.document_id == document_id).order_by(KnowledgeVersionAuditLog.id.desc()))).scalars().all()
    return Result.success_with_data([{
        "id": row.id, "version_id": row.version_id, "action": row.action,
        "operator_id": row.operator_id, "payload": row.payload_json or {}, "created_at": row.created_at,
    } for row in rows])


@router.post("/documents/{document_id}/initialize-versioning", summary="将现有知识文档初始化为 v1.0")
async def initialize_versioning(document_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.ADMIN)
    row = await _call(KnowledgeVersionService(db).initialize_legacy_document(document_id, current_user.id))
    await KnowledgeVersionService(db).reindex(row.id, current_user.id)
    await db.flush()
    return Result.success_with_data(_version_dict(row))


@router.get("/machine-models", summary="获取设备型号及代次")
async def machine_models(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    models = (await db.execute(select(MachineModel).order_by(MachineModel.model_code))).scalars().all()
    aliases = (await db.execute(select(MachineModelAlias))).scalars().all()
    revisions = (await db.execute(select(MachineRevision))).scalars().all()
    alias_map, revision_map = {}, {}
    for item in aliases:
        alias_map.setdefault(item.machine_model_id, []).append(item.alias_name)
    for item in revisions:
        revision_map.setdefault(item.machine_model_id, []).append({
            "id": item.id, "revision_code": item.revision_code, "hardware_version": item.hardware_version,
            "software_version_min": item.software_version_min, "software_version_max": item.software_version_max,
            "serial_number_from": item.serial_number_from, "serial_number_to": item.serial_number_to,
        })
    return Result.success_with_data([{
        "id": item.id, "model_code": item.model_code, "model_name": item.model_name,
        "lifecycle_status": item.lifecycle_status, "aliases": alias_map.get(item.id, []),
        "revisions": revision_map.get(item.id, []),
    } for item in models])


@router.post("/machine-models", summary="新增设备型号")
async def create_machine_model(request: MachineModelInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    exists = (await db.execute(select(MachineModel.id).where(MachineModel.model_code == request.model_code))).scalar_one_or_none()
    if exists:
        raise AppException(status.HTTP_409_CONFLICT, BizCode.DOC_REQUEST_INVALID, "设备型号编码已存在")
    row = MachineModel(**request.model_dump(exclude={"aliases"}))
    db.add(row)
    await db.flush()
    for alias in dict.fromkeys(value.strip() for value in request.aliases if value.strip()):
        db.add(MachineModelAlias(machine_model_id=row.id, alias_name=alias))
    return Result.success_with_data({"id": row.id})


@router.put("/machine-models/{model_id}", summary="更新设备型号")
async def update_machine_model(model_id: int, request: MachineModelInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    row = (await db.execute(select(MachineModel).where(MachineModel.id == model_id).with_for_update())).scalar_one_or_none()
    if not row:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "设备型号不存在")
    for key, value in request.model_dump(exclude={"aliases"}).items():
        setattr(row, key, value)
    await db.execute(delete(MachineModelAlias).where(MachineModelAlias.machine_model_id == model_id))
    for alias in dict.fromkeys(value.strip() for value in request.aliases if value.strip()):
        db.add(MachineModelAlias(machine_model_id=model_id, alias_name=alias))
    return Result.success_with_data({"id": row.id})


@router.post("/machine-models/{model_id}/revisions", summary="新增设备代次")
async def create_machine_revision(model_id: int, request: MachineRevisionInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    _require(current_user, PermissionCode.READ_WRITE)
    if not (await db.execute(select(MachineModel.id).where(MachineModel.id == model_id))).scalar_one_or_none():
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "设备型号不存在")
    row = MachineRevision(machine_model_id=model_id, **request.model_dump())
    db.add(row)
    await db.flush()
    return Result.success_with_data({"id": row.id})
