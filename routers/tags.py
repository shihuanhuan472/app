from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from models import Tag, User
from schemas import Result, TagCreate, TagQuery, TagResponse, TagUpdate
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.pagination import build_pagination_payload
from utils.roles import UserRole, has_role
from utils.tag_service import get_tag_document_counts, normalize_tag_names, remove_tag_from_documents

router = APIRouter(prefix="/tag", tags=["标签"])


def _require_tag_operator(user: User):
    if not (has_role(user, UserRole.ADMIN) or has_role(user, UserRole.TECHNICIAN)):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "仅技术人员或管理员可操作标签")


def _normalize_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    color = str(color).strip()
    if not color:
        return None
    if len(color) > 20:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "颜色值过长")
    return color


def _tag_to_response(tag: Tag, document_count: int = 0) -> TagResponse:
    return TagResponse(
        id=tag.id,
        name=tag.name,
        description=tag.description,
        color=tag.color,
        document_count=document_count,
        created_by=tag.created_by,
        created_time=tag.created_time,
        updated_time=tag.updated_time,
    )


async def _get_existing_tag_by_name(db: AsyncSession, name: str) -> Optional[Tag]:
    result = await db.execute(select(Tag).where(Tag.name == name))
    return result.scalar_one_or_none()


async def _get_active_tag_or_404(db: AsyncSession, tag_id: int) -> Tag:
    result = await db.execute(select(Tag).where(Tag.id == tag_id, Tag.is_deleted == 0))
    tag = result.scalar_one_or_none()
    if not tag:
        raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "标签不存在")
    return tag


@router.get("/list", summary="获取所有标签")
async def list_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_tag_operator(current_user)
    result = await db.execute(select(Tag).where(Tag.is_deleted == 0).order_by(Tag.name.asc()))
    tags = result.scalars().all()
    counts = await get_tag_document_counts(db, [tag.id for tag in tags])
    return Result.success_with_data([_tag_to_response(tag, counts.get(tag.id, 0)) for tag in tags])


@router.post("/page", summary="分页查询标签")
async def page_tags(
    query: TagQuery,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_tag_operator(current_user)
    page = max(int(query.page or 1), 1)
    size = max(int(query.size or 10), 1)
    keyword = str(query.data or "").strip()
    conditions = [Tag.is_deleted == 0]
    if keyword:
        conditions.append(or_(Tag.name.like(f"%{keyword}%"), Tag.description.like(f"%{keyword}%")))

    total_count_result = await db.execute(select(func.count()).select_from(Tag).where(*conditions))
    total_count = int(total_count_result.scalar_one() or 0)
    result = await db.execute(
        select(Tag)
        .where(*conditions)
        .order_by(Tag.updated_time.desc(), Tag.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    tags = result.scalars().all()
    counts = await get_tag_document_counts(db, [tag.id for tag in tags])
    data = build_pagination_payload(
        total_count,
        page,
        size,
        [_tag_to_response(tag, counts.get(tag.id, 0)) for tag in tags],
        "tags",
    )
    return Result.success_with_data(data)


@router.post("/add", summary="新增标签")
async def add_tag(
    payload: TagCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_tag_operator(current_user)
    names = normalize_tag_names([payload.name])
    if not names:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "标签名称不能为空")
    name = names[0]

    existing = await _get_existing_tag_by_name(db, name)
    now = datetime.now()
    if existing:
        if existing.is_deleted:
            existing.is_deleted = 0
            existing.description = payload.description
            existing.color = _normalize_color(payload.color)
            existing.updated_time = now
            await db.commit()
            await db.refresh(existing)
            return Result.success_with_data(_tag_to_response(existing, 0))
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "标签名称已存在")

    tag = Tag(
        name=name,
        description=payload.description,
        color=_normalize_color(payload.color),
        is_deleted=0,
        created_by=current_user.id,
        created_time=now,
        updated_time=now,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return Result.success_with_data(_tag_to_response(tag, 0))


@router.patch("/update", summary="更新标签")
async def update_tag(
    payload: TagUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_tag_operator(current_user)
    tag = await _get_active_tag_or_404(db, payload.id)

    if payload.name is not None:
        names = normalize_tag_names([payload.name])
        if not names:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "标签名称不能为空")
        new_name = names[0]
        if new_name != tag.name:
            existing = await _get_existing_tag_by_name(db, new_name)
            if existing and existing.id != tag.id:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "标签名称已存在")
            tag.name = new_name

    if payload.description is not None:
        tag.description = payload.description
    if payload.color is not None:
        tag.color = _normalize_color(payload.color)

    tag.updated_time = datetime.now()
    await db.commit()
    await db.refresh(tag)
    counts = await get_tag_document_counts(db, [tag.id])
    return Result.success_with_data(_tag_to_response(tag, counts.get(tag.id, 0)))


@router.delete("/delete/{tag_id}", summary="删除标签")
async def delete_tag(
    tag_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _require_tag_operator(current_user)
    tag = await _get_active_tag_or_404(db, tag_id)
    await remove_tag_from_documents(db, tag.id)
    tag.is_deleted = 1
    tag.updated_time = datetime.now()
    await db.commit()
    return Result.success()
