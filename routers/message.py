# routers/message.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from models import Message
from schemas import MessageCreate, MessageResponse
from database import get_db

router = APIRouter()

# 创建消息
@router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def create_message(message: MessageCreate, db: Session = Depends(get_db)):
    db_message = Message(**message.dict())
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    return db_message

# 获取对话的所有消息
@router.get("/conversation/{conversation_id}", response_model=List[MessageResponse])
def get_conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
    messages = db.query(Message).filter(Message.session_id == conversation_id).order_by(Message.message_order).all()
    return messages