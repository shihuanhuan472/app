import json
from datetime import datetime
from typing import Optional

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentBreakdown, DocumentKnowledge, Tag


DOCUMENT_MODELS = {
    "breakdown": DocumentBreakdown,
    "knowledge": DocumentKnowledge,
}


def normalize_tag_name(value) -> str:
    """Normalize a tag's display name without interpreting digits as an id."""
    return str(value or "").strip()


def normalize_match_aliases(values) -> list[str]:
    """Normalize aliases without treating numeric-looking values as tag ids."""
    aliases = []
    seen = set()
    for item in _parse_tag_values(values):
        value = str(item or "").strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        aliases.append(value)
    return aliases


async def get_active_tag_snapshot(db: AsyncSession) -> list[dict]:
    result = await db.execute(select(Tag).where(Tag.is_deleted == 0).order_by(Tag.id.asc()))
    return [
        {
            "id": int(tag.id),
            "name": tag.name,
            "description": tag.description or "",
            "aliases": normalize_match_aliases(tag.match_aliases),
        }
        for tag in result.scalars().all()
    ]


def normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _parse_tag_values(tag) -> list:
    if not tag:
        return []
    if isinstance(tag, str):
        text = tag.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            tag = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            tag = text.replace("，", ",").split(",")
    if not isinstance(tag, (list, tuple, set)):
        tag = [tag]
    result = []
    seen = set()
    for item in tag:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(item)
    return result


def _to_int(value):
    try:
        if isinstance(value, bool):
            return None
        text = str(value).strip()
        if not text or not text.isdigit():
            return None
        return int(text)
    except Exception:
        return None


def normalize_tag_ids(tag) -> list[int]:
    ids = []
    seen = set()
    for item in _parse_tag_values(tag):
        tag_id = _to_int(item)
        if tag_id is None or tag_id in seen:
            continue
        seen.add(tag_id)
        ids.append(tag_id)
    return ids


def normalize_tag_names(tag) -> list[str]:
    """
    兼容旧接口名称：用于标签创建/按名称输入时的规范化。
    文档表 tag 字段实际存储 tag id 数组，不再存名称数组。
    """
    names = []
    seen = set()
    for item in _parse_tag_values(tag):
        if _to_int(item) is not None:
            continue
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def get_document_library_type(document) -> str:
    return normalize_library_type(getattr(document, "library_type", "breakdown"))


async def set_document_tag_ids(db: AsyncSession, document, values) -> list[int]:
    """Store active tag ids on a document; tag creation belongs to tag management."""
    tag_ids = normalize_tag_ids(values)
    if not tag_ids:
        document.tag = []
        return []
    result = await db.execute(select(Tag.id).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
    active_ids = set(result.scalars().all())
    resolved_ids = [tag_id for tag_id in tag_ids if tag_id in active_ids]
    if hasattr(document, "tag"):
        document.tag = resolved_ids
    await db.flush()
    return resolved_ids


async def migrate_legacy_document_tag_names(
    db: AsyncSession, document, values, created_by: Optional[int] = None
) -> None:
    """One-time compatibility path for documents that stored tag names."""
    names = normalize_tag_names(values)
    if not names:
        return
    result = await db.execute(select(Tag).where(Tag.name.in_(names)))
    tags_by_name = {tag.name: tag for tag in result.scalars().all()}
    now = datetime.now()
    for name in names:
        tag = tags_by_name.get(name)
        if tag is None:
            tag = Tag(name=name, is_deleted=0, created_by=created_by, created_time=now, updated_time=now)
            db.add(tag)
            tags_by_name[name] = tag
        elif tag.is_deleted:
            tag.is_deleted = 0
            tag.updated_time = now
    await db.flush()
    document.tag = [tags_by_name[name].id for name in names]


async def get_document_tag_names(db: AsyncSession, document) -> list[str]:
    tag_ids = normalize_tag_ids(getattr(document, "tag", []))
    if tag_ids:
        result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
        tags = result.scalars().all()
        name_by_id = {tag.id: tag.name for tag in tags}
        return [name_by_id[tag_id] for tag_id in tag_ids if tag_id in name_by_id]

    # 兼容旧数据：如果 tag JSON 里还是名称，则直接返回名称。
    return normalize_tag_names(getattr(document, "tag", []))


def _json_contains_tag_id(column, tag_id: int):
    return func.JSON_CONTAINS(column, func.JSON_ARRAY(tag_id)) == 1


def tag_filter_for_model(document_model, tags):
    values = _parse_tag_values(tags)
    if not values:
        return None

    tag_ids = normalize_tag_ids(values)
    conditions = []
    for tag_id in tag_ids:
        conditions.append(_json_contains_tag_id(document_model.tag, tag_id))

    return or_(*conditions) if conditions else None


def tag_keyword_filter_for_model(document_model, keyword: str):
    keyword = str(keyword or "").strip()
    if not keyword:
        return None
    return exists(
        select(Tag.id).where(
            Tag.is_deleted == 0,
            Tag.name.like(f"%{keyword}%"),
            _json_contains_tag_id(document_model.tag, Tag.id),
        )
    )


async def get_tag_document_count(db: AsyncSession, tag_id: int) -> int:
    total = 0
    for document_model in DOCUMENT_MODELS.values():
        result = await db.execute(
            select(func.count()).select_from(document_model).where(
                document_model.is_deleted == 0,
                _json_contains_tag_id(document_model.tag, tag_id),
            )
        )
        total += int(result.scalar_one() or 0)
    return total


async def get_tag_document_counts(db: AsyncSession, tag_ids: list[int]) -> dict[int, int]:
    counts = {tag_id: 0 for tag_id in tag_ids}
    for tag_id in tag_ids:
        counts[tag_id] = await get_tag_document_count(db, tag_id)
    return counts


async def remove_tag_from_documents(db: AsyncSession, tag_id: int) -> None:
    for document_model in DOCUMENT_MODELS.values():
        result = await db.execute(
            select(document_model).where(_json_contains_tag_id(document_model.tag, tag_id))
        )
        for document in result.scalars().all():
            document.tag = [item for item in normalize_tag_ids(getattr(document, "tag", [])) if item != tag_id]
