import json
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentBreakdown, DocumentBreakdownTag, DocumentKnowledge, DocumentKnowledgeTag, Tag


DOCUMENT_TAG_LINK_MODELS = {
    "breakdown": DocumentBreakdownTag,
    "knowledge": DocumentKnowledgeTag,
}


def normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def normalize_tag_names(tag) -> list[str]:
    if not tag:
        return []
    if isinstance(tag, str):
        text = tag.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                tag = parsed
            else:
                tag = [text]
        except Exception:
            tag = text.replace("，", ",").split(",")

    seen = set()
    result = []
    for item in tag:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def get_document_library_type(document) -> str:
    return normalize_library_type(getattr(document, "library_type", "breakdown"))


def get_document_tag_link_model(document_or_library_type):
    if isinstance(document_or_library_type, str):
        library_type = normalize_library_type(document_or_library_type)
    else:
        library_type = get_document_library_type(document_or_library_type)
    return DOCUMENT_TAG_LINK_MODELS[library_type]


async def ensure_tags(
    db: AsyncSession,
    names,
    created_by: Optional[int] = None,
) -> list[Tag]:
    normalized_names = normalize_tag_names(names)
    if not normalized_names:
        return []

    result = await db.execute(select(Tag).where(Tag.name.in_(normalized_names)))
    existing_tags = {tag.name: tag for tag in result.scalars().all()}
    now = datetime.now()

    for name in normalized_names:
        tag = existing_tags.get(name)
        if tag:
            if tag.is_deleted:
                tag.is_deleted = 0
                tag.updated_time = now
            continue

        tag = Tag(
            name=name,
            color=None,
            description=None,
            is_deleted=0,
            created_by=created_by,
            created_time=now,
            updated_time=now,
        )
        db.add(tag)
        existing_tags[name] = tag

    await db.flush()
    return [existing_tags[name] for name in normalized_names]


async def set_document_tag_names(
    db: AsyncSession,
    document,
    names,
    created_by: Optional[int] = None,
) -> list[str]:
    normalized_names = normalize_tag_names(names)
    link_model = get_document_tag_link_model(document)

    await db.execute(delete(link_model).where(link_model.document_id == document.id))
    tags = await ensure_tags(db, normalized_names, created_by=created_by)
    now = datetime.now()
    for tag in tags:
        db.add(link_model(document_id=document.id, tag_id=tag.id, created_time=now))

    # Keep the legacy JSON column as a compatibility cache for old pages/tools.
    if hasattr(document, "tag"):
        document.tag = normalized_names
    await db.flush()
    return normalized_names


async def get_document_tag_names(db: AsyncSession, document) -> list[str]:
    link_model = get_document_tag_link_model(document)
    result = await db.execute(
        select(Tag.name)
        .join(link_model, link_model.tag_id == Tag.id)
        .where(
            link_model.document_id == document.id,
            Tag.is_deleted == 0,
        )
        .order_by(Tag.name.asc())
    )
    names = [row[0] for row in result.all()]
    if names:
        return names
    return normalize_tag_names(getattr(document, "tag", []))


def tag_filter_for_model(document_model, tags):
    normalized_tags = normalize_tag_names(tags)
    if not normalized_tags:
        return None
    link_model = get_document_tag_link_model(getattr(document_model, "library_type", "breakdown"))
    return document_model.id.in_(
        select(link_model.document_id)
        .join(Tag, link_model.tag_id == Tag.id)
        .where(
            Tag.is_deleted == 0,
            Tag.name.in_(normalized_tags),
        )
    )


def tag_keyword_filter_for_model(document_model, keyword: str):
    keyword = str(keyword or "").strip()
    if not keyword:
        return None
    link_model = get_document_tag_link_model(getattr(document_model, "library_type", "breakdown"))
    return document_model.id.in_(
        select(link_model.document_id)
        .join(Tag, link_model.tag_id == Tag.id)
        .where(
            Tag.is_deleted == 0,
            Tag.name.like(f"%{keyword}%"),
        )
    )


async def get_tag_document_count(db: AsyncSession, tag_id: int) -> int:
    total = 0
    for link_model in DOCUMENT_TAG_LINK_MODELS.values():
        result = await db.execute(
            select(func.count()).select_from(link_model).where(link_model.tag_id == tag_id)
        )
        total += int(result.scalar_one() or 0)
    return total


async def get_tag_document_counts(db: AsyncSession, tag_ids: list[int]) -> dict[int, int]:
    counts = {tag_id: 0 for tag_id in tag_ids}
    if not tag_ids:
        return counts

    for link_model in DOCUMENT_TAG_LINK_MODELS.values():
        result = await db.execute(
            select(link_model.tag_id, func.count())
            .where(link_model.tag_id.in_(tag_ids))
            .group_by(link_model.tag_id)
        )
        for tag_id, count in result.all():
            counts[int(tag_id)] = counts.get(int(tag_id), 0) + int(count or 0)
    return counts


async def remove_tag_from_documents(db: AsyncSession, tag_id: int) -> None:
    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = tag_result.scalar_one_or_none()
    tag_name = tag.name if tag else None
    document_models = {
        "breakdown": DocumentBreakdown,
        "knowledge": DocumentKnowledge,
    }

    for library_type, link_model in DOCUMENT_TAG_LINK_MODELS.items():
        document_ids_result = await db.execute(
            select(link_model.document_id).where(link_model.tag_id == tag_id)
        )
        document_ids = [row[0] for row in document_ids_result.all()]
        await db.execute(delete(link_model).where(link_model.tag_id == tag_id))
        if not tag_name or not document_ids:
            continue

        document_model = document_models[library_type]
        documents_result = await db.execute(select(document_model).where(document_model.id.in_(document_ids)))
        for document in documents_result.scalars().all():
            document.tag = [name for name in normalize_tag_names(getattr(document, "tag", [])) if name != tag_name]
