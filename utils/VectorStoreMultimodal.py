# utils/vector_store.py

import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")

from PIL import Image
from qwen_token_counter import get_token_count
import base64
import mimetypes

from pymilvus.orm import utility
from openai import OpenAI
import torch  # 添加导入
from typing import List, Dict, Any, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType
from models import Document
import json
from visual_bge.visual_bge.modeling import Visualized_BGE

"""
向量生成的核心模块，使用BAAI/bge-m3模型
"""

class VectorStoreMultimodal:
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
        # self.collection_name = "documents_collection_multimodal"
        # self.collection_name = "documents_collection_new"
        self.collection_name = "documents_collection_main_chunk"
        self.create_or_load_collection()

        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model_chat = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = 2000

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
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="image_url", dtype=DataType.VARCHAR, max_length=200),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim),
            FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=2000),
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
            content = ""
            # 处理标题块
            if section[0] == "title":
                content = f"标题：{document.title}，\n问题简介：{document.problem_intro}"
                images_str = getattr(document, "image_urls_problem_intro", "")
                images = [img.strip() for img in images_str.split(',') if img.strip()] if images_str else []
                flag = 0
                for image in images:
                    url = os.path.join(self.get_config()["DOCUMENT_IMAGE_BASE_DIR"], image)
                    print(url)
                    if os.path.exists(url):
                        flag = 1
                        chunk = {
                            "doc_id": document.id,
                            "title": document.title,
                            "content": content,
                            "image_url": url,
                            "metadata": json.dumps({
                                "contributor_id": document.contributor_id,
                                "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                                "chunk_index": len(chunks),
                                "chunk_size": len(content)
                            })
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
                        "doc_id": document.id,
                        "title": document.title,
                        "content": content,
                        "image_url": "",
                        "metadata": json.dumps({
                            "contributor_id": document.contributor_id,
                            "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                            "chunk_index": len(chunks),
                            "chunk_size": len(content)
                        })
                    })
                continue
            if getattr(document, section[0], None) == None:
                content = f"{section[1]}："
            else:
                content = f"{section[1]}：{getattr(document, section[0], None)}"
            images_str = getattr(document, f"image_urls_{section[0]}", "")
            images = [img.strip() for img in images_str.split(',') if img.strip()] if images_str else []
            flag = 0
            for image in images:
                url = os.path.join(self.get_config()["DOCUMENT_IMAGE_BASE_DIR"], image)
                if os.path.exists(url):
                    flag = 1
                    chunks.append({
                        "doc_id": document.id,
                        "title": document.title,
                        "content": content,
                        "image_url": url,
                        "metadata": json.dumps({
                            "contributor_id": document.contributor_id,
                            "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                            "chunk_index": len(chunks),
                            "chunk_size": len(content)
                        })
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
            if flag == 0 and len(getattr(document, section[0])) > 0:
                chunks.append({
                    "doc_id": document.id,
                    "title": document.title,
                    "content": content,
                    "image_url": "",
                    "metadata": json.dumps({
                        "contributor_id": document.contributor_id,
                        "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                        "chunk_index": len(chunks),
                        "chunk_size": len(content)
                    })
                })
        return chunks

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

        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题

        chunks = self.chunk_document(document)

        if not chunks:
            return

        main_chunk = self.get_main_chunk(document)
        # print(111)
        if main_chunk is not None:
            content = f"问题简介：{main_chunk['problem_intro']}\n核心成因：{main_chunk['causes']}\n关键特征：{main_chunk['feature']}"
            chunks.append({
                "doc_id": document.id,
                "title": document.title,
                "content": content,
                "image_url": "",
                "metadata": json.dumps({
                    "contributor_id": document.contributor_id,
                    "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                    "chunk_index": len(chunks),
                    "chunk_size": len(content)
                })
            })
        # print(222)

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

        print(f"query_embedding完成，{query_embedding}")

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
                retrieved_docs.append({
                    "doc_id": hit.entity.get("doc_id"),
                    "title": hit.entity.get("title"),
                    "content": hit.entity.get("content"),
                    "image_url": hit.entity.get("image_url"),
                    "metadata": json.loads(hit.entity.get("metadata")),
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

    def delete_document(self, doc_id: int):
        """从向量库中删除文档"""
        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题
        self.collection.delete(f'doc_id == {doc_id}')
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
        token_cnt = get_token_count(prompt)
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

    def get_main_chunk(self, document: Document):
        content = f"【标题】：{document.title}\n【问题简介】：{document.problem_intro}\n【成因】：{document.causes}\n"

        if document.image_urls_problem_intro is not None and document.image_urls_problem_intro != "":
            images = document.image_urls_problem_intro.split(", ")
        else:
            images = []
        if document.image_urls_causes is not None and document.image_urls_causes != "":
            images.extend(document.image_urls_causes.split(", "))

        try:
            client = OpenAI(
                base_url=f"http://{self.ai}:8000/v1",
                api_key=self.api_key
            )
            messages = self.generate_message(content, images)

            response = client.chat.completions.create(
                model=self.model_chat,
                messages=messages,
                max_tokens=self.max_token
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
                encode_result = self.model.encode(text=chunk["content"], image=chunk["image_url"])
                embeds.append(encode_result.cpu().numpy().flatten().tolist())
        return embeds

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
    pass
    # connections.connect(host='localhost', port='19530')
    # collection_name = "documents_collection_main_chunk"
    # if utility.has_collection(collection_name):
    #     collection = Collection(collection_name)
    #     collection.drop()
    #     print(f"集合 '{collection_name}' 已成功删除。")

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