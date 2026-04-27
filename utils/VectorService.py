import os
import asyncio
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")

import base64
import json
import logging
import mimetypes
from datetime import datetime
from typing import List, Dict
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from utils.VectorStoreMultimodal import vector_store_multimodal
from models import Document
import time

from sqlalchemy import select
logger = logging.getLogger(__name__)

"""
向量库service，文档增删查在向量层面的service层，会操控mysql数据库
"""

class VectorService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # self.vector_store = vector_store
        self.vector_store_multimodal = vector_store_multimodal
        self.top_k = int(os.getenv("TOP_K", 10))
        self.batch_size = int(os.getenv("BATCH_SIZE", 10))
        self.similarity_low_limit = float(os.getenv("SIMILARITY_LOWER_LIMIT", 0.5))
        self.message_image_base_dir = os.getenv("MESSAGE_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.top_k_documents = int(os.getenv("TOP_K_DOCUMENTS", 2))
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.rerank_model_name = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")

        # self.tokenizer = AutoTokenizer.from_pretrained(self.rerank_model_name)
        # self.rerank_model = AutoModel.from_pretrained(self.rerank_model_name)
        # self.rerank_model.eval()

    async def add_document_to_vector_store(self, document: Document):
        """将文档添加到向量数据库"""
        try:
            # 检查文档是否已向量化
            if document.is_vectorized:
                print(f"文档 {document.id} 已向量化，跳过")
                return

            # 添加到向量数据库
            # self.vector_store.add_document(document)
            await asyncio.to_thread(self.vector_store_multimodal.add_document, document)
            print("向量化完成")
            # 更新数据库状态
            document.is_vectorized = 1
            document.vector_update_time = datetime.now()
            await self.db.commit()

            print(f"文档 {document.id} 向量化完成")

        except Exception as e:
            print(f"文档向量化失败: {e}")
            await self.db.rollback()
            raise

    async def delete_document_from_vector_store(self, doc_id: int):
        """从向量库删除文档"""
        try:
            # self.vector_store.delete_document(doc_id)
            # self.vector_store_multimodal.delete_document(doc_id)
            await asyncio.to_thread(self.vector_store_multimodal.delete_document, doc_id)
            print(f"文档 {doc_id} 已从向量库删除")
        except Exception as e:
            print(f"从向量库删除文档失败: {e}")
            raise

    def generate_prompt(self, result, query: str = None, query_image: str = None):
        chunk_image = result['image_url']
        if chunk_image is not None and len(chunk_image) == 0:
            chunk_image = None
        if query_image is not None and len(query_image) == 0:
            query_image = None
        prompt = f"根据给定问题内容，请判断文档内容的相关性（0-1）：\n【问题文本】：{query}\n【相关文档文本】：{result['content']}\n"
        if query_image is not None and chunk_image is not None:
            prompt += "\n你将看到两张图像，第一张为用户查询的图像，第二张为文档块中的图像。\n"
            prompt += "请判断相关文档文本和文档块中图像（第二张图像）与问题文本和问题图像（第一张图像）的相关性。\n"
        elif query_image is not None:
            prompt += "\n你将看到一张图像，为问题图像。\n"
            prompt += "请判断相关文档文本与问题文本和问题图像（第一张图像）的相关性。\n"
        elif chunk_image is not None:
            prompt += "\n你将看到一张图像，为相关文档中的图像。\n"
            prompt += "请判断相关文档文本和图像（第一张图像）与问题文本的相关性。\n"
        else:
            prompt += "请判断相关文档文本与问题文本的语义相关性。\n"
        # prompt += "注意：1. 仅回答一个得分，得分为0-1之间的实数，并且得分精细一些，保留五位小数。\n2. 请重视设备类型对相关性的影响。"
        prompt += """请判断该文档是否对回答用户问题有帮助（而不是是否完全匹配）。

【评分核心标准（非常重要）】
请严格区分以下层级：
1.0 = 完全命中（设备 + 故障 + 原因/解决方案都匹配）
0.85 = 核心相关（同设备 + 同类故障）
0.7 = 较强相关（同设备，但不同具体问题）
0.5 = 一般相关（提供通用方法/原理）
0.3 = 弱相关（仅领域相关）
0.0 = 完全无关（设备或领域不一致）

关键要求：
- 必须拉开差距，不允许大量0.9/1.0
- 只有“真正直接解决问题”的才给1.0
- 若有图像，识别图像信息
- 若能解决用户的问题，也视为相关

====================

【额外排序原则（必须遵守）】

优先级排序如下：
1. 设备类型是否一致。
2. 故障类型是否相关。
3. 设备类型优先级略高于故障类型。
4. 若未展现出故障，则判断查询和文档是否相关。

【输出格式】
仅输出一个JSON对象，不要包含任何其他解释文本。
{
  "reason": "简要列出缺失的关键信息（如：设备信息完全不吻合）",
  "score": 0.8
}
"""
        return prompt

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

    def add_picture_to_message(self, pic_path: str):
        mime_type, _ = mimetypes.guess_type(pic_path)
        if mime_type is None:
            ext = os.path.splitext(pic_path)[1].lower()
            mime_type = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp'
            }.get(ext, 'image/jpeg')
        image_base64 = self.image_to_base64(pic_path)
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
        }

    async def rerank_by_llm(self, result, query: str = None, query_image: str = None):
        """
        query_image和result['image_url']如果有，都是绝对路径
        """
        print(f"调用rerank_by_llm")
        prompt = self.generate_prompt(result, query, query_image)
        # print(prompt)
        def _call_openai():
            messages = []
            data = {}
            msg_content = [{"type": "text", "text": prompt}]
            if query_image is not None and len(query_image) > 0:
                msg_content.append(self.add_picture_to_message(query_image))
            if result['image_url'] is not None and len(result['image_url']) > 0:
                msg_content.append(self.add_picture_to_message(result['image_url']))
            # print(msg_content)
            data["role"] = "user"
            data["content"] = msg_content
            messages.append(data)
            client = OpenAI(base_url=f"http://{self.ai}:8000/v1", api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token
            )
            return response.choices[0].message.content

        try:
            # client = OpenAI(
            #     base_url=f"http://{self.ai}:8000/v1",
            #     api_key=self.api_key
            # )
            # response = client.chat.completions.create(
            #     model=self.model,
            #     messages=messages,
            #     max_tokens=self.max_token
            # )
            # ans = response.choices[0].message.content

            ans = await asyncio.to_thread(_call_openai)
            print(ans)
            new_score = float(json.loads(ans)['score'])
            # time.sleep(3)
            return new_score
        except Exception as e:
            print(e)
            return None

    # def get_relevance_by_reranker(self, query: str, chunk_result):
    #     data = [[query, chunk_data] for chunk_data in chunk_result]
    #     with torch.no_grad():
    #         inputs = self.tokenizer(data, padding=True, truncation=True, return_tensors='pt', max_length=512)
    #         scores = self.rerank_model(**inputs, return_dict=True).logits.view(-1, ).float()
    #         print(scores)

    async def describe_image(self, image_url):
        def _call_openai():
            messages = []
            data = {}
            msg_content = [{"type": "text", "text": "请详细描述图像信息，重点包含设备信息或故障信息（若无故障信息则无需给出故障信息）。\n仅返回答案，不要任何markdown渲染。"}]
            msg_content.append(self.add_picture_to_message(image_url))
            # print(msg_content)
            data["role"] = "user"
            data["content"] = msg_content
            messages.append(data)
            client = OpenAI(base_url=f"http://{self.ai}:8000/v1", api_key=self.api_key)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token
            )
            return response.choices[0].message.content
        try:
            print("-=-==-==================")
            ans = await asyncio.to_thread(_call_openai)
            print(f"图像描述：{ans}")
            return ans
        except Exception as e:
            print(e)
            return None


    async def search_similar_documents(self, query: str, query_images: str = None, top_k: int = -1) -> List[Dict]:
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
                    exists = await asyncio.to_thread(os.path.exists, image_url)
                    if exists:

                        image_description = await self.describe_image(image_url)

                        if image_description is not None:
                            query += f"\n【图像信息】：{image_description}"
                        print(query)
                        result = await asyncio.to_thread(
                            self.vector_store_multimodal.search, query, image_url, top_k
                        )

                        for r in result:
                            new_score = await self.rerank_by_llm(r, query, image_url)
                            if new_score is not None:
                                print(f"content: {r['content']}")
                                print(f"old_score: {r['score']}, new_score: {new_score}")
                                r['score'] = 0.75 * r['score'] + 0.25 * new_score

                        # result = self.vector_store_multimodal.search(query, image_url, top_k)
                        results.extend(result)
                        flag = 1
            else:
                result = await asyncio.to_thread(self.vector_store_multimodal.search, query, None, top_k)
                for r in result:
                    new_score = await self.rerank_by_llm(r, query, None)
                    if new_score is not None:
                        print(f"old_score: {r['score']}, new_score: {new_score}")
                        r['score'] = 0.75 * r['score'] + 0.25 * new_score
                results.extend(result)
                flag = 1
            if flag == 0:
                result = await asyncio.to_thread(self.vector_store_multimodal.search, query, None, top_k)
                for r in result:
                    new_score = await self.rerank_by_llm(r, query, None)
                    if new_score is not None:
                        print(f"old_score: {r['score']}, new_score: {new_score}")
                        r['score'] = 0.75 * r['score'] + 0.25 * new_score
                results.extend(result)
            # results = self.vector_store_multimodal.search(query, query_image, top_k)
            # print(results)
            # 整理结果，去重（按文档ID）
            results.sort(key=lambda x: x["score"], reverse=True)
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
            # print(unique_docs.keys())
            # for doc_id in unique_docs:
            #     print(doc_id, unique_docs[doc_id]["score"])
            return list(unique_docs.values())[:self.top_k_documents]

        except Exception as e:
            print(f"向量搜索失败: {e}")
            return []

    async def batch_vectorize_existing_documents(self, batch_size: int = -1):
        """批量向量化现有文档"""
        try:
            batch_size = self.batch_size if batch_size < 1 else batch_size
            # 获取未向量化的文档
            # documents = self.db.query(Document) \
            #     .filter(Document.is_vectorized == 0) \
            #     .limit(batch_size) \
            #     .all()

            result = await self.db.execute(
                select(Document).where(Document.is_vectorized == 0).limit(batch_size)
            )
            documents = result.scalars().all()

            for doc in documents:
                await self.add_document_to_vector_store(doc)

            return len(documents)

        except Exception as e:
            print(f"批量向量化失败: {e}")
            return 0


if __name__ == "__main__":
    db = AsyncSession()
    vector_service = VectorService(db)

    result = vector_service.search_similar_documents("", "eval_images\\120-1.jpg")

    print(result)