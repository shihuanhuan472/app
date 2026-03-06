# routers/message.py
import os
import uuid
import base64
from openai import OpenAI
from datetime import datetime
from pathlib import Path
import requests
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from typing import List
from schemas import Result
from models import Message, User, Conversation, Document
from schemas import MessageCreate, MessageResponse
from database import get_db
from dependencies import get_current_active_user
from utils.VectorService import VectorService

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
    MESSAGE_BASE_DIR: str = os.getenv("MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
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
        print(1100)
        try:
            if image.size > config["MESSAGE_MAX_IMAGE_SIZE"]:
                continue
            file_ext = Path(image.filename).suffix.lower()
            if file_ext not in config["ALLOWED_EXTENSIONS"]:
                continue

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{uuid.uuid4().hex}{file_ext}"
            save_path = Path(url) / unique_filename
            print("save_path: ", save_path)
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

        # print(111)
        db_message = Message(
            session_id=message.session_id,
            role=1,
            message_order=max_order + 1,
            content_text=message.content_text,
            user_uploaded_images=message.user_uploaded_images,
            created_time=datetime.now()
        )
        print(222)

        answer = get_answer(max_order + 1, message.session_id, db_message, db)
        db.add(db_message)
        db.flush()

        db.add(answer)
        db.flush()

        conversation.updated_time = datetime.now()
        if max_order == 0:
            new_title = get_new_title_by_ai(message.content_text)
            conversation.title = new_title
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

def generate_messages(db, id, message_now, prompt):
    print("generate_messages")
    messages_db = (db.query(Message).
                   filter(Message.session_id == id).
                   order_by(Message.created_time).
                   all())
    messages = []
    config = get_image_config()
    print("get_config")
    if messages_db:
        print("messages_db")
        for message in messages_db:
            data = {}
            role = "user" if message.role == 1 else "system"
            # print("role: ", role)
            # msg_text = message.content_text
            # if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
            #     images = message.user_uploaded_images.split(", ")
            #     data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
            msg_text = []
            msg_text.append({"type": "text", "text": message.content_text})
            if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
                images = message.user_uploaded_images.split(", ")
                for image in images:
                    image_base64 = image_to_base64(image, config["MESSAGE_BASE_DIR"])
                    msg_text.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
            if role == "system":
                print("123")
            data["role"] = role
            data["content"] = msg_text
            messages.append(data)
    # print("messages: ", messages)
    data = {}
    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{message_now.content_text}"}]
    print(msg_content)
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        images = message_now.user_uploaded_images.split(", ")

        # images = message.user_uploaded_images.split(", ")
        for image in images:
            image_base64 = image_to_base64(image, config["MESSAGE_BASE_DIR"])
            msg_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})

        # print(images)
        # data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
    data["role"] = "user"
    # data["content"] = prompt + "\n问题：" + message_now.content_text
    data["content"] = msg_content

    messages.append(data)
    # print("messages: ", messages)
    return messages

def get_new_title_by_ai(content):
    # ai_url: str = os.getenv("AI_API")
    # model = os.getenv("MODEL")
    # message = [{"role": "user", "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求对话标题正式，简洁。内容：{content}"}]

    messages = [{"role": "user", "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求对话标题正式，简洁。并且只给出标题，不要有任何多余内容。\n内容：{content}"}]

    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=f"http://{server_ip}:8000/v1",
        api_key=api_key
    )
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-4B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_token
    )
    new_title = response.choices[0].message.content
    # data = {"model": model, "messages": message, "stream": False}
    # new_title = requests.post(ai_url, json=data).json()["message"]["content"]
    print("new_title: ", new_title)
    # print("message: ", message)
    if len(new_title) > 15 or len(new_title) == 0:
        new_title = "新标题"

    return new_title

def get_reference_documents(db, question: str, image: str = None):
    vector_service = VectorService(db)
    vector_service.batch_vectorize_existing_documents()
    documents = vector_service.search_similar_documents(question, image)
    # return documents
    document_ids = []
    for document in documents:
        document_ids.append(document["doc_id"])
    return document_ids
    # return ", ".join(document_ids) if len(document_ids) > 0 else None

def get_prompt(db, document_ids):
    if not document_ids:
        return ""
    prompts = []
    for i, document_id in enumerate(document_ids):
        document = db.query(Document).filter(Document.id == document_id).scalar()
        if not document:
            continue
        doc_prompt = f"""【文档{i}：】{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
        prompts.append(doc_prompt)

        # 添加指令
        if prompts:
            final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
            final_prompt += "\n---\n".join(prompts)
            final_prompt += "\n\n请参考上述文档，并结合你自己的知识库，回答用户的问题。"
            return final_prompt

        return ""

def get_ai_reference_document_ids_str(ai_reference_document_ids):
    if len(ai_reference_document_ids) == 0:
        return ""
    result = ", ".join(map(str, ai_reference_document_ids))
    return result

def get_ai_answer(db, session_id, message_now):
    ai_reference_document_ids = get_reference_documents(db, message_now.content_text, message_now.user_uploaded_images)
    ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)
    prompt = get_prompt(db, ai_reference_document_ids)
    messages = generate_messages(db, session_id, message_now, prompt)
    # print(messages)
    # ai_url: str = os.getenv("AI_API")
    # model = os.getenv("MODEL")
    # data = {"model": model, "messages": messages, "stream": False}
    # result = requests.post(ai_url, json=data)
    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=f"http://{server_ip}:8000/v1",
        api_key=api_key
    )
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_token
    )

    # print(response)
    # return result.json()["message"]["content"], ai_reference_document_ids_str
    return response.choices[0].message.content, ai_reference_document_ids_str


"""

import base64
from openai import OpenAI

SERVER_IP = "192.168.246.200"

client = OpenAI(
    base_url=f"http://{SERVER_IP}:8000/v1",
    api_key="EMPTY"
)

img_path = "D:/桌面/temp/test/Cat03.jpg"

# 读取本地图片并转base64
with open(img_path, "rb") as f:
    image_base64 = base64.b64encode(f.read()).decode("utf-8")

response = client.chat.completions.create(
    model="/models/Qwen3-VL-4B-Instruct",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请描述这张图片"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ],
        }
    ],
    max_tokens=7000
)

print(response.choices[0].message.content)

"""

def get_answer(message_order: int,
               session_id: int,
               message_now: Message,
               db):
    try:
        content_text, ai_reference_document_ids = get_ai_answer(db, session_id, message_now)

        message = Message(
            session_id=session_id,
            role=0,
            message_order=message_order,
            content_text=content_text,
            ai_reference_doc_ids=ai_reference_document_ids,
            created_time=datetime.now()
        )

        return message
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试，或尝试新建对话"
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


@router.get("/test_get_reference/{question}")
async def get_reference(question: str,
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_active_user)):
    documents = get_reference_documents(db, question)
    print(documents)
    return Result.success_with_data(documents)