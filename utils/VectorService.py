import logging
import os
from datetime import datetime
from typing import List, Dict
from sqlalchemy.orm import Session

from utils.VectorStoreMultimodal import vector_store_multimodal
from models import Document

logger = logging.getLogger(__name__)

"""
向量库service，文档增删查在向量层面的service层，会操控mysql数据库
"""

class VectorService:
    def __init__(self, db: Session):
        self.db = db
        # self.vector_store = vector_store
        self.vector_store_multimodal = vector_store_multimodal
        self.top_k = int(os.getenv("TOP_K", 10))
        self.batch_size = int(os.getenv("BATCH_SIZE", 10))
        self.similarity_low_limit = float(os.getenv("SIMILARITY_LOWER_LIMIT", 0.5))
        self.message_image_base_dir = os.getenv("MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")

    def add_document_to_vector_store(self, document: Document):
        """将文档添加到向量数据库"""
        try:
            # 检查文档是否已向量化
            if document.is_vectorized:
                print(f"文档 {document.id} 已向量化，跳过")
                return

            # 添加到向量数据库
            # self.vector_store.add_document(document)
            self.vector_store_multimodal.add_document(document)
            print("向量化完成")
            # 更新数据库状态
            document.is_vectorized = 1
            document.vector_update_time = datetime.now()
            self.db.commit()

            print(f"文档 {document.id} 向量化完成")

        except Exception as e:
            print(f"文档向量化失败: {e}")
            self.db.rollback()
            raise

    def delete_document_from_vector_store(self, doc_id: int):
        """从向量库删除文档"""
        try:
            # self.vector_store.delete_document(doc_id)
            self.vector_store_multimodal.delete_document(doc_id)
            print(f"文档 {doc_id} 已从向量库删除")
        except Exception as e:
            print(f"从向量库删除文档失败: {e}")
            raise

    def search_similar_documents(self, query: str, query_images: str = None, top_k: int = -1) -> List[Dict]:
        """搜索相似文档"""
        try:
            top_k = self.top_k if top_k < 1 else top_k
            # results = self.vector_store.search(query, top_k)
            results = []
            images = [query_image.strip() for query_image in query_images.split(",")] if query_images is not None else []
            flag = 0
            if len(images) > 0:
                for image in images:
                    if not image.strip():
                        continue
                    image_url = os.path.join(self.message_image_base_dir, image.strip())
                    if os.path.exists(image_url):
                        result = self.vector_store_multimodal.search(query, image_url, top_k)
                        results.extend(result)
                        flag = 1
            else:
                result = self.vector_store_multimodal.search(query, None, top_k)
                results.extend(result)
                flag = 1
            if flag == 0:
                result = self.vector_store_multimodal.search(query, None, top_k)
                results.extend(result)
            # results = self.vector_store_multimodal.search(query, query_image, top_k)
            # print(results)
            # 整理结果，去重（按文档ID）
            unique_docs = {}
            for result in results:
                if result["score"] < self.similarity_low_limit:
                    continue
                doc_id = result["doc_id"]
                if doc_id not in unique_docs:
                    unique_docs[doc_id] = {
                        "doc_id": doc_id,
                        "title": result["title"],
                        "content": result["content"],
                        "image_url": result["image_url"],
                        "score": result["score"],
                        "chunks": [result]  # 存储所有相关chunk
                    }
                else:
                    unique_docs[doc_id]["chunks"].append(result)
                    # 更新最高分
                    if result["score"] > unique_docs[doc_id]["score"]:
                        unique_docs[doc_id]["score"] = result["score"]
            print(unique_docs.keys())
            return list(unique_docs.values())

        except Exception as e:
            print(f"向量搜索失败: {e}")
            return []

    def batch_vectorize_existing_documents(self, batch_size: int = -1):
        """批量向量化现有文档"""
        try:
            batch_size = self.batch_size if batch_size < 1 else batch_size
            # 获取未向量化的文档
            documents = self.db.query(Document) \
                .filter(Document.is_vectorized == 0) \
                .limit(batch_size) \
                .all()

            for doc in documents:
                self.add_document_to_vector_store(doc)

            return len(documents)

        except Exception as e:
            print(f"批量向量化失败: {e}")
            return 0