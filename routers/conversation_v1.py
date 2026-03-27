import base64
import json
import mimetypes
import os
from datetime import datetime
from openai import OpenAI, AsyncOpenAI
import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, status, Query
from qwen_token_counter import get_token_count
from sqlalchemy import desc, and_, asc, func
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse
from dependencies import get_current_active_user
from models import User, Message, Document
from models import Conversation
from schemas import ResultNew, ConversationCreateNew, ConversationDeleteRequest, MessageCreateNew
from database import get_db
from utils.VectorService import VectorService

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

def compress_image(image_path: str, max_size=512, pad_color=(0, 0, 0)):
    if not os.path.exists(image_path):
        raise FileNotFoundError()
    return image_path
    image = Image.open(image_path).convert("RGB")
    # new_size = (448, 448)
    max_length = max(image.width, image.height)
    # if short_length < max_size:
    #     return image_path
    # if max_length <= max_size:
    #     return image_path
    rate = max_size / max_length
    new_size = (int(image.width * rate), int(image.height * rate))
    resized_image = image.resize(new_size)

    new_image = Image.new("RGB", (max_size, max_size), pad_color)

    x = (max_size - new_size[0]) // 2
    y = (max_size - new_size[1]) // 2

    new_image.paste(resized_image, (x, y))

    # new_size = (512, 512)
    # print(new_size)

    dir_name, filename = os.path.split(image_path)
    name, ext = os.path.splitext(filename)
    new_path = f"{name}_compressed{ext}"
    new_path = os.path.join(dir_name, new_path)
    new_image.save(new_path)
    return new_path

def generate_messages(db, id, message_now, documents_id):
    """
    生成给ai发送的消息的，涵盖图片编码和上下文提取（不包含提示词生成）
    """
    print("generate_messages")
    message_order = max(message_now.message_order - 6, 0)
    messages_db = (db.query(Message).
                   filter(Message.session_id == id).
                   filter(Message.message_order > message_order).
                   order_by(Message.created_time).
                   all())
    messages = []
    config = get_image_config()
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    print("get_config")
    tokens = 0

    if messages_db:
        print("messages_db")
        for message in messages_db:
            data = {}
            role = "user" if message.role == 1 else "assistant"
            # print("role: ", role)
            # msg_text = message.content_text
            # if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
            #     images = message.user_uploaded_images.split(", ")
            #     data["images"] = [image_to_base64(image, config["MESSAGE_BASE_DIR"]) for image in images]
            msg_text = []
            msg_text.append({"type": "text", "text": message.content_text})

            tokens += get_token_count(message.content_text)

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
                    tokens += 258

                    # msg_text.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
            data["role"] = role
            data["content"] = msg_text
            messages.append(data)
            print(f"token: {tokens}")
            if tokens > tokens_max:
                raise HTTPException(status_code=500, detail="对话内容达到上限，请重新创建对话")
    # print("messages: ", messages)
    data = {}

    print(f"tokens: {tokens}")

    image_tokens = 0
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        image_tokens = len(message_now.user_uploaded_images.split(", ")) * 258
    tokens_tmp = tokens_max - tokens - image_tokens - get_token_count(message_now.content_text)

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
    # print("messages: ", messages)
    return messages

def get_new_title_by_ai(content):
    """
    让ai给我总结一个标题
    """
    # ai_url: str = os.getenv("AI_API")
    # model = os.getenv("MODEL")
    # message = [{"role": "user", "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求对话标题正式，简洁。内容：{content}"}]

    messages = [{"role": "user",
                 "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求对话标题正式，简洁。并且只给出标题，不要有任何多余内容。\n内容：{content}"}]

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
        new_title = "新对话"

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
        token_tmp = 0
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


async def stream_ai_response(id, messages: list, db: Session, session_id: int, doc_ids):
    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = AsyncOpenAI(base_url=f"http://{server_ip}:8000/v1", api_key=api_key)
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))

    response_data = {}
    data = {}
    data["id"] = id
    data["session_id"] = session_id
    if doc_ids and len(doc_ids) > 0:
        doc_aggs = []
        for doc_id in doc_ids:
            doc_title = db.query(Document.title).filter(Document.id == doc_id).scalar()
            doc_aggs.append({"doc_id": doc_id, "doc_name": doc_title})
        data["reference"] = {
            "total": len(doc_ids),
            "doc_aggs": doc_aggs
        }
    else:
        data["reference"] = {}

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
                if not full_content == "":
                    data["reference"] = {}
                content = chunk.choices[0].delta.content
                full_content += content
                data["answer"] = full_content
                response_data["code"] = 0
                response_data["data"] = data
                yield f"data: {json.dumps(response_data)}\n\n"

        # print(full_content)
        # 流结束，保存 AI 消息
        ai_msg = db.query(Message).filter(Message.id == id).first()
        if ai_msg:
            ai_msg.content_text = full_content
            db.commit()
        final_data = {"code": 0, "data": "true"}
        yield f"{json.dumps(final_data)}\n\n"
    except Exception as e:
        # error_msg = f"AI服务错误: {str(e)}"
        print(e)
        error_data = {
            "code": 102,
            "message": "回答失败"
        }
        yield f"data: {json.dumps(error_data)}\n\n"


def get_ai_answer(messages, db: Session, id):
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
        ai_msg.content_text = final_ans
        db.commit()

    return final_ans


@router.post("/{chat_id}/completions")
async def chat(message: MessageCreateNew,
               db: Session = Depends(get_db),
               current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        # 检查对话存在
        conversation = db.query(Conversation).filter(Conversation.id == message.session_id).first()
        if not conversation:
            return ResultNew.result(102, "请先新建对话！", None)
        max_order = (db.query(func.max(Message.message_order))
                     .filter(Message.session_id == message.session_id)
                     .scalar()) or 0

        # 检查图片已上传服务器
        if message.user_uploaded_images is not None:
            urls = [url.strip() for url in message.user_uploaded_images.split(", ") if url.strip()]
            base_url = os.path.join(config["MESSAGE_BASE_DIR"],
                                    config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

            for url in urls:
                url_check = os.path.basename(url)
                url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
                # print(url_check)
                if not os.path.exists(url_check):
                    print(url_check)
                    return ResultNew.result(102, "图片未上传", None)
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
            content_text=message.question,
            user_uploaded_images=message.user_uploaded_images,
            created_time=datetime.now()
        )

        db.add(db_message)
        db.commit()
        db.refresh(db_message)

        ai_reference_document_ids = get_reference_documents(db, db_message.content_text,
                                                            db_message.user_uploaded_images)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)

        messages = generate_messages(db, conversation.id, db_message, ai_reference_document_ids)

        conversation.updated_time = datetime.now()

        # 如果是该对话的首个消息，就为这个对话总结一个标题
        if conversation.title == "新对话":
            new_title = get_new_title_by_ai(message.question)
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
                stream_ai_response(ai_msg.id, messages, db, message.session_id, ai_reference_document_ids),
                media_type="text/event-stream"
            )
        else:
            answer = get_ai_answer(messages, db, ai_msg.id)
            return ResultNew.result(0, None, {
                "answer": answer,
                "reference": ai_reference_document_ids_str,
                "id": ai_msg.id,
                "session_id": message.session_id
            })

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(e)
        return ResultNew.result(102, "回答失败", None)