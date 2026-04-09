# -*- coding: utf-8 -*-
"""
LangChain 多模态 RAG 系统
使用 BGE-Visualized 直接嵌入图片，Milvus 向量库，本地 LLM 生成答案
模型导入方式与用户项目保持一致
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
import torch
import json
from typing import List, Tuple, Optional, Dict
from PIL import Image

from unstructured.partition.docx import partition_docx
from visual_bge.visual_bge.modeling import Visualized_BGE

# LangChain 相关导入
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

# Milvus 相关
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility

from unstructured.partition.pdf import partition_pdf


# ===================== 1. 初始化 BGE-Visualized 模型（与您的代码一致） =====================
class BGEM3MultimodalEmbeddings:
    """封装 BGE-Visualized 模型，提供文本/图片嵌入接口"""

    def __init__(self):
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        print(f"Using device: {device}")

        # 环境变量或直接指定（与您的代码风格一致）
        self.model_name = os.getenv("MODEL_NAME", "BAAI/bge-m3")
        self.model_weight = os.getenv("MODEL_WEIGHT",
                                      "D:/Pycharm/code/Maintenance_Assistance_System/bge/Visualized_m3.pth")

        # 完全按照您的初始化方式
        self.model = Visualized_BGE(
            model_name_bge=self.model_name,
            model_weight=self.model_weight
        )
        self.model.eval()
        self.model.to(self.device)
        self.embedding_dim = 1024  # BGE-M3 稠密向量维度

    def embed_text(self, text: str) -> List[float]:
        """将文本转为向量"""
        with torch.no_grad():
            vec = self.model.encode(text=text)
            return vec.cpu().numpy().flatten().tolist()

    def embed_image(self, image: Image.Image) -> List[float]:
        """将 PIL 图片转为向量"""
        with torch.no_grad():
            vec = self.model.encode(image=image)
            return vec.cpu().numpy().flatten().tolist()

    def embed_query(self, text: str, image: Optional[Image.Image] = None) -> List[float]:
        """查询时可同时传入文本和图片，或者单独文本"""
        with torch.no_grad():
            vec = self.model.encode(text=text, image=image)
            return vec.cpu().numpy().flatten().tolist()


# ===================== 2. Milvus 向量库管理 =====================
class MilvusVectorStore:
    def __init__(self, embed_model: BGEM3MultimodalEmbeddings, collection_name: str = "langchain_multimodal"):
        self.embed_model = embed_model
        self.collection_name = collection_name
        self.connect_milvus()
        self.create_or_load_collection()

    def connect_milvus(self):
        milvus_host = os.getenv("MILVUS_HOST", "localhost")
        milvus_port = os.getenv("MILVUS_PORT", "19530")
        try:
            connections.get_connection("default")
            connections.disconnect("default")
        except:
            pass
        connections.connect(alias="default", host=milvus_host, port=milvus_port, timeout=10)
        print(f"Connected to Milvus {milvus_host}:{milvus_port}")

    def create_or_load_collection(self):
        if utility.has_collection(self.collection_name):
            self.collection = Collection(self.collection_name)
            print(f"Loaded existing collection {self.collection_name}")
        else:
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
                FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="image_path", dtype=DataType.VARCHAR, max_length=500),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embed_model.embedding_dim),
                FieldSchema(name="metadata", dtype=DataType.JSON),
            ]
            schema = CollectionSchema(fields, description="Multimodal RAG collection")
            self.collection = Collection(self.collection_name, schema)
            # 创建索引
            index_params = {"metric_type": "IP", "index_type": "IVF_FLAT", "params": {"nlist": 128}}
            self.collection.create_index("embedding", index_params)
            print(f"Created new collection {self.collection_name}")
        self.collection.load()

    def insert(self, texts: List[str], image_paths: List[Optional[str]], metadatas: List[dict]):
        if not texts:
            print("警告：没有要插入的数据，跳过")
            return

        # 确保三个列表长度一致
        if not (len(texts) == len(image_paths) == len(metadatas)):
            raise ValueError(
                f"长度不一致: texts={len(texts)}, image_paths={len(image_paths)}, metadatas={len(metadatas)}")

        embeddings = []
        for text, img_path in zip(texts, image_paths):
            if img_path and os.path.exists(img_path):
                try:
                    img = Image.open(img_path).convert('RGB')
                    vec = self.embed_model.embed_image(img)
                except Exception as e:
                    print(f"处理图片失败 {img_path}: {e}，改用文本嵌入")
                    vec = self.embed_model.embed_text(text)
            else:
                vec = self.embed_model.embed_text(text)
            embeddings.append(vec)

        # 将 None 转为空字符串
        image_paths_clean = [path if path is not None else "" for path in image_paths]

        # 插入
        self.collection.insert(
            [texts, image_paths_clean, embeddings, metadatas],
            field_names=["text", "image_path", "embedding", "metadata"]
        )
        self.collection.flush()
        print(f"成功插入 {len(texts)} 条记录到 Milvus")

    def search(self, query_text: str, query_image: Optional[Image.Image] = None, top_k: int = 3) -> List[Document]:
        """混合检索（文本+图片）"""
        query_vec = self.embed_model.embed_query(query_text, query_image)
        search_params = {"metric_type": "IP", "params": {"nprobe": 10}}
        results = self.collection.search(
            data=[query_vec],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["text", "image_path", "metadata"]
        )
        docs = []
        for hits in results:
            for hit in hits:
                doc = Document(
                    page_content=hit.entity.get("text"),
                    metadata={
                        "image_path": hit.entity.get("image_path"),
                        "score": hit.score,
                        **hit.entity.get("metadata")
                    }
                )
                docs.append(doc)
        return docs

    def search_with_threshold(self, query_text: str, query_image=None, top_k=3, score_threshold=0.65):
        docs = self.search(query_text, query_image, top_k)  # 先取更多结果
        filtered = [doc for doc in docs if doc.metadata["score"] >= score_threshold]
        # 如果过滤后不足 top_k，可以保留这些或继续检索更多
        return filtered[:top_k]


# ===================== 3. 文档解析（与您项目中的 chunk 方式类似） =====================
def parse_document_to_chunks(file_path: str) -> List[Dict]:
    """
    解析 PDF 或 Word 文档，返回 chunk 列表
    """
    ext = os.path.splitext(file_path)[1].lower()
    raw_elements = []

    if ext == '.pdf':
        raw_elements = partition_pdf(
            filename=file_path,
            extract_images_in_pdf=True,
            infer_table_structure=True,
            chunking_strategy="by_title",
            max_characters=4000,
            new_after_n_chars=3800,
            combine_text_under_n_chars=2000,
            image_output_dir_path="extracted_images",
            strategy="fast"
        )
    elif ext == '.docx':
        raw_elements = partition_docx(
            filename=file_path,
            extract_images_in_docx=True,
            infer_table_structure=True,
            chunking_strategy="by_title",
            max_characters=4000,
            new_after_n_chars=3800,
            combine_text_under_n_chars=2000,
            image_output_dir_path="extracted_images",
        )
    else:
        raise ValueError(f"不支持的文件类型: {ext}，仅支持 .pdf 和 .docx")

    print(f"解析 {file_path} 后共得到 {len(raw_elements)} 个元素")

    texts = []
    images = []

    for el in raw_elements:
        # 检查是否是文本元素：有 text 属性且内容非空
        if hasattr(el, 'text') and el.text and el.text.strip():
            texts.append(el.text)
        # 检查是否是图片元素（unstructured 中图片元素可能有 image_path 或 base64 数据）
        elif hasattr(el, 'metadata') and hasattr(el.metadata, 'image_path'):
            img_path = el.metadata.image_path
            if img_path and os.path.exists(img_path):
                images.append(img_path)
        # 其他情况，打印类型以便调试（可选）
        else:
            print(f"  跳过未知元素类型: {type(el).__name__}")

    # 构造 chunks
    chunks = []
    for i, text in enumerate(texts):
        chunks.append({
            "text": text,
            "image_path": None,
            "metadata": {"source": file_path, "chunk_type": "text", "index": i}
        })
    for img_path in images:
        chunks.append({
            "text": "[图片]",  # 占位文本，后续检索时可以用图片向量
            "image_path": img_path,
            "metadata": {"source": file_path, "chunk_type": "image"}
        })

    # print(f"  提取到 {len(texts)} 个文本块, {len(images)} 个图片")
    return chunks


# ===================== 4. 自定义检索器（兼容 LangChain） =====================
class MultimodalRetriever(BaseRetriever):
    def __init__(self, vector_store: MilvusVectorStore, top_k: int = 3):
        super().__init__()
        self.vector_store = vector_store
        self.top_k = top_k

    def _get_relevant_documents(self, query: str, **kwargs) -> List[Document]:
        # 如果查询中包含图片路径，可解析出来；此处假设仅文本查询
        return self.vector_store.search_with_threshold(query_text=query, query_image=None, top_k=self.top_k, score_threshold=0.5)


# ===================== 6. 主程序示例 =====================
if __name__ == "__main__":
    # 初始化多模态嵌入模型（与您的导入方式完全一致）
    embed_model = BGEM3MultimodalEmbeddings()

    # 初始化 Milvus 向量库
    vector_store = MilvusVectorStore(embed_model, collection_name="multimodal_rag_demo")

    # 解析文档并入库（示例：假设有一个 PDF 文件）
    # chunks = parse_document_to_chunks("your_document.pdf")
    # texts = [chunk["text"] for chunk in chunks]
    # image_paths = [chunk["image_path"] for chunk in chunks]
    # metadatas = [chunk["metadata"] for chunk in chunks]
    # vector_store.insert(texts, image_paths, metadatas)

    # 模拟已有数据：直接插入几条测试数据
    test_chunks = [
        {"text": "笔记本电脑黑屏的常见原因包括内存条接触不良、屏幕排线松动等。", "image_path": None,
         "metadata": {"doc_id": 1}},
        {"text": "检查方法：连接外接显示器，若正常显示则屏幕或排线故障。", "image_path": None, "metadata": {"doc_id": 1}},
        {"text": "解决方案：重新插拔内存条，擦拭金手指后装回。", "image_path": None, "metadata": {"doc_id": 1}},
    ]
    vector_store.insert(
        texts=[c["text"] for c in test_chunks],
        image_paths=[c["image_path"] for c in test_chunks],
        metadatas=[c["metadata"] for c in test_chunks]
    )