import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Document, DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection
from utils.VectorStoreMultimodal import vector_store_multimodal
from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_chat_completion, create_openai_client, parse_chat_completion_json

load_dotenv()
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HOME"] = os.getenv(
    "MODEL_DOWNLOAD_URL", "D:/Pycharm/code/Maintenance_Assistance_System/bge/model"
)

logger = logging.getLogger(__name__)

DOCUMENT_LIBRARY_MODELS = {"breakdown": DocumentBreakdown, "knowledge": DocumentKnowledge}
DOMAIN_TERM_ALIASES = {
    "FIT": ["FIT", "FIT值", "FIT value"],
    "Q30": ["Q30"],
    "G50": ["G50"],
    "NSB": ["NSB", "Non-Specific Binding", "Non Specific Binding", "非特异性吸附", "非特异性结合"],
    "SBC": ["SBC"],
    "DNQ": ["DNQ"],
}
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


def _snapshot_section_for_vector_store(section: KnowledgeDocumentSection) -> Dict[str, Any]:
    return {
        "id": section.id,
        "document_id": section.document_id,
        "document_library_type": section.document_library_type,
        "section_index": section.section_index,
        "section_title": section.section_title,
        "section_type": section.section_type,
        "plain_text": section.plain_text,
        "image_urls": section.image_urls or [],
        "char_start": section.char_start,
        "char_end": section.char_end,
        "metadata": section.section_metadata or {},
    }


class VectorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.vector_store_multimodal = vector_store_multimodal
        self.top_k = int(os.getenv("TOP_K", 10))
        self.batch_size = int(os.getenv("BATCH_SIZE", 10))
        self.similarity_low_limit = float(os.getenv("SIMILARITY_LOWER_LIMIT", 0.5))
        self.top_k_documents = int(os.getenv("TOP_K_DOCUMENTS", 2))
        # self.enable_llm_rerank = True
        # self.rerank_top_k = 8
        # self.rerank_low_limit = self.similarity_low_limit
        self.message_image_base_dir = os.getenv(
            "MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System"
        )
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        from utils.token_config import IMAGE_MAX_OUTPUT_TOKENS
        self.max_token = IMAGE_MAX_OUTPUT_TOKENS

    async def add_document_to_vector_store(self, document: Document, commit: bool = True):
        """将文档添加到向量库。"""
        try:
            if document.is_vectorized:
                print(f"文档 {document.id} 已向量化，跳过")
                return

            knowledge_sections = []
            if _normalize_library_type(getattr(document, "library_type", "breakdown")) == "knowledge":
                section_result = await self.db.execute(
                    select(KnowledgeDocumentSection)
                    .where(KnowledgeDocumentSection.document_id == document.id)
                    .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
                )
                section_models = list(section_result.scalars().all())
                section_snapshots = [_snapshot_section_for_vector_store(section) for section in section_models]
                prepared_sections = await asyncio.to_thread(
                    self.vector_store_multimodal.prepare_knowledge_sections,
                    section_snapshots,
                )
                prepared_by_id = {section.get("id"): section for section in prepared_sections}
                for section_model in section_models:
                    prepared = prepared_by_id.get(section_model.id)
                    if prepared is not None:
                        section_model.section_metadata = prepared.get("metadata") or {}
                        section_model.updated_time = datetime.now()
                        knowledge_sections.append(prepared)

            vector_document = _snapshot_document_for_vector_store(document)
            if knowledge_sections:
                vector_document.knowledge_sections = knowledge_sections
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

            client = create_openai_client(base_url=get_ai_base_url(), api_key=self.api_key)
            response = create_chat_completion(
                client,
                model=self.model,
                messages=messages,
                max_tokens=self.max_token,
                json_mode=True,
            )
            return response

        try:
            response = await asyncio.to_thread(_call_openai)
            payload = parse_chat_completion_json(response)
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
            client = create_openai_client(base_url=get_ai_base_url(), api_key=self.api_key)
            response = create_chat_completion(
                client,
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

    @staticmethod
    def _extract_domain_terms(query: str) -> List[str]:
        """Extract technical terms that vector similarity may treat too loosely."""
        if not query:
            return []

        terms: List[str] = []
        query_text = VectorService._normalize_term_text(query)
        for canonical, aliases in DOMAIN_TERM_ALIASES.items():
            for alias in aliases:
                if VectorService._contains_term_alias(query_text, [alias]):
                    terms.append(canonical)
                    break
        return terms

    @staticmethod
    def _normalize_term_text(text: str) -> str:
        """Normalize case and separators while preserving Chinese/technical terms."""
        return re.sub(r"[\s_\-]+", " ", str(text or "").casefold()).strip()

    @staticmethod
    def _contains_term_alias(text: str, aliases: List[str]) -> bool:
        text = VectorService._normalize_term_text(text)
        for alias in aliases:
            normalized_alias = VectorService._normalize_term_text(alias)
            pattern = re.escape(normalized_alias).replace(r"\ ", r"[\s_-]*")
            if re.search(pattern, text):
                return True
        return False

    @staticmethod
    def _apply_domain_term_score(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Re-rank vector candidates with conservative exact-term signals."""
        terms = VectorService._extract_domain_terms(query)
        if not terms:
            return results

        adjusted_results = []
        for item in results:
            vector_score = float(item.get("score", 0.0))
            title = str(item.get("title") or "").lower()
            content = str(item.get("content") or "").lower()

            matched_terms = []
            title_hit_count = 0
            for term in terms:
                aliases = DOMAIN_TERM_ALIASES.get(term, [term])
                title_hit = VectorService._contains_term_alias(title, aliases)
                content_hit = VectorService._contains_term_alias(content, aliases)
                if not title_hit and not content_hit:
                    continue

                matched_terms.append(term)
                if title_hit:
                    title_hit_count += 1

            if matched_terms:
                coverage = len(set(matched_terms)) / max(len(set(terms)), 1)
                title_coverage = title_hit_count / max(len(set(terms)), 1)
                # Short term queries need a stronger exact-hit signal; cap the bonus to avoid score inflation.
                bonus = 0.08 + (0.06 * coverage) + (0.03 * title_coverage)
                bonus = min(bonus, 0.15)
            else:
                bonus = 0.0

            missing_penalty = max(
                0.0,
                min(0.20, float(os.getenv("DOMAIN_TERM_MISSING_PENALTY", 0.10))),
            )
            if matched_terms:
                adjusted_score = min(1.0, vector_score + bonus)
            else:
                adjusted_score = max(0.0, vector_score - missing_penalty)

            item["vector_score"] = vector_score
            item["term_bonus"] = bonus
            item["matched_terms"] = matched_terms
            item["score"] = adjusted_score
            adjusted_results.append(item)

        return adjusted_results

    @staticmethod
    def _classify_graph_query_intent(query: str) -> str:
        text = str(query or "").strip().lower()
        if re.search(r"(完整|全部|整体|总览|有哪些|包括什么|结构|思维导图)", text):
            return "overview"
        if re.search(r"(怎么|如何|步骤|流程|处理|检查|排查|操作|解决)", text):
            return "procedure"
        if re.search(r"(上级|下级|父节点|子节点|属于|关系|关联|分支|下面)", text):
            return "relation"
        return "node_lookup"

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        text = str(query or "").lower()
        text = re.sub(
            r"(请问|请帮我|帮我|一下|应该|需要|怎么|如何|是什么|有哪些|什么|相关|进行|处理|说明|介绍)",
            " ",
            text,
        )
        terms = re.findall(r"[a-zA-Z0-9_.+-]{2,}|[\u4e00-\u9fff]{2,}", text)
        stop = {"什么", "如何", "怎么", "哪些", "一下", "请问", "相关", "进行", "这个"}
        expanded = []
        for term in terms:
            if term in stop:
                continue
            expanded.append(term)
            if re.fullmatch(r"[\u4e00-\u9fff]{5,}", term):
                expanded.extend(term[index:index + 4] for index in range(0, len(term) - 3, 2))
        return list(dict.fromkeys(expanded))[:12]

    @staticmethod
    def _apply_image_graph_score(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        intent = VectorService._classify_graph_query_intent(query)
        terms = VectorService._query_terms(query)
        for item in results:
            metadata = VectorService._metadata_dict(item.get("metadata"))
            if metadata.get("parser_type") != "long_hierarchical_image":
                continue
            path_text = str(metadata.get("path_text") or "").lower()
            content = str(item.get("content") or "").lower()
            matched = [term for term in terms if term in path_text or term in content]
            coverage = len(set(matched)) / max(1, len(set(terms))) if terms else 0.0
            path_bonus = min(0.16, 0.04 + coverage * 0.12)
            relation_confidence = float(metadata.get("relation_confidence") or 0.0)
            quality_adjustment = min(0.04, relation_confidence * 0.04)
            if metadata.get("needs_review"):
                quality_adjustment -= 0.08
            original_score = float(item.get("score", 0.0))
            item["score"] = max(0.0, min(1.0, original_score + path_bonus + quality_adjustment))
            item["graph_score_bonus"] = round(path_bonus + quality_adjustment, 6)
            item["graph_query_intent"] = intent
            item["matched_graph_terms"] = matched
        return results

    @staticmethod
    def _first_image_url(value: Any) -> str:
        if isinstance(value, list):
            return str(value[0]) if value else ""
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return str(parsed[0]) if parsed else ""
            except Exception:
                pass
            return value.split(",")[0].strip() if value.strip() else ""
        return ""

    @staticmethod
    def _metadata_dict(metadata: Any) -> Dict[str, Any]:
        if isinstance(metadata, dict):
            return metadata
        if isinstance(metadata, str):
            try:
                parsed = json.loads(metadata)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _merge_retrieval_candidates(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for item in results:
            metadata = VectorService._metadata_dict(item.get("metadata"))
            key = "|".join([
                str(item.get("library_type") or "breakdown"),
                str(item.get("doc_id") or ""),
                str(metadata.get("section_index") or metadata.get("section_title") or ""),
            ])
            existing = merged.get(key)
            if not existing:
                merged[key] = item
                continue

            existing_score = float(existing.get("score", 0.0))
            item_score = float(item.get("score", 0.0))
            if item_score > existing_score:
                merged[key] = item
            else:
                existing["score"] = existing_score

        return list(merged.values())

    @staticmethod
    def _debug_print_search_results(stage: str, results: List[Dict[str, Any]], limit: int = 20):
        print(f"\n========== RAG DEBUG: {stage} count={len(results or [])} ==========")
        for index, item in enumerate((results or [])[:limit], start=1):
            metadata = VectorService._metadata_dict(item.get("metadata"))
            content = str(item.get("content") or "").replace("\n", " ").strip()
            preview = content[:180]
            print(
                "[RAG DEBUG] "
                f"rank={index} "
                f"doc={item.get('library_type')}:{item.get('doc_id')} "
                f"title={item.get('title')} "
                f"score={float(item.get('score', 0.0)):.6f} "
                f"vector_score={float(item.get('vector_score', item.get('score', 0.0))):.6f} "
                f"term_bonus={float(item.get('term_bonus', 0.0)):.6f} "
                f"matched_terms={item.get('matched_terms', [])} "
                f"content_type={metadata.get('content_type')} "
                f"section={metadata.get('section_title')} "
                f"preview={preview}"
            )
        print(f"========== RAG DEBUG END: {stage} ==========\n")

    async def search_similar_documents(
        self,
        query: str,
        query_images: str = None,
        top_k: int = -1,
        top_k_documents: int = -1,
        user_id: Optional[int] = None,
        session_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """检索相似文档并聚合为文档级结果。"""
        # 返回格式举例（知识库的matadata为简写，具体格式看search函数的注释）
        # {
        #         "doc_id": 17,
        #         "library_type": "knowledge",     # 知识库类型
        #         "title": "SBC 系列主板硬件设计手册",
        #         "content": "3.3V 电源模块由 TPS6521815 芯片提供，典型工作电压范围为...",
        #         "image_url": "upload/images/sbc_power_design.png",
        #         "score": 0.691,
        #         "score_max": 0.725,
        #         "chunks": [
        #             {
        #                 "doc_id": "17",
        #                 "library_type": "knowledge",
        #                 "title": "SBC 系列主板硬件设计手册",
        #                 "content": "3.3V 电源模块由 TPS6521815 芯片提供...",
        #                 "image_url": "upload/images/sbc_power_design.png",
        #                 "metadata": {"source_doc_id": 17, "library_type": "knowledge", "section_id": 3},
        #                 "score": 0.725
        #             },
        #             {
        #                 "doc_id": "17",
        #                 "library_type": "knowledge",
        #                 "title": "SBC 系列主板硬件设计手册",
        #                 "content": "供电异常排查：若 3.3V 输出低于 2.8V...",
        #                 "image_url": "",
        #                 "metadata": {"source_doc_id": 17, "library_type": "knowledge", "section_id": 5},
        #                 "score": 0.688
        #             }
        #         ],
        #         # ===== 知识库专属字段 =====
        #         "matched_section_ids": [3, 5],   # 匹配到的章节 ID 列表（去重）
        #         "matched_image_urls": [          # 匹配到的图片 URL 列表（去重）
        #             "upload/images/sbc_power_design.png"
        #         ]
        #     }
        try:
            top_k = self.top_k if top_k < 1 else top_k
            top_k_documents = self.top_k_documents if top_k_documents < 1 else top_k_documents
            all_results: List[Dict[str, Any]] = []

            # 如果有图片：describe_image() 调用视觉模型提取语义然后与文本一起Milvus 多模态检索
            # 否则：Milvus 纯文本检索
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

            # 合并/去重/按分数排序
            all_results = self._merge_retrieval_candidates(all_results)
            all_results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            self._debug_print_search_results("raw vector results", all_results)
            all_results = self._apply_domain_term_score(all_results, query)
            all_results = self._apply_image_graph_score(all_results, query)
            all_results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            self._debug_print_search_results("after domain term score", all_results)
            # if self.enable_llm_rerank:
            #     rerank_candidates = all_results[: max(self.rerank_top_k, 1)]
            #     reranked_results = []
            #     for item in rerank_candidates:
            #         rerank_score = await self.rerank_by_llm(item, query=query, query_image=None)
            #         if rerank_score is None:
            #             rerank_score = float(item.get("score", 0.0))
            #         item["vector_score"] = float(item.get("score", 0.0))
            #         item["score"] = rerank_score
            #         item["rerank_score"] = rerank_score
            #         if rerank_score >= self.rerank_low_limit:
            #             reranked_results.append(item)
            #     all_results = sorted(reranked_results, key=lambda x: float(x.get("score", 0.0)), reverse=True)
            #     if not all_results:
            #         return []


            # 按 (library_type, doc_id) 分组聚合
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

            # 知识库文档：提取 matched_section_ids + matched_image_urls
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
                # 提取知识库文档匹配到的 section_id 和图片，供 get_prompt 按相关性取章节
                if doc.get("library_type") == "knowledge":
                    matched_section_ids = []
                    matched_image_urls = []
                    matched_node_ids = []
                    matched_graph_regions = []
                    graph_query_intent = None
                    for chunk in chunks_sorted:
                        metadata = self._metadata_dict(chunk.get("metadata"))
                        section_id = metadata.get("section_id")
                        if section_id is not None and section_id not in matched_section_ids:
                            matched_section_ids.append(section_id)
                        img = chunk.get("image_url")
                        if img and img not in matched_image_urls:
                            matched_image_urls.append(img)
                        node_id = metadata.get("node_id")
                        if node_id and node_id not in matched_node_ids:
                            matched_node_ids.append(node_id)
                            matched_graph_regions.append({
                                "node_id": node_id,
                                "path": metadata.get("path") or [],
                                "path_text": metadata.get("path_text") or "",
                                "bbox": metadata.get("bbox"),
                                "crop_image_url": metadata.get("crop_image_url") or img or "",
                                "original_image_url": metadata.get("original_image_url") or "",
                                "confidence": metadata.get("confidence"),
                                "relation_confidence": metadata.get("relation_confidence"),
                                "needs_review": bool(metadata.get("needs_review")),
                            })
                        graph_query_intent = graph_query_intent or chunk.get("graph_query_intent")
                    doc["matched_section_ids"] = matched_section_ids
                    doc["matched_image_urls"] = matched_image_urls
                    if matched_node_ids:
                        doc["matched_node_ids"] = matched_node_ids
                        doc["matched_graph_regions"] = matched_graph_regions
                        doc["graph_query_intent"] = graph_query_intent or self._classify_graph_query_intent(query)
                docs.append(doc)

            # 过滤低于阈值的低分文档
            docs.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            self._debug_print_search_results("grouped docs after threshold", docs)
            return docs[:top_k_documents]

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
