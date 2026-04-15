# routers/message.py
import json
import mimetypes
import os
import uuid
import base64
from PIL import Image
from openai import OpenAI, AsyncOpenAI
from datetime import datetime
from pathlib import Path
from qwen_token_counter import get_token_count
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
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/message", tags=["消息"])

"""
ai对话部分其实包含对话和消息，一个对话里有很多消息
前面的对话路由只是针对对话的增删改查
该路由为消息路由，是用户对话的具体操作
"""

def image_to_base64(image: str, dir: str = None):
    """
    将图片编码的，用于跟ai对话传输的
    """
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
    # 预设：消息中的图片存储于upload/ask
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


def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)):
    if not os.path.exists(image_path):
        raise FileNotFoundError()
    dir_name, filename = os.path.split(image_path)
    name, ext = os.path.splitext(filename)
    new_path = f"{name}_compressed_{max_size}{ext}"
    new_path = os.path.join(dir_name, new_path)

    if os.path.exists(new_path):
        return new_path

    image = Image.open(image_path).convert("RGB")
    # new_size = (448, 448)
    max_length = max(image.width, image.height)
    rate = max_size / max_length
    new_size = (int(image.width * rate), int(image.height * rate))
    resized_image = image.resize(new_size)

    new_image = Image.new("RGB", (max_size, max_size), pad_color)

    x = (max_size - new_size[0]) // 2
    y = (max_size - new_size[1]) // 2

    new_image.paste(resized_image, (x, y))

    new_image.save(new_path)
    return new_path

async def stream_ai_response(id, messages: list, db: Session, session_id: int, doc_ids, doc_ids_str):
    print("stream ai answer")
    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = AsyncOpenAI(base_url=f"http://{server_ip}:8000/v1", api_key=api_key)
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))

    # response_data = {}
    data = {}
    data["id"] = id
    data["session_id"] = session_id
    data["reference"] = None if doc_ids is None or len(doc_ids) == 0 else doc_ids_str

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token,
            stream=True
        )
        full_content = ""
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                full_content += content
                data["answer"] = full_content
                data["code"] = 1
                yield f"data: {json.dumps(data)}\n\n"
        # print(f"full_content: {full_content}")
        # 流结束，保存 AI 消息
        print(id)
        ai_msg = db.query(Message).filter(Message.id == id).first()
        if ai_msg:
            ai_msg.content_text = full_content
            db.commit()
        final_data = {"code": 1, "data": "true"}
        yield f"{json.dumps(final_data)}\n\n"
    except Exception as e:
        # error_msg = f"AI服务错误: {str(e)}"
        print(e)
        error_data = {
            "code": 0,
            "message": "回答失败"
        }
        yield f"data: {json.dumps(error_data)}\n\n"

@router.post("/ask", summary="提问以获得回答")
async def ask(message: MessageCreate,
              db: Session = Depends(get_db),
              current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        # 检查对话存在
        conversation = db.query(Conversation).filter(Conversation.id == message.session_id).first()
        if not conversation:
            return Result.error("请先新建对话！")
        max_order = (db.query(func.max(Message.message_order))
                     .filter(Message.session_id == message.session_id)
                     .scalar()) or 0

        # 检查图片已上传服务器
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

        # 创建用户的消息
        # print(111)
        db_message = Message(
            session_id=message.session_id,
            role=1,
            message_order=max_order + 1,
            content_text=message.content_text,
            user_uploaded_images=message.user_uploaded_images,
            created_time=datetime.now()
        )

        db.add(db_message)
        db.commit()
        db.refresh(db_message)

        ai_reference_document_ids = get_reference_documents(db, db_message.content_text,
                                                            db_message.user_uploaded_images)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)

        messages = generate_messages(db, db_message.session_id, db_message, ai_reference_document_ids)

        conversation.updated_time = datetime.now()

        if max_order == 0:
            new_title = get_new_title_by_ai(message.content_text)
            conversation.title = new_title

        db.commit()

        ai_msg = Message(
            session_id=message.session_id,
            role=0,
            message_order=max_order + 1,
            content_text="",
            ai_reference_doc_ids=ai_reference_document_ids_str,
            created_time=datetime.now()
        )
        db.add(ai_msg)
        db.commit()
        db.refresh(ai_msg)

        if message.stream:
            return StreamingResponse(
                stream_ai_response(ai_msg.id, messages, db, message.session_id,
                                   ai_reference_document_ids, ai_reference_document_ids_str),
                media_type="text/event-stream"
            )
        else:
            answer = get_ai_answer(db, messages, ai_msg.id)
            ai_response = MessageResponse.from_orm(answer)
            return Result.success_with_data(ai_response)


        # # 得到ai的回答
        # # answer = get_answer(max_order + 1, message.session_id, db_message, db)
        # # 如果是该对话的首个消息，就为这个对话总结一个标题
        #
        # db.commit()
        # db.refresh(db_message)
        #
        # user_response = MessageResponse.from_orm(db_message)
        #
        # return Result.success_with_data([user_response, ai_response])
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试"
        )

def generate_messages(db, id, message_now, documents_id):
    """
    生成给ai发送的消息的，涵盖图片编码和上下文提取（不包含提示词生成）
    """
    print("generate_messages")
    message_order = max(message_now.message_order - 6, 0)
    messages_db = (db.query(Message).
                   filter(Message.session_id == id).
                   filter(Message.message_order > message_order).
                   order_by(Message.created_time.desc()).
                   all())
    messages = []
    config = get_image_config()
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    print("get_config")
    tokens = 0

    user_question_tokens = get_token_count(message_now.content_text)
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        user_question_tokens += len(message_now.user_uploaded_images.split(",")) * 258

    tokens_max -= user_question_tokens

    if tokens_max < 0:
        raise HTTPException(500, "消息长度过长")

    flag = 0

    if messages_db:
        print("messages_db")
        for message in messages_db:
            if flag == 0:
                flag = 1
                continue
            data = {}
            token_tmp = 0
            role = "user" if message.role == 1 else "assistant"
            # print("role: ", role)
            # msg_text = message.content_text
            # if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
            #     images = message.user_uploaded_images.split(", ")
            #     data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
            msg_text = []
            msg_text.append({"type": "text", "text": message.content_text})

            # tokens += get_token_count(message.content_text)
            token_tmp += get_token_count(message.content_text)

            if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
                images = message.user_uploaded_images.split(", ")
                for image in images:

                    image_compressed = compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image))

                    # image_base64 = image_to_base64(image, config["MESSAGE_BASE_DIR"])

                    mime_type, _ = mimetypes.guess_type(image_compressed)
                    if mime_type is None:
                        ext = os.path.splitext(image_compressed)[1].lower()
                        mime_type = {
                            '.png': 'image/png',
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.webp': 'image/webp',
                            '.bmp': 'image/bmp'
                        }.get(ext, 'image/jpeg')
                    image_base64 = image_to_base64(image_compressed)
                    msg_text.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                    })
                    # tokens += 258
                    token_tmp += 258


                    # msg_text.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
            data["role"] = role
            data["content"] = msg_text

            if tokens + token_tmp >= tokens_max:
                break
            messages.append(data)
            tokens += token_tmp
            print(f"token: {tokens}")
            if tokens > tokens_max:
                raise HTTPException(status_code=500, detail="对话内容达到上限，请重新创建对话")
    # print("messages: ", messages)
    data = {}
    messages.reverse()
    print(f"tokens: {tokens}")

    tokens_tmp = tokens_max - tokens
    prompt = get_prompt(db, documents_id, tokens_tmp)


    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{message_now.content_text}"}]
    print(msg_content)
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        images = message_now.user_uploaded_images.split(", ")

        # images = message.user_uploaded_images.split(", ")
        for image in images:
            image_compressed = compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image))
            # image_base64 = image_to_base64(image_compressed)

            mime_type, _ = mimetypes.guess_type(image_compressed)
            if mime_type is None:
                ext = os.path.splitext(image_compressed)[1].lower()
                mime_type = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp'
                }.get(ext, 'image/jpeg')
            image_base64 = image_to_base64(image_compressed)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })


            # msg_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})

        # print(images)
        # data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
    data["role"] = "user"
    # data["content"] = prompt + "\n问题：" + message_now.content_text
    data["content"] = msg_content

    messages.append(data)
    print(len(messages))

    # print("messages: ", messages)
    return messages

def get_new_title_by_ai(content):
    """
    让ai给我总结一个标题
    """
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
    """
    检索出相关文档，并返回文档id
    """
    vector_service = VectorService(db)
    vector_service.batch_vectorize_existing_documents()
    documents = vector_service.search_similar_documents(question, image)
    # return documents
    document_ids = []
    for document in documents:
        document_ids.append(document["doc_id"])
    return document_ids
    # return ", ".join(document_ids) if len(document_ids) > 0 else None

def get_prompt(db, document_ids, max_tokens):
    """
    生成提示词（包括根据相关文档id，提取文档内容作为提示词）
    """
    if not document_ids:
        return ""
    tokens = 0
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
        token_tmp = get_token_count(doc_prompt)
        if tokens + token_tmp >= max_tokens:
            break
        tokens += token_tmp
        prompts.append(doc_prompt)

    # 添加指令
    if prompts:
        final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
        final_prompt += "\n---\n".join(prompts)
        final_prompt += "\n\n请参考上述文档，并结合你自己的知识库，回答用户的问题。"
        return final_prompt

    return ""

def get_ai_reference_document_ids_str(ai_reference_document_ids):
    """
    把相关文档id列表转为字符串
    为了存进mysql数据库
    """
    if len(ai_reference_document_ids) == 0:
        return ""
    result = ", ".join(map(str, ai_reference_document_ids))
    return result

# def get_ai_answer(db, session_id, message_now):
#     """
#     获取ai回答.
#     流程：获取相关文档id列表（并得到字符串版） -> 生成提示词 -> 生成消息
#           -> 消息丢给ai得到回答 -> 返回答案和相关文档id（字符串）
#     """
#     ai_reference_document_ids = get_reference_documents(db, message_now.content_text, message_now.user_uploaded_images)
#     ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)
#     # prompt = get_prompt(db, ai_reference_document_ids)
#     messages = generate_messages(db, session_id, message_now, ai_reference_document_ids)
#     # print(messages)
#     # ai_url: str = os.getenv("AI_API")
#     # model = os.getenv("MODEL")
#     # data = {"model": model, "messages": messages, "stream": False}
#     # result = requests.post(ai_url, json=data)
#     server_ip = os.getenv("SERVER_IP", "192.168.246.200")
#     api_key = os.getenv("API_KEY", "EMPTY")
#     client = OpenAI(
#         base_url=f"http://{server_ip}:8000/v1",
#         api_key=api_key
#     )
#     model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
#     max_token = int(os.getenv("MAX_TOKEN", 3000))
#     response = client.chat.completions.create(
#         model=model,
#         messages=messages,
#         max_tokens=max_token
#     )
#
#     final_ans = (response.choices[0].message.content
#                  .replace("\n---\n", "---")
#                  .replace("\n\n", "\n"))
#
#     # print(response)
#     # return result.json()["message"]["content"], ai_reference_document_ids_str
#     return final_ans, ai_reference_document_ids_str


def get_ai_answer(db, messages, id):
    """
    获取ai回答.
    流程：获取相关文档id列表（并得到字符串版） -> 生成提示词 -> 生成消息
          -> 消息丢给ai得到回答 -> 返回答案和相关文档id（字符串）
    """
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

    final_ans = (response.choices[0].message.content
                 .replace("\n---\n", "---")
                 .replace("\n\n", "\n"))

    ai_msg = db.query(Message).filter(Message.id == id).first()
    if ai_msg:
        ai_msg.text = final_ans
        db.commit()

    return ai_msg


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

# def get_answer(message_order: int,
#                session_id: int,
#                message_now: Message,
#                db):
#     try:
#         content_text, ai_reference_document_ids = get_ai_answer(db, session_id, message_now)
#
#         # 生成ai回答的消息
#         message = Message(
#             session_id=session_id,
#             role=0,
#             message_order=message_order,
#             content_text=content_text,
#             ai_reference_doc_ids=ai_reference_document_ids,
#             created_time=datetime.now()
#         )
#
#         return message
#     except HTTPException:
#         raise
#     except Exception as e:
#         print(e)
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="服务器内部错误，请稍后重试，或尝试新建对话"
#         )

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
    """
    一个拿来后端测试的api，前端并未调用，不用管
    """
    documents = get_reference_documents(db, question)
    print(documents)
    return Result.success_with_data(documents)