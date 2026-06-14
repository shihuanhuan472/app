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
import uuid
import logging
from dotenv import load_dotenv
load_dotenv()
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
# 导入路由
# from routers import auth
from routers import auth, users, admin, conversation, message, conversation_v1, file_manage, review, source_documents
# from routers import auth, conversation_v1
from routers import documents
from models import Base
from database import engine
from starlette.types import Scope
from sqlalchemy import text
from utils.app_exceptions import AppException
from utils.api_key import generate_api_key
from utils.error_codes import BizCode, HTTP_TO_BIZ_CODE

logger = logging.getLogger(__name__)

# 创建数据库表（使用异步引擎）
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_document_tables_for_library_split():
    async with engine.begin() as conn:
        for table_name in ("document_breakdown", "document_knowledge"):
            tag_column_result = await conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                      AND COLUMN_NAME = 'tag'
                    """
                ),
                {"table_name": table_name},
            )
            if int(tag_column_result.scalar_one() or 0) == 0:
                await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tag JSON NULL"))

            index_result = await conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM INFORMATION_SCHEMA.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = :table_name
                      AND INDEX_NAME = :index_name
                    """
                ),
                {"table_name": table_name, "index_name": f"idx_{table_name}_is_deleted"},
            )
            if int(index_result.scalar_one() or 0) == 0:
                await conn.execute(text(f"CREATE INDEX idx_{table_name}_is_deleted ON {table_name} (is_deleted)"))


async def ensure_review_library_columns():
    async with engine.begin() as conn:
        fk_result = await conn.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'document_reviews'
                  AND COLUMN_NAME = 'document_id'
                  AND REFERENCED_TABLE_NAME = 'documents'
                """
            )
        )
        for row in fk_result.all():
            await conn.execute(text(f"ALTER TABLE document_reviews DROP FOREIGN KEY `{row.CONSTRAINT_NAME}`"))

        library_column_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'document_reviews'
                  AND COLUMN_NAME = 'document_library_type'
                """
            )
        )
        if int(library_column_result.scalar_one() or 0) == 0:
            await conn.execute(text("ALTER TABLE document_reviews ADD COLUMN document_library_type VARCHAR(32) NOT NULL DEFAULT 'breakdown' AFTER document_id"))

        tag_column_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'document_reviews'
                  AND COLUMN_NAME = 'tag'
                """
            )
        )
        if int(tag_column_result.scalar_one() or 0) == 0:
            await conn.execute(text("ALTER TABLE document_reviews ADD COLUMN tag JSON NULL AFTER origin_file_dir"))


async def ensure_user_api_key_column():
    async with engine.begin() as conn:
        column_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'users'
                  AND COLUMN_NAME = 'api_key'
                """
            )
        )
        if int(column_result.scalar_one() or 0) == 0:
            await conn.execute(text("ALTER TABLE users ADD COLUMN api_key VARCHAR(128) NULL AFTER department"))

        users_without_key_result = await conn.execute(
            text("SELECT id FROM users WHERE api_key IS NULL OR api_key = ''")
        )
        for row in users_without_key_result.all():
            api_key = generate_api_key()
            await conn.execute(
                text("UPDATE users SET api_key = :api_key WHERE id = :user_id"),
                {"api_key": api_key, "user_id": row.id},
            )

        index_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'users'
                  AND INDEX_NAME = 'idx_users_api_key'
                """
            )
        )
        if int(index_result.scalar_one() or 0) == 0:
            await conn.execute(text("CREATE UNIQUE INDEX idx_users_api_key ON users (api_key)"))


async def migrate_legacy_documents_to_breakdown():
    async with engine.begin() as conn:
        legacy_table_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'documents'
                """
            )
        )
        if int(legacy_table_result.scalar_one() or 0) == 0:
            return

        breakdown_count_result = await conn.execute(text("SELECT COUNT(*) FROM document_breakdown"))
        if int(breakdown_count_result.scalar_one() or 0) > 0:
            return

        await conn.execute(
            text(
                """
                INSERT INTO document_breakdown (
                    id, title, contributor_id, first_edit_date, problem_intro, image_urls,
                    image_urls_problem_intro, causes, image_urls_causes, evaluation,
                    image_urls_evaluation, inspection, image_urls_inspection, solutions,
                    image_urls_solutions, key_points, image_urls_key_points, is_vectorized,
                    is_deleted, vector_update_time, origin_file_name, origin_file_dir, tag
                )
                SELECT
                    id, title, contributor_id, first_edit_date, problem_intro, image_urls,
                    image_urls_problem_intro, causes, image_urls_causes, evaluation,
                    image_urls_evaluation, inspection, image_urls_inspection, solutions,
                    image_urls_solutions, key_points, image_urls_key_points, 0,
                    is_deleted, vector_update_time, origin_file_name, origin_file_dir, JSON_ARRAY()
                FROM documents
                """
            )
        )

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


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response

@app.on_event("startup")
async def on_startup():
    await init_db()
    await ensure_user_api_key_column()
    await ensure_document_tables_for_library_split()
    await ensure_review_library_columns()
    await migrate_legacy_documents_to_breakdown()

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
app.include_router(review.router)
app.include_router(source_documents.router)
app.include_router(conversation.router)
app.include_router(message.router)
app.include_router(conversation_v1.router)
app.include_router(file_manage.router)

# Enterprise-facing API namespace. Keep legacy routes above for backward
# compatibility while exposing a consistent /api/v1 prefix for integrations.
API_V1_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_V1_PREFIX)
app.include_router(users.router, prefix=API_V1_PREFIX)
app.include_router(admin.router, prefix=API_V1_PREFIX)
app.include_router(documents.router, prefix=API_V1_PREFIX)
app.include_router(review.router, prefix=API_V1_PREFIX)
app.include_router(source_documents.router, prefix=API_V1_PREFIX)
app.include_router(conversation.router, prefix=API_V1_PREFIX)
app.include_router(message.router, prefix=API_V1_PREFIX)

@app.get("/", summary="根路径")
async def root():
    return RedirectResponse(url="/static/index.html")


# 全局异常处理
def _error_payload(code: int, message: str, trace_id: str, detail=None):
    return {
        "code": int(code),
        "msg": message,
        "message": message,
        "detail": detail,
        "trace_id": trace_id,
        "data": None
    }


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=exc.http_status,
        content=_error_payload(exc.biz_code, exc.message, trace_id, exc.detail),
        headers={"X-Trace-Id": trace_id}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    biz_code = HTTP_TO_BIZ_CODE.get(exc.status_code, BizCode.INTERNAL_ERROR)
    message = str(exc.detail) if exc.detail else "请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(biz_code, message, trace_id, exc.detail),
        headers={"X-Trace-Id": trace_id}
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    detail = exc.errors()
    return JSONResponse(
        status_code=400,
        content=_error_payload(BizCode.BAD_REQUEST, "请求核心参数无效", trace_id, detail),
        headers={"X-Trace-Id": trace_id}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    logger.exception("Unhandled exception, trace_id=%s", trace_id)
    return JSONResponse(
        status_code=500,
        content=_error_payload(BizCode.INTERNAL_ERROR, "服务器内部错误", trace_id, str(exc)),
        headers={"X-Trace-Id": trace_id}
    )
