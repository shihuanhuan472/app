"""Plan replacement by stable page identity, independent of the import root."""
import json


RESOURCE_WARNING_PREFIXES = ('图片下载失败：', '图片未本地化：', '附件下载失败：')


def blocking_warnings(page):
    """Resource failures are reported but do not prevent subtree replacement."""
    return list(dict.fromkeys(str(w) for w in page.get('warnings', [])
                             if not str(w).startswith(RESOURCE_WARNING_PREFIXES)))


def metadata(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def identity(meta):
    return (str(meta.get('site', '')).rstrip('/'), str(meta.get('page_id', '')))


def in_subtree(meta, roots):
    site, page = identity(meta)
    return any((site, str(value)) in roots for value in [page, *(meta.get('ancestor_ids') or [])])


def plan_refresh(pages, old_rows):
    """Replace only successfully collected pages and their own attachments.

    Missing or failed descendants are never treated as deleted pages.
    """
    old = {}
    for document_id, value in old_rows:
        meta = metadata(value)
        if meta.get('source_type') != 'confluence':
            continue
        old.setdefault(identity(meta), []).append((document_id, meta))
    changed, unchanged = set(), set()
    for page in pages:
        source = page['source']
        key = identity(source)
        previous = [m for _, m in old.get(key, []) if not m.get('attachment_id')]
        if not previous:
            continue
        current_version = source.get('source_version')
        same = current_version is not None and all(
            str(m.get('source_version')) == str(current_version) for m in previous)
        # Children and attachment changes need not increment the parent's version.
        for m in previous:
            if 'child_pages' in m and m['child_pages'] != source.get('child_pages', []):
                same = False
            if 'attachment_versions' in m and m['attachment_versions'] != source.get('attachment_versions', []):
                same = False
        current_attachments = {str(a['id']): str(a['version']) for a in source.get('attachment_versions', [])}
        for _, m in old.get(key, []):
            if m.get('attachment_id') and current_attachments.get(str(m['attachment_id'])) != str(m.get('attachment_version')):
                same = False
        (unchanged if same else changed).add(key)
    purge = set()
    for rows in old.values():
        for document_id, meta in rows:
            if identity(meta) in changed:
                purge.add(document_id)
    unchanged = {identity(p['source']) for p in pages
                 if identity(p['source']) in unchanged}
    return changed, purge, unchanged


async def prepare_refresh(db, pages):
    from sqlalchemy import select, delete
    from models import DocumentKnowledge, KnowledgeDocumentSection, SourceDocument, ParseTaskItem
    from services.knowledge_document_delete import purge_knowledge_document, upload_path
    from utils.VectorService import VectorService

    rows = (await db.execute(select(DocumentKnowledge.id, KnowledgeDocumentSection.section_metadata)
        .join(KnowledgeDocumentSection, KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
        .where(DocumentKnowledge.is_deleted == 0))).all()
    all_sources = list((await db.execute(select(SourceDocument).where(
        SourceDocument.is_deleted == 0, SourceDocument.document_library_type == 'knowledge'))).scalars().all())
    orphan_metadata = {}
    orphan_images = {}
    for source in all_sources:
        if source.document_id or '/confluence/' not in source.stored_file_path.replace('\\', '/'):
            continue
        path = upload_path(source.stored_file_path)
        if not path or not path.is_file():
            continue
        if path.suffix.lower() == '.html':
            from bs4 import BeautifulSoup
            marker = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser').find(id='confluence-source-v1')
            if marker:
                bundle = json.loads(marker.string or marker.get_text())
                orphan_metadata[source.id] = bundle.get('source', {})
                orphan_images[source.id] = [image.get('image_url') for image in bundle.get('images', [])]
        else:
            from pathlib import Path
            sidecar = Path(str(path) + '.source.json')
            if sidecar.is_file():
                orphan_metadata[source.id] = json.loads(sidecar.read_text(encoding='utf-8'))
    changed, purge_ids, unchanged = plan_refresh(pages, rows)
    orphan_ids = {sid for sid, meta in orphan_metadata.items() if identity(meta) in changed}
    if changed and any(blocking_warnings(page) for page in pages):
        details = []
        for page in pages:
            source = page.get('source', {})
            title = ' / '.join(source.get('title_path') or []) or '未命名页面'
            warnings = blocking_warnings(page)
            if warnings:
                details.append(f"页面：{title}（ID：{source.get('page_id', '未知')}）")
                details.extend(f"  - {warning}" for warning in warnings)
        raise ValueError('采集存在警告，已停止替换旧子树，旧数据未删除。请解决以下问题后重试：\n'
                         + '\n'.join(details))
    if changed and any(not p['source'].get('attachments_included') for p in pages):
        raise ValueError('更新子树需要勾选“采集页面附件”，以便删除旧附件后重新导入。')

    # Do not remove files still in use by a parser.
    sources = [s for s in all_sources if s.document_id in purge_ids or s.id in orphan_ids]
    if any(s.status in ('parsing', 'pending', 'processing') for s in sources):
        raise ValueError('需要替换的子树仍有解析任务，请等待任务结束后重新导入。')
    paths = set()
    if sources:
        active = (await db.execute(select(ParseTaskItem.id).where(
            ParseTaskItem.source_document_id.in_([s.id for s in sources]),
            ParseTaskItem.status.in_(['pending', 'running', 'parsing', 'processing'])))).first()
        if active:
            raise ValueError('需要替换的子树仍有排队或运行中的解析任务，请等待任务结束后重试。')
    for document_id in sorted(purge_ids):
        document = (await db.execute(select(DocumentKnowledge).where(
            DocumentKnowledge.id == document_id).with_for_update())).scalar_one()
        paths.update(await purge_knowledge_document(db, document, VectorService(db)))
    for source in sources:
        if source.id in orphan_ids:
            path = upload_path(source.stored_file_path)
            if path:
                paths.add(str(path))
            for value in orphan_images.get(source.id, []):
                image = upload_path(value)
                if image:
                    paths.add(str(image))
    if orphan_ids:
        await db.execute(delete(ParseTaskItem).where(ParseTaskItem.source_document_id.in_(orphan_ids)))
        await db.execute(delete(SourceDocument).where(SourceDocument.id.in_(orphan_ids)))
    # Source sidecars are part of the deleted snapshot as well.
    for value in list(paths):
        for suffix in ('.source.json', '.raw.json'):
            sidecar = upload_path(value + suffix)
            if sidecar:
                paths.add(str(sidecar))
    reusable_attachments = set()
    for document_id, value in rows:
        meta = metadata(value)
        if document_id not in purge_ids and meta.get('attachment_id'):
            reusable_attachments.add((*identity(meta), str(meta['attachment_id']), str(meta.get('attachment_version'))))
    return unchanged, reusable_attachments, paths, len(purge_ids)
