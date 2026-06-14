from datetime import datetime
from typing import Iterable, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentKnowledge, KnowledgeDocumentSection


def _section_to_model(document_id: int, section, index: int) -> KnowledgeDocumentSection:
    return KnowledgeDocumentSection(
        document_id=document_id,
        document_library_type="knowledge",
        section_index=section.section_index if getattr(section, "section_index", None) is not None else index,
        section_title=getattr(section, "section_title", None),
        section_type=getattr(section, "section_type", None) or str(index + 1),
        plain_text=getattr(section, "plain_text", None),
        image_urls=getattr(section, "image_urls", None) or [],
        char_start=getattr(section, "char_start", None),
        char_end=getattr(section, "char_end", None),
        section_metadata=getattr(section, "metadata", None) or {},
        created_time=datetime.now(),
        updated_time=datetime.now(),
    )


async def replace_knowledge_document_sections(
    db: AsyncSession,
    document: DocumentKnowledge,
    sections: Iterable,
):
    await db.execute(delete(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document.id))
    section_models = []
    for index, section in enumerate(sections or []):
        section_model = _section_to_model(document.id, section, index)
        section_models.append(section_model)
        db.add(section_model)
    await db.flush()
    if hasattr(document, "section_ids"):
        document.section_ids = [section.id for section in section_models if section.id is not None]
        await db.flush()


async def get_knowledge_document_sections(db: AsyncSession, document_id: int) -> List[KnowledgeDocumentSection]:
    result = await db.execute(
        select(KnowledgeDocumentSection)
        .where(KnowledgeDocumentSection.document_id == document_id)
        .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
    )
    return list(result.scalars().all())
