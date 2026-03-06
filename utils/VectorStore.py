# utils/vector_store.py
import os
import jieba
from pymilvus.orm import utility

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
import torch  # 添加导入
from typing import List, Dict, Any, Optional
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType
from sentence_transformers import SentenceTransformer
from models import Document
import json
from visual_bge.visual_bge.modeling import Visualized_BGE

class VectorStore:
    def __init__(self):
        # 检测可用设备
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        print(f"Using device: {device}")
        self.model_embedding = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
        self.model_embedding_local = os.getenv("EMBEDDING_MODEL_LOCAL_PATH", 
                                                "D:/Pycharm/code/Maintenance_Assistance_System/embedding-model")

        self.model_name = os.getenv("MODEL_NAME", "BAAI/bge-visualized-m3")
        self.model_weight = os.getenv("MODEL_WEIGHT", "D:/Pycharm/code/Maintenance_Assistance_System/bge")
        # self.model = Visualized_BGE(model_name_bge=self.model_name,
        #                             model_weight=self.model_weight)
        # self.model.eval()
        # self.model.to(self.device)
        self.image_config = self.get_config()

        self.top_k = int(os.getenv("TOP_K", 3))
        self.chunk_size = int(os.getenv("CHUNK_SIZE", 500))
        self.overlap = int(os.getenv("OVERLAP", 50))
        # 尝试使用flash_attention_2加速（如果可用）
        try:
            if device == "cuda":
                self.embedding_model = SentenceTransformer(
                    self.model_embedding,
                    model_kwargs={
                        "attn_implementation": "flash_attention_2",
                        "torch_dtype": torch.float16 if device == "cuda" else torch.float32
                    },
                    tokenizer_kwargs={"padding_side": "left"},
                    cache_folder=self.model_embedding_local
                )
            else:
                self.embedding_model = SentenceTransformer(
                    self.model_embedding,
                    cache_folder=self.model_embedding_local,
                    device=device
                )
        except Exception as e:
            print(f"Flash attention not available, using default: {e}")
            self.embedding_model = SentenceTransformer(
                self.model_embedding,
                cache_folder=self.model_embedding_local,
                device=device
            )

        self.embedding_dim = 1024

        # 连接Milvus
        self.connect_milvus()

        # 创建或加载Collection
        self.collection_name = "documents_collection"
        self.create_or_load_collection()

    def get_config(self):
        IMAGE_DIR: str = os.getenv("IMAGE_DIR", "upload/images")
        BASE_DIR: str = os.getenv("BASE_DIR", "/")
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
            self.validate_collection_schema()

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

            required_fields = ["doc_id", "chunk_id", "title", "content", "embedding", "metadata"]

            for field in required_fields:
                if field not in field_names:
                    raise ValueError(f"集合缺少必要字段: {field}")

            # 检查向量维度
            for field in fields:
                if field.name == "embedding":
                    if field.dim != self.embedding_dim:
                        raise ValueError(
                            f"向量维度不匹配: 集合维度={field.dim}, 模型维度={self.embedding_dim}"
                        )

            print("集合schema验证通过")

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
        """创建新集合"""
        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="chunk_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=500),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.embedding_dim),
            FieldSchema(name="metadata", dtype=DataType.VARCHAR, max_length=2000),
        ]

        schema = CollectionSchema(fields, description="文档向量存储")
        self.collection = Collection(self.collection_name, schema)

        # 创建索引
        self.create_index()

        self.load_collection()

    def load_collection(self):
        """加载集合到内存"""
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
        """改进的文档分块方法"""
        # 使用传入参数或默认值
        chunk_size = chunk_size if chunk_size > 0 else self.chunk_size
        overlap = overlap if overlap > 0 else self.overlap

        # 确保参数有效
        if chunk_size <= overlap:
            print(f"警告: chunk_size({chunk_size}) <= overlap({overlap})，设置为默认值")
            chunk_size = chunk_size if chunk_size > 1 else self.chunk_size
            overlap = overlap if overlap > 1 else self.overlap

        # 构建完整文本
        text = f"""
            标题: {document.title}
            问题描述: {document.problem_intro}
            原因: {document.causes}
            评估: {document.evaluation}
            检查: {document.inspection}
            解决方案: {document.solutions}
            关键点: {document.key_points}
        """

        # 使用jieba进行精确模式分词
        words = list(jieba.cut(text, cut_all=False))
        print(f"分词后词数: {len(words)}")

        chunks = []

        # 分块
        for i in range(0, len(words), chunk_size - overlap):
            chunk_words = words[i:i + chunk_size]
            chunk_text = "".join(chunk_words)  # 中文不用空格连接

            # 确保块不以截断的词开头/结尾
            if i > 0 and len(chunk_text) > 10:
                # 移除可能被截断的第一个词
                chunk_text = chunk_text.lstrip("，。！？；,.!?;")

            if chunk_text.strip():
                chunks.append({
                    "doc_id": document.id,
                    "title": document.title,
                    "content": chunk_text.strip(),
                    "metadata": json.dumps({
                        "contributor_id": document.contributor_id,
                        "first_edit_date": document.first_edit_date.isoformat() if document.first_edit_date else None,
                        "chunk_index": len(chunks),
                        "word_count": len(chunk_words)
                    })
                })

        return chunks

    def embed_texts(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """将文本转换为向量"""
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

        # 准备批量插入数据
        data = []
        chunk_contents = []

        for i, chunk in enumerate(chunks):
            data.append([
                chunk["doc_id"],  # doc_id
                i,  # chunk_id
                chunk["title"],  # title
                chunk["content"],  # content
                chunk["metadata"]  # metadata
            ])
            chunk_contents.append(chunk["content"])

        # 生成向量（文档不使用提示词）
        embeddings = self.embed_texts(chunk_contents, is_query=False)

        # 插入数据
        insert_data = [
            [item[0] for item in data],  # doc_ids
            [item[1] for item in data],  # chunk_ids
            [item[2] for item in data],  # titles
            [item[3] for item in data],  # contents
            embeddings,  # embeddings
            [item[4] for item in data]  # metadata
        ]

        self.collection.insert(insert_data)
        self.collection.flush()
        print(f"Added {len(chunks)} chunks from document {document.id} to vector store")

    def search(self, query: str, top_k: int = -1) -> List[Dict]:
        """搜索相似文档"""

        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题

        # 查询使用提示词
        top_k = self.top_k if top_k < 1 else top_k
        query_embedding = self.embed_texts([query], is_query=True)[0]

        # 搜索参数
        search_params = {
            "metric_type": "IP",
            "params": {"nprobe": 10}
        }

        # 执行搜索
        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["doc_id", "title", "content", "metadata"]
        )

        # 整理结果
        retrieved_docs = []
        for hits in results:
            for hit in hits:
                retrieved_docs.append({
                    "doc_id": hit.entity.get("doc_id"),
                    "title": hit.entity.get("title"),
                    "content": hit.entity.get("content"),
                    "metadata": json.loads(hit.entity.get("metadata")),
                    "score": hit.score
                })

        # 按分数排序
        retrieved_docs.sort(key=lambda x: x["score"], reverse=True)
        return retrieved_docs

    def delete_document(self, doc_id: int):
        """从向量库中删除文档"""
        try:
            self.load_collection()
        except:
            pass  # 如果加载失败，继续执行，可能在插入时会有问题
        self.collection.delete(f'doc_id == {doc_id}')
        self.collection.flush()
        print(f"Deleted document {doc_id} from vector store")

    # def embed_multimodal(self, texts, images_dir=None, is_query: bool = False):



# 全局向量存储实例
# vector_store = VectorStore()

if __name__ == "__main__":
    vector_store = VectorStore()

    document = Document()
    document.id = 100
    document.title = "作业的基本概念"
    document.contributor_id = 1
    document.problem_intro = "作业：要求计算机系统按照指定步骤对应用程序进行处理并得到计算结果的加工工作。从用户角度看的话，就是在一次应用业务处理过程中，从输入开始到输出结束，用户要求计算机所做的有关该次业务处理的全部工作"
    document.causes = "作业说明书中有<u>作业的基本情况</u>（用户名，作业名，编程语言等），<u>作业控制描述</u>（作业的控制方式，作业步的操作顺序，作业执行出错处理），<u>作业资源要求描述</u>（处理时间，优先级，内存空间，外设类型和数量等）"
    document.evaluation = """作业的输入：将作业的程序，数据和作业说明书从输入设备输入到外存

作业控制块的建立：作业控制块就是一张表，里面有一些必要信息，比如作业名，优先数，资源要求等等。操作系统通过这张表了解到作业的要求，并分配资源和控制作业中程序和数据的编译，连接，装入和执行等

当一个作业的全部程序和数据输入到外存并且在系统中建立了作业控制块之后，一个作业就建立了"""
    document.inspection = "系统调用指令（访管指令，陷阱指令）：由于系统调用引起的处理机中断指令（可能你在执行你的程序，跑的好好的，系统给个指令，处理机就跑去他那里了，处理机就中断了）"
    document.solutions = "程序执行的两种方式 —— 顺序执行（单道批处理），并发执行（提高资源利用率）"
    document.key_points = "区别是，系统进程被分配一个初始资源集合，它可以独占，有着最高优先权，用户进程通过系统服务请求手段竞争使用系统资源。系统进程可以直接进行IO操作，用户进程不行。系统在核心态（系统态，管态）下活动，用户进程就在用户态了"
    document.is_vectorized = 0

    chunk_document = vector_store.chunk_document(document, chunk_size=100, overlap=10)
    print(chunk_document)
    print(len(chunk_document))

    # 准备批量插入数据
    data = []
    chunk_contents = []

    for i, chunk in enumerate(chunk_document):
        data.append([
            chunk["doc_id"],  # doc_id
            i,  # chunk_id
            chunk["title"],  # title
            chunk["content"],  # content
            chunk["metadata"]  # metadata
        ])
        chunk_contents.append(chunk["content"])

    # 生成向量（文档不使用提示词）
    embeddings = vector_store.embed_texts(chunk_contents, is_query=False)
    # print(embeddings)
    # print(len(embeddings))