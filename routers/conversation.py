# routers/conversation.py
from datetime import datetime
from sqlalchemy import desc, and_, select, func, delete
from fastapi import APIRouter, Depends, status
from sqlalchemy import desc, and_
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from dependencies import get_current_active_user
from models import User, Message
from models import Conversation
from schemas import ConversationResponse, Result, Page
from database import get_db
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.pagination import build_pagination_payload
from agents.memory import MemoryService

router = APIRouter(prefix="/conversation", tags=["对话"])

@router.post("/create", summary="创建新对话")
async def create_conversation(db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_active_user)):
    conversation = Conversation()
    conversation.title = "新对话"
    conversation.user_id = current_user.id
    now = datetime.now()
    conversation.created_time = now
    conversation.updated_time = now
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    conversationResponse = ConversationResponse.from_orm(conversation)
    return Result.success_with_data(conversationResponse)

@router.get("/history", summary="获取对话历史")
async def get_history(db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_active_user)):
    try:
        # history = (db.query(Conversation)
        #            .filter(Conversation.user_id == current_user.id)
        #            .order_by(desc(Conversation.updated_time))
        #            .all())

        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == current_user.id)
            .order_by(desc(Conversation.updated_time))
        )
        history = result.scalars().all()

        history_response = [ConversationResponse.from_orm(history_tmp) for history_tmp in history]
        return Result.success_with_data(history_response)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")

@router.post("/history/page", summary="分页获取对话历史")
async def create_page(page: Page,
                      db: AsyncSession = Depends(get_db),
                      current_user: User = Depends(get_current_active_user)):
    try:
        offset = (page.page - 1) * page.size
        # total_count = db.query(Conversation).filter(Conversation.user_id == current_user.id).count()

        total_count_result = await db.execute(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == current_user.id)
        )
        total_count = total_count_result.scalar_one()

        # history = (db.query(Conversation)
        #              .filter(Conversation.user_id == current_user.id)
        #              .order_by(desc(Conversation.updated_time))
        #              .offset(offset)
        #              .limit(page.size)
        #              .all())

        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == current_user.id)
            .order_by(desc(Conversation.updated_time))
            .offset(offset)
            .limit(page.size)
        )
        history = result.scalars().all()

        history_data = [ConversationResponse.from_orm(history_tmp) for history_tmp in history]
        data = build_pagination_payload(total_count, page.page, page.size, history_data, "history")
        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")

@router.get("/get_by_id/{id}", summary="根据id获取对话")
async def get_conversation(id: int,
                           db: AsyncSession = Depends(get_db),
                           current_user: User = Depends(get_current_active_user)):
    try:
        # conversation = (db.query(Conversation)
        #                 .filter(and_(Conversation.id == id, Conversation.user_id == current_user.id))
        #                 .first())

        result = await db.execute(
            select(Conversation)
            .where(and_(Conversation.id == id, Conversation.user_id == current_user.id))
        )
        conversation = result.scalar_one_or_none()

        if conversation is not None:
            conversation = ConversationResponse.from_orm(conversation)
        return Result.success_with_data(conversation)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")

@router.put("/update_title", summary="更新对话标题")
async def update_title(id: int,
                       new_title: str,
                       db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    try:
        # conversation = db.query(Conversation).filter(Conversation.id == id).first()

        result = await db.execute(select(Conversation).where(Conversation.id == id))
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "对话不存在")

        if conversation.user_id != current_user.id:
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.CONVERSATION_FORBIDDEN, "您无权更新该对话标题")
        conversation.title = new_title
        await db.commit()
        await db.refresh(conversation)
        return Result.success_with_data(ConversationResponse.from_orm(conversation))
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试")

@router.delete("/delete", summary="删除对话")
async def delete_conversation(id: int,
                 db: AsyncSession = Depends(get_db),
                 current_user: User = Depends(get_current_active_user)):
    try:
        result = await db.execute(select(Conversation).where(Conversation.id == id))
        conversation = result.scalar_one_or_none()
        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "对话不存在")

        if conversation.user_id != current_user.id:
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.CONVERSATION_FORBIDDEN, "您无权删除此对话")

        await MemoryService(db).delete_session_runtime_state(id)
        await db.execute(delete(Message).where(Message.session_id == id))
        await db.execute(delete(Conversation).where(Conversation.id == id))
        await db.commit()
        print(f"对话{id}已删除")
        return Result.success()
    except AppException:
        raise
    except Exception as e:
        # 其他异常回滚
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"删除文档失败: {str(e)}")

@router.get("/query", summary="搜索对话历史")
async def query(data: str,
                db: AsyncSession = Depends(get_db),
                current_user: User = Depends(get_current_active_user)):
    try:
        result = await db.execute(
            select(Conversation)
            .where(and_(Conversation.user_id == current_user.id,
                        Conversation.title.like(f"%{data}%")))
            .order_by(desc(Conversation.updated_time))
        )
        conversations = result.scalars().all()
        # conversations = (db.query(Conversation)
        #                  .filter(and_(Conversation.user_id == current_user.id, Conversation.title.like(f"%{data}%")))
        #                  .order_by(desc(Conversation.updated_time))
        #                  .all())
        conversation_response = [ConversationResponse.from_orm(conversation) for conversation in conversations]
        return Result.success_with_data(conversation_response)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "查询失败")
