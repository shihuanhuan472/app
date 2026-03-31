"""
对python版本应该没有过多要求，我本地是python3.10，服务器是python3.9，都可以运行.
当然配置环境中间可能会有差异.

main.py为整个程序入口，后端采用FastAPI框架，结合sqlalchemy操作MySQL数据库.
前端使用HTML + CSS + JS（因本人前端功底极差，约等于无，所以多为AI写的）.
嵌入模型使用的是BAAI/bge-m3，支撑多模态嵌入，向量数据库使用Milvus.

文件结构：
bge: 嵌入模型（未提供，请自行下载权重文件）.
routers：路由文件夹，为各api的核心代码.
static：前端代码.
upload：所有上传的文件图片均存储于此.
utils：工具，包含jwt生成与解析，向量嵌入及各类文档导入处理,
    （VectorStore.py为废弃代码，前期使用单模嵌入的产物）.
volumes：配置Milvus后自动生成.
----------------------------
.env：配置文件，请根据本地环境自行填写.
database.py：数据库配置文件，请根据本地实际情况链接数据库.
dependencies.py：依赖，用于获取当前用户.
docker-compose.yml：milvus的配置信息，请根据此，使用docker安装Milvus.
models.py：即所有数据模型，与MySQL数据库一一对应.
schemas.py：模式，前后端交互用的.
requirements.txt：项目所需的包，因涉及到torch，不同硬件设施，包会有差距.
（如：我本地电脑无Nvidia显卡，因此我的torch是仅cpu的，而服务器有，因此其torch是有n卡加速版本的）.
请根据自己硬件情况进行下载，无需强行按照给定的requirements.txt.
并且因版本更迭，部分包实际不再使用，如：jieba.
----------------------------

代码中可能包含很多无用注释或print，均为调试或版本更迭的产物，不用理会.

from cxx
2026.3.16

"""
import os
from urllib.request import Request
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
# 导入路由
from routers import auth, users, admin, conversation, message, conversation_v1, file_manage
# from routers import auth, conversation_v1
from routers import documents
from models import Base
from database import engine
from starlette.types import Scope

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 自定义 StaticFiles 类，添加 CORS 头，用于跨域
class CORSStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> FileResponse:
        response = await super().get_response(path, scope)
        # 添加 CORS 头，允许所有来源（开发环境适用）
        response.headers["Access-Control-Allow-Origin"] = "*"
        # 如果需要支持带凭证的请求（如 cookie），可以设置为具体的来源
        # response.headers["Access-Control-Allow-Origin"] = "http://localhost"
        # response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

app = FastAPI(
    title="维修辅助系统API",
    description="基于FastAPI的维修知识管理和对话系统",
    version="1.0.0"
)

# 配置 CORS（跨域资源共享）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:80",
        "http://127.0.0.1:80",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:63342",  # PyCharm内置服务器
        "http://localhost:8000",   # 添加后端自身
        "http://127.0.0.1:8000",
        "*"  # 开发时可以使用 *，生产环境要限制
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "upload")
app.mount("/upload", CORSStaticFiles(directory=UPLOAD_DIR), name="upload")
app.mount("/static", CORSStaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(conversation.router)
app.include_router(message.router)
app.include_router(conversation_v1.router)
app.include_router(file_manage.router)

@app.get("/", summary="根路径")
async def root():
    """API 根路径"""
    return {
        "message": "JWT 认证 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": f"服务器内部错误: {str(exc)}"
        }
    )