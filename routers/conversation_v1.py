import asyncio
import base64
import json
import mimetypes
import os
from openai import OpenAI, AsyncOpenAI
from qwen_token_counter import get_token_count
# from sqlalchemy import desc, and_, asc, func
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse
from models import User, Message, Document, DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection
from schemas import ResultNew, ConversationCreateNew, ConversationDeleteRequest, MessageCreateNew
from utils.VectorService import VectorService
from PIL import Image
from datetime import datetime
from fastapi import APIRouter, Depends, status, Query
# from sqlalchemy import desc, and_
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from dependencies import get_current_active_user
from models import User, Message, Conversation
from schemas import ConversationResponse, Result, Page
from database import get_db, AsyncSessionLocal
from sqlalchemy import select, func, asc, desc as desc_func, delete
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.ai_endpoint import get_ai_base_url
from utils.pagination import build_pagination_payload

router = APIRouter(prefix="/api/v1/chats", tags=["对话"])

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}


def _normalize_library_type(library_type: str) -> str:
    """统一向量检索返回的库类型，确保旧对话接口也能回查正确表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """根据库类型选择旧对话接口读取的文档表。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]

@router.post("/{chat_id}/session", summary="创建聊天助手对话")
async def create_session(chat_id: str,
                         conversation_create: ConversationCreateNew,
                         db: AsyncSession = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        conversation = Conversation()
        conversation.title = conversation_create.name if conversation_create.name else "新对话"
        conversation.user_id = current_user.id
        now = datetime.now()
        conversation.created_time = now
        conversation.updated_time = now
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)

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
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "创建对话失败")

@router.put("/{chat_id}/session/{session_id}")
async def update_session(chat_id: str, session_id: int,
                         conversation_create: ConversationCreateNew,
                         db: AsyncSession = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        # conversation = db.query(Conversation).filter(Conversation.id == session_id).first()

        result = await db.execute(select(Conversation).where(Conversation.id == session_id))
        conversation = result.scalar_one_or_none()

        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "对话不存在")

        if conversation.user_id != current_user.id:
            raise AppException(status.HTTP_403_FORBIDDEN, BizCode.CONVERSATION_FORBIDDEN, "您无权更新该对话标题")
        conversation.title = conversation_create.name
        await db.commit()
        await db.refresh(conversation)
        return ResultNew.result(0, None, None)
    except Exception as e:
        print(e)
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "更新对话失败")

@router.get("/{chat_id}/sessions")
async def get_sessions(chat_id: str, page: int = Query(1, ge=1), page_size: int = Query(30, ge=1),
                       order_by: str = Query("create_time"), desc: bool = Query(True), name: str = Query(None),
                       id: int = Query(None), user_id: str = Query(None), db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_active_user)):
    try:
        offset = (page - 1) * page_size

        conditions = [Conversation.user_id == current_user.id]

        if name is not None:
            conditions.append(Conversation.title.like(f"%{name}%"))
        if id is not None:
            conditions.append(Conversation.id == id)
        if order_by != "create_time" and order_by != "update_time":
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "排序方式有误")

        order_field = Conversation.created_time if order_by == "create_time" else Conversation.update_time
        order_clause = desc_func(order_field) if desc else asc(order_field)

        stmt = select(Conversation).where(*conditions).order_by(order_clause).offset(offset).limit(page_size)
        result = await db.execute(stmt)
        conversations = result.scalars().all()

        total_count_result = await db.execute(
            select(func.count()).select_from(Conversation).where(*conditions)
        )
        total_count = total_count_result.scalar_one()

        data = []
        for conversation in conversations:
            # messages = (db.query(Message).filter(Message.session_id == conversation.id)
            #            .order_by(Message.created_time).all())

            msg_stmt = select(Message).where(Message.session_id == conversation.id).order_by(Message.created_time)
            msg_result = await db.execute(msg_stmt)
            messages = msg_result.scalars().all()

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
        return ResultNew.result(0, None, build_pagination_payload(total_count, page, page_size, data, "sessions"))
    except Exception as e:
        print(e)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "查询对话失败")

@router.delete("/{chat_id}/sessions")
async def delete_session(chat_id: str, ids: ConversationDeleteRequest,
                         db: AsyncSession = Depends(get_db),
                         current_user: User = Depends(get_current_active_user)):
    try:
        error_session_ids = []
        for id in ids.ids:
            print(id)

            result = await db.execute(select(Conversation).where(Conversation.id == id))
            conversation = result.scalar_one_or_none()

            # conversation = db.query(Conversation).filter(Conversation.id == id).first()
            if not conversation:
                continue
                # return ResultNew.result(102, f"对话不存在", None)

            if conversation.user_id != current_user.id:
                error_session_ids.append(id)
                # return ResultNew.result(102, "您无权删除此对话", None)

            await db.execute(delete(Message).where(Message.session_id == id))
            await db.execute(delete(Conversation).where(Conversation.id == id))
            await db.commit()

            print(f"对话{id}已删除")
        msg = ""
        if len(error_session_ids) > 0:
            msg = ", ".join(map(str, error_session_ids))
            msg = "对话" + msg + "无权限删除"

        return ResultNew.result(0, msg, None)
    except Exception as e:
        await db.rollback()
        print(e)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "删除对话失败")

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

async def compress_image(image_path: str, max_size=512, pad_color=(0, 0, 0)):
    def _compress():
        if not os.path.exists(image_path):
            raise FileNotFoundError()

        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = f"{name}_compressed_{max_size}{ext}"
        new_path = os.path.join(dir_name, new_path)

        if os.path.exists(new_path):
            return new_path

        # return image_path
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

    return await asyncio.to_thread(_compress)


async def generate_messages(db, id, message_now, documents_id):
    """
    生成给ai发送的消息的，涵盖图片编码和上下文提取（不包含提示词生成）
    """
    print("generate_messages")
    message_order = max(message_now.message_order - 6, 0)
    result = await db.execute(
        select(Message)
        .where(Message.session_id == id, Message.message_order > message_order)
        .order_by(desc_func(Message.created_time))
    )
    messages_db = result.scalars().all()

    messages = []
    config = get_image_config()
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    print("get_config")
    tokens = 0

    # user_question_tokens = get_token_count(message_now.content_text)

    user_question_tokens = await asyncio.to_thread(get_token_count, message_now.content_text)

    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        user_question_tokens += len(message_now.user_uploaded_images.split(",")) * 578

    tokens_max -= user_question_tokens

    if tokens_max < 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.MESSAGE_CONTEXT_TOO_LONG, "消息长度过长")

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
            msg_text = []
            msg_text.append({"type": "text", "text": message.content_text})

            # token_tmp += get_token_count(message.content_text)
            token_tmp += await asyncio.to_thread(get_token_count, message.content_text)

            if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
                images = message.user_uploaded_images.split(", ")
                for image in images:

                    image_compressed = await compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image))


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
                    # image_base64 = image_to_base64(image_compressed)
                    image_base64 = await asyncio.to_thread(image_to_base64, image_compressed)
                    msg_text.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                    })
                    # tokens += 258
                    token_tmp += 258


            data["role"] = role
            data["content"] = msg_text

            if tokens + token_tmp >= tokens_max:
                break
            messages.append(data)
            tokens += token_tmp
            print(f"token: {tokens}")
            if tokens > tokens_max:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.MESSAGE_CONTEXT_TOO_LONG, "对话内容达到上限，请重新创建对话")
    # print("messages: ", messages)
    data = {}
    messages.reverse()
    print(f"tokens: {tokens}")

    tokens_tmp = tokens_max - tokens
    prompt = await get_prompt(db, documents_id, tokens_tmp)


    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{message_now.content_text}"}]
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        images = message_now.user_uploaded_images.split(", ")

        # images = message.user_uploaded_images.split(", ")
        for image in images:
            image_compressed = await compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image), max_size=768)
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
            # image_base64 = image_to_base64(image_compressed)

            image_base64 = await asyncio.to_thread(image_to_base64, image_compressed)

            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
    data["role"] = "user"
    # data["content"] = prompt + "\n问题：" + message_now.content_text
    data["content"] = msg_content

    messages.append(data)
    print(len(messages))

    # print("messages: ", messages)
    return messages

async def get_new_title_by_ai(content):
    """
    让ai给我总结一个标题
    """

    messages = [{"role": "user",
                 "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求对话标题正式，简洁。并且只给出标题，不要有任何多余内容。\n内容：{content}"}]

    api_key = os.getenv("API_KEY", "EMPTY")
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))

    def _call_openai():
        client = OpenAI(base_url=get_ai_base_url(), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token
        )
        return response.choices[0].message.content

    new_title = await asyncio.to_thread(_call_openai)
    print("new_title: ", new_title)
    # print("message: ", message)
    if len(new_title) > 15 or len(new_title) == 0:
        new_title = "新对话"

    return new_title

async def get_reference_documents(db, question: str, image: str = None):
    """
    检索出相关文档，并返回文档id
    """
    vector_service = VectorService(db)
    # vector_service.batch_vectorize_existing_documents()
    documents = await vector_service.search_similar_documents(question, image)
    normalized_docs = [
        {"doc_id": int(document["doc_id"]), "library_type": _normalize_library_type(document.get("library_type", "breakdown"))}
        for document in documents
        if document.get("doc_id") is not None
    ]
    if not normalized_docs:
        return []

    active_refs = set()
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        candidate_ids = [doc["doc_id"] for doc in normalized_docs if doc["library_type"] == library_type]
        if not candidate_ids:
            continue
        active_result = await db.execute(
            select(document_model.id).where(
                document_model.id.in_(candidate_ids),
                document_model.is_deleted == 0,
            )
        )
        active_refs.update({f"{library_type}:{row.id}" for row in active_result.all()})
    document_ids = [f"{doc['library_type']}:{doc['doc_id']}" for doc in normalized_docs if f"{doc['library_type']}:{doc['doc_id']}" in active_refs]

    return document_ids

async def get_prompt(db, document_ids, max_tokens):
    """
    生成提示词（包括根据相关文档id，提取文档内容作为提示词）
    """
    if not document_ids:
        return ""
    tokens = 0
    prompts = []

    document_refs = []
    for value in document_ids:
        library_type, _, raw_doc_id = str(value).partition(":")
        document_refs.append((_normalize_library_type(library_type), int(raw_doc_id or library_type)))

    documents = []
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        ids = [doc_id for ref_library_type, doc_id in document_refs if ref_library_type == library_type]
        if not ids:
            continue
        result = await db.execute(
            select(document_model).where(
                document_model.id.in_(ids),
                document_model.is_deleted == 0,
            )
        )
        documents.extend(result.scalars().all())

    for i, document in enumerate(documents):
        # document = db.query(Document).filter(Document.id == document_id).scalar()
        if not document:
            continue
        if _normalize_library_type(getattr(document, "library_type", "breakdown")) == "knowledge":
            section_result = await db.execute(
                select(KnowledgeDocumentSection)
                .where(KnowledgeDocumentSection.document_id == document.id)
                .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
                .limit(8)
            )
            section_text = "\n".join(
                f"{section.section_title or '未命名章节'}：{section.plain_text or ''}"
                for section in section_result.scalars().all()
            )
            doc_prompt = f"""【知识库文档{i + 1}：】{document.title}
章节内容：
{section_text}
        """
        else:
            doc_prompt = f"""【文档{i + 1}：】{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
        # token_tmp = get_token_count(doc_prompt)
        token_tmp = await asyncio.to_thread(get_token_count, doc_prompt)
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


async def stream_ai_response(id, messages: list, session_id: int, doc_ids):
    api_key = os.getenv("API_KEY", "EMPTY")
    client = AsyncOpenAI(base_url=get_ai_base_url(), api_key=api_key)
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))

    response_data = {}
    data = {}
    data["id"] = id
    data["session_id"] = session_id


    if doc_ids and len(doc_ids) > 0:
        doc_aggs = []
        doc_refs = []
        for value in doc_ids:
            library_type, _, raw_doc_id = str(value).partition(":")
            doc_refs.append((_normalize_library_type(library_type), int(raw_doc_id or library_type), str(value)))

        async with AsyncSessionLocal() as db:
            doc_map = {}
            for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
                ids = [doc_id for ref_library_type, doc_id, _ in doc_refs if ref_library_type == library_type]
                if not ids:
                    continue
                result = await db.execute(
                    select(document_model.id, document_model.title).where(
                        document_model.id.in_(ids),
                        document_model.is_deleted == 0,
                    )
                )
                doc_map.update({(library_type, row.id): row.title for row in result.all()})
            for library_type, doc_id, raw_ref in doc_refs:
                doc_title = doc_map.get((library_type, doc_id))
                if doc_title is None:
                    continue
                doc_aggs.append({"doc_id": raw_ref, "doc_name": doc_title, "library_type": library_type})
            data["reference"] = {
                "total": len(doc_aggs),
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
        # ai_msg = db.query(Message).filter(Message.id == id).first()

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Message).where(Message.id == id))
            ai_msg = result.scalar_one_or_none()

            if ai_msg:
                ai_msg.content_text = full_content
                await db.commit()
        final_data = {"code": 1, "data": "true"}

        yield f"data: {json.dumps(final_data)}\n\n"
    except Exception as e:
        # error_msg = f"AI服务错误: {str(e)}"
        print(e)
        error_data = {
            "code": 102,
            "message": "回答失败"
        }
        yield f"data: {json.dumps(error_data)}\n\n"


async def get_ai_answer(messages, db: AsyncSession, id):
    api_key = os.getenv("API_KEY", "EMPTY")
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))

    def _call_openai():
        client = OpenAI(base_url=get_ai_base_url(), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token
        )
        return response.choices[0].message.content

    final_ans = await asyncio.to_thread(_call_openai)
    final_ans = final_ans.replace("\n---\n", "---").replace("\n\n", "\n")

    # ai_msg = db.query(Message).filter(Message.id == id).first()

    result = await db.execute(select(Message).where(Message.id == id))
    ai_msg = result.scalar_one_or_none()

    if ai_msg:
        ai_msg.content_text = final_ans
        await db.commit()
        await db.refresh(ai_msg)

    return final_ans


@router.post("/{chat_id}/completions")
async def chat(message: MessageCreateNew,
               db: AsyncSession = Depends(get_db),
               current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()
        # 检查对话存在
        # conversation = db.query(Conversation).filter(Conversation.id == message.session_id).first()

        conv_result = await db.execute(select(Conversation).where(Conversation.id == message.session_id))
        conversation = conv_result.scalar_one_or_none()

        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "请先新建对话！")

        max_order_result = await db.execute(
            select(func.max(Message.message_order)).where(Message.session_id == message.session_id)
        )
        max_order = max_order_result.scalar() or 0

        # 检查图片已上传服务器
        if message.user_uploaded_images is not None:
            urls = [url.strip() for url in message.user_uploaded_images.split(", ") if url.strip()]
            base_url = os.path.join(config["MESSAGE_BASE_DIR"],
                                    config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

            for url in urls:
                url_check = os.path.basename(url)
                url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
                # print(url_check)
                if not await asyncio.to_thread(os.path.exists, url_check):
                    print(url_check)
                    # raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="图片未上传")
                    raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传")
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
        await db.commit()
        await db.refresh(db_message)

        ai_reference_document_ids = await get_reference_documents(db, db_message.content_text,
                                                            db_message.user_uploaded_images)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)

        messages = await generate_messages(db, conversation.id, db_message, ai_reference_document_ids)

        conversation.updated_time = datetime.now()

        # 如果是该对话的首个消息，就为这个对话总结一个标题
        if conversation.title == "新对话":
            new_title = await get_new_title_by_ai(message.question)
            conversation.title = new_title
        # db.commit()


        ai_msg = Message(
            session_id=message.session_id,
            role=0,
            message_order=max_order + 1,
            content_text="",
            ai_reference_doc_ids=ai_reference_document_ids_str,
            created_time=datetime.now()
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        if message.stream:
            return StreamingResponse(
                stream_ai_response(ai_msg.id, messages, message.session_id, ai_reference_document_ids),
                media_type="text/event-stream"
            )
        else:
            answer = await get_ai_answer(messages, db, ai_msg.id)
            return ResultNew.result(0, None, {
                "answer": answer,
                "reference": ai_reference_document_ids_str,
                "id": ai_msg.id,
                "session_id": message.session_id
            })

    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        print(e)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "回答失败")
