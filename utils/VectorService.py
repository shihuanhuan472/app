import asyncio
import base64
import json
import logging
import mimetypes
import os
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Document, DocumentBreakdown, DocumentKnowledge
from utils.VectorStoreMultimodal import vector_store_multimodal
from utils.ai_endpoint import get_ai_base_url

load_dotenv()
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.getenv(
    "MODEL_DOWNLOAD_URL", "D:/Pycharm/code/Maintenance_Assistance_System/bge/model"
)

logger = logging.getLogger(__name__)

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}
VECTOR_DOCUMENT_FIELDS = [
    "id",
    "title",
    "contributor_id",
    "first_edit_date",
    "problem_intro",
    "image_urls",
    "image_urls_problem_intro",
    "causes",
    "image_urls_causes",
    "evaluation",
    "image_urls_evaluation",
    "inspection",
    "image_urls_inspection",
    "solutions",
    "image_urls_solutions",
    "key_points",
    "image_urls_key_points",
    "origin_file_name",
    "origin_file_dir",
    "tag",
    "is_vectorized",
    "vector_update_time",
]


def _normalize_library_type(library_type: str) -> str:
    """统一库类型，防止向量删除和回查文档时跨库误操作。"""
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _snapshot_document_for_vector_store(document: Document):
    """复制 ORM 文档的普通字段，避免同步线程里触发异步 ORM 懒加载。"""
    data = {field: getattr(document, field, None) for field in VECTOR_DOCUMENT_FIELDS}
    data["library_type"] = _normalize_library_type(getattr(document, "library_type", "breakdown"))
    return SimpleNamespace(**data)


class VectorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.vector_store_multimodal = vector_store_multimodal
        self.top_k = int(os.getenv("TOP_K", 10))
        self.batch_size = int(os.getenv("BATCH_SIZE", 10))
        self.similarity_low_limit = float(os.getenv("SIMILARITY_LOWER_LIMIT", 0.5))
        self.top_k_documents = int(os.getenv("TOP_K_DOCUMENTS", 2))
        self.message_image_base_dir = os.getenv(
            "MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
        )
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))

    async def add_document_to_vector_store(self, document: Document, commit: bool = True):
        """将文档添加到向量库。"""
        try:
            if document.is_vectorized:
                print(f"文档 {document.id} 已向量化，跳过")
                return

            vector_document = _snapshot_document_for_vector_store(document)
            await asyncio.to_thread(self.vector_store_multimodal.add_document, vector_document)
            document.is_vectorized = 1
            document.vector_update_time = datetime.now()
            if commit:
                await self.db.commit()
            else:
                await self.db.flush()
            print(f"文档 {document.id} 向量化完成")
        except Exception as e:
            await self.db.rollback()
            print(f"文档向量化失败: {e}")
            raise

    async def delete_document_from_vector_store(self, doc_id: int, library_type: str = "breakdown"):
        """从向量库删除文档。"""
        try:
            await asyncio.to_thread(self.vector_store_multimodal.delete_document, doc_id, _normalize_library_type(library_type))
            print(f"文档 {doc_id} 已从向量库删除")
        except Exception as e:
            print(f"从向量库删除文档失败: {e}")
            raise

    def image_to_base64(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def add_picture_to_message(self, image_path: str) -> Dict[str, Any]:
        mime_type, _ = mimetypes.guess_type(image_path)
        if mime_type is None:
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
            }.get(ext, "image/jpeg")

        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{self.image_to_base64(image_path)}"
            },
        }

    def _build_rerank_prompt(
        self,
        result: Dict[str, Any],
        query: Optional[str] = None,
        query_image: Optional[str] = None,
    ) -> str:
        chunk_image = result.get("image_url") or None
        query_image = query_image or None

        prompt = (
            "请判断文档片段对用户问题的帮助程度，并给出0~1分。\n"
            f"[用户问题]\n{query or ''}\n\n"
            f"[文档片段]\n{result.get('content', '')}\n"
        )

        if query_image and chunk_image:
            prompt += "你将看到两张图：第一张是用户问题图，第二张是文档图，请综合判断。\n"
        elif query_image:
            prompt += "你将看到用户问题图，请结合文本判断。\n"
        elif chunk_image:
            prompt += "你将看到文档图，请结合文本判断。\n"

        prompt += (
            "仅输出一个JSON对象，不要输出其他内容：\n"
            "{\"reason\": \"简要原因\", \"score\": 0.0}"
        )
        return prompt

    async def rerank_by_llm(
        self,
        result: Dict[str, Any],
        query: Optional[str] = None,
        query_image: Optional[str] = None,
    ) -> Optional[float]:
        """调用大模型对单个 chunk 重打分。"""

        def _call_openai() -> str:
            prompt = self._build_rerank_prompt(result, query, query_image)
            messages = [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

            if query_image:
                messages[0]["content"].append(self.add_picture_to_message(query_image))
            chunk_image = result.get("image_url")
            if chunk_image:
                messages[0]["content"].append(self.add_picture_to_message(chunk_image))

            client = OpenAI(base_url=get_ai_base_url(), api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token,
            )
            return response.choices[0].message.content or ""

        try:
            ans = await asyncio.to_thread(_call_openai)
            payload = json.loads(ans)
            score = float(payload.get("score", 0.0))
            return max(0.0, min(1.0, score))
        except Exception as e:
            print(f"rerank失败: {e}")
            return None

    async def describe_image(self, image_path: str) -> Optional[str]:
        """提取查询图片语义，增强召回。"""

        def _call_openai() -> str:
            msg = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "请详细描述图像信息，重点包含设备信息和故障信息。仅返回答案文本。",
                        },
                        self.add_picture_to_message(image_path),
                    ],
                }
            ]
            client = OpenAI(base_url=get_ai_base_url(), api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=msg,
                max_tokens=self.max_token,
            )
            return response.choices[0].message.content or ""

        try:
            return await asyncio.to_thread(_call_openai)
        except Exception as e:
            print(f"图像描述失败: {e}")
            return None

    @staticmethod
    def _aggregate_doc_score(chunks: List[Dict[str, Any]]) -> float:
        """文档匹配度：Top3 chunk 加权平均。"""
        if not chunks:
            return 0.0
        weights = [0.6, 0.3, 0.1]
        sorted_chunks = sorted(chunks, key=lambda x: float(x.get("score", 0.0)), reverse=True)
        top_chunks = sorted_chunks[: len(weights)]
        used_weights = weights[: len(top_chunks)]
        weight_sum = sum(used_weights)
        if weight_sum <= 0:
            return 0.0
        score = sum(float(chunk.get("score", 0.0)) * w for chunk, w in zip(top_chunks, used_weights)) / weight_sum
        return score

    async def search_similar_documents(
        self,
        query: str,
        query_images: str = None,
        top_k: int = -1,
    ) -> List[Dict[str, Any]]:
        """检索相似文档并聚合为文档级结果。"""
        try:
            top_k = self.top_k if top_k < 1 else top_k
            all_results: List[Dict[str, Any]] = []

            images = [img.strip() for img in (query_images or "").split(",") if img and img.strip()]

            if images:
                for image in images:
                    image_path = os.path.join(self.message_image_base_dir, image)
                    exists = await asyncio.to_thread(os.path.exists, image_path)
                    if not exists:
                        continue

                    enhanced_query = query
                    image_description = await self.describe_image(image_path)
                    if image_description:
                        enhanced_query = f"{query}\n[图像语义]：{image_description}"

                    results = await asyncio.to_thread(
                        self.vector_store_multimodal.search, enhanced_query, image_path, top_k
                    )

                    all_results.extend(results)
            else:
                results = await asyncio.to_thread(self.vector_store_multimodal.search, query, None, top_k)
                all_results.extend(results)

            if not all_results:
                return []

            all_results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)

            grouped: Dict[str, Dict[str, Any]] = {}
            for item in all_results:
                score = float(item.get("score", 0.0))
                if score < self.similarity_low_limit:
                    continue
                doc_id = item.get("doc_id")
                if doc_id is None:
                    continue

                doc_id = int(doc_id)
                library_type = _normalize_library_type(item.get("library_type", "breakdown"))
                group_key = f"{library_type}:{doc_id}"
                if group_key not in grouped:
                    grouped[group_key] = {
                        "doc_id": doc_id,
                        "library_type": library_type,
                        "title": item.get("title", ""),
                        "content": item.get("content", ""),
                        "image_url": item.get("image_url", ""),
                        "score": score,
                        "chunks": [item],
                    }
                else:
                    grouped[group_key]["chunks"].append(item)

            docs = []
            for doc in grouped.values():
                chunks_sorted = sorted(
                    doc["chunks"], key=lambda x: float(x.get("score", 0.0)), reverse=True
                )
                best_chunk = chunks_sorted[0]
                doc["content"] = best_chunk.get("content", "")
                doc["image_url"] = best_chunk.get("image_url", "")
                doc["score_max"] = float(best_chunk.get("score", 0.0))
                doc["score"] = self._aggregate_doc_score(chunks_sorted)
                docs.append(doc)

            docs.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            return docs[: self.top_k_documents]

        except Exception as e:
            print(f"向量检索失败: {e}")
            return []

    async def batch_vectorize_existing_documents(self, batch_size: int = -1) -> int:
        """批量向量化现有文档。"""
        try:
            batch_size = self.batch_size if batch_size < 1 else batch_size
            total = 0
            for document_model in DOCUMENT_LIBRARY_MODELS.values():
                result = await self.db.execute(
                    select(document_model).where(document_model.is_vectorized == 0).limit(batch_size)
                )
                documents = result.scalars().all()
                for doc in documents:
                    await self.add_document_to_vector_store(doc)
                total += len(documents)
            return total
        except Exception as e:
            print(f"批量向量化失败: {e}")
            return 0
