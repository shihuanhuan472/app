import os
import re
from dotenv import load_dotenv
load_dotenv()
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")

from PIL import Image
from qwen_token_counter import get_token_count
import base64
import mimetypes
from functools import lru_cache

from pymilvus.orm import utility
from openai import OpenAI
import torch  # 添加导入
from typing import List, Dict, Any, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType
from models import Document
from utils.ai_endpoint import get_ai_base_url
import json
from visual_bge.visual_bge.modeling import Visualized_BGE


@lru_cache(maxsize=8192)
def _count_tokens_cached(text: str) -> int:
    return int(get_token_count(text or ""))

"""
向量生成的核心模块，使用BAAI/bge-m3模型
"""

class VectorStoreMultimodal:
    KNOWLEDGE_DOC_ID_OFFSET = 1000000000

    def __init__(self):
        # 检测可用设备
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        print(f"Using device: {device}")
        print(f"HF_HOME = {os.environ.get('HF_HOME')}")
        print(f"TRANSFORMERS_CACHE = {os.environ.get('TRANSFORMERS_CACHE')}")

        # self.model_embedding = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
        # self.model_embedding_local = os.getenv("EMBEDDING_MODEL_LOCAL_PATH",
        #                                         "D:/Pycharm/code/Maintenance_Assistance_System/embedding-model")

        self.model_name = os.getenv("MODEL_NAME", "BAAI/bge-m3")
        self.model_weight = os.getenv("MODEL_WEIGHT", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\Visualized_m3.pth")
        self.model = Visualized_BGE(model_name_bge=self.model_name,
                                    model_weight=self.model_weight)
        self.model.eval()
        self.model.to(self.device)
        self.image_config = self.get_config()

        self.top_k = int(os.getenv("TOP_K", 3))
        # 虽然有硬分块参数，但是其实根本没用上（最开始怕某个字段特别长，后来感觉再长也不会好几千个字）
        self.chunk_size = int(os.getenv("CHUNK_SIZE", 500))
        self.overlap = int(os.getenv("OVERLAP", 50))

        self.embedding_dim = 1024

        # 连接Milvus
        self.connect_milvus()

        # 创建或加载Collection
        # self.collection_name = "documents_collection_multimodal" # 原始的collection
        # self.collection_name = "documents_collection_new" # 图片单独分块
        self.collection_name = "documents_collection_main_chunk" # 加上主chunk
        # self.collection_name = "documents_collection_reranker" # 主chunk + 图像语义
        self.create_or_load_collection()

        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model_chat = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = 2000
        self.chat_context_window = int(os.getenv("LLM_CONTEXT_WINDOW", 4096))
        self.context_margin_token = int(os.getenv("CONTEXT_MARGIN_TOKEN", 128))
        self.image_input_token = int(os.getenv("IMAGE_INPUT_TOKEN", 1500))
        self.enable_vector_image_description = os.getenv("ENABLE_VECTOR_IMAGE_DESCRIPTION", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.enable_knowledge_main_chunk_ai = os.getenv("ENABLE_KNOWLEDGE_MAIN_CHUNK_AI", "0").strip().lower() in {"1", "true", "yes", "on"}

    def _normalize_library_type(self, library_type: str) -> str:
        """统一库类型，保证向量库 metadata 中只出现 breakdown/knowledge 两种值。"""
        return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"

    def _get_document_library_type(self, document: Document) -> str:
        """从 ORM 对象取库类型，默认兼容旧文档为故障库。"""
        return self._normalize_library_type(getattr(document, "library_type", "breakdown"))

    def _encode_doc_id(self, doc_id: int, library_type: str) -> int:
        """给知识库文档 id 加偏移，避免两张表自增 id 在 Milvus 中冲突。"""
        return int(doc_id) + self.KNOWLEDGE_DOC_ID_OFFSET if self._normalize_library_type(library_type) == "knowledge" else int(doc_id)

    def _decode_doc_id(self, encoded_doc_id: int, library_type: str) -> int:
        """把 Milvus 内部 id 还原为数据库表中的真实 id。"""
        return int(encoded_doc_id) - self.KNOWLEDGE_DOC_ID_OFFSET if self._normalize_library_type(library_type) == "knowledge" else int(encoded_doc_id)

    def _normalize_tags(self, raw_tags) -> List[Any]:
        if isinstance(raw_tags, str):
            try:
                raw_tags = json.loads(raw_tags)
            except Exception:
                raw_tags = [raw_tags]
        if raw_tags is None:
            raw_tags = []
        result = []
        seen = set()
        for tag in raw_tags:
            text = str(tag).strip()
            if not text:
                continue
            value = int(text) if text.isdigit() else text
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def _tags_to_text(self, raw_tags) -> str:
        return "，".join(str(tag) for tag in self._normalize_tags(raw_tags))

    def _normalize_image_urls(self, image_urls) -> List[str]:
        if not image_urls:
            return []
        if isinstance(image_urls, str):
            try:
                image_urls = json.loads(image_urls)
            except Exception:
                image_urls = [item.strip() for item in image_urls.split(",") if item.strip()]
        return [str(item).strip() for item in image_urls if str(item).strip()]

    def _absolute_document_image_path(self, image_url: str) -> str:
        if not image_url:
            return ""
        if os.path.isabs(image_url):
            return image_url
        return os.path.join(self.get_config()["DOCUMENT_IMAGE_BASE_DIR"], image_url)

    def get_config(self):
        IMAGE_DIR: str = os.getenv("IMAGE_DIR", "upload/images")
        BASE_DIR: str = os.getenv("BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        MESSAGE_IMAGE_DIR: str = os.getenv("MESSAGE_IMAGE_DIR", "upload/images")
        MESSAGE_BASE_DIR: str = os.getenv("MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")

        return {
            "DOCUMENT_IMAGE_DIR": IMAGE_DIR,
            "DOCUMENT_IMAGE_BASE_DIR": BASE_DIR,
            "MESSAGE_IMAGE_DIR": MESSAGE_IMAGE_DIR,
            "MESSAGE_BASE_DIR": MESSAGE_BASE_DIR
        }

    def connect_milvus(self):
        """连接Milvus数据库"""
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")

        try:
            # 先检查是否已连接
            try:
                connections.get_connection(alias="default")
                connections.disconnect(alias="default")
            except:
                pass

            connections.connect(
                alias="default",
                host=milvus_host,
                port=milvus_port,
                timeout=10  # 添加超时
            )
            print(f"Connected to Milvus at {milvus_host}:{milvus_port}")

            # 测试连接
            version = utility.get_server_version()
            print(f"Milvus server version: {version}")

        except Exception as e:
            print(f"Failed to connect to Milvus: {e}")
            raise

    def create_or_load_collection(self):
        """创建或加载集合"""

        # 检查集合是否存在
        if utility.has_collection(self.collection_name):
            print(f"集合 '{self.collection_name}' 已存在，加载集合...")
            self.collection = Collection(self.collection_name)

            # 验证schema是否匹配
            if not self.validate_collection_schema():
                utility.drop_collection(self.collection_name)
                self.create_new_collection()

            # 检查索引是否存在
            if not self.collection.has_index():
                print("集合没有索引，正在创建索引...")
                self.create_index()

            self.load_collection()
        else:
            print(f"集合 '{self.collection_name}' 不存在，创建新集合...")
            self.create_new_collection()

    def validate_collection_schema(self):
        """验证集合schema是否匹配"""
        try:
            # 检查关键字段是否存在
            fields = self.collection.schema.fields
            field_names = [field.name for field in fields]

            required_fields = ["doc_id", "chunk_id", "title", "content", "image_url", "embedding", "metadata"]

            for field in required_fields:
                if field not in field_names:
                    return False
                    # raise ValueError(f"集合缺少必要字段: {field}")

            # 检查向量维度
            for field in fields:
                if field.name == "embedding":
                    if field.dim != self.embedding_dim:
                        raise ValueError(
                            f"向量维度不匹配: 集合维度={field.dim}, 模型维度={self.embedding_dim}"
                        )

            print("集合schema验证通过")
            return True

        except Exception as e:
            print(f"集合schema验证失败: {e}")
            raise

    def create_index(self):
        """创建索引"""
        index_params = {
            "metric_type": "IP",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128}
        }

        self.collection.create_index("embedding", index_params)
        print("索引创建完成")

    def create_new_collection(self):
        """
        创建新集合
        metadata为原数据
        """
        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=30000),
            FieldSchema(name="image_url", dtype=DataType.VARCHAR, max_length=1000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim),
            FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=30000),
        ]

        schema = CollectionSchema(fields, description="文档向量存储")
        self.collection = Collection(self.collection_name, schema)

        # 创建索引
        self.create_index()

        self.load_collection()

    def load_collection(self):
        """
        加载集合到内存
        （所以快是真快，但也挺吃内存的）
        """
        try:
            print(f"正在加载集合 '{self.collection_name}' 到内存...")
            self.collection.load()
            print(f"集合 '{self.collection_name}' 加载完成")
        except Exception as e:
            # 如果集合已经加载，可能会抛出异常，这里我们捕获并继续
            if "already loaded" in str(e).lower():
                print(f"集合 '{self.collection_name}' 已在内存中")
            else:
                print(f"加载集合失败: {e}")
                raise

    def chunk_document(self, document: Document, chunk_size: int = -1, overlap: int = -1) -> List[Dict]:
        chunks = []
        library_type = self._get_document_library_type(document)
        if library_type == "knowledge":
            return self.chunk_knowledge_document(document)

        vector_doc_id = self._encode_doc_id(document.id, library_type)
        tags = self._normalize_tags(getattr(document, "tag", []) or [])
        tag_text = self._tags_to_text(tags)
        tag_prefix = f"标签：{tag_text}\n" if tag_text else ""
        sections = [
            ("title", "problem_intro", "标题", "问题简介"),
            ("causes", "原因"),
            ("evaluation", "评估"),
            ("inspection", "检查"),
            ("solutions", "解决方案"),
            ("key_points", "总结")
        ]

        # 按照字段分块，标题和问题简介在同一块

        for section in sections:
            content_origin = ""
            # 处理标题块
            if section[0] == "title":
                content_origin = f"{tag_prefix}标题：{document.title}，\n问题简介：{document.problem_intro}"
                images_str = getattr(document, "image_urls_problem_intro", "")
                images = [img.strip() for img in images_str.split(',') if img.strip()] if images_str else []
                flag = 0
                for image in images:
                    url = os.path.join(self.get_config()["DOCUMENT_IMAGE_BASE_DIR"], image)
                    print(url)
                    if os.path.exists(url):
                        flag = 1
                        messages = self.generate_descript_image_messages(url)
                        answer = self.get_ai_answer(messages)
                        content = content_origin + "\n[图像信息]\n" + answer if answer is not None else content_origin
                        print(content)
                        print("=====================")
                        chunk = {
                            "doc_id": vector_doc_id,
                            "title": document.title,
                            "content": content,
                            "image_url": url,
                            "metadata": json.dumps({
                                "contributor_id": document.contributor_id,
                                "source_doc_id": document.id,
                                "library_type": library_type,
                                "tag": tags,
                                "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                                "chunk_index": len(chunks),
                                "subchunk_index": len(chunks),
                                "unit_type": "text",
                                "content_type": "text_with_image_description",
                                "chunk_id": f"{library_type}-{document.id}-{len(chunks)}",
                                "chunk_size": len(content),
                                "semantic_method": "field_section_v1",
                            }, ensure_ascii=False)
                        }
                        chunks.append(chunk)

                        # chunk1 = {
                        #     "doc_id": document.id,
                        #     "title": document.title,
                        #     "content": "",
                        #     "image_url": url,
                        #     "metadata": json.dumps({
                        #         "contributor_id": document.contributor_id,
                        #         "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                        #         "chunk_index": len(chunks),
                        #         "chunk_size": len(content)
                        #     })
                        # }
                        # chunks.append(chunk1)

                # 如果没有图片
                if flag == 0:
                    chunks.append({
                        "doc_id": vector_doc_id,
                        "title": document.title,
                        "content": content_origin,
                        "image_url": "",
                        "metadata": json.dumps({
                            "contributor_id": document.contributor_id,
                            "source_doc_id": document.id,
                            "library_type": library_type,
                            "tag": tags,
                            "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                            "chunk_index": len(chunks),
                            "subchunk_index": len(chunks),
                            "unit_type": "text",
                            "content_type": "section_text",
                            "chunk_id": f"{library_type}-{document.id}-{len(chunks)}",
                            "chunk_size": len(content_origin),
                            "semantic_method": "field_section_v1",
                        }, ensure_ascii=False)
                    })
                continue
            if getattr(document, section[0], None) == None:
                content_origin = f"{tag_prefix}{section[1]}："
            else:
                content_origin = f"{tag_prefix}{section[1]}：{getattr(document, section[0], None)}"
            images_str = getattr(document, f"image_urls_{section[0]}", "")
            images = [img.strip() for img in images_str.split(',') if img.strip()] if images_str else []
            flag = 0
            for image in images:
                url = os.path.join(self.get_config()["DOCUMENT_IMAGE_BASE_DIR"], image)
                if os.path.exists(url):
                    flag = 1
                    messages = self.generate_descript_image_messages(url)
                    answer = self.get_ai_answer(messages)
                    content = content_origin + "\n[图像信息]\n" + answer if answer is not None else content_origin
                    print(content)
                    print("=======================")
                    chunks.append({
                        "doc_id": vector_doc_id,
                        "title": document.title,
                        "content": content,
                        "image_url": url,
                        "metadata": json.dumps({
                            "contributor_id": document.contributor_id,
                            "source_doc_id": document.id,
                            "library_type": library_type,
                            "tag": tags,
                            "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                            "chunk_index": len(chunks),
                            "subchunk_index": len(chunks),
                            "unit_type": "text",
                            "content_type": "text_with_image_description",
                            "chunk_id": f"{library_type}-{document.id}-{len(chunks)}",
                            "chunk_size": len(content),
                            "semantic_method": "field_section_v1",
                        }, ensure_ascii=False)
                    })
                    #
                    # chunks.append({
                    #     "doc_id": document.id,
                    #     "title": document.title,
                    #     "content": "",
                    #     "image_url": url,
                    #     "metadata": json.dumps({
                    #         "contributor_id": document.contributor_id,
                    #         "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                    #         "chunk_index": len(chunks),
                    #         "chunk_size": len(content)
                    #     })
                    # })

            # 没图片但是有文本
            if flag == 0 and len(str(getattr(document, section[0], "") or "")) > 0:
                chunks.append({
                    "doc_id": vector_doc_id,
                    "title": document.title,
                    "content": content_origin,
                    "image_url": "",
                        "metadata": json.dumps({
                            "contributor_id": document.contributor_id,
                            "source_doc_id": document.id,
                            "library_type": library_type,
                            "tag": tags,
                            "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                            "chunk_index": len(chunks),
                            "subchunk_index": len(chunks),
                            "unit_type": "text",
                            "content_type": "section_text",
                            "chunk_id": f"{library_type}-{document.id}-{len(chunks)}",
                            "chunk_size": len(content_origin),
                            "semantic_method": "field_section_v1",
                        }, ensure_ascii=False)
                    })
        return chunks

    def prepare_knowledge_sections(self, sections: List[Dict]) -> List[Dict]:
        """准备知识库章节；默认不调用视觉模型逐图生成描述，只保留图片位置。"""
        prepared = []
        for section in sections or []:
            section = dict(section)
            metadata = dict(section.get("metadata") or {})
            image_urls = self._normalize_image_urls(section.get("image_urls"))
            image_descriptions = list(metadata.get("image_descriptions") or [])
            if not self.enable_vector_image_description:
                # 大 PDF 往往包含上百张图片。逐图调用视觉模型既慢，又容易因为图片 token
                # 超过 4096 上下文报错。默认跳过图片描述，保留图片位置，继续文本分块和向量化。
                metadata["image_descriptions"] = image_descriptions
                section["image_urls"] = image_urls
                section["metadata"] = metadata
                prepared.append(section)
                continue
            description_by_url = {
                item.get("image_url"): item
                for item in image_descriptions
                if isinstance(item, dict) and item.get("image_url")
            }

            for image_url in image_urls:
                if image_url in description_by_url and description_by_url[image_url].get("description"):
                    continue
                absolute_url = self._absolute_document_image_path(image_url)
                description = None
                if os.path.exists(absolute_url):
                    messages = self.generate_descript_image_messages(absolute_url)
                    description = self.get_ai_answer(messages)
                description_by_url[image_url] = {
                    "image_url": image_url,
                    "description": description or "",
                    "described_time": None,
                }

            metadata["image_descriptions"] = [
                description_by_url[image_url]
                for image_url in image_urls
                if image_url in description_by_url
            ]
            section["image_urls"] = image_urls
            section["metadata"] = metadata
            prepared.append(section)
        return prepared

    def _first_existing_image_path(self, image_urls: List[str]) -> str:
        for image_url in image_urls or []:
            absolute_url = self._absolute_document_image_path(image_url)
            if absolute_url and os.path.exists(absolute_url):
                return absolute_url
        return ""

    def _knowledge_section_content(self, document: Document, section: Dict, tag_prefix: str) -> str:
        section_title = section.get("section_title") or getattr(document, "title", "")
        section_text = section.get("plain_text") or ""
        section_type = section.get("section_type") or ""
        parts = [tag_prefix.rstrip()] if tag_prefix else []
        parts.extend([
            f"文档标题：{getattr(document, 'title', '')}",
            f"目录编号：{section_type}",
            f"目录标题：{section_title}",
            f"章节正文：{section_text}",
        ])
        content = "\n".join(part for part in parts if part).strip()
        # Milvus content 字段上限为 30000，按目录整章分块时留出余量。
        if len(content) > 29000:
            content = content[:29000].rstrip() + "\n【提示：该目录章节较长，向量内容已截断，原章节表仍保留完整正文。】"
        return content

    def _is_markdown_table_line(self, line: str) -> bool:
        line = str(line or "").strip()
        return line.startswith("|") and line.endswith("|")

    def _is_markdown_table_separator(self, line: str) -> bool:
        return bool(re.match(r"^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", str(line or "").strip()))

    def _split_markdown_table_row(self, line: str) -> List[str]:
        return [cell.strip() for cell in str(line or "").strip().strip("|").split("|")]

    def _render_markdown_table(self, rows: List[List[str]]) -> str:
        rows = [[str(cell or "").replace("|", " ").strip() for cell in row] for row in rows if any(row)]
        if not rows:
            return ""
        max_cols = max(len(row) for row in rows)
        rows = [row + [""] * (max_cols - len(row)) for row in rows]

        def render(row):
            return "| " + " | ".join(row) + " |"

        return "\n".join([render(rows[0]), render(["---"] * max_cols)] + [render(row) for row in rows[1:]])

    def _normalize_table_header_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())

    def _is_record_table_header(self, header: List[str]) -> bool:
        keys = {self._normalize_table_header_key(cell) for cell in header if cell}
        record_markers = {
            "devicecode",
            "errorcode",
            "code",
            "错误码",
            "错误代码",
            "故障码",
            "报警码",
            "description",
            "vendorcode",
            "workflowstep",
            "devicename",
            "position",
            "设备码",
            "设备名称",
            "描述",
            "处理方法",
            "原因",
            "解决方案",
        }
        has_code_header = any("code" in key or "\u7801" in key for key in keys)
        has_name_header = any("name" in key or "\u540d\u79f0" in key for key in keys)
        return (
            len(keys & record_markers) >= 2
            or any("errorcode" in key or "\u9519\u8bef\u7801" in key for key in keys)
            or (has_code_header and has_name_header)
        )

    def _table_row_to_field_text(self, header: List[str], row: List[str]) -> str:
        pairs = []
        max_cols = max(len(header), len(row))
        for index in range(max_cols):
            key = header[index].strip() if index < len(header) and header[index].strip() else f"字段{index + 1}"
            value = row[index].strip() if index < len(row) else ""
            if value:
                pairs.append(f"{key}: {value}")
        return "\n".join(pairs)

    def _table_row_embedding_text(self, header: List[str], row: List[str]) -> str:
        field_text = self._table_row_to_field_text(header, row)
        compact_values = " ".join(str(cell or "").strip() for cell in row if str(cell or "").strip())
        return "\n".join(part for part in [field_text, compact_values] if part).strip()

    def _strip_markdown_tables(self, text: str) -> str:
        lines = str(text or "").splitlines()
        kept = []
        index = 0
        while index < len(lines):
            if not self._is_markdown_table_line(lines[index]):
                kept.append(lines[index])
                index += 1
                continue
            table_lines = []
            while index < len(lines) and self._is_markdown_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            if len(table_lines) >= 2 and self._is_markdown_table_separator(table_lines[1]):
                header = self._split_markdown_table_row(table_lines[0])
                rows_count = max(0, len(table_lines) - 2)
                kept.append(f"【表格：{' / '.join(cell for cell in header if cell)}，共{rows_count}行，已按表格记录单独向量化。】")
            else:
                kept.extend(table_lines)
        return "\n".join(line for line in kept if str(line).strip()).strip()

    def _split_text_for_vector(self, text: str, max_tokens: int = 1200) -> List[str]:
        text = str(text or "").strip()
        if not text:
            return []
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
        chunks = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n{paragraph}".strip() if current else paragraph
            if current and _count_tokens_cached(candidate) > max_tokens:
                chunks.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append(current)
        final_chunks = []
        for chunk in chunks:
            if _count_tokens_cached(chunk) <= max_tokens:
                final_chunks.append(chunk)
            else:
                final_chunks.append(self._truncate_text_by_tokens(chunk, max_tokens))
        return final_chunks

    def _extract_markdown_tables_for_vector(self, text: str, max_rows: int = 18) -> List[Dict[str, Any]]:
        lines = str(text or "").splitlines()
        chunks = []
        index = 0
        table_index = 0
        while index < len(lines):
            if not self._is_markdown_table_line(lines[index]):
                index += 1
                continue
            start = index
            table_lines = []
            while index < len(lines) and self._is_markdown_table_line(lines[index]):
                table_lines.append(lines[index])
                index += 1
            if len(table_lines) < 2 or not self._is_markdown_table_separator(table_lines[1]):
                continue

            table_index += 1
            header = self._split_markdown_table_row(table_lines[0])
            rows = [self._split_markdown_table_row(line) for line in table_lines[2:]]
            is_record_table = self._is_record_table_header(header)
            if is_record_table:
                for row_index, row in enumerate(rows, start=1):
                    field_text = self._table_row_to_field_text(header, row)
                    if not field_text:
                        continue
                    chunks.append({
                        "table_index": table_index,
                        "table_group_index": row_index - 1,
                        "row_start": row_index,
                        "row_end": row_index,
                        "text": field_text,
                        "embedding_text": self._table_row_embedding_text(header, row),
                        "line_start": start,
                        "table_chunk_type": "record_row",
                        "table_header": header,
                    })
                continue
            for group_index in range(0, len(rows), max_rows):
                group = rows[group_index: group_index + max_rows]
                table_text = self._render_markdown_table([header] + group)
                if not table_text:
                    continue
                chunks.append({
                    "table_index": table_index,
                    "table_group_index": group_index // max_rows,
                    "row_start": group_index + 1,
                    "row_end": group_index + len(group),
                    "text": table_text,
                    "embedding_text": table_text,
                    "line_start": start,
                    "table_chunk_type": "row_group",
                    "table_header": header,
                })
        return chunks

    def chunk_knowledge_document(self, document: Document) -> List[Dict]:
        """Build adaptive vector chunks for knowledge documents.

        Display sections stay intact in the database. Vector chunks are derived
        by content type: section text, record-like table rows, and generic table
        row groups. This keeps the reader faithful to the Word document while
        making table lookup precise.
        """
        chunks = []
        library_type = "knowledge"
        vector_doc_id = self._encode_doc_id(document.id, library_type)
        tags = self._normalize_tags(getattr(document, "tag", []) or [])
        tag_text = self._tags_to_text(tags)
        tag_prefix = f"标签：{tag_text}\n" if tag_text else ""
        sections = self.prepare_knowledge_sections(getattr(document, "knowledge_sections", []) or [])

        for section in sections:
            section_id = section.get("id")
            section_title = section.get("section_title") or getattr(document, "title", "")
            section_text = section.get("plain_text") or ""
            image_urls = self._normalize_image_urls(section.get("image_urls"))
            metadata = section.get("metadata") or {}
            image_positions = metadata.get("image_positions") or []
            if not section_text.strip() and not image_urls:
                continue

            base_metadata = {
                "contributor_id": getattr(document, "contributor_id", None),
                "source_doc_id": getattr(document, "id", None),
                "library_type": library_type,
                "tag": tags,
                "first_edit_date": document.first_edit_date.isoformat() if getattr(document, "first_edit_date", None) else None,
                "section_id": section_id,
                "section_title": section_title,
                "section_type": section.get("section_type"),
                "section_index": section.get("section_index", 0),
                "image_urls": image_urls,
                "image_positions": image_positions,
            }

            text_without_tables = self._strip_markdown_tables(section_text)
            text_chunks = self._split_text_for_vector(text_without_tables)
            if not text_chunks and image_urls:
                text_chunks = [f"图片所在章节：{section_title}"]

            for text_index, text_chunk in enumerate(text_chunks):
                content = "\n".join(
                    part
                    for part in [
                        tag_prefix.rstrip() if tag_prefix else "",
                        f"文档标题：{getattr(document, 'title', '')}",
                        f"目录编号：{section.get('section_type') or ''}",
                        f"目录标题：{section_title}",
                        f"章节正文：{text_chunk}",
                    ]
                    if part
                )
                chunk_metadata = {
                    **base_metadata,
                    "subchunk_index": text_index,
                    "unit_type": "section_text",
                    "content_type": "section_text",
                    "chunk_id": f"knowledge-section-{section_id or section.get('section_index', 0)}-text-{text_index}",
                    "chunk_size": len(content),
                    "semantic_method": "knowledge_section_text_v3",
                    "chunk_strategy": metadata.get("chunk_strategy") or "enterprise_docx_adaptive_text_v1",
                }
                chunks.append({
                    "doc_id": vector_doc_id,
                    "title": getattr(document, "title", ""),
                    "content": content,
                    "embedding_content": content,
                    "image_url": self._first_existing_image_path(image_urls) if text_index == 0 else "",
                    "metadata": json.dumps(chunk_metadata, ensure_ascii=False),
                })

            table_chunks = self._extract_markdown_tables_for_vector(section_text)
            for table_chunk in table_chunks:
                table_content = "\n".join(
                    part
                    for part in [
                        tag_prefix.rstrip() if tag_prefix else "",
                        f"文档标题：{getattr(document, 'title', '')}",
                        f"目录编号：{section.get('section_type') or ''}",
                        f"目录标题：{section_title}",
                        f"表格{table_chunk['table_index']} 行 {table_chunk['row_start']}-{table_chunk['row_end']}：",
                        table_chunk["text"],
                    ]
                    if part
                )
                table_metadata = {
                    **base_metadata,
                    "subchunk_index": table_chunk["table_group_index"],
                    "unit_type": "table_rows",
                    "content_type": "table",
                    "chunk_id": (
                        f"knowledge-table-section-{section_id or section.get('section_index', 0)}"
                        f"-t{table_chunk['table_index']}-g{table_chunk['table_group_index']}"
                    ),
                    "chunk_size": len(table_content),
                    "semantic_method": "knowledge_table_rows_v2",
                    "chunk_strategy": "enterprise_docx_adaptive_table_v2",
                    "table_index": table_chunk["table_index"],
                    "table_group_index": table_chunk["table_group_index"],
                    "row_start": table_chunk["row_start"],
                    "row_end": table_chunk["row_end"],
                    "table_chunk_type": table_chunk.get("table_chunk_type"),
                    "table_header": table_chunk.get("table_header"),
                }
                chunks.append({
                    "doc_id": vector_doc_id,
                    "title": getattr(document, "title", ""),
                    "content": table_content,
                    "embedding_content": "\n".join(
                        part
                        for part in [
                            tag_prefix.rstrip() if tag_prefix else "",
                            f"文档标题：{getattr(document, 'title', '')}",
                            f"目录编号：{section.get('section_type') or ''}",
                            f"目录标题：{section_title}",
                            table_chunk.get("embedding_text") or table_chunk["text"],
                        ]
                        if part
                    ),
                    "image_url": self._first_existing_image_path(image_urls),
                    "metadata": json.dumps(table_metadata, ensure_ascii=False),
                })
        return chunks

    def _truncate_text_by_tokens(self, text: str, token_budget: int) -> str:
        text = text or ""
        if token_budget <= 0:
            return ""
        if _count_tokens_cached(text) <= token_budget:
            return text
        suffix = "\n\n[Content truncated to fit the model context window.]"
        budget = max(1, token_budget - _count_tokens_cached(suffix))
        approx_chars = max(1, budget * 4)
        candidate = text[:approx_chars]
        if _count_tokens_cached(candidate) > budget:
            candidate = text[:max(1, int(approx_chars * 0.75))]
        return candidate.rstrip() + suffix

    def _message_input_tokens(self, messages, image_token: int = None) -> int:
        image_token = image_token or self.image_input_token
        total = 0
        for message in messages or []:
            content = message.get("content", "")
            if isinstance(content, str):
                total += _count_tokens_cached(content)
                continue
            for item in content or []:
                if item.get("type") == "text":
                    total += _count_tokens_cached(item.get("text", ""))
                elif item.get("type") == "image_url":
                    total += image_token
        return total

    def _safe_max_tokens(self, messages, preferred: int = None) -> int:
        preferred = preferred or int(os.getenv("VECTOR_AI_MAX_OUTPUT_TOKEN", 512))
        available = self.chat_context_window - self._message_input_tokens(messages) - self.context_margin_token
        return max(1, min(preferred, self.max_token, available))

    def generate_descript_image_messages(self, image_url: str):
        prompt = """请详细描述图像信息，重点包含设备信息、操作信息、维修信息或知识点。\n仅返回答案，不要任何markdown渲染。回答长度不超过300字。"""
        messages = []
        data = {}
        msg_content = [{"type": "text", "text": prompt}]
        mime_type, _ = mimetypes.guess_type(image_url)
        if mime_type is None:
            ext = os.path.splitext(image_url)[1].lower()
            mime_type = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp'
            }.get(ext, 'image/jpeg')
        image_base64 = self.image_to_base64(image_url)
        msg_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
        })
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        return messages

    def get_ai_answer(self, messages):
        try:
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )

            response = client.chat.completions.create(
                model=self.model_chat,
                messages=messages,
                max_tokens=self._safe_max_tokens(messages, int(os.getenv("IMAGE_DESCRIBE_MAX_OUTPUT_TOKEN", 512)))
            )
            ans = response.choices[0].message.content
            # print(ans)
            return ans
        except Exception as e:
            print("ai回答失败！！！！")
            print(e)
            return None

    def embed_texts(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """
        将文本转换为向量
        （废弃，之前单模用的）
        """
        # 根据是否是查询使用不同的编码方式
        if is_query:
            # 查询使用提示词（query prompt）
            embeddings = self.embedding_model.encode(
                texts,
                prompt_name="query",  # 使用查询提示词
                normalize_embeddings=True,
                show_progress_bar=False
            )
        else:
            # 文档不使用提示词
            embeddings = self.embedding_model.encode(
                texts,
                normalize_embeddings=True,
                show_progress_bar=False
            )

        return embeddings.tolist()

    def add_document(self, document: Document):
        """添加文档到向量数据库"""
        library_type = self._get_document_library_type(document)
        vector_doc_id = self._encode_doc_id(document.id, library_type)

        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题

        chunks = self.chunk_document(document)

        main_chunk = self.get_main_chunk(document)
        # print(111)
        if main_chunk is not None:
            if library_type == "knowledge":
                content = (
                    f"标题：{main_chunk.get('title', getattr(document, 'title', ''))}\n"
                    f"摘要：{main_chunk.get('summary', '')}\n"
                    f"核心主题：{main_chunk.get('core_topic', '')}\n"
                    f"关键知识点：{main_chunk.get('key_points', '')}\n"
                    f"适用范围：{main_chunk.get('scope', '')}\n"
                    f"标签：{main_chunk.get('tags', '')}"
                )
                tags = self._normalize_tags(getattr(document, "tag", []) or [])
                metadata = {
                    "contributor_id": document.contributor_id,
                    "source_doc_id": document.id,
                    "library_type": library_type,
                    "tag": tags,
                    "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                    "section_id": None,
                    "section_title": "文档摘要",
                    "section_index": -1,
                    "subchunk_index": 0,
                    "unit_type": "text",
                    "content_type": "main_chunk",
                    "chunk_id": f"knowledge-main-{document.id}",
                    "chunk_size": len(content),
                    "semantic_method": "knowledge_main_chunk_v1",
                }
            else:
                content = f"问题简介：{main_chunk['problem_intro']}\n核心成因：{main_chunk['causes']}\n关键特征：{main_chunk['feature']}"
                metadata = {
                    "contributor_id": document.contributor_id,
                    "source_doc_id": document.id,
                    "library_type": library_type,
                    "tag": self._normalize_tags(getattr(document, "tag", []) or []),
                    "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                    "chunk_index": len(chunks),
                    "subchunk_index": len(chunks),
                    "unit_type": "text",
                    "content_type": "main_chunk",
                    "chunk_id": f"{library_type}-main-{document.id}",
                    "chunk_size": len(content),
                    "semantic_method": "field_main_chunk_v1",
                }
            chunks.append({
                "doc_id": vector_doc_id,
                "title": document.title,
                "content": content,
                "image_url": "",
                "metadata": json.dumps(metadata, ensure_ascii=False)
            })
        # print(222)

        if not chunks:
            return

        # 准备批量插入数据
        data = []
        chunk_contents = []

        for i, chunk in enumerate(chunks):
            data.append([
                chunk["doc_id"],  # doc_id
                i,  # chunk_id
                chunk["title"],  # title
                chunk["content"],  # content
                chunk["image_url"],  # image_url
                chunk["metadata"]  # metadata
            ])
            chunk_contents.append(chunk["content"])
        # print(333)
        # 生成向量（文档不使用提示词）
        # embeddings = self.embed_texts(chunk_contents, is_query=False)
        embeddings = self.embed_multimodal(chunks)
        # print(444)
        # 插入数据
        insert_data = [
            [item[0] for item in data],  # doc_ids
            [item[1] for item in data],  # chunk_ids
            [item[2] for item in data],  # titles
            [item[3] for item in data],  # contents
            [item[4] for item in data],  # image_url
            embeddings,  # embeddings
            [item[5] for item in data]  # metadata
        ]

        self.collection.insert(insert_data)
        self.collection.flush()
        print(f"Added {len(chunks)} chunks from document {document.id} to vector store")
        print(embeddings[0])

    def search(self, query_text: str, query_image=None, top_k: int = -1) -> List[Dict]:
        """搜索相似文档"""
        # 返回格式举例
        # {
        #     "contributor_id": 2,
        #     "source_doc_id": 17,
        #     "library_type": "knowledge",
        #     "tag": [],
        #     "first_edit_date": "2026-01-20T08:00:00",
        #     "section_id": 3,                // 知识库独有：章节主键
        #     "section_title": "供电模块",     // 知识库独有：章节标题
        #     "section_type": "3.2",          // 知识库独有：目录编号
        #     "section_index": 2,             // 知识库独有：章节排序序号
        #     "image_urls": ["..."],          // 知识库独有：章节关联图片列表
        #     "image_positions": [],
        #     "subchunk_index": 0,
        #     "unit_type": "section_text",
        #     "content_type": "section_text",
        #     "chunk_id": "knowledge-section-3-text-0",
        #     "chunk_size": 412,
        #     "semantic_method": "knowledge_section_text_v3",
        #     "chunk_strategy": "enterprise_docx_adaptive_text_v1"
        # }
        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题
        print("调用VectorStoreMultimodal的search函数")
        # 查询使用提示词
        top_k = self.top_k if top_k < 1 else top_k
        # query_embedding = self.embed_texts([query], is_query=True)[0]
        query_embedding = self.embed_multimodal_query(query_text, query_image)

        # 搜索参数
        search_params = {
            "metric_type": "IP",
            "params": {"nprobe": 10}
        }

        # print(f"query_embedding完成，{query_embedding}")

        # 执行搜索
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["doc_id", "title", "content", "image_url", "metadata"]
        )

        # 整理结果
        retrieved_docs = []
        for hits in results:
            for hit in hits:
                metadata = json.loads(hit.entity.get("metadata"))
                library_type = self._normalize_library_type(metadata.get("library_type", "breakdown"))
                retrieved_docs.append({
                    "doc_id": metadata.get("source_doc_id", self._decode_doc_id(hit.entity.get("doc_id"), library_type)),
                    "library_type": library_type,
                    "title": hit.entity.get("title"),
                    "content": hit.entity.get("content"),
                    "image_url": hit.entity.get("image_url"),
                    "metadata": metadata,
                    "score": hit.score
                })

        # 按分数排序
        retrieved_docs.sort(key=lambda x: x["score"], reverse=True)
        # print(retrieved_docs)
        return retrieved_docs

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

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

    def delete_document(self, doc_id: int, library_type: str = "breakdown"):
        """从向量库中删除文档"""
        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题
        vector_doc_id = self._encode_doc_id(doc_id, library_type)
        self.collection.delete(f'doc_id == {vector_doc_id}')
        self.collection.flush()
        print(f"Deleted document {doc_id} from vector store")

    def generate_message(self, content, images):
        messages = []
        data = {}
        prompt = """我将提供一段设备维修相关的内容，请根据后续文本和图像内容，按照给定模板，总结提炼核心内容。
【模板】：
标题：<简洁的标题>
问题简介：<提取核心的问题介绍，点明设备类型，品牌，问题等核心内容>
核心原因：<点明核心原因>
关键特征：<故障的核心特征，重点描述感官现象，如：黑屏，气味刺鼻等>

输出JSON格式如下：
{{
    "title": "标题",
    "problem_intro": "问题简介",
    "causes": "核心原因",
    "feature": "关键特征"
}}

注意：
1. 不得杜撰内容，请确保内容的准确性与简洁性。
2. 若包含图像，可以从图像中分析特征。
3. 回答长度不得超过500个token。
4. 关键特征部分可相对详细一点。

现在给定内容如下：
[文本内容]
{text}

[图片内容由后续base64给出]
""".format(text=content)
        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = _count_tokens_cached(prompt)
        if images is not None:
            for image in images:
                image = image.strip()
                compress_image = self.compress_image(image, max_size=768)
                mime_type, _ = mimetypes.guess_type(compress_image)
                if mime_type is None:
                    ext = os.path.splitext(compress_image)[1].lower()
                    mime_type = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.webp': 'image/webp',
                        '.bmp': 'image/bmp'
                    }.get(ext, 'image/jpeg')
                image_base64 = self.image_to_base64(compress_image)

                if token_cnt + 578 > 6000:
                    break
                token_cnt += 578

                msg_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                })
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        return messages

    def generate_knowledge_message(self, content, images):
        messages = []
        data = {}
        prompt_template = """我将提供一段知识库文档内容，请根据文本内容，总结文档级检索入口。
请不要使用“故障原因、检查、解决方案”等故障库模板字段。

输出JSON格式如下：
{{
    "title": "标题",
    "summary": "文档摘要",
    "core_topic": "核心主题",
    "key_points": "关键知识点",
    "scope": "适用范围",
    "tags": "相关标签"
}}

注意：
1. 不得杜撰内容，必须基于给定文本。
2. 回答仅包含JSON对象，不要输出其他内容。
3. 回答长度不得超过500个token。

现在给定内容如下：
[文本内容]
{text}
"""
        preferred_output = int(os.getenv("KNOWLEDGE_MAIN_CHUNK_MAX_OUTPUT_TOKEN", 512))
        base_prompt = prompt_template.format(text="")
        text_budget = self.chat_context_window - preferred_output - self.context_margin_token - _count_tokens_cached(base_prompt)
        content = self._truncate_text_by_tokens(content or "", text_budget)
        prompt = prompt_template.format(text=content)
        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = _count_tokens_cached(prompt)

        include_images = os.getenv("ENABLE_KNOWLEDGE_MAIN_CHUNK_IMAGES", "0").strip().lower() in {"1", "true", "yes", "on"}
        if include_images and images is not None:
            for image in images:
                image = image.strip()
                if not image:
                    continue
                image_path = self._absolute_document_image_path(image)
                if not os.path.exists(image_path):
                    continue
                compress_image = self.compress_image(image_path, max_size=336)
                mime_type, _ = mimetypes.guess_type(compress_image)
                if mime_type is None:
                    ext = os.path.splitext(compress_image)[1].lower()
                    mime_type = {
                        '.png': 'image/png',
                        '.jpg': 'image/jpeg',
                        '.jpeg': 'image/jpeg',
                        '.webp': 'image/webp',
                        '.bmp': 'image/bmp'
                    }.get(ext, 'image/jpeg')
                if token_cnt + self.image_input_token > self.chat_context_window - preferred_output - self.context_margin_token:
                    break
                image_base64 = self.image_to_base64(compress_image)
                token_cnt += self.image_input_token
                msg_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
                })
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        return messages

    def get_main_chunk(self, document: Document):
        if self._get_document_library_type(document) == "knowledge":
            sections = getattr(document, "knowledge_sections", []) or []
            section_text = "\n".join(
                f"【{section.get('section_title') or '章节'}】\n{section.get('plain_text') or ''}"
                for section in sections[:8]
            )
            tag_text = self._tags_to_text(getattr(document, "tag", []) or [])
            content = (
                f"【标题】：{getattr(document, 'title', '')}\n"
                f"【章节内容】：{section_text}\n"
                f"【标签】：{tag_text}\n"
            )
            images = []
            for section in sections:
                images.extend(self._normalize_image_urls(section.get("image_urls")))

            fallback_summary = section_text[:300]
            fallback_main_chunk = {
                "title": getattr(document, "title", ""),
                "summary": fallback_summary,
                "core_topic": getattr(document, "title", ""),
                "key_points": "；".join(
                    str(section.get("section_title") or "").strip()
                    for section in sections[:8]
                    if str(section.get("section_title") or "").strip()
                ) or fallback_summary,
                "scope": "",
                "tags": tag_text,
            }

            if not self.enable_knowledge_main_chunk_ai:
                return fallback_main_chunk

            try:
                client = OpenAI(
                    base_url=get_ai_base_url(),
                    api_key=self.api_key
                )
                messages = self.generate_knowledge_message(content, images)
                response = client.chat.completions.create(
                    model=self.model_chat,
                    messages=messages,
                    max_tokens=self._safe_max_tokens(messages, int(os.getenv("KNOWLEDGE_MAIN_CHUNK_MAX_OUTPUT_TOKEN", 512)))
                )
                ans = response.choices[0].message.content
                print("生成知识库主chunk的ai回答")
                print(ans)
                return json.loads(ans)
            except Exception as e:
                print(f"知识库主chunk AI生成失败，使用文本兜底主chunk: {e}")
                return fallback_main_chunk

        content = f"【标题】：{document.title}\n【问题简介】：{document.problem_intro}\n【成因】：{document.causes}\n"

        if document.image_urls_problem_intro is not None and document.image_urls_problem_intro != "":
            images = document.image_urls_problem_intro.split(", ")
        else:
            images = []
        if document.image_urls_causes is not None and document.image_urls_causes != "":
            images.extend(document.image_urls_causes.split(", "))

        try:
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )
            messages = self.generate_message(content, images)

            response = client.chat.completions.create(
                model=self.model_chat,
                messages=messages,
                max_tokens=self._safe_max_tokens(messages, int(os.getenv("MAIN_CHUNK_MAX_OUTPUT_TOKEN", 512)))
            )
            ans = response.choices[0].message.content
            print("生成主chunk的ai回答")
            print(ans)
            result = json.loads(ans)
            return result
        except Exception as e:
            print(e)
            return None

    def embed_multimodal(self, chunks):
        """生成向量"""
        embeds = []
        with torch.no_grad():
            for chunk in chunks:
                chunk["image_url"] = None if chunk["image_url"] == "" else chunk["image_url"]
                embedding_text = self._safe_embedding_text(
                    chunk.get("embedding_content") or chunk.get("content") or ""
                )
                encode_result = self.model.encode(text=embedding_text, image=chunk["image_url"])
                embeds.append(encode_result.cpu().numpy().flatten().tolist())
        return embeds

    def _safe_embedding_text(self, text: str, token_limit: int = 7800) -> str:
        text = text or ""
        if _count_tokens_cached(text) <= token_limit:
            return text
        suffix = "\n【提示：该内容超过向量模型长度限制，已截断用于向量化。】"
        budget = max(1, token_limit - _count_tokens_cached(suffix))
        low, high = 0, len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid]
            if _count_tokens_cached(candidate) <= budget:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return (best or text[: max(1, budget * 2)]).rstrip() + suffix

    def embed_multimodal_query(self, text=None, image=None):
        """查询"""
        # print(f"{text}")
        # if image is None:
        #     print("None")
        # else:
        #     print(image)
        with torch.no_grad():
            encode_result = self.model.encode(text=text, image=image)
        return encode_result.cpu().numpy().flatten().tolist()

# 全局向量存储实例
vector_store_multimodal = VectorStoreMultimodal()

if __name__ == "__main__":
    # pass
    connections.connect(host='localhost', port='19530')
    collection_name = "documents_collection_reranker"
    if utility.has_collection(collection_name):
        collection = Collection(collection_name)
        collection.drop()
        print(f"集合 '{collection_name}' 已成功删除。")

    # pass
    # vector_store = VectorStoreMultimodal()
# #
#     document = Document()
#     document.id = 100
#     document.title = "笔记本电脑开机黑屏"
#     document.contributor_id = 1
#     document.problem_intro = "用户按下笔记本电脑电源键后，电源指示灯亮起，风扇正常转动，但屏幕始终保持黑屏状态，无法显示任何图像或Logo。"
#     document.causes = """可能导致此故障的原因主要有：
# 电源或电池问题：供电不稳导致屏幕无法正常点亮。
# 显示器或排线故障：屏幕本身损坏或连接屏幕的排线松动。
# 内存或硬件接触不良：内存条松动导致无法完成开机自检。
# 显卡或主板故障：集成或独立显卡异常，或主板电路出现问题。
# 系统或BIOS异常：严重系统错误或BIOS设置混乱。"""
#     document.evaluation = """故障等级：中级（可能涉及硬件检测，但通常可自行排查）。
# 维修建议：优先尝试简单排查，若无效应送修专业机构。"""
#     document.inspection = """连接外接显示器，按“Win + P”切换显示模式，检查是否外接显示器能正常显示。
#
# 尝试重启电脑，并反复按“F2”或“Del”键（根据不同品牌）尝试进入BIOS界面。
# 关闭电脑，断开电源和电池，打开后盖重新插拔内存条，用橡皮擦拭金手指后重新安装。
# 检查屏幕排线是否松动（需一定动手能力，参考型号拆机教程）。
# 若以上无效，尝试清除CMOS（重置BIOS，需查找主板电池或跳线）。"""
#     document.solutions = """若外接显示器正常，说明屏幕或排线故障，需更换屏幕或维修排线。
# 若能进入BIOS，可能是系统问题，尝试重装系统或恢复BIOS默认设置。
# 重新插拔内存后问题解决，说明是接触不良，固定好内存即可。
# 若所有步骤无效，可能是主板或显卡硬件损坏，建议送修专业维修点检测。"""
#     document.key_points = "笔记本电脑黑屏是常见问题，多数情况下可通过重新插拔内存、外接显示器测试等方式解决。操作前请确保断电，若缺乏经验，切勿强行拆卸精密部件。定期清理电脑内部灰尘、避免撞击可减少此类故障发生。如问题复杂，应及时寻求专业技术支持。"
#     document.is_vectorized = 0
#     document.image_urls_problem_intro = "upload/images/20260119_020444_25afb7e930ca4f7dbcbcf880e931d392.jpg, upload/images/20260119_212456_7ffd38b88ef54bcbb3512642c07cc9c2.jpg"
#     document.image_urls_solutions = "upload/images/20260304_211154_3c248201b81e4da6983d2f4407f07774.jpg, upload/images/20260123_061045_c5299e11599c47cf8bec7d6c9156d0c5.jpg"
#
#     # chunk_document = vector_store.chunk_document(document, chunk_size=100, overlap=10)
#     # print(chunk_document)
#     # print(len(chunk_document))
#
#     vector_store.add_document(document)

    # # 准备批量插入数据
    # data = []
    # chunk_contents = []
    #
    # for i, chunk in enumerate(chunk_document):
    #     data.append([
    #         chunk["doc_id"],  # doc_id
    #         i,  # chunk_id
    #         chunk["title"],  # title
    #         chunk["content"],  # content
    #         chunk["metadata"]  # metadata
    #     ])
    #     chunk_contents.append(chunk["content"])
    #
    # # 生成向量（文档不使用提示词）
    # embeddings = vector_store.embed_texts(chunk_contents, is_query=False)
    # # print(embeddings)
    # # print(len(embeddings))

    # query = "笔记本电脑黑屏"
    # vector_store.search(query)
