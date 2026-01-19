# main.py
import os
from urllib.request import Request

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
# 导入路由
from routers import auth, users, admin, documents
from models import Base
from database import engine
from routers import users

# 创建数据库表
Base.metadata.create_all(bind=engine)

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
        "http://localhost:5500",  # VS Code Live Server默认端口
        "http://127.0.0.1:5500",
        "http://localhost:63342",  # PyCharm内置服务器
        "http://localhost:8000",   # 添加后端自身
        "http://127.0.0.1:8000",
        "*"  # 开发时可以使用 *，生产环境要限制
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "upload")
app.mount("/upload", StaticFiles(directory=UPLOAD_DIR), name="upload")
app.mount("/static", StaticFiles(directory="static"), name="static")
# 包含路由 - 先只包含users路由测试
app.include_router(users.router, prefix="/api/users", tags=["用户管理"])

# 注册路由
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(documents.router)


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