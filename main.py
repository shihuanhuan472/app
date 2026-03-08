# main.py
import os
from urllib.request import Request

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
# 导入路由
from routers import auth, users, admin, conversation, message
from routers import documents
from models import Base
from database import engine
from starlette.types import Scope

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 自定义 StaticFiles 类，添加 CORS 头
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
        "http://localhost:3000",
        "http://127.0.0.1:3000",
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
# 包含路由 - 先只包含users路由测试
# app.include_router(users.router, prefix="/api/users", tags=["用户管理"])

# 注册路由
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(documents.router)
app.include_router(conversation.router)
app.include_router(message.router)


@app.get("/", summary="根路径")
async def root():
    """API 根路径"""
    return {
        "message": "JWT 认证 API",
        "docs": "/docs",
        "redoc": "/redoc"
    }


# 全局异常处理（可选）
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

# Whut_456123