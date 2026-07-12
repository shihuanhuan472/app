"""
旧对话接口
旧对话接口，已弃用，请使用新接口。message.py
"""



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

 
async def generate_messages(db, id, message_now, documents_id, search_results=None):
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
    prompt = await get_prompt(db, documents_id, tokens_tmp, search_results)

    # 收集文档中匹配到的图片，使 AI 能"看到"参考文档中的图片内容
    doc_image_urls = []
    if search_results:
        for sr in search_results:
            for chunk in sr.get("chunks", []):
                img = chunk.get("image_url")
                if img and img not in doc_image_urls:
                    doc_image_urls.append(img)
            for img in sr.get("matched_image_urls", []):
                if img and img not in doc_image_urls:
                    doc_image_urls.append(img)
    max_doc_images = int(os.getenv("MAX_DOC_IMAGES", 5))
    doc_image_urls = [url for url in doc_image_urls if url][:max_doc_images]

    # 构建消息内容：文本在最前，然后是文档参考图片，最后是用户上传图片
    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{message_now.content_text}"}]

    # 添加文档中的图片，让 AI 能结合图片理解文档内容
    for image_url in doc_image_urls:
        image_path = os.path.join(config["MESSAGE_BASE_DIR"], image_url)
        if not await asyncio.to_thread(os.path.exists, image_path):
            continue
        try:
            image_compressed = await compress_image(image_path, max_size=512)
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
            image_base64 = await asyncio.to_thread(image_to_base64, image_compressed)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
        except Exception as e:
            print(f"添加文档图片失败 {image_url}: {e}")

    # 添加用户上传的图片
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

# 返回格式举例
# (
#     # ==================== 第 1 个元素: document_ids ====================
#     # 仅包含未被软删除的文档，格式 "library_type:doc_id"
#     [
#         "breakdown:42",
#         "knowledge:17"
#     ],

#     # ==================== 第 2 个元素: active_documents ====================
#     # 与 document_ids 一一对应，保留 search_similar_documents 的完整字段（含 chunks）
#     [
#         {
#             "doc_id": 42,
#             "library_type": "breakdown",
#             "title": "SBC 主板无法上电故障排障",
#             "content": "设备型号：SBC-2000，故障现象：按下电源键后主板无任何反应...",
#             "image_url": "upload/images/sbc_power_failure.jpg",
#             "score": 0.782,           # 聚合分数
#             "score_max": 0.851,
#             "chunks": [
#                 {
#                     "doc_id": "42",
#                     "library_type": "breakdown",
#                     "title": "SBC 主板无法上电故障排障",
#                     "content": "设备型号：SBC-2000，故障现象：...",
#                     "image_url": "upload/images/sbc_power_failure.jpg",
#                     "metadata": {"source_doc_id": 42, "library_type": "breakdown", "chunk_index": 0, ...},
#                     "score": 0.851
#                 },
#                 { "doc_id": "42", ..., "score": 0.720 },
#                 { "doc_id": "42", ..., "score": 0.603 }
#             ]
#         },
#         {
#             "doc_id": 17,
#             "library_type": "knowledge",
#             "title": "SBC 系列主板硬件设计手册",
#             "content": "3.3V 电源模块由 TPS6521815 芯片提供...",
#             "image_url": "upload/images/sbc_power_design.png",
#             "score": 0.691,
#             "score_max": 0.725,
#             "chunks": [
#                 {
#                     "doc_id": "17",
#                     "library_type": "knowledge",
#                     "title": "SBC 系列主板硬件设计手册",
#                     "content": "3.3V 电源模块由 TPS6521815 芯片提供...",
#                     "image_url": "upload/images/sbc_power_design.png",
#                     "metadata": {"source_doc_id": 17, "library_type": "knowledge", "section_id": 3, ...},
#                     "score": 0.725
#                 },
#                 { "doc_id": "17", ..., "metadata": {..."section_id": 5}, "score": 0.688 }
#             ],
#             # ===== 知识库专属 =====
#             "matched_section_ids": [3, 5],
#             "matched_image_urls": ["upload/images/sbc_power_design.png"]
#         }
#     ]
# )
async def get_reference_documents(db, question: str, image: str = None):
    """
    检索出相关文档，返回 (文档ID列表, 完整检索结果含chunks)
    """
    # 数据准备工作
    vector_service = VectorService(db)
    # vector_service.batch_vectorize_existing_documents()
    documents = await vector_service.search_similar_documents(question, image)
    if not documents:
        return [], []

    # 标准化文档ID
    normalized_docs = [
        {"doc_id": int(document["doc_id"]), "library_type": _normalize_library_type(document.get("library_type", "breakdown"))}
        for document in documents
        if document.get("doc_id") is not None
    ]
    if not normalized_docs:
        return [], []

    # 获取活跃的文档ID
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

    # 过滤 search_results 只保留 active 的文档，供 get_prompt 使用 chunk 内容
    active_doc_keys = set()
    for ref in document_ids:
        lib, _, did = ref.partition(":")
        active_doc_keys.add((lib, int(did)))
    active_documents = [
        doc for doc in documents
        if (doc.get("library_type"), int(doc.get("doc_id", 0))) in active_doc_keys
    ]

    return document_ids, active_documents

async def get_prompt(db, document_ids, max_tokens, search_results=None):
    """
    生成提示词。search_results 含 chunks 用于按相关性取章节（而非取前8个section）
    """
    if not document_ids:
        return ""
    tokens = 0
    prompts = []

    # 构建 search_results 索引
    search_index = {}
    if search_results:
        for sr in search_results:
            key = (sr.get("library_type", "breakdown"), int(sr.get("doc_id", 0)))
            search_index[key] = sr

    document_refs = []
    for value in document_ids:
        library_type, _, raw_doc_id = str(value).partition(":")
        document_refs.append((_normalize_library_type(library_type), int(raw_doc_id or library_type)))

    for i, (ref_lib, ref_doc_id) in enumerate(document_refs):
        sr = search_index.get((ref_lib, ref_doc_id))

        if ref_lib == "knowledge" and sr:
            # 知识库文档：使用向量检索匹配到的 chunk 内容（按相关性排序，去重section_id）
            chunks_sorted = sorted(
                sr.get("chunks", []),
                key=lambda x: float(x.get("score", 0.0)),
                reverse=True,
            )
            seen_section_ids = set()
            section_texts = []
            for chunk in chunks_sorted:
                metadata = chunk.get("metadata") or {}
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except Exception:
                        metadata = {}
                section_id = metadata.get("section_id")
                if section_id is not None:
                    if section_id in seen_section_ids:
                        continue
                    seen_section_ids.add(section_id)
                chunk_content = chunk.get("content", "")
                chunk_img = chunk.get("image_url", "")
                if chunk_img:
                    chunk_content += f"\n[本文配图路径：{chunk_img}]"
                section_texts.append(chunk_content)
                if len(section_texts) >= 8:
                    break
            if section_texts:
                section_text = "\n---\n".join(section_texts)
            else:
                section_text = "(无匹配内容)"
            doc_prompt = f"""【知识库文档{i + 1}：】{sr.get("title", "")}
匹配章节内容：
{section_text}
        """
        elif ref_lib == "knowledge":
            # 兜底：search_results 缺失时回退到 DB 查询
            result = await db.execute(
                select(DocumentKnowledge).where(
                    DocumentKnowledge.id == ref_doc_id,
                    DocumentKnowledge.is_deleted == 0,
                )
            )
            document = result.scalar_one_or_none()
            if not document:
                continue
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
            # breakdown 文档：保持原有 DB 查询逻辑
            result = await db.execute(
                select(DocumentBreakdown).where(
                    DocumentBreakdown.id == ref_doc_id,
                    DocumentBreakdown.is_deleted == 0,
                )
            )
            document = result.scalar_one_or_none()
            if not document:
                continue
            doc_prompt = f"""【文档{i + 1}：】{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
            # 附加故障库文档中的配图路径，让 AI 可以引用
            breakdown_image_fields = [
                document.image_urls,
                document.image_urls_problem_intro,
                document.image_urls_causes,
                document.image_urls_evaluation,
                document.image_urls_inspection,
                document.image_urls_solutions,
                document.image_urls_key_points,
            ]
            breakdown_images = []
            for field_val in breakdown_image_fields:
                if not field_val:
                    continue
                if isinstance(field_val, str):
                    breakdown_images.extend([u.strip() for u in field_val.split(",") if u.strip()])
                elif isinstance(field_val, list):
                    breakdown_images.extend([str(u).strip() for u in field_val if str(u).strip()])
            if breakdown_images:
                doc_prompt += "文档配图路径：" + ", ".join(breakdown_images) + "\n"
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
        final_prompt += "\n回答中如需引用文档配图，请严格使用格式：![图片描述](配图路径)，不要使用【图片N】这样的占位符。"
        return final_prompt

    return ""

def _path_to_web_url(image_path: str) -> str:
    """将本地绝对/相对图片路径转换为 Web 可访问的 URL（如 /upload/images/xxx.png）。"""
    import re
    normalized = str(image_path).replace('\\', '/')
    match = re.search(r'(?:^|/)upload/(.+)$', normalized, re.IGNORECASE)
    if match:
        return f'/upload/{match.group(1)}'
    filename = os.path.basename(normalized)
    return f'/upload/images/{filename}'


async def _replace_image_urls_in_text(text: str, config: dict, known_image_paths: list = None) -> str:
    """将 AI 回答中的本地图片路径替换为 Web URL（/upload/images/...），前端可直接渲染。"""
    import re

    # 预扫描：处理 AI 可能直接输出的裸路径 → Web URL
    if known_image_paths:
        for img_path in known_image_paths:
            if not img_path:
                continue
            escaped = re.escape(img_path)
            bare_pattern = r'(?<!\]\()' + escaped
            if not re.search(bare_pattern, text):
                continue
            web_url = _path_to_web_url(img_path)
            text = re.sub(bare_pattern, web_url, text)
            print(f"[图片替换-裸路径] ✓ {img_path} → {web_url}")

    # 正式替换：Markdown ![]() 语法中的本地路径 → Web URL
    markdown_pattern = r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)'
    matches = list(re.finditer(markdown_pattern, text))
    if not matches:
        print(f"[图片替换] 未找到 Markdown 图片语法，文本前200字: {text[:200]}")
        return text

    print(f"[图片替换] 找到 {len(matches)} 个 Markdown 图片引用")

    def _convert_match(match: re.Match) -> str:
        alt_text = match.group(1)
        image_url = match.group(2)
        if image_url.startswith("data:") or image_url.startswith("http://") or image_url.startswith("https://"):
            return match.group(0)
        if image_url.startswith("/upload/"):
            return match.group(0)
        web_url = _path_to_web_url(image_url)
        print(f"[图片替换] {image_url[:80]} → {web_url}")
        return f'![{alt_text}]({web_url})'

    result_parts = []
    last_end = 0
    for match in matches:
        result_parts.append(text[last_end:match.start()])
        replacement = _convert_match(match)
        result_parts.append(replacement)
        last_end = match.end()
    result_parts.append(text[last_end:])
    return ''.join(result_parts)


def get_ai_reference_document_ids_str(ai_reference_document_ids):
    """
    把相关文档id列表转为字符串
    为了存进mysql数据库
    """
    if len(ai_reference_document_ids) == 0:
        return ""
    result = ", ".join(map(str, ai_reference_document_ids))
    return result


async def stream_ai_response(id, messages: list, session_id: int, doc_ids, search_results=None):
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

        # 构建 search_results 索引，提取匹配到的图片URL
        search_image_map = {}
        if search_results:
            for sr in search_results:
                key = (sr.get("library_type", "breakdown"), int(sr.get("doc_id", 0)))
                images = list(sr.get("matched_image_urls", [])) if sr.get("library_type") == "knowledge" else []
                if not images:
                    for chunk in sr.get("chunks", []):
                        img = chunk.get("image_url")
                        if img and img not in images:
                            images.append(img)
                        if len(images) >= 3:
                            break
                search_image_map[key] = images

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
                doc_agg = {"doc_id": raw_ref, "doc_name": doc_title, "library_type": library_type}
                # 附带匹配到的图片URL，供前端展示
                ref_images = search_image_map.get((library_type, doc_id), [])
                if ref_images:
                    doc_agg["image_urls"] = ref_images
                doc_aggs.append(doc_agg)
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

        # 打印response
        print(f"AI response : {response}")
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

        # 流结束，将 AI 回答中的本地图片路径替换为 base64，使前端可显示
        print(f"[AI流式原始回答-前500字]: {full_content[:500]}")

        # 收集 search_results 中的已知图片路径
        known_image_paths_stream = []
        if search_results:
            for sr in search_results:
                for chunk in sr.get("chunks", []):
                    img = chunk.get("image_url")
                    if img and img not in known_image_paths_stream:
                        known_image_paths_stream.append(img)
                for img in sr.get("matched_image_urls", []):
                    if img and img not in known_image_paths_stream:
                        known_image_paths_stream.append(img)

        config = get_image_config()
        full_content_processed = await _replace_image_urls_in_text(full_content, config, known_image_paths_stream)
        if full_content_processed != full_content:
            # 发送最终处理后的回答（含 base64 图片），前端会渲染实际图片
            data["answer"] = full_content_processed
            response_data["code"] = 0
            response_data["data"] = data
            yield f"data: {json.dumps(response_data)}\n\n"
            full_content = full_content_processed

        # 保存 AI 消息到 DB
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


async def get_ai_answer(messages, db: AsyncSession, id, search_results=None):
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
    print(f"[AI原始回答-前500字]: {final_ans[:500]}")
    final_ans = final_ans.replace("\n---\n", "---").replace("\n\n", "\n")

    # 收集 search_results 中的所有已知图片路径
    known_image_paths = []
    if search_results:
        for sr in search_results:
            for chunk in sr.get("chunks", []):
                img = chunk.get("image_url")
                if img and img not in known_image_paths:
                    known_image_paths.append(img)
            for img in sr.get("matched_image_urls", []):
                if img and img not in known_image_paths:
                    known_image_paths.append(img)
    print(f"[图片替换] 已知图片路径数: {len(known_image_paths)}")

    # 将 AI 回答中的本地图片路径替换为 base64，使前端可直接显示图片
    config = get_image_config()
    final_ans = await _replace_image_urls_in_text(final_ans, config, known_image_paths)

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

        ai_reference_document_ids, search_results = await get_reference_documents(db, db_message.content_text,
                                                            db_message.user_uploaded_images)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)

        messages = await generate_messages(db, conversation.id, db_message, ai_reference_document_ids, search_results)

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
                stream_ai_response(ai_msg.id, messages, message.session_id, ai_reference_document_ids, search_results),
                media_type="text/event-stream"
            )
        else:
            answer = await get_ai_answer(messages, db, ai_msg.id, search_results)
            # 构建非流式响应的 reference_images
            ref_images = {}
            if search_results:
                for sr in search_results:
                    lib = sr.get("library_type", "breakdown")
                    did = int(sr.get("doc_id", 0))
                    images = list(sr.get("matched_image_urls", [])) if lib == "knowledge" else []
                    if not images:
                        for chunk in sr.get("chunks", []):
                            img = chunk.get("image_url")
                            if img and img not in images:
                                images.append(img)
                            if len(images) >= 3:
                                break
                    if images:
                        ref_images[f"{lib}:{did}"] = images
            return ResultNew.result(0, None, {
                "answer": answer,
                "reference": ai_reference_document_ids_str,
                "reference_images": ref_images,
                "id": ai_msg.id,
                "session_id": message.session_id
            })

    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        print(e)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "回答失败")
