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
from openai import OpenAI
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Document, DocumentBreakdown, DocumentKnowledge, KnowledgeDocumentSection
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
DOMAIN_TERM_ALIASES = {
    "FIT": ["FIT", "FIT值", "FIT value"],
    "Q30": ["Q30"],
    "G50": ["G50"],
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
        self.keyword_recall_seed_score = float(
            os.getenv("KEYWORD_RECALL_SEED_SCORE", max(0.0, self.similarity_low_limit - 0.08))
        )
        self.top_k_documents = int(os.getenv("TOP_K_DOCUMENTS", 2))
        # self.enable_llm_rerank = True
        # self.rerank_top_k = 8
        # self.rerank_low_limit = self.similarity_low_limit
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

    @staticmethod
    def _extract_domain_terms(query: str) -> List[str]:
        """Extract technical terms that embedding search may treat too loosely."""
        if not query:
            return []

        terms: List[str] = []
        query_text = query.lower()
        for canonical, aliases in DOMAIN_TERM_ALIASES.items():
            for alias in aliases:
                pattern = re.escape(alias.lower()).replace(r"\ ", r"[\s_-]*")
                if re.search(pattern, query_text):
                    terms.append(canonical)
                    break
        return terms

    @staticmethod
    def _contains_term_alias(text: str, aliases: List[str]) -> bool:
        text = (text or "").lower()
        for alias in aliases:
            pattern = re.escape(alias.lower()).replace(r"\ ", r"[\s_-]*")
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
            metadata = item.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
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
                if metadata.get("retrieval_source") == "keyword":
                    bonus = float(item.get("term_bonus", 0.0))
                else:
                    bonus = 0.08 + (0.06 * coverage) + (0.03 * title_coverage)
                    bonus = min(bonus, 0.15)
            else:
                bonus = 0.0

            missing_penalty = float(os.getenv("DOMAIN_TERM_MISSING_PENALTY", 0.18))
            if metadata.get("retrieval_source") == "keyword":
                adjusted_score = vector_score
            elif matched_terms:
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
    def _term_alias_patterns(terms: List[str]) -> List[str]:
        patterns: List[str] = []
        seen = set()
        for term in terms:
            for alias in DOMAIN_TERM_ALIASES.get(term, [term]):
                alias = str(alias or "").strip()
                if not alias:
                    continue
                key = alias.lower()
                if key in seen:
                    continue
                seen.add(key)
                patterns.append(f"%{alias}%")
        return patterns

    @staticmethod
    def _keyword_hit_score(
        title: str,
        content: str,
        terms: List[str],
        base_bonus: float,
    ) -> Dict[str, Any]:
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

        if not matched_terms:
            return {"bonus": 0.0, "matched_terms": []}

        coverage = len(set(matched_terms)) / max(len(set(terms)), 1)
        title_coverage = title_hit_count / max(len(set(terms)), 1)
        bonus = base_bonus + (0.05 * coverage) + (0.02 * title_coverage)
        return {
            "bonus": min(bonus, 0.15),
            "matched_terms": matched_terms,
        }

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

    async def _search_by_domain_terms(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        terms = self._extract_domain_terms(query)
        if not terms:
            return []

        patterns = self._term_alias_patterns(terms)
        if not patterns:
            return []

        candidates: List[Dict[str, Any]] = []

        breakdown_fields = [
            DocumentBreakdown.title,
            DocumentBreakdown.problem_intro,
            DocumentBreakdown.causes,
            DocumentBreakdown.evaluation,
            DocumentBreakdown.inspection,
            DocumentBreakdown.solutions,
            DocumentBreakdown.key_points,
        ]
        breakdown_conditions = [
            field.like(pattern)
            for field in breakdown_fields
            for pattern in patterns
        ]
        breakdown_result = await self.db.execute(
            select(DocumentBreakdown)
            .where(DocumentBreakdown.is_deleted == 0, or_(*breakdown_conditions))
            .order_by(DocumentBreakdown.first_edit_date.desc())
            .limit(limit)
        )

        for doc in breakdown_result.scalars().all():
            parts = [
                getattr(doc, "problem_intro", "") or "",
                getattr(doc, "causes", "") or "",
                getattr(doc, "evaluation", "") or "",
                getattr(doc, "inspection", "") or "",
                getattr(doc, "solutions", "") or "",
                getattr(doc, "key_points", "") or "",
            ]
            content = "\n".join(part for part in parts if part).strip()
            score_info = self._keyword_hit_score(doc.title or "", content, terms, 0.04)
            if not score_info["matched_terms"]:
                continue
            keyword_bonus = float(score_info["bonus"])
            candidates.append({
                "doc_id": doc.id,
                "library_type": "breakdown",
                "title": doc.title or "",
                "content": content,
                "image_url": self._first_image_url(doc.image_urls),
                "score": min(1.0, self.keyword_recall_seed_score + keyword_bonus),
                "vector_score": 0.0,
                "term_bonus": keyword_bonus,
                "matched_terms": score_info["matched_terms"],
                "metadata": {
                    "content_type": "keyword_document",
                    "retrieval_source": "keyword",
                },
            })

        knowledge_conditions = [
            field.like(pattern)
            for field in (
                DocumentKnowledge.title,
                KnowledgeDocumentSection.section_title,
                KnowledgeDocumentSection.plain_text,
            )
            for pattern in patterns
        ]
        knowledge_result = await self.db.execute(
            select(DocumentKnowledge, KnowledgeDocumentSection)
            .join(KnowledgeDocumentSection, KnowledgeDocumentSection.document_id == DocumentKnowledge.id)
            .where(
                DocumentKnowledge.is_deleted == 0,
                KnowledgeDocumentSection.document_library_type == "knowledge",
                or_(*knowledge_conditions),
            )
            .order_by(DocumentKnowledge.first_edit_date.desc(), KnowledgeDocumentSection.section_index.asc())
            .limit(limit)
        )

        for doc, section in knowledge_result.all():
            section_title = section.section_title or ""
            content = "\n".join(part for part in [section_title, section.plain_text or ""] if part).strip()
            title_hit = self._keyword_hit_score(doc.title or "", content, terms, 0.08)
            section_hit = self._keyword_hit_score(section_title, content, terms, 0.06)
            body_hit = self._keyword_hit_score(doc.title or "", content, terms, 0.04)
            score_info = max([title_hit, section_hit, body_hit], key=lambda item: float(item["bonus"]))
            if not score_info["matched_terms"]:
                continue
            keyword_bonus = float(score_info["bonus"])
            metadata = dict(section.section_metadata or {})
            metadata.update({
                "content_type": "keyword_section",
                "retrieval_source": "keyword",
                "section_title": section_title,
                "section_index": section.section_index,
            })
            candidates.append({
                "doc_id": doc.id,
                "library_type": "knowledge",
                "title": doc.title or "",
                "content": content,
                "image_url": self._first_image_url(section.image_urls),
                "score": min(1.0, self.keyword_recall_seed_score + keyword_bonus),
                "vector_score": 0.0,
                "term_bonus": keyword_bonus,
                "matched_terms": score_info["matched_terms"],
                "metadata": metadata,
            })

        candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return candidates[:limit]

    @staticmethod
    def _merge_retrieval_candidates(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for item in results:
            metadata = item.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
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
            existing_source = (existing.get("metadata") or {}).get("retrieval_source") if isinstance(existing.get("metadata"), dict) else ""
            item_source = (item.get("metadata") or {}).get("retrieval_source") if isinstance(item.get("metadata"), dict) else ""
            existing_bonus = float(existing.get("term_bonus", 0.0))
            item_bonus = float(item.get("term_bonus", 0.0))

            if existing_source == "keyword" and item_source != "keyword":
                item["score"] = min(1.0, item_score + existing_bonus)
                item["term_bonus"] = max(float(item.get("term_bonus", 0.0)), existing_bonus)
                item["matched_terms"] = existing.get("matched_terms") or item.get("matched_terms") or []
                merged[key] = item
                continue

            if item_source == "keyword" and existing_source != "keyword":
                existing["score"] = min(1.0, existing_score + item_bonus)
                existing["term_bonus"] = max(existing_bonus, item_bonus)
                existing["matched_terms"] = item.get("matched_terms") or existing.get("matched_terms") or []
                continue

            if item_score > existing_score:
                merged[key] = item
            else:
                existing["score"] = existing_score

        return list(merged.values())

    @staticmethod
    def _debug_print_search_results(stage: str, results: List[Dict[str, Any]], limit: int = 20):
        print(f"\n========== RAG DEBUG: {stage} count={len(results or [])} ==========")
        for index, item in enumerate((results or [])[:limit], start=1):
            metadata = item.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
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

            keyword_results = await self._search_by_domain_terms(query)
            if keyword_results:
                self._debug_print_search_results("keyword term results", keyword_results)
                all_results.extend(keyword_results)

            if not all_results:
                return []

            all_results = self._merge_retrieval_candidates(all_results)
            all_results.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
            self._debug_print_search_results("raw vector results", all_results)
            all_results = self._apply_domain_term_score(all_results, query)
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
            self._debug_print_search_results("grouped docs after threshold", docs)
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
