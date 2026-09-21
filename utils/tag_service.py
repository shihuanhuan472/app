import json

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from models import DocumentBreakdown, DocumentKnowledge, Tag
from utils.app_exceptions import AppException
from utils.error_codes import BizCode


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


def get_document_library_type(document) -> str:
    return normalize_library_type(getattr(document, "library_type", "breakdown"))


async def validate_active_tag_ids(db: AsyncSession, values) -> list[int]:
    """Return canonical tag ids after checking them against active managed tags."""
    raw_values = _parse_tag_values(values)
    invalid_values = [value for value in raw_values if _to_int(value) is None]
    if invalid_values:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            BizCode.BAD_REQUEST,
            "标签必须使用标签管理中的数字 ID",
        )
    tag_ids = normalize_tag_ids(values)
    if not tag_ids:
        return []
    result = await db.execute(select(Tag.id).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
    active_ids = set(result.scalars().all())
    invalid_ids = [tag_id for tag_id in tag_ids if tag_id not in active_ids]
    if invalid_ids:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            BizCode.BAD_REQUEST,
            f"标签不存在或已停用: {', '.join(map(str, invalid_ids))}",
        )
    return [tag_id for tag_id in tag_ids if tag_id in active_ids]


async def set_document_tag_ids(db: AsyncSession, document, values) -> list[int]:
    """Store tag ids after checking them against current tag management."""
    resolved_ids = await validate_active_tag_ids(db, values)
    if hasattr(document, "tag"):
        document.tag = resolved_ids
    await db.flush()
    return resolved_ids


async def get_document_tag_names(db: AsyncSession, document) -> list[str]:
    tag_ids = normalize_tag_ids(getattr(document, "tag", []))
    if tag_ids:
        result = await db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.is_deleted == 0))
        tags = result.scalars().all()
        name_by_id = {tag.id: tag.name for tag in tags}
        return [name_by_id[tag_id] for tag_id in tag_ids if tag_id in name_by_id]

    return []


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
