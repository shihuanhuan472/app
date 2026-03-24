from datetime import datetime

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import desc, and_, asc
from sqlalchemy.orm import Session

from dependencies import get_current_active_user
from models import User, Message
from models import Conversation
from schemas import ResultNew, ConversationCreateNew, ConversationDeleteRequest
from database import get_db

router = APIRouter(prefix="/api/v1/chats", tags=["对话"])

@router.post("/{chat_id}/session", summary="创建聊天助手对话")
async def create_session(chat_id: str,
                         conversation_create: ConversationCreateNew,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        conversation = Conversation()
        conversation.title = conversation_create.name if conversation_create.name else "新对话"
        conversation.user_id = current_user.id
        now = datetime.now()
        conversation.created_time = now
        conversation.updated_time = now
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

        # conversation_response_new = ConversationResponseNew()
        # conversation_response_new.code = 0
        data = {
            "chat_id": chat_id,
            "create_date": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "create_time": now.timestamp(),
            "id": conversation.id,
            "messages": [],
            "name": conversation.title,
            "update_date": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "update_time": now.timestamp()
        }
        # conversation_response_new.data = data
        return ResultNew.result(0, None, data)
    except Exception as e:
        # conversation_response_new = ConversationResponseNew()
        # conversation_response_new.code = 102
        # conversation_response_new.message = "创建对话失败"
        print(e)
        db.rollback()
        return ResultNew.result(102, "创建对话失败", None)

@router.put("/{chat_id}/session/{session_id}")
async def update_session(chat_id: str, session_id: int,
                         conversation_create: ConversationCreateNew,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        conversation = db.query(Conversation).filter(Conversation.id == session_id).first()
        if not conversation:
            return ResultNew.error(102, "对话不存在", None)

        if conversation.user_id != current_user.id:
            return ResultNew.error(102, "您无权更新该对话标题", None)
        conversation.title = conversation_create.name
        db.commit()
        db.refresh(conversation)
        return ResultNew.result(0, None, None)
    except Exception as e:
        print(e)
        db.rollback()
        return ResultNew.result(102, "更新对话失败", None)

@router.get("/{chat_id}/sessions")
async def get_sessions(chat_id: str, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1),
                       order_by: str = Query("create_time"), desc: bool = Query(True), name: str = Query(None),
                       id: int = Query(None), user_id: str = Query(None), db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    try:
        offset = (page - 1) * page_size
        query = db.query(Conversation).filter(Conversation.user_id == current_user.id)
        if name is not None:
            query = query.filter(Conversation.title.like(f"%{name}%"))
        if id is not None:
            query = query.filter(Conversation.id == id)
        if order_by != "create_time" and order_by != "update_time":
            return ResultNew.result(102, "排序方式有误")

        order_field = Conversation.created_time if order_by == "create_time" else Conversation.update_time

        if desc:
            query = query.order_by(sqlalchemy.desc(order_field))
        else:
            query = query.order_by(asc(order_field))

        conversations = query.offset(offset).limit(page_size).all()
        data = []
        for conversation in conversations:
            messages = (db.query(Message).filter(Message.session_id == conversation.id)
                       .order_by(Message.created_time).all())
            message_data = []
            for message in messages:
                message_data.append({
                    "content": message.content_text,
                    "role": "user" if message.role == 1 else "assistant"
                })
            data_tmp = {
                "chat": chat_id,
                "create_date": conversation.created_time.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "create_time": conversation.created_time.timestamp(),
                "id": "4606b4ec87ad11efbc4f0242ac120006",
                "messages": message_data,
                "name": conversation.title,
                "update_date": conversation.updated_time.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "update_time": conversation.updated_time.timestamp()
            }
            data.append(data_tmp)
        return ResultNew.result(0, None, data)
    except Exception as e:
        print(e)
        return ResultNew.result(102, "查询对话失败", None)

@router.delete("/{chat_id}/sessions")
async def delete_session(chat_id: str, ids: ConversationDeleteRequest,
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        for id in ids.ids:
            print(id)
            conversation = db.query(Conversation).filter(Conversation.id == id).first()
            if not conversation:
                return ResultNew.result(102, f"对话不存在", None)

            if conversation.user_id != current_user.id:
                return ResultNew.result(102, "您无权删除此对话", None)

            db.query(Message).filter(Message.session_id == id).delete()

            db.delete(conversation)
            db.commit()
            print(f"对话{id}已删除")
        return ResultNew.result(0, None, None)
    except Exception as e:
        db.rollback()
        print(e)
        return ResultNew.result(102, "删除对话失败", None)