"""Permanent document deletion shared by single, batch and reviewed deletions."""
import asyncio
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import delete, or_, select, update

from models import (DocumentKnowledge, DocumentBreakdown, Document_review,
    KnowledgeDocumentReview, KnowledgeDocumentSection, KnowledgeDocumentVersion,
    KnowledgeDocumentVersionSection, KnowledgeDocumentVersionFile,
    KnowledgeVersionApplicability, KnowledgeVersionRelation, KnowledgeVersionAuditLog,
    SourceDocument, ParseTaskItem)
from utils.file_cleanup import delete_image_with_variants


def upload_path(value):
    """Accept only local uploads; never delete arbitrary paths or remote URLs."""
    if not isinstance(value, str) or not value or '://' in value:
        return None
    base = Path(os.getenv('BASE_DIR', '.')).resolve()
    root = (base / 'upload').resolve()
    text = value.replace('\\', '/')
    if text.startswith('/upload/'):
        text = text.lstrip('/')
    path = Path(text)
    if not path.is_absolute():
        path = base / text.lstrip('/')
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path != root else None


def file_references(row):
    result = set()
    def add(value):
        if isinstance(value, list):
            for item in value:
                add(item)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, str):
            for part in value.split(', '):
                path = upload_path(part)
                if path:
                    result.add(path)
    for name in ('origin_file_dir', 'storage_path', 'stored_file_path', 'image_urls', 'image_urls_snapshot',
                 'image_urls_problem_intro', 'image_urls_causes', 'image_urls_evaluation',
                 'image_urls_inspection', 'image_urls_solutions', 'image_urls_key_points',
                 'sections', 'section_metadata'):
        add(getattr(row, name, None))
    return result


async def purge_knowledge_document(db, document, vector_service):
    """Delete relational data in the caller's transaction; return unshared files."""
    document_id = document.id
    versions = list((await db.execute(select(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.document_id == document_id).with_for_update())).scalars().all())
    ids = [v.id for v in versions]
    sections = list((await db.execute(select(KnowledgeDocumentSection).where(
        KnowledgeDocumentSection.document_id == document_id))).scalars().all())
    version_sections = list((await db.execute(select(KnowledgeDocumentVersionSection).where(
        KnowledgeDocumentVersionSection.version_id.in_(ids)))).scalars().all())
    files = list((await db.execute(select(KnowledgeDocumentVersionFile).where(
        KnowledgeDocumentVersionFile.version_id.in_(ids)))).scalars().all())
    reviews = list((await db.execute(select(KnowledgeDocumentReview).where(
        KnowledgeDocumentReview.document_id == document_id))).scalars().all())
    sources = list((await db.execute(select(SourceDocument).where(
        SourceDocument.document_id == document_id,
        SourceDocument.document_library_type == 'knowledge'))).scalars().all())
    source_ids = [s.id for s in sources]
    rows = [document, *versions, *sections, *version_sections, *files, *reviews, *sources]
    candidates = set().union(*(file_references(row) for row in rows))
    # Check actual references, including other libraries and unpublished drafts.
    owned = {(type(row), row.id) for row in rows}
    for model in (DocumentKnowledge, DocumentBreakdown, Document_review,
                  KnowledgeDocumentReview, KnowledgeDocumentVersion, KnowledgeDocumentSection,
                  KnowledgeDocumentVersionSection, KnowledgeDocumentVersionFile, SourceDocument):
        for row in (await db.execute(select(model))).scalars().all():
            if (model, row.id) not in owned:
                candidates.difference_update(file_references(row))

    # Explicit version deletion also covers vectors carrying version metadata.
    for version_id in ids:
        await vector_service.delete_knowledge_version_vectors(version_id)
    await vector_service.delete_document_from_vector_store(document_id, 'knowledge')
    await db.execute(delete(KnowledgeVersionRelation).where(or_(
        KnowledgeVersionRelation.source_version_id.in_(ids),
        KnowledgeVersionRelation.target_version_id.in_(ids))))
    await db.execute(delete(KnowledgeVersionAuditLog).where(or_(
        KnowledgeVersionAuditLog.document_id == document_id,
        KnowledgeVersionAuditLog.version_id.in_(ids))))
    for model in (KnowledgeVersionApplicability, KnowledgeDocumentVersionFile, KnowledgeDocumentVersionSection):
        await db.execute(delete(model).where(model.version_id.in_(ids)))
    # Break self-referential draft ancestry before deleting releases.
    await db.execute(update(KnowledgeDocumentVersion).where(
        KnowledgeDocumentVersion.based_on_version_id.in_(ids)).values(based_on_version_id=None))
    await db.execute(delete(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id.in_(ids)))
    await db.execute(delete(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document_id))
    await db.execute(delete(KnowledgeDocumentReview).where(KnowledgeDocumentReview.document_id == document_id))
    await db.execute(delete(ParseTaskItem).where(or_(
        (ParseTaskItem.document_id == document_id) & (ParseTaskItem.document_library_type == 'knowledge'),
        ParseTaskItem.source_document_id.in_(source_ids))))
    await db.execute(delete(SourceDocument).where(SourceDocument.id.in_(source_ids)))
    await db.execute(delete(DocumentKnowledge).where(DocumentKnowledge.id == document_id))
    return sorted(str(path) for path in candidates)


async def cleanup_deleted_files(paths):
    """Persist a retry manifest so a disk error never silently loses cleanup work."""
    if not paths:
        return
    directory = Path('.runtime/knowledge-deletions')
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f'{uuid.uuid4().hex}.json'
    manifest.write_text(json.dumps(paths, ensure_ascii=False), encoding='utf-8')
    for value in paths:
        path = upload_path(value)
        if path:
            await asyncio.to_thread(delete_image_with_variants, str(path))
    manifest.unlink()
