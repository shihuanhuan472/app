"""Admin-only source ingestion. Publication uses existing knowledge parse/review endpoints."""
import asyncio
from uuid import UUID
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import SourceDocument, DocumentKnowledge, KnowledgeDocumentSection
from schemas import Result
from services.confluence_client import ConfluenceClient, ConfluenceError
from services.confluence_import_service import stage_page, discard_staged
from utils.file_classifier import get_document_category, normalize_uploaded_relative_filename
from utils.roles import UserRole, has_role

router = APIRouter(prefix="/confluence", tags=["Confluence 导入"])


@router.get("/directory", summary="浏览知识库中的 Confluence 来源目录")
async def source_directory(user=Depends(get_current_active_user), db: AsyncSession = Depends(get_db)):
    from services.confluence_directory import build_directory
    # Same read scope as the existing knowledge document list/detail endpoints.
    rows = (await db.execute(select(
        DocumentKnowledge.id, DocumentKnowledge.title,
        KnowledgeDocumentSection.id, KnowledgeDocumentSection.section_title,
        KnowledgeDocumentSection.section_index,
        KnowledgeDocumentSection.section_metadata,
    ).join(KnowledgeDocumentSection, KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
      .where(DocumentKnowledge.is_deleted == 0))).all()
    # Keep large section metadata out of MySQL filesort (error 1038).
    # Preserve the directory builder's document/section traversal order in Python.
    rows = sorted(rows, key=lambda row: (row[0], row[4]))
    return Result.success_with_data(build_directory(rows))


class CredentialsRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator('username')
    @classmethod
    def validate_username(cls, value):
        if not value.strip():
            raise ValueError('请输入 Confluence 用户名')
        return value.strip()

    def credentials(self):
        return dict(username=self.username, password=self.password.get_secret_value())


class TreeRequest(CredentialsRequest):
    root_url: str = Field(min_length=1, max_length=2048)
    max_pages: int = Field(default=500, ge=1, le=500)


@router.get("/parsed-attachments", summary="查询页面附件的已完成解析结果")
async def parsed_attachments(site: str, page_id: str, user=Depends(get_current_active_user),
                             db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(
        DocumentKnowledge.id, KnowledgeDocumentSection.section_metadata,
    ).join(KnowledgeDocumentSection, KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
      # Parsed sections can be viewed before vector indexing finishes.
      .where(DocumentKnowledge.is_deleted == 0,
             KnowledgeDocumentSection.section_metadata["site"].as_string() == site,
             KnowledgeDocumentSection.section_metadata["page_id"].as_string() == page_id)
      .order_by(DocumentKnowledge.id.desc()))).all()
    output, seen = [], set()
    for document_id, meta in rows:
        if not meta or not meta.get("attachment_id") or meta.get("source_type") != "confluence":
            continue
        key = str(meta["attachment_id"])
        if key in seen:
            continue
        seen.add(key)
        output.append({"document_id": document_id, "attachment_id": key,
                       "title": meta.get("attachment_title") or "附件"})
    return Result.success_with_data(output)


class StageRequest(CredentialsRequest):
    root_id: str = Field(pattern=r"^\d+$")
    page_id: str = Field(pattern=r"^\d+$")
    include_attachments: bool = False


class ImportRequest(CredentialsRequest):
    progress_id: UUID | None = None
    root_url: str = Field(min_length=1, max_length=2048)
    max_pages: int = Field(default=500, ge=1, le=500)
    include_attachments: bool = False


def require_importer(user=Depends(get_current_active_user)):
    if not has_role(user, UserRole.ADMIN):
        raise HTTPException(403, "Confluence 服务账号内容仅允许管理员导入；请确认目标知识库可见范围")
    return user


def fetch_tree(request, include_page_data=False):
    client = ConfluenceClient(**request.credentials())
    try:
        return client.tree(request.root_url, request.max_pages, include_page_data=include_page_data)
    finally:
        client.close()


@router.get('/branch-delete/{document_id}')
async def preview_branch_delete(document_id: int, user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    from services.confluence_branch_delete import branch_plan
    plan = await branch_plan(db, document_id)
    return Result.success_with_data({'items': plan['items'], 'token': plan['token']})


class BranchDeleteRequest(BaseModel):
    token: str = Field(min_length=64, max_length=64)


@router.post('/branch-delete/{document_id}')
async def remove_branch(document_id: int, request: BranchDeleteRequest,
                        user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    from services.confluence_branch_delete import delete_branch
    await delete_branch(db, document_id, request.token)
    return Result.success()


def fetch_page(request):
    client = ConfluenceClient(**request.credentials())
    try:
        return stage_page(client, request.root_id, request.page_id, request.include_attachments)
    finally:
        client.close()


def fetch_branch(request, progress=None):
    client = ConfluenceClient(**request.credentials())
    client.progress_callback = progress
    staged_pages = []
    try:
        snapshot = client.tree(request.root_url, request.max_pages, include_page_data=True, allow_partial=True)
        failures = list(snapshot.get('failures', []))
        for page in snapshot["pages"]:
            if progress:
                progress(phase='采集正文及附件', page_id=page['page_id'], title=page['title'],
                         completed=len(staged_pages), total=len(snapshot['pages']))
            try:
                result = stage_page(
                    client, snapshot["root_id"], page["page_id"], request.include_attachments,
                    page_data=page["page"], child_pages=page["child_pages"],
                )
                from services.confluence_refresh import blocking_warnings
                problems = blocking_warnings(result)
                if problems:
                    discard_staged(result['files'], result['image_paths'])
                    failures.append({'page_id': page['page_id'], 'title': page['title'],
                                     'error': '；'.join(problems), 'scope': 'page'})
                    continue
                staged_pages.append(result)
            except (ConfluenceError, ValueError) as exc:
                failures.append({'page_id': page['page_id'], 'title': page['title'],
                                 'error': str(exc), 'scope': 'page'})
                if any(code in str(exc) for code in ('403', '429', '401', '网络请求失败')):
                    failures.append({'page_id': page['page_id'], 'title': page['title'],
                                     'error': '连接或认证异常，本次剩余页面暂停采集；已完成页面继续入库', 'scope': 'remaining'})
                    break
        return {"root_id": snapshot["root_id"], "pages": staged_pages, "failures": failures}
    except Exception:
        discard_staged([item for result in staged_pages for item in result["files"]],
                       [image for result in staged_pages for image in result["image_paths"]])
        raise
    finally:
        client.close()


@router.post("/tree", summary="预览根页面及所有可见后代页面")
async def tree(request: TreeRequest, user=Depends(require_importer)):
    try:
        return Result.success_with_data(await asyncio.to_thread(fetch_tree, request))
    except ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/stage", summary="采集单页及附件到源文档库，随后使用现有解析流程")
async def stage(request: StageRequest, user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    user_id = user.id
    await db.rollback()  # Do not hold a transaction during network/image work.
    try:
        result = await asyncio.to_thread(fetch_page, request)
    except ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from None
    output, duplicates = [], []
    for item in result["files"]:
        ext = Path(item["path"]).suffix.lower()
        # Stable source names deduplicate repeat imports of the same snapshot.
        key = result["content_hash"][:16] + "_" + Path(item["path"]).stem
        name = normalize_uploaded_relative_filename(Path(item["name"]).stem)[:140] + "_" + key + ext
        existing = (await db.execute(select(SourceDocument).where(
            SourceDocument.origin_file_name == name, SourceDocument.is_deleted == 0))).scalars().first()
        if existing is None:
            existing = SourceDocument(origin_file_name=name, stored_file_path=item["path"],
                                      file_ext=ext, file_category=get_document_category(ext), file_size=item["size"],
                                      uploader_id=user_id, upload_time=datetime.now(), status="uploaded",
                                      document_library_type="knowledge", review_library_type="knowledge", is_deleted=0)
            db.add(existing)
            await db.flush()
        else:
            duplicates.append(item)
        output.append({"id": existing.id, "name": existing.origin_file_name,
                       "url": existing.stored_file_path, "size": existing.file_size, "status": existing.status})
    await db.commit()
    page_duplicate = any(item["path"].endswith(".html") for item in duplicates)
    await asyncio.to_thread(discard_staged, duplicates, result["image_paths"] if page_duplicate else [])
    return Result.success_with_data({"files": output, "source": result["source"], "warnings": result["warnings"], "notices": result.get("notices", [])})


@router.get('/import-progress/{token}')
async def import_progress(token: UUID, user=Depends(require_importer)):
    from services.confluence_progress import read_progress
    return Result.success_with_data(await asyncio.to_thread(read_progress, user.id, token))


@router.post('/import-jobs')
async def create_import_job(request: ImportRequest, user=Depends(require_importer)):
    from services.confluence_jobs import create_job, launch, read_job, public_job
    user_id = user.id
    try:
        token = await asyncio.to_thread(create_job, user_id, request.root_url, request.include_attachments, request.credentials())
    except ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from None
    launch(token, user_id, credentials=request.credentials())
    return Result.success_with_data(public_job(read_job(user_id, token)))


@router.get('/import-jobs/latest')
async def latest_import_job(user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    from services.confluence_jobs import read_job, public_job, launch
    job = read_job(user.id)
    if job and job['status'] == 'running':
        launch(job['id'], user.id)
    return Result.success_with_data(await current_job_files(public_job(job), db))


@router.get('/import-jobs/{token}')
async def get_import_job(token: UUID, user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    from services.confluence_jobs import read_job, public_job, launch
    job = read_job(user.id, token)
    if not job:
        raise HTTPException(404, '采集任务不存在')
    if job['status'] == 'running':
        launch(token, user.id)
    return Result.success_with_data(await current_job_files(public_job(job), db))


async def current_job_files(job, db):
    # Completed task snapshots must not re-add already parsed/deleted sources on refresh.
    if job and job['files']:
        rows = (await db.execute(select(SourceDocument.id, SourceDocument.status).where(
            SourceDocument.id.in_([f['id'] for f in job['files']]), SourceDocument.is_deleted == 0))).all()
        statuses = dict(rows)
        job['files'] = [{**f, 'status': statuses[f['id']]} for f in job['files'] if f['id'] in statuses]
    return job


@router.post('/import-jobs/{token}/resume')
async def resume_import_job(token: UUID, request: CredentialsRequest, user=Depends(require_importer)):
    from services.confluence_jobs import read_job, public_job, launch
    if not read_job(user.id, token):
        raise HTTPException(404, '采集任务不存在')
    launch(token, user.id, resume=True, credentials=request.credentials())
    return Result.success_with_data(public_job(read_job(user.id, token)))


@router.post("/import", summary="后台遍历并采集整个 Confluence 页面分支")
async def import_branch(request: ImportRequest, user=Depends(require_importer), db: AsyncSession = Depends(get_db)):
    user_id = user.id
    progress_state = {}
    def progress(**values):
        progress_state.update(values)
        if request.progress_id:
            from services.confluence_progress import write_progress
            write_progress(user_id, request.progress_id, progress_state)
    await db.rollback()
    try:
        branch = await asyncio.to_thread(fetch_branch, request, progress)
    except ConfluenceError as exc:
        raise HTTPException(400, str(exc)) from None

    return await persist_branch(db, user_id, branch, progress)


async def persist_branch(db, user_id, branch, progress=lambda **values: None):
    snapshot_paths = [item for page_result in branch["pages"] for item in page_result["files"]]
    progress(phase='正在整理入库', completed=len(branch['pages']), total=len(branch['pages']), url='')
    all_image_paths = [image for page_result in branch["pages"] for image in page_result["image_paths"]]

    outputs, duplicates = [], []
    deleted_paths = set()
    replaced_count = 0
    try:
        from services.confluence_refresh import prepare_refresh, identity
        unchanged, reusable_attachments, deleted_paths, replaced_count = await prepare_refresh(db, branch["pages"])
        for page_result in branch["pages"]:
            for item in page_result["files"]:
                ext = Path(item["path"]).suffix.lower()
                source = page_result["source"]
                page_key = identity(source)
                attachment_id = Path(item["path"]).stem
                attachment_version = next((str(a["version"]) for a in source.get("attachment_versions", [])
                                           if str(a["id"]) == attachment_id), None)
                if (ext == '.html' and page_key in unchanged) or (
                    ext != '.html' and (*page_key, attachment_id, attachment_version) in reusable_attachments
                ):
                    duplicates.append(item)
                    continue
                key = page_result["content_hash"][:16] + "_" + Path(item["path"]).stem
                name = normalize_uploaded_relative_filename(Path(item["name"]).stem)[:140] + "_" + key + ext
                existing = (await db.execute(select(SourceDocument).where(
                    SourceDocument.origin_file_name == name, SourceDocument.is_deleted == 0
                ))).scalars().first()
                if existing is None:
                    existing = SourceDocument(
                        origin_file_name=name, stored_file_path=item["path"], file_ext=ext,
                        file_category=get_document_category(ext), file_size=item["size"],
                        uploader_id=user_id, upload_time=datetime.now(), status="uploaded",
                        document_library_type="knowledge", review_library_type="knowledge", is_deleted=0,
                    )
                    db.add(existing)
                    await db.flush()
                else:
                    duplicates.append(item)
                outputs.append({"id": existing.id, "name": existing.origin_file_name,
                                "url": existing.stored_file_path, "size": existing.file_size,
                                "status": existing.status})
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await asyncio.to_thread(discard_staged, snapshot_paths, all_image_paths)
        if isinstance(exc, ValueError):
            raise HTTPException(409, str(exc)) from exc
        raise

    page_duplicate_paths = {item["path"] for item in duplicates if item["path"].endswith(".html")}
    duplicate_images = [image for result in branch["pages"]
                        if any(file["path"] in page_duplicate_paths for file in result["files"] if file["path"].endswith(".html"))
                        for image in result["image_paths"]]
    await asyncio.to_thread(discard_staged, duplicates, duplicate_images)
    from services.knowledge_document_delete import cleanup_deleted_files
    await cleanup_deleted_files(deleted_paths)
    warnings = [warning for result in branch["pages"] for warning in result["warnings"]]
    notices = [notice for result in branch["pages"] for notice in result.get("notices", [])]
    failures = branch.get('failures', [])
    notices.extend(f"页面：{item['title']}（ID：{item['page_id']}）未更新，旧数据保留：{item['error']}" for item in failures)
    return Result.success_with_data({"files": outputs, "page_count": len(branch["pages"]), "warnings": warnings, "notices": notices,
                                     "failures": failures, "replaced_documents": replaced_count, "skipped_files": len(duplicates)})
