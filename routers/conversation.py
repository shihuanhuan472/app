# routers/conversation.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, and_
from sqlalchemy.orm import Session

from dependencies import get_current_active_user
from models import User
from models import Conversation
from schemas import ConversationResponse, Result, Page
from database import get_db

router = APIRouter(prefix="/conversation", tags=["对话"])

@router.post("/create", summary="创建新对话")
async def create_conversation(db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_active_user)):
    conversation = Conversation()
    conversation.title = "新对话"
    conversation.user_id = current_user.id
    now = datetime.now()
    conversation.created_time = now
    conversation.updated_time = now
    db.add(conversation)
    db.commit()
    db.refresh(conversation)



    conversationResponse = ConversationResponse.from_orm(conversation)
    return Result.success_with_data(conversationResponse)

@router.get("/history", summary="获取对话历史")
async def get_history(db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_active_user)):
    try:
        history = (db.query(Conversation)
                   .filter(Conversation.user_id == current_user.id)
                   .order_by(desc(Conversation.updated_time))
                   .all())
        history_response = [ConversationResponse.from_orm(history_tmp) for history_tmp in history]
        return Result.success_with_data(history_response)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )

@router.post("/history/page", summary="分页获取对话历史")
async def create_page(page: Page,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_active_user)):
    print("分页获取对话历史")
    try:
        offset = (page.page - 1) * page.size
        total_count = db.query(Conversation).filter(Conversation.user_id == current_user.id).count()
        history = (db.query(Conversation)
                     .filter(Conversation.user_id == current_user.id)
                     .order_by(desc(Conversation.updated_time))
                     .offset(offset)
                     .limit(page.size)
                     .all())
        total_pages = (total_count + page.size - 1) // page.size
        history_data = [ConversationResponse.from_orm(history_tmp) for history_tmp in history]
        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "history": history_data
        }
        return Result.success_with_data(data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )

@router.get("/get_by_id/{id}", summary="根据id获取对话")
async def get_conversation(id: int,
                           db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_active_user)):
    try:
        conversation = (db.query(Conversation)
                        .filter(and_(Conversation.id == id, Conversation.user_id == current_user.id))
                        .first())
        if conversation is not None:
            conversation = ConversationResponse.from_orm(conversation)
        return Result.success_with_data(conversation)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )

@router.put("/update_title", summary="更新对话标题")
async def update_title(id: int,
                       new_title: str,
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    try:
        conversation = db.query(Conversation).filter(Conversation.id == id).first()
        if not conversation:
            return Result.error(f"对话不存在")

        if conversation.user_id != current_user.id:
            return Result.error(f"您无权更新该对话标题")
        conversation.title = new_title
        db.commit()
        db.refresh(conversation)
        return Result.success_with_data(ConversationResponse.from_orm(conversation))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试"
        )

@router.delete("/delete", summary="删除对话")
async def delete(id: int,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_active_user)):
    try:
        conversation = db.query(Conversation).filter(Conversation.id == id).first()
        if not conversation:
            return Result.error(f"对话不存在")

        if conversation.user_id != current_user.id:
            return Result.error("您无权删除此对话")

        db.delete(conversation)
        db.commit()
        return Result.success()
    except HTTPException:
        raise
    except Exception as e:
        # 其他异常回滚
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除文档失败: {str(e)}"
        )

@router.get("/query", summary="搜索对话历史")
async def query(data: str,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_active_user)):
    try:
        conversations = (db.query(Conversation)
                         .filter(and_(Conversation.user_id == current_user.id, Conversation.title.like(f"%{data}%")))
                         .order_by(desc(Conversation.updated_time))
                         .all())
        conversation_response = [ConversationResponse.from_orm(conversation) for conversation in conversations]
        return Result.success_with_data(conversation_response)
    except Exception as e:
        return Result.error("查询失败")

# 创建对话
# @router.post("/", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
# def create_conversation(conversation: ConversationCreate, db: Session = Depends(get_db)):
#     db_conversation = Conversation(**conversation.dict())
#     db.add(db_conversation)
#     db.commit()
#     db.refresh(db_conversation)
#     return db_conversation
#
# # 获取用户的所有对话
# @router.get("/user/{user_id}", response_model=List[ConversationResponse])
# def get_user_conversations(user_id: int, db: Session = Depends(get_db)):
#     conversations = db.query(Conversation).filter(Conversation.user_id == user_id).all()
#     return conversations