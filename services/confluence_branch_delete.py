"""Preview and permanently remove a local imported branch."""
import hashlib
import json
from pathlib import Path

from sqlalchemy import select, delete
from fastapi import HTTPException
from models import DocumentKnowledge, KnowledgeDocumentSection, SourceDocument, ParseTaskItem
from services.confluence_refresh import metadata, identity, in_subtree
from services.knowledge_document_delete import upload_path, purge_knowledge_document, cleanup_deleted_files


async def branch_plan(db, document_id):
    rows = (await db.execute(select(DocumentKnowledge.id, DocumentKnowledge.title,
        KnowledgeDocumentSection.section_metadata).join(KnowledgeDocumentSection,
        KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
        .where(DocumentKnowledge.is_deleted == 0))).all()
    root = next((metadata(m) for did, _, m in rows if did == document_id
                 and metadata(m).get('source_type') == 'confluence'), None)
    if not root or root.get('attachment_id'):
        raise HTTPException(400, '请选择网页页面进行分支删除')
    roots = {identity(root)}
    entries, ids, sources, paths = {}, set(), set(), set()
    def add(meta, title):
        key = (*identity(meta), str(meta.get('attachment_id') or ''))
        entries[key] = {'title': meta.get('attachment_title') or title,
                        'kind': 'attachment' if meta.get('attachment_id') else 'page',
                        'path': ' / '.join(meta.get('title_path') or [])}
    for did, title, value in rows:
        meta = metadata(value)
        if meta.get('source_type') == 'confluence' and in_subtree(meta, roots):
            ids.add(did); add(meta, title)
    all_sources = (await db.execute(select(SourceDocument).where(
        SourceDocument.document_library_type == 'knowledge', SourceDocument.is_deleted == 0))).scalars().all()
    for source in all_sources:
        if source.document_id in ids:
            sources.add(source.id)
            continue
        if source.document_id or '/confluence/' not in source.stored_file_path.replace('\\', '/'):
            continue
        path = upload_path(source.stored_file_path)
        if not path or not path.is_file():
            continue
        images = []
        try:
            if path.suffix.lower() == '.html':
                from bs4 import BeautifulSoup
                marker = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser').find(id='confluence-source-v1')
                if not marker: continue
                bundle = json.loads(marker.get_text())
                meta = bundle.get('source', {})
                images = [i.get('image_url') for i in bundle.get('images', [])]
            else:
                sidecar = Path(str(path) + '.source.json')
                if not sidecar.is_file(): continue
                meta = json.loads(sidecar.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            raise HTTPException(409, '来源文件无法读取，无法确认完整删除范围')
        if meta.get('source_type') == 'confluence' and in_subtree(meta, roots):
            sources.add(source.id); paths.add(str(path)); add(meta, source.origin_file_name)
            for image in images:
                local = upload_path(image)
                if local: paths.add(str(local))
    items = sorted(entries.values(), key=lambda e: (e['path'], e['kind'], e['title']))
    token = hashlib.sha256(json.dumps([sorted(ids), sorted(sources), items], sort_keys=True).encode()).hexdigest()
    return {'items': items, 'token': token, 'ids': ids, 'sources': sources, 'paths': paths}


async def delete_branch(db, document_id, token):
    plan = await branch_plan(db, document_id)
    if plan['token'] != token:
        raise HTTPException(409, '分支内容已变化，请关闭弹窗后重新确认删除范围')
    active = (await db.execute(select(ParseTaskItem.id).where(
        ParseTaskItem.source_document_id.in_(plan['sources']),
        ParseTaskItem.status.in_(['pending', 'running', 'processing', 'parsing'])))).first()
    if active:
        raise HTTPException(409, '分支中仍有解析任务，请等待结束后删除')
    from utils.VectorService import VectorService
    paths = plan['paths']
    for did in sorted(plan['ids']):
        doc = (await db.execute(select(DocumentKnowledge).where(DocumentKnowledge.id == did).with_for_update())).scalar_one()
        paths.update(await purge_knowledge_document(db, doc, VectorService(db)))
    await db.execute(delete(ParseTaskItem).where(ParseTaskItem.source_document_id.in_(plan['sources'])))
    await db.execute(delete(SourceDocument).where(SourceDocument.id.in_(plan['sources'])))
    for path in list(paths):
        for suffix in ('.raw.json', '.source.json'):
            sidecar = upload_path(path + suffix)
            if sidecar: paths.add(str(sidecar))
    await db.commit()
    await cleanup_deleted_files(paths)
