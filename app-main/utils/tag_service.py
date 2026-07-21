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


def normalize_tag_values(tag) -> list:
    values = []
    seen = set()
    for item in _parse_tag_values(tag):
        tag_id = _to_int(item)
        value = tag_id if tag_id is not None else str(item or "").strip()
        if value == "" or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def get_document_library_type(document) -> str:
    return normalize_library_type(getattr(document, "library_type", "breakdown"))


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


async def resolve_tags(
    db: AsyncSession,
    values,
    created_by: Optional[int] = None,
) -> list[Tag]:
    tag_ids = normalize_tag_ids(values)
    tag_names = normalize_tag_names(values)
    tags_by_id = {}

    if tag_ids:
        result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
        tags_by_id = {tag.id: tag for tag in result.scalars().all()}

    tags_by_name = {tag.name: tag for tag in await ensure_tags(db, tag_names, created_by=created_by)}

    resolved = []
    seen = set()
    for item in _parse_tag_values(values):
        tag_id = _to_int(item)
        tag = tags_by_id.get(tag_id) if tag_id is not None else tags_by_name.get(str(item).strip())
        if tag and tag.id not in seen:
            seen.add(tag.id)
            resolved.append(tag)
    return resolved


async def set_document_tag_names(
    db: AsyncSession,
    document,
    names,
    created_by: Optional[int] = None,
) -> list[str]:
    """
    保留旧函数名，实际行为改为：
    - 接收 tag id 数组或 tag name 数组；
    - name 会自动创建/复用 Tag；
    - document.tag 存储 tag id 数组。
    """
    tags = await resolve_tags(db, names, created_by=created_by)
    if hasattr(document, "tag"):
        document.tag = [tag.id for tag in tags]
    await db.flush()
    return [tag.name for tag in tags]


async def get_document_tag_names(db: AsyncSession, document) -> list[str]:
    tag_ids = normalize_tag_ids(getattr(document, "tag", []))
    if tag_ids:
        result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
        tags = result.scalars().all()
        name_by_id = {tag.id: tag.name for tag in tags}
        return [name_by_id[tag_id] for tag_id in tag_ids if tag_id in name_by_id]

    # 兼容旧数据：如果 tag JSON 里还是名称，则直接返回名称。
    return normalize_tag_names(getattr(document, "tag", []))


async def get_document_fault_tag_names(db: AsyncSession, document) -> list[str]:
    """Resolve fault_tag IDs to tag names, same as get_document_tag_names but for fault_tag column."""
    fault_tag_ids = normalize_tag_ids(getattr(document, "fault_tag", []))
    if fault_tag_ids:
        result = await db.execute(select(Tag).where(Tag.id.in_(fault_tag_ids), Tag.is_deleted == 0))
        tags = result.scalars().all()
        name_by_id = {tag.id: tag.name for tag in tags}
        return [name_by_id[tid] for tid in fault_tag_ids if tid in name_by_id]
    return []


def _json_contains_tag_id(column, tag_id: int):
    return func.JSON_CONTAINS(column, func.JSON_ARRAY(tag_id)) == 1


def tag_filter_for_model(document_model, tags):
    values = _parse_tag_values(tags)
    if not values:
        return None

    tag_ids = normalize_tag_ids(values)
    tag_names = normalize_tag_names(values)
    conditions = []
    for tag_id in tag_ids:
        conditions.append(_json_contains_tag_id(document_model.tag, tag_id))

    if tag_names:
        conditions.append(
            exists(
                select(Tag.id).where(
                    Tag.is_deleted == 0,
                    Tag.name.in_(tag_names),
                    _json_contains_tag_id(document_model.tag, Tag.id),
                )
            )
        )
    return or_(*conditions) if conditions else None


def fault_tag_filter_for_model(document_model, tags):
    """Same as tag_filter_for_model but filters on fault_tag column."""
    values = _parse_tag_values(tags)
    if not values:
        return None

    tag_ids = normalize_tag_ids(values)
    tag_names = normalize_tag_names(values)
    conditions = []
    for tag_id in tag_ids:
        conditions.append(_json_contains_tag_id(document_model.fault_tag, tag_id))

    if tag_names:
        conditions.append(
            exists(
                select(Tag.id).where(
                    Tag.is_deleted == 0,
                    Tag.name.in_(tag_names),
                    _json_contains_tag_id(document_model.fault_tag, Tag.id),
                )
            )
        )
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
