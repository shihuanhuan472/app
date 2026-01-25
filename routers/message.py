# routers/message.py
import os
import uuid
import base64
from datetime import datetime
from pathlib import Path
import requests
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from typing import List
from schemas import Result
from models import Message, User, Conversation
from schemas import MessageCreate, MessageResponse
from database import get_db
from dependencies import get_current_active_user

router = APIRouter(prefix="/message", tags=["消息"])

def image_to_base64(image: str, dir: str = None):
    if dir is not None:
        image = os.path.join(dir, image)
    with open(image, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
        return image_base64

def get_image_config():
    MESSAGE_MAX_IMAGE_SIZE: int = int(os.getenv("MESSAGE_MAX_IMAGE_SIZE", 20 * 1024 * 1024))
    MESSAGE_IMAGE_DIR: str = os.getenv("MESSAGE_IMAGE_DIR", "upload/images")
    MESSAGE_BASE_DIR: str = os.getenv("MESSAGE_BASE_DIR", "/")
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    return {
        "MESSAGE_MAX_IMAGE_SIZE": MESSAGE_MAX_IMAGE_SIZE,
        "MESSAGE_IMAGE_DIR": MESSAGE_IMAGE_DIR,
        "MESSAGE_BASE_DIR": MESSAGE_BASE_DIR,
        "ALLOWED_EXTENSIONS": ALLOWED_EXTENSIONS
    }

@router.post("/upload_images", summary="上传图片")
async def upload_images(images: List[UploadFile]):
    config = get_image_config()
    url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))
    uploaded_images = []
    if not os.path.exists(url):
        os.makedirs(url)
        print(f"创建路径{url}")
    for image in images:
        try:
            if image.size > config["MESSAGE_MAX_IMAGE_SIZE"]:
                continue
            file_ext = Path(image.filename).suffix.lower()
            if file_ext not in config["ALLOWED_EXTENSIONS"]:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"
            save_path = Path(url) / unique_filename

            # 保存文件
            contents = await image.read()
            with open(save_path, "wb") as buffer:
                buffer.write(contents)

            # 构建文件信息
            relative_url = Path(config["MESSAGE_IMAGE_DIR"]) / unique_filename
            uploaded_images.append({
                "url": relative_url,
                # "relative_url": relative_url,
                "filename": unique_filename,
                "original_name": image.filename
            })
        except Exception as e:
            # 记录错误但继续处理其他文件
            print(f"文件 {image.filename} 上传失败: {str(e)}")

    return Result.success_with_data(uploaded_images)


@router.post("/ask", summary="提问以获得回答")
async def ask(message: MessageCreate,
              db: Session = Depends(get_db),
              current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        conversation = db.query(Conversation).filter(Conversation.id == message.session_id).first()
        if not conversation:
            return Result.error("请先新建对话！")
        max_order = (db.query(func.max(Message.message_order))
                     .filter(Message.session_id == message.session_id)
                     .scalar()) or 0

        if message.user_uploaded_images is not None:
            urls = [url.strip() for url in message.user_uploaded_images.split(", ") if url.strip()]
            base_url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

            for url in urls:
                url_check = os.path.basename(url)
                url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
                # print(url_check)
                if not os.path.exists(url_check):
                    print(url_check)
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                        detail="图片未上传")
        message.user_uploaded_images = (message.user_uploaded_images.replace("\\", "/")
                                        .replace(", /", ", ")
                                        .removeprefix("/")
                                        .removesuffix(", "))

        print(111)
        db_message = Message(
            session_id=message.session_id,
            role=1,
            message_order=max_order + 1,
            content_text=message.content_text,
            user_uploaded_images=message.user_uploaded_images,
            created_time=datetime.now()
        )
        print(222)
        # TODO: 得到ai的回答
        answer = get_answer(max_order + 1, message.session_id, db_message, db)
        db.add(db_message)
        db.flush()

        db.add(answer)
        db.flush()

        conversation.updated_time = datetime.now()
        db.commit()
        db.refresh(db_message)

        user_response = MessageResponse.from_orm(db_message)
        ai_response = MessageResponse.from_orm(answer)
        return Result.success_with_data([user_response, ai_response])
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试"
        )

def generate_messages(db, id, message_now):
    messages_db = (db.query(Message).
                   filter(Message.session_id == id).
                   order_by(Message.created_time).
                   all())
    messages = []
    config = get_image_config()

    if messages_db:
        print("messages_db")
        for message in messages_db:
            data = {}
            role = "user" if message.role == 1 else "system"
            print("role: ", role)
            msg_text = message.content_text
            if message.user_uploaded_images is not None:
                images = message.user_uploaded_images.split(", ")
                data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]

            data["role"] = role
            data["content"] = msg_text
            messages.append(data)
    print("messages:", messages)
    data = {}
    if message_now.user_uploaded_images is not None:
        images = message_now.user_uploaded_images.split(", ")
        data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
    data["role"] = "user"
    data["content"] = message_now.content_text

    messages.append(data)
    return messages

def get_ai_answer(db, session_id, message_now):
    messages = generate_messages(db, session_id, message_now)
    print(messages)
    ai_url: str = os.getenv("AI_API")
    model = os.getenv("MODEL")
    data = {"model": model, "messages": messages, "stream": False}
    result = requests.post(ai_url, json=data)
    print(result)
    return result.json()["message"]["content"]

def get_answer(message_order: int,
               session_id: int,
               message_now: Message,
               db):
    try:
        content_text = get_ai_answer(db, session_id, message_now)

        ai_reference_doc_ids = ""
        message = Message(
            session_id=session_id,
            role=0,
            message_order=message_order,
            content_text=content_text,
            ai_reference_doc_ids=ai_reference_doc_ids,
            created_time=datetime.now()
        )

        return message
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试"
        )

@router.get("/get_by_conversation", summary="获得某个对话的消息")
async def get_by_conversation(id: int,
                              db: Session = Depends(get_db),
                              current_user: User = Depends(get_current_active_user)):
    try:
        conversation = (db.query(Conversation)
                        .filter(and_(Conversation.id == id, Conversation.user_id == current_user.id))
                        .first())
        if not conversation:
            return Result.error("无该对话或无权限访问该对话")
        messages = (db.query(Message)
                    .filter(Message.session_id == id)
                    .order_by(Message.created_time)
                    .all())
        message_response = [MessageResponse.from_orm(message) for message in messages]
        return Result.success_with_data(message_response)
    except Exception as e:
        return Result.error("获取对话消息失败")

# 创建消息
# @router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
# def create_message(message: MessageCreate, db: Session = Depends(get_db)):
#     db_message = Message(**message.dict())
#     db.add(db_message)
#     db.commit()
#     db.refresh(db_message)
#     return db_message
#
# # 获取对话的所有消息
# @router.get("/conversation/{conversation_id}", response_model=List[MessageResponse])
# def get_conversation_messages(conversation_id: int, db: Session = Depends(get_db)):
#     messages = db.query(Message).filter(Message.session_id == conversation_id).order_by(Message.message_order).all()
#     return messages