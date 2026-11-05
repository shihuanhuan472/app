"""Keyword recall must carry section evidence, not just document titles."""
import json

from sqlalchemy import or_, select

from models import (DocumentKnowledge, KnowledgeDocumentSection,
                    KnowledgeDocumentVersion, KnowledgeDocumentVersionSection)
from .schemas import RetrievalResult


def section_evidence(section, terms):
    metadata = section.section_metadata or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    terms = [term.lower() for term in terms]
    chunks = []
    for result in (metadata.get("mindmap_parsing") or []) + (metadata.get("image_parsing") or []):
        if result.get("status") not in {"parsed", "needs_review"}:
            continue
        maps = result.get("chunks")
        if maps is None and result.get("tree"):
            from knowledge_parsers.mindmap_chunker import chunk_mindmap
            maps = chunk_mindmap(result["tree"], source_file=result.get("source_file", ""))
        for chunk in maps or []:
            text = str(chunk.get("text") or "")
            hits = sum(term in text.lower() for term in terms)
            if not hits:
                continue
            path = chunk.get("path") or []
            focus = str(path[-1] if path else "").lower()
            path_text = " ".join(path).lower()
            score = (.4 * hits / len(terms) + .3 * sum(t in focus for t in terms) / len(terms)
                     + .2 * sum(t in path_text for t in terms) / len(terms) + .1 / max(1, len(path)))
            chunks.append({"content": text, "score": score, "metadata": {
                **{k: v for k, v in chunk.items() if k != "text"}, "content_type": "mindmap"}})
    # Native trees already provide meaningful complete paths. Do not replace them
    # with the beginning of a very long section (often an unrelated branch).
    if not chunks:
        text = str(section.plain_text or "")
        lower = text.lower()
        positions = sorted({lower.find(t) for t in terms if t in lower})
        ends = -1
        for position in positions or ([0] if text else []):
            if position < ends:
                continue
            start = max(0, position - 250)
            ends = min(len(text), position + 1250)
            chunks.append({"content": text[start:ends], "score": .4,
                           "metadata": {"content_type": "section_text"}})
    for chunk in chunks:
        chunk["metadata"].update({"section_id": section.id,
            "section_title": section.section_title, "section_index": section.section_index,
            "evidence_source": "sql_keyword"})
    return sorted(chunks, key=lambda c: c["score"], reverse=True)[:8]


async def keyword_knowledge(db, terms, level, top_k, allowed_document_ids=None):
    results = []
    scope = [DocumentKnowledge.is_deleted == 0]
    if allowed_document_ids is not None:
        scope.append(DocumentKnowledge.id.in_(allowed_document_ids.get("knowledge", set())))
    for versioned in (False, True):
        section_model = KnowledgeDocumentVersionSection if versioned else KnowledgeDocumentSection
        title = KnowledgeDocumentVersion.title_snapshot if versioned else DocumentKnowledge.title
        conditions = [or_(title.ilike(f"%{term}%"), section_model.section_title.ilike(f"%{term}%"),
                          section_model.plain_text.ilike(f"%{term}%")) for term in terms]
        if versioned:
            stmt = (select(DocumentKnowledge, KnowledgeDocumentVersion)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.document_id == DocumentKnowledge.id)
                .outerjoin(section_model, section_model.version_id == KnowledgeDocumentVersion.id)
                .where(*scope, *conditions, KnowledgeDocumentVersion.workflow_status == "published",
                       KnowledgeDocumentVersion.validity_status.in_(["active", "legacy_active"]),
                       KnowledgeDocumentVersion.index_status == "indexed"))
        else:
            # A managed document must never fall back to an unversioned snapshot:
            # otherwise a draft/replaced version can leak into answer evidence.
            managed = select(KnowledgeDocumentVersion.id).where(
                KnowledgeDocumentVersion.document_id == DocumentKnowledge.id).exists()
            stmt = (select(DocumentKnowledge).outerjoin(section_model, section_model.document_id == DocumentKnowledge.id)
                    .where(*scope, *conditions, ~managed))
        rows = (await db.execute(stmt.distinct().order_by(DocumentKnowledge.id).limit(top_k))).all()
        if not rows:
            continue
        identifiers = [row[1].id if versioned else row[0].id for row in rows]
        key = section_model.version_id if versioned else section_model.document_id
        sections = (await db.execute(select(section_model).where(key.in_(identifiers))
                    .order_by(section_model.section_index, section_model.id))).scalars().all()
        grouped = {}
        for section in sections:
            group_id = section.version_id if versioned else section.document_id
            searchable = f"{section.section_title or ''}\n{section.plain_text or ''}".lower()
            if any(t.lower() in searchable for t in terms):
                grouped.setdefault(group_id, []).extend(section_evidence(section, terms))
        for row in rows:
            document, version = row[0], row[1] if versioned else None
            group_id = version.id if version else document.id
            chunks = sorted(grouped.get(group_id, []), key=lambda c: c["score"], reverse=True)[:8]
            doc_title = version.title_snapshot if version else document.title
            version_meta = {"version_id": version.id, "version": version.version_label,
                            "hardware_version": version.hardware_version} if version else {}
            for chunk in chunks:
                chunk.update({"doc_id": document.id, "library_type": "knowledge", "title": doc_title})
                chunk["metadata"].update(version_meta)
            content = "\n\n".join([str(doc_title or "")] + [c["content"] for c in chunks[:3]])
            matched = [t for t in terms if t.lower() in content.lower()]
            results.append(RetrievalResult(document_id=str(document.id), library_type="knowledge",
                title=doc_title or "", content=content, chunks=chunks, source=f"sql:{level}",
                score=float(len(matched)), base_score=float(len(matched)), matched_terms=matched,
                **version_meta))
    return results
