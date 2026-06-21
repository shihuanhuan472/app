# routers/message.py
import asyncio
import json
import mimetypes
import os
import uuid
import base64
from functools import lru_cache

import aiofiles
from PIL import Image
from openai import OpenAI, AsyncOpenAI
from datetime import datetime
from pathlib import Path
from qwen_token_counter import get_token_count
from fastapi import APIRouter, Depends, status, UploadFile
from sqlalchemy import func, and_
# from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, and_, select, desc
from typing import List, Dict, Any, Optional
from schemas import Result
from models import Message, User, Conversation, Document, DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection
from schemas import MessageCreate, MessageResponse
from database import get_db, AsyncSessionLocal
from dependencies import get_current_active_user
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.ai_endpoint import get_ai_base_url, get_ai_base_url_alt
from utils.VectorService import VectorService
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/message", tags=["消息"])

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}


def _normalize_library_type(library_type: str) -> str:
    """统一向量检索返回的库类型，确保回查正确的文档表。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _get_document_model(library_type: str):
    """根据库类型选择消息提示词要读取的文档表。"""
    return DOCUMENT_LIBRARY_MODELS[_normalize_library_type(library_type)]

def image_to_base64(image: str, dir: str = None):
    """将图片读取并编码为 base64 字符串。"""
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


def _split_uploaded_images(uploaded_images: str) -> List[str]:
    if not uploaded_images:
        return []
    return [image.strip() for image in uploaded_images.split(",") if image.strip()]


async def _count_text_tokens(text: str) -> int:
    if not text:
        return 0
    return int(await asyncio.to_thread(_count_text_tokens_cached, text))


@lru_cache(maxsize=8192)
def _count_text_tokens_cached(text: str) -> int:
    return int(get_token_count(text or ""))


def _truncate_text_to_token_budget(text: str, token_budget: int) -> str:
    text = str(text or "").strip()
    if not text or token_budget <= 0:
        return ""
    if _count_text_tokens_cached(text) <= token_budget:
        return text
    suffix = "\n[内容因上下文长度限制已截断]"
    suffix_tokens = _count_text_tokens_cached(suffix)
    budget = max(1, token_budget - suffix_tokens)
    approx_chars = max(1, budget * 3)
    candidate = text[:approx_chars].rstrip()
    while candidate and _count_text_tokens_cached(candidate) > budget:
        candidate = candidate[: max(1, int(len(candidate) * 0.8))].rstrip()
    return f"{candidate}{suffix}" if candidate else ""


async def _get_or_update_text_token_count(db: AsyncSession, message: Message) -> int:
    token_count = int(getattr(message, "token_count", 0) or 0)
    if token_count > 0 or not message.content_text:
        return token_count

    token_count = await _count_text_tokens(message.content_text)
    message.token_count = token_count
    return token_count


def _estimate_image_tokens(uploaded_images: str, per_image_tokens: int) -> int:
    return len(_split_uploaded_images(uploaded_images)) * per_image_tokens


def _is_ai_service_unavailable_error(error: Exception) -> bool:
    error_type = type(error).__name__
    if error_type in {"APIConnectionError", "APITimeoutError"}:
        return True

    message = str(error).lower()
    keywords = [
        "connection error",
        "connection",
        "connect",
        "timed out",
        "timeout",
        "refused",
        "service unavailable",
        "temporarily unavailable",
        "name resolution",
        "max retries exceeded",
        "502",
        "503",
        "504",
    ]
    return any(keyword in message for keyword in keywords)

@router.post("/upload_images", summary="上传图片")
async def upload_images(images: List[UploadFile]):
    # 聊天消息图片统一存放在 upload/images
    config = get_image_config()
    url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

    uploaded_images = []
    if not os.path.exists(url):
        os.makedirs(url)
        print(f"创建路径 {url}")
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
            contents = await image.read()

            async with aiofiles.open(save_path, "wb") as buffer:
                await buffer.write(contents)

            relative_url = Path(config["MESSAGE_IMAGE_DIR"]) / unique_filename
            uploaded_images.append({
                "url": relative_url,
                "filename": unique_filename,
                "original_name": image.filename
            })
            print("上传图片成功")
            print(uploaded_images[0])
        except Exception as e:
            # 单个文件失败不影响其他文件继续上传
            print(f"文件 {image.filename} 上传失败: {str(e)}")

    return Result.success_with_data(uploaded_images)

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

async def stream_ai_response(id, messages: list, session_id: int, reference_docs, reference_ids_str: str):
    print("stream ai answer")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = AsyncOpenAI(base_url=get_ai_base_url(), api_key=api_key)
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))

    data = {}
    data["id"] = id
    data["session_id"] = session_id
    data["reference"] = None if not reference_ids_str else reference_ids_str
    data["reference_docs"] = reference_docs or []

    async def persist_ai_content(content_text: str, with_token_count: bool = False):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Message).where(Message.id == id))
            ai_msg = result.scalar_one_or_none()
            if ai_msg:
                ai_msg.content_text = content_text
                await db.commit()
                if with_token_count:
                    ai_msg.token_count = await _count_text_tokens(content_text)
                    await db.commit()

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
                await persist_ai_content(full_content)
                yield f"data: {json.dumps(data)}\n\n"

        final_content = full_content.strip()
        if not final_content:
            final_content = "已检索到相关文档，但回答生成为空，请稍后重试。"
            data["answer"] = final_content
            data["code"] = 1
            await persist_ai_content(final_content, with_token_count=True)
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        else:
            await persist_ai_content(final_content, with_token_count=True)

        final_data = {"code": 1, "data": "true"}
        yield f"data: {json.dumps(final_data)}\n\n"
    except Exception as e:
        print(e)
        fallback_content = "回答生成失败，请稍后重试。"
        try:
            await persist_ai_content(fallback_content, with_token_count=True)
        except Exception as persist_error:
            print(persist_error)
        error_data = {
            "code": 0,
            "message": fallback_content
        }
        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

@router.post("/ask", summary="提问以获得回答")
async def ask(message: MessageCreate,
              db: AsyncSession = Depends(get_db),
              current_user: User = Depends(get_current_active_user)):
    try:
        config = get_image_config()

        conv_result = await db.execute(select(Conversation).where(Conversation.id == message.session_id))
        conversation = conv_result.scalar_one_or_none()

        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "请先新建对话")

        max_order_result = await db.execute(
            select(func.max(Message.message_order)).where(Message.session_id == message.session_id)
        )
        max_order = max_order_result.scalar() or 0
        print(message.user_uploaded_images)

        # 检查消息里引用的图片是否都已存在
        if message.user_uploaded_images is not None:
            message.user_uploaded_images = message.user_uploaded_images.replace("\\", "/")
            urls = [url.strip() for url in message.user_uploaded_images.split(", ") if url.strip()]
            base_url = os.path.join(config["MESSAGE_BASE_DIR"], config["MESSAGE_IMAGE_DIR"].lstrip("/").lstrip("\\"))

            for url in urls:
                url_check = os.path.basename(url)
                url_check = os.path.join(base_url, url_check.lstrip("/").lstrip("\\"))
                if not await asyncio.to_thread(os.path.exists, url_check):
                    print(url_check)
                    raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "图片未上传")

            message.user_uploaded_images = (message.user_uploaded_images.replace("\\", "/")
                                            .replace(", /", ", ")
                                            .removeprefix("/")
                                            .removesuffix(", "))

        print(message.user_uploaded_images)
        user_text_token_count = await _count_text_tokens(message.content_text)
        db_message = Message(
            session_id=message.session_id,
            role=1,
            message_order=max_order + 1,
            content_text=message.content_text,
            user_uploaded_images=message.user_uploaded_images,
            token_count=user_text_token_count,
            created_time=datetime.now()
        )

        db.add(db_message)
        await db.commit()
        await db.refresh(db_message)

        ai_reference_documents = await get_reference_documents(
            db, db_message.content_text, db_message.user_uploaded_images
        )
        ai_reference_document_ids = get_ai_reference_document_ids(ai_reference_documents)
        ai_reference_prompt_refs = get_ai_reference_prompt_refs(ai_reference_documents)
        ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)
        ai_reference_document_payload = get_ai_reference_documents_payload(ai_reference_documents)

        messages = await generate_messages(db, db_message.session_id, db_message, ai_reference_prompt_refs)

        conversation.updated_time = datetime.now()

        if max_order == 0:
            new_title = await get_new_title_by_ai(message.content_text)
            conversation.title = new_title

        ai_msg = Message(
            session_id=message.session_id,
            role=0,
            message_order=max_order + 1,
            content_text="回答生成中，请稍后刷新。",
            ai_reference_doc_ids=ai_reference_document_payload,
            token_count=0,
            created_time=datetime.now()
        )
        db.add(ai_msg)
        await db.commit()
        await db.refresh(ai_msg)

        return StreamingResponse(
            stream_ai_response(
                ai_msg.id,
                messages,
                message.session_id,
                ai_reference_documents,
                ai_reference_document_ids_str
            ),
            media_type="text/event-stream"
        )


    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        print(e)
        if _is_ai_service_unavailable_error(e):
            raise AppException(
                status.HTTP_502_BAD_GATEWAY,
                BizCode.AI_SERVICE_UNAVAILABLE,
                "AI服务不可用，请稍后重试"
            )
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试")

async def generate_messages(db, id, message_now, documents_id):
    """构建发给大模型的上下文消息列表（含最近历史、图片与提示词）。"""
    print("generate_messages")
    message_order = max(message_now.message_order - 6, 0)

    result = await db.execute(
        select(Message)
        .where(Message.session_id == id, Message.message_order > message_order)
        .order_by(desc(Message.created_time))
    )
    messages_db = result.scalars().all()

    messages = []
    config = get_image_config()
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    print("get_config")
    tokens = 0

    user_question_tokens = await _get_or_update_text_token_count(db, message_now)

    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        user_question_tokens += _estimate_image_tokens(message_now.user_uploaded_images, 578)

    tokens_max -= user_question_tokens

    if tokens_max < 0:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.MESSAGE_CONTEXT_TOO_LONG, "消息长度过长")

    flag = 0
    cached_tokens_updated = False

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

            had_cached_token = int(getattr(message, "token_count", 0) or 0) > 0 or not message.content_text
            token_tmp += await _get_or_update_text_token_count(db, message)
            cached_tokens_updated = cached_tokens_updated or not had_cached_token

            if message.user_uploaded_images and len(message.user_uploaded_images) > 0:
                images = _split_uploaded_images(message.user_uploaded_images)
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
                    image_base64 = image_to_base64(image_compressed)
                    msg_text.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                    })
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
    if cached_tokens_updated:
        await db.commit()
    data = {}
    messages.reverse()
    print(f"tokens: {tokens}")

    tokens_tmp = tokens_max - tokens
    prompt = await get_prompt(db, documents_id, tokens_tmp)

    constrain_tip = "\n回答只依据知识文档的内容，不添加多余的信息；若无知识文档，则提示知识库无相关内容。"
    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{message_now.content_text}{constrain_tip}"}]
    print(msg_content)
    if message_now.user_uploaded_images and len(message_now.user_uploaded_images) > 0:
        images = _split_uploaded_images(message_now.user_uploaded_images)
        for image in images:
            image_compressed = await compress_image(os.path.join(config["MESSAGE_BASE_DIR"], image), max_size=768)
            print(image_compressed)
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
    data["role"] = "user"
    data["content"] = msg_content

    messages.append(data)
    print(len(messages))

    return messages

async def get_new_title_by_ai(content):
    """使用大模型为新会话生成简短标题。"""

    messages = [{
        "role": "user",
        "content": f"请根据下面的内容，生成一个10字以内的对话标题，要求正式简洁，并且只给出标题，不要有任何多余内容。\n内容：{content}"
    }]

    api_key = os.getenv("API_KEY", "EMPTY")
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 3000))

    def _call_openai():
        client = OpenAI(base_url=get_ai_base_url_alt(), api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token
        )
        return response.choices[0].message.content

    new_title = await asyncio.to_thread(_call_openai)
    print("new_title: ", new_title)
    if len(new_title) > 15 or len(new_title) == 0:
        new_title = "新对话"

    return new_title

async def get_reference_documents(db, question: str, image: str = None):
    """向量检索相关文档，并返回可展示的文档匹配信息。"""
    vector_service = VectorService(db)
    documents = await vector_service.search_similar_documents(question, image)
    normalized_docs = []
    for doc in documents:
        doc_id = doc.get("doc_id")
        if doc_id is None:
            continue
        score = float(doc.get("score", 0.0))
        library_type = _normalize_library_type(doc.get("library_type", "breakdown"))
        normalized_docs.append({
            "doc_id": int(doc_id),
            "library_type": library_type,
            "title": doc.get("title", ""),
            "score": round(score, 6),
            "chunks": doc.get("chunks") or [],
        })

    if not normalized_docs:
        return []

    active_map = {}
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        candidate_ids = [doc["doc_id"] for doc in normalized_docs if doc["library_type"] == library_type]
        if not candidate_ids:
            continue
        active_docs_result = await db.execute(
            select(document_model.id, document_model.title).where(
                document_model.id.in_(candidate_ids),
                document_model.is_deleted == 0,
            )
        )
        active_map.update({(library_type, row.id): row.title for row in active_docs_result.all()})

    filtered_docs = []
    for doc in normalized_docs:
        doc_id = int(doc["doc_id"])
        library_type = _normalize_library_type(doc.get("library_type", "breakdown"))
        if (library_type, doc_id) not in active_map:
            continue
        filtered_docs.append({
            "doc_id": doc_id,
            "library_type": library_type,
            "title": active_map.get((library_type, doc_id)) or doc.get("title", ""),
            "score": doc.get("score", 0.0),
            "chunks": doc.get("chunks") or [],
        })
    return filtered_docs


def get_ai_reference_document_ids(reference_docs: List[Dict[str, Any]]) -> List[str]:
    """从参考文档中提取文档 id 列表（用于拼接提示词）。"""
    return [f"{_normalize_library_type(doc.get('library_type', 'breakdown'))}:{int(doc['doc_id'])}" for doc in reference_docs if doc.get("doc_id") is not None]

def get_ai_reference_prompt_refs(reference_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保留命中的 chunk/section 信息，用于组装更精确的 RAG prompt。"""
    refs = []
    for doc in reference_docs:
        if doc.get("doc_id") is None:
            continue
        refs.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
            "chunks": doc.get("chunks") or [],
        })
    return refs

def _chunk_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
    metadata = chunk.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    return metadata if isinstance(metadata, dict) else {}

def _section_has_body(section: KnowledgeDocumentSection) -> bool:
    return bool(str(section.plain_text or "").strip())

def _section_marker(section: KnowledgeDocumentSection) -> str:
    return str(section.section_type or "").strip()

def _is_child_or_self_section(candidate: KnowledgeDocumentSection, parent: KnowledgeDocumentSection) -> bool:
    parent_marker = _section_marker(parent)
    candidate_marker = _section_marker(candidate)
    if not parent_marker or not candidate_marker:
        return False
    return candidate_marker == parent_marker or candidate_marker.startswith(parent_marker + ".")

def _select_prompt_sections(
    sections: List[KnowledgeDocumentSection],
    matched_chunks: List[Dict[str, Any]],
    max_sections: int = 10,
) -> List[KnowledgeDocumentSection]:
    if not sections:
        return []

    by_id = {section.id: section for section in sections if section.id is not None}
    by_index = {section.section_index: section for section in sections}
    selected: List[KnowledgeDocumentSection] = []
    selected_ids = set()

    def add(section: Optional[KnowledgeDocumentSection]):
        if not section or section.id in selected_ids:
            return
        if not _section_has_body(section):
            return
        selected.append(section)
        selected_ids.add(section.id)

    matched_sections: List[KnowledgeDocumentSection] = []
    for chunk in matched_chunks or []:
        metadata = _chunk_metadata(chunk)
        section = None
        section_id = metadata.get("section_id")
        if section_id is not None:
            try:
                section = by_id.get(int(section_id))
            except Exception:
                section = None
        if section is None and metadata.get("section_index") is not None:
            try:
                section = by_index.get(int(metadata.get("section_index")))
            except Exception:
                section = None
        if section and section not in matched_sections:
            matched_sections.append(section)

    for section in matched_sections:
        add(section)
        current_pos = sections.index(section)
        if current_pos > 0:
            add(sections[current_pos - 1])
        if current_pos + 1 < len(sections):
            add(sections[current_pos + 1])
        for candidate in sections:
            if len(selected) >= max_sections:
                break
            if _is_child_or_self_section(candidate, section):
                add(candidate)

    if not selected:
        for section in sections:
            add(section)
            if len(selected) >= max_sections:
                break

    return selected[:max_sections]


def _matched_chunk_texts(matched_chunks: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    texts = []
    seen = set()
    for chunk in matched_chunks or []:
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        marker = content[:300]
        if marker in seen:
            continue
        seen.add(marker)
        texts.append(content)
        if len(texts) >= limit:
            break
    return texts


def _append_prompt_piece(parts: List[str], text: str, remaining_tokens: int) -> int:
    text = str(text or "").strip()
    if not text or remaining_tokens <= 0:
        return 0
    token_count = _count_text_tokens_cached(text)
    if token_count <= remaining_tokens:
        parts.append(text)
        return token_count
    truncated = _truncate_text_to_token_budget(text, remaining_tokens)
    if truncated:
        parts.append(truncated)
        return _count_text_tokens_cached(truncated)
    return 0

async def get_prompt(db, document_ids, max_tokens):
    """根据检索文档组装提示词，并受 token 上限约束。"""
    if not document_ids:
        return ""
    tokens = 0
    prompts = []

    document_refs = []
    for value in document_ids:
        if isinstance(value, dict):
            document_refs.append((
                _normalize_library_type(value.get("library_type", "breakdown")),
                int(value.get("doc_id")),
                value.get("chunks") or [],
            ))
        else:
            library_type, _, raw_doc_id = str(value).partition(":")
            document_refs.append((_normalize_library_type(library_type), int(raw_doc_id or library_type), []))

    documents = []
    for library_type, document_model in DOCUMENT_LIBRARY_MODELS.items():
        ids = [doc_id for ref_library_type, doc_id, _chunks in document_refs if ref_library_type == library_type]
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
        if not document:
            continue
        if _normalize_library_type(getattr(document, "library_type", "breakdown")) == "knowledge":
            matched_chunks = []
            for ref_library_type, ref_doc_id, chunks in document_refs:
                if ref_library_type == "knowledge" and ref_doc_id == document.id:
                    matched_chunks.extend(chunks or [])
            print(f"RAG prompt doc={document.id} matched_chunks={len(matched_chunks)} max_tokens={max_tokens}")
            section_result = await db.execute(
                select(KnowledgeDocumentSection)
                .where(KnowledgeDocumentSection.document_id == document.id)
                .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
            )
            all_sections = list(section_result.scalars().all())
            selected_sections = _select_prompt_sections(all_sections, matched_chunks, max_sections=10)
            doc_parts = [f"【知识库文档{i + 1}】：{document.title}"]
            remaining = max_tokens - tokens - _count_text_tokens_cached(doc_parts[0])

            matched_texts = _matched_chunk_texts(matched_chunks, limit=8)
            if matched_texts and remaining > 0:
                used = _append_prompt_piece(
                    doc_parts,
                    "命中的相关片段：\n" + "\n\n".join(
                        f"片段{index + 1}：\n{text}" for index, text in enumerate(matched_texts)
                    ),
                    remaining,
                )
                remaining -= used

            if selected_sections and remaining > 0:
                section_parts = []
                for section in selected_sections:
                    section_piece = f"{section.section_title or '未命名章节'}：{section.plain_text or ''}"
                    used = _append_prompt_piece(section_parts, section_piece, remaining)
                    remaining -= used
                    if remaining <= 0:
                        break
                if section_parts:
                    doc_parts.append("相关章节内容：\n" + "\n\n".join(section_parts))

            doc_prompt = "\n".join(part for part in doc_parts if part).strip()
        else:
            doc_prompt = f"""【文档{i + 1}】：{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
        token_tmp = _count_text_tokens_cached(doc_prompt)

        if tokens + token_tmp >= max_tokens:
            remaining = max_tokens - tokens
            doc_prompt = _truncate_text_to_token_budget(doc_prompt, remaining)
            token_tmp = _count_text_tokens_cached(doc_prompt)
            if not doc_prompt or token_tmp <= 0:
                break
        tokens += token_tmp
        prompts.append(doc_prompt)
        print(f"RAG prompt appended doc={getattr(document, 'id', '')} tokens={token_tmp} total={tokens}")

    # 增加统一指令
    if prompts:
        final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
        final_prompt += "\n---\n".join(prompts)
        final_prompt += "\n\n请只依据上述知识文档回答用户的问题。若上述片段包含可回答的信息，请直接给出答案；只有上述文档确实没有相关信息时，才提示知识库无相关内容。"
        return final_prompt

    return ""

def get_ai_reference_document_ids_str(ai_reference_document_ids):
    """将文档 id 列表转为逗号分隔字符串，便于存储。"""
    if len(ai_reference_document_ids) == 0:
        return ""
    result = ", ".join(map(str, ai_reference_document_ids))
    return result


def get_ai_reference_documents_payload(reference_docs: List[Dict[str, Any]]) -> str:
    """将参考文档匹配信息序列化为 JSON 字符串，便于持久化。"""
    if not reference_docs:
        return ""
    payload = []
    for doc in reference_docs:
        if doc.get("doc_id") is None:
            continue
        chunks = []
        for chunk in (doc.get("chunks") or [])[:3]:
            metadata = _chunk_metadata(chunk)
            chunks.append({
                "score": float(chunk.get("score", 0.0)),
                "content_type": metadata.get("content_type"),
                "section_title": metadata.get("section_title"),
                "table_index": metadata.get("table_index"),
                "row_start": metadata.get("row_start"),
                "row_end": metadata.get("row_end"),
                "preview": str(chunk.get("content") or "")[:300],
            })
        payload.append({
            "doc_id": int(doc["doc_id"]),
            "library_type": _normalize_library_type(doc.get("library_type", "breakdown")),
            "title": doc.get("title", ""),
            "score": float(doc.get("score", 0.0)),
            "chunks": chunks,
        })
    if not payload:
        return ""
    return json.dumps(payload, ensure_ascii=False)

async def get_ai_answer(db, messages, id):
    """调用大模型得到回答，并回写到 AI 消息记录。"""
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

    result = await db.execute(select(Message).where(Message.id == id))
    ai_msg = result.scalar_one_or_none()

    if ai_msg:
        ai_msg.content_text = final_ans
        ai_msg.token_count = await _count_text_tokens(final_ans)
        await db.commit()
        await db.refresh(ai_msg)

    return ai_msg

@router.get("/get_by_conversation", summary="获得某个对话的消息")
async def get_by_conversation(id: int,
                              db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_active_user)):
    try:
        conv_result = await db.execute(
            select(Conversation).where(
                and_(Conversation.id == id, Conversation.user_id == current_user.id))
        )
        conversation = conv_result.scalar_one_or_none()
        if not conversation:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.CONVERSATION_NOT_FOUND, "无该对话或无权限访问该对话")

        msg_result = await db.execute(
            select(Message).where(Message.session_id == id).order_by(Message.created_time)
        )
        messages = msg_result.scalars().all()

        message_response = [MessageResponse.from_orm(message) for message in messages]
        return Result.success_with_data(message_response)
    except Exception as e:
        if isinstance(e, AppException):
            raise
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "获取对话消息失败")



