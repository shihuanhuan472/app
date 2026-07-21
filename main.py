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
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
# 导入路由
# from routers import auth
from routers import auth, users, admin, conversation, message, file_manage, review, source_documents, tags
# conversation_v1 已废弃；/api/v1/chats/* 由 routers/message.py 中的 chat_router 统一维护。
from routers import documents
from models import Base, DocumentBreakdown, DocumentKnowledge
from database import AsyncSessionLocal, engine
from starlette.types import Scope
from sqlalchemy import func, select, text
from utils.tag_service import normalize_tag_names, set_document_tag_names
from utils.app_exceptions import AppException
from utils.api_key import generate_api_key
from utils.error_codes import BizCode, HTTP_TO_BIZ_CODE
from utils.roles import DEFAULT_ROLE_GROUPS
from utils.upload_paths import normalize_upload_path

logger = logging.getLogger(__name__)


LEGACY_KNOWLEDGE_BREAKDOWN_COLUMNS = (
    "problem_intro",
    "image_urls_problem_intro",
    "causes",
    "image_urls_causes",
    "evaluation",
    "image_urls_evaluation",
    "inspection",
    "image_urls_inspection",
    "solutions",
    "image_urls_solutions",
    "key_points",
    "image_urls_key_points",
)

LEGACY_TAG_RELATION_TABLES = {
    "document_breakdown_tags": "document_breakdown",
    "document_knowledge_tags": "document_knowledge",
}

# 创建数据库表（使用异步引擎）
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _table_exists(conn, table_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return int(result.scalar_one() or 0) > 0


async def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return int(result.scalar_one() or 0) > 0


async def _column_type(conn, table_name: str, column_name: str) -> str:
    result = await conn.execute(
        text(
            """
            SELECT COLUMN_TYPE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return str(result.scalar_one_or_none() or "").lower()


async def _ensure_mediumtext_column(conn, table_name: str, column_name: str):
    if not await _table_exists(conn, table_name) or not await _column_exists(conn, table_name, column_name):
        return
    column_type = await _column_type(conn, table_name, column_name)
    if column_type not in {"mediumtext", "longtext"}:
        await conn.execute(text(f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` MEDIUMTEXT NULL"))


async def _index_exists(conn, table_name: str, index_name: str) -> bool:
    result = await conn.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return int(result.scalar_one() or 0) > 0


def _parse_json_list(raw_value):
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        text_value = raw_value.strip()
        if not text_value:
            return []
        try:
            raw_value = json.loads(text_value)
        except Exception:
            raw_value = [item.strip() for item in text_value.replace("，", ",").split(",")]
    if not isinstance(raw_value, (list, tuple, set)):
        raw_value = [raw_value]
    result = []
    seen = set()
    for item in raw_value:
        if item is None:
            continue
        text_value = str(item).strip()
        if not text_value:
            continue
        normalized = int(text_value) if text_value.isdigit() else text_value
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


async def _merge_and_drop_legacy_tag_relation_table(conn, relation_table: str, document_table: str):
    """旧版标签关联表不再使用；删除前把其中 tag_id 合并回文档 JSON tag。"""
    if not await _table_exists(conn, relation_table):
        return
    if not await _table_exists(conn, document_table) or not await _column_exists(conn, document_table, "tag"):
        await conn.execute(text(f"DROP TABLE IF EXISTS `{relation_table}`"))
        return

    relation_rows = await conn.execute(
        text(f"SELECT document_id, tag_id FROM `{relation_table}` WHERE document_id IS NOT NULL AND tag_id IS NOT NULL")
    )
    relation_tags = {}
    for document_id, tag_id in relation_rows.all():
        relation_tags.setdefault(int(document_id), []).append(int(tag_id))

    for document_id, tag_ids in relation_tags.items():
        raw_tag = (
            await conn.execute(
                text(f"SELECT tag FROM `{document_table}` WHERE id = :document_id"),
                {"document_id": document_id},
            )
        ).scalar_one_or_none()
        merged = _parse_json_list(raw_tag)
        seen = set(merged)
        for tag_id in tag_ids:
            if tag_id not in seen:
                merged.append(tag_id)
                seen.add(tag_id)
        await conn.execute(
            text(f"UPDATE `{document_table}` SET tag = :tag WHERE id = :document_id"),
            {"tag": json.dumps(merged, ensure_ascii=False), "document_id": document_id},
        )

    await conn.execute(text(f"DROP TABLE IF EXISTS `{relation_table}`"))


async def _cleanup_legacy_tag_relation_tables(conn):
    for relation_table, document_table in LEGACY_TAG_RELATION_TABLES.items():
        await _merge_and_drop_legacy_tag_relation_table(conn, relation_table, document_table)


async def _ensure_document_knowledge_schema(conn):
    if not await _table_exists(conn, "document_knowledge"):
        return

    if not await _column_exists(conn, "document_knowledge", "library_type"):
        await conn.execute(
            text(
                "ALTER TABLE document_knowledge "
                "ADD COLUMN library_type VARCHAR(32) NOT NULL DEFAULT 'knowledge' AFTER id"
            )
        )
    await conn.execute(text("UPDATE document_knowledge SET library_type = 'knowledge' WHERE library_type IS NULL OR library_type = ''"))
    if not await _index_exists(conn, "document_knowledge", "idx_document_knowledge_library_type"):
        await conn.execute(text("CREATE INDEX idx_document_knowledge_library_type ON document_knowledge (library_type)"))

    if not await _column_exists(conn, "document_knowledge", "sections"):
        await conn.execute(text("ALTER TABLE document_knowledge ADD COLUMN sections JSON NULL"))

    await _ensure_mediumtext_column(conn, "document_knowledge", "image_urls")

    summary_exists = await _column_exists(conn, "document_knowledge", "summary")
    content_exists = await _column_exists(conn, "document_knowledge", "content")
    if await _table_exists(conn, "knowledge_document_sections") and (summary_exists or content_exists):
        text_exprs = []
        if content_exists:
            text_exprs.append("NULLIF(dk.content, '')")
        if summary_exists:
            text_exprs.append("NULLIF(dk.summary, '')")
        plain_text_expr = f"COALESCE({', '.join(text_exprs)})"
        await conn.execute(
            text(
                f"""
                INSERT INTO knowledge_document_sections
                    (document_id, document_library_type, section_index, section_title, section_type,
                     plain_text, image_urls, char_start, char_end, metadata, created_time, updated_time)
                SELECT dk.id, 'knowledge', 0, COALESCE(NULLIF(dk.title, ''), '文档正文'), '1',
                       {plain_text_expr}, JSON_ARRAY(), 0, CHAR_LENGTH({plain_text_expr}), JSON_OBJECT(), NOW(), NOW()
                FROM document_knowledge dk
                WHERE {plain_text_expr} IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM knowledge_document_sections ks WHERE ks.document_id = dk.id
                  )
                """
            )
        )

    existing_legacy_columns = [
        column_name
        for column_name in LEGACY_KNOWLEDGE_BREAKDOWN_COLUMNS
        if await _column_exists(conn, "document_knowledge", column_name)
    ]
    if existing_legacy_columns:
        text_parts = []
        legacy_text_fields = [
            ("problem_intro", "问题简介"),
            ("causes", "原因"),
            ("evaluation", "评估"),
            ("inspection", "检查"),
            ("solutions", "解决方案"),
            ("key_points", "总结"),
        ]
        for column_name, label in legacy_text_fields:
            if column_name in existing_legacy_columns:
                text_parts.append(
                    f"IF(NULLIF(dk.{column_name}, '') IS NULL, NULL, CONCAT('{label}：', dk.{column_name}))"
                )
        if text_parts and await _table_exists(conn, "knowledge_document_sections"):
            legacy_content_expr = f"NULLIF(CONCAT_WS('\\n\\n', {', '.join(text_parts)}), '')"
            await conn.execute(
                text(
                    f"""
                    INSERT INTO knowledge_document_sections
                        (document_id, document_library_type, section_index, section_title, section_type,
                         plain_text, image_urls, char_start, char_end, metadata, created_time, updated_time)
                    SELECT dk.id, 'knowledge', 0, COALESCE(NULLIF(dk.title, ''), '历史知识内容'), '1',
                           {legacy_content_expr}, JSON_ARRAY(), 0, CHAR_LENGTH({legacy_content_expr}), JSON_OBJECT(), NOW(), NOW()
                    FROM document_knowledge dk
                    WHERE {legacy_content_expr} IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM knowledge_document_sections ks WHERE ks.document_id = dk.id
                      )
                    """
                )
            )

        image_columns = [
            column_name
            for column_name in (
                "image_urls_problem_intro",
                "image_urls_causes",
                "image_urls_evaluation",
                "image_urls_inspection",
                "image_urls_solutions",
                "image_urls_key_points",
            )
            if column_name in existing_legacy_columns
        ]
        if image_columns:
            await conn.execute(
                text(
                    f"""
                    UPDATE document_knowledge
                    SET image_urls = NULLIF(CONCAT_WS(', ', {', '.join(f"NULLIF({column_name}, '')" for column_name in image_columns)}), '')
                    WHERE (image_urls IS NULL OR image_urls = '')
                    """
                )
            )

        for column_name in existing_legacy_columns:
            await conn.execute(text(f"ALTER TABLE document_knowledge DROP COLUMN {column_name}"))

    for column_name in ("summary", "content"):
        if await _column_exists(conn, "document_knowledge", column_name):
            await conn.execute(text(f"ALTER TABLE document_knowledge DROP COLUMN {column_name}"))

    if await _table_exists(conn, "knowledge_document_sections"):
        await conn.execute(
            text(
                """
                UPDATE document_knowledge dk
                LEFT JOIN (
                    SELECT document_id, JSON_ARRAYAGG(id) AS section_ids
                    FROM knowledge_document_sections
                    GROUP BY document_id
                ) ks ON ks.document_id = dk.id
                SET dk.sections = COALESCE(ks.section_ids, JSON_ARRAY())
                WHERE dk.sections IS NULL
                """
            )
        )


async def _ensure_knowledge_section_schema(conn):
    await _ensure_mediumtext_column(conn, "knowledge_document_sections", "plain_text")
    if await _table_exists(conn, "knowledge_document_sections") and await _column_exists(conn, "knowledge_document_sections", "section_type"):
        await conn.execute(
            text(
                """
                UPDATE knowledge_document_sections
                SET section_type = CAST(section_index + 1 AS CHAR)
                WHERE section_type IS NULL
                   OR section_type = ''
                   OR section_type IN ('knowledge_section', 'document_start', 'heading', 'paragraph', 'image')
                   OR section_type LIKE 'level\\_%'
                """
            )
        )


async def ensure_document_tables_for_library_split():
    async with engine.begin() as conn:
        for table_name in ("document_breakdown", "document_knowledge"):
            if not await _column_exists(conn, table_name, "tag"):
                await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN tag JSON NULL"))

            if not await _index_exists(conn, table_name, f"idx_{table_name}_is_deleted"):
                await conn.execute(text(f"CREATE INDEX idx_{table_name}_is_deleted ON {table_name} (is_deleted)"))

        await _ensure_knowledge_section_schema(conn)
        await _ensure_document_knowledge_schema(conn)
        await _cleanup_legacy_tag_relation_tables(conn)


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


async def ensure_role_group_schema():
    async with engine.begin() as conn:
        if not await _column_exists(conn, "users", "role_group_id"):
            await conn.execute(text("ALTER TABLE users ADD COLUMN role_group_id INT NULL AFTER perm"))
        if not await _index_exists(conn, "users", "idx_users_role_group_id"):
            await conn.execute(text("CREATE INDEX idx_users_role_group_id ON users (role_group_id)"))

        for role_group in DEFAULT_ROLE_GROUPS:
            existing_id = (
                await conn.execute(
                    text("SELECT id FROM role_groups WHERE code = :code"),
                    {"code": role_group["code"]},
                )
            ).scalar_one_or_none()

            if existing_id is None:
                await conn.execute(
                    text(
                        """
                        INSERT INTO role_groups
                            (code, name, description, is_system, is_deleted, created_time, updated_time)
                        VALUES
                            (:code, :name, :description, 1, 0, NOW(), NOW())
                        """
                    ),
                    {
                        "code": role_group["code"],
                        "name": role_group["name"],
                        "description": role_group.get("description"),
                    },
                )
                existing_id = (
                    await conn.execute(
                        text("SELECT id FROM role_groups WHERE code = :code"),
                        {"code": role_group["code"]},
                    )
                ).scalar_one()
            else:
                await conn.execute(
                    text(
                        """
                        UPDATE role_groups
                        SET name = :name, description = :description, is_system = 1, is_deleted = 0
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": existing_id,
                        "name": role_group["name"],
                        "description": role_group.get("description"),
                    },
                )

            for permission_code in role_group["permissions"]:
                exists = (
                    await conn.execute(
                        text(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM role_group_permissions
                            WHERE role_group_id = :role_group_id
                              AND permission_code = :permission_code
                            """
                        ),
                        {"role_group_id": existing_id, "permission_code": permission_code},
                    )
                ).scalar_one()
                if int(exists or 0) == 0:
                    await conn.execute(
                        text(
                            """
                            INSERT INTO role_group_permissions (role_group_id, permission_code)
                            VALUES (:role_group_id, :permission_code)
                            """
                        ),
                        {"role_group_id": existing_id, "permission_code": permission_code},
                    )

            await conn.execute(
                text(
                    """
                    UPDATE users
                    SET role_group_id = :role_group_id
                    WHERE role_group_id IS NULL AND role = :legacy_role
                    """
                ),
                {
                    "role_group_id": existing_id,
                    "legacy_role": role_group["legacy_role"],
                },
            )


async def ensure_source_document_library_columns():
    async with engine.begin() as conn:
        fk_result = await conn.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'source_documents'
                  AND COLUMN_NAME = 'document_id'
                  AND REFERENCED_TABLE_NAME = 'documents'
                """
            )
        )
        for row in fk_result.all():
            await conn.execute(text(f"ALTER TABLE source_documents DROP FOREIGN KEY `{row.CONSTRAINT_NAME}`"))

        review_fk_result = await conn.execute(
            text(
                """
                SELECT CONSTRAINT_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'source_documents'
                  AND COLUMN_NAME = 'review_id'
                  AND REFERENCED_TABLE_NAME = 'document_reviews'
                """
            )
        )
        for row in review_fk_result.all():
            await conn.execute(text(f"ALTER TABLE source_documents DROP FOREIGN KEY `{row.CONSTRAINT_NAME}`"))

        library_column_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'source_documents'
                  AND COLUMN_NAME = 'document_library_type'
                """
            )
        )
        if int(library_column_result.scalar_one() or 0) == 0:
            await conn.execute(text("ALTER TABLE source_documents ADD COLUMN document_library_type VARCHAR(32) NOT NULL DEFAULT 'breakdown' AFTER document_id"))

        if not await _column_exists(conn, "source_documents", "review_library_type"):
            await conn.execute(text("ALTER TABLE source_documents ADD COLUMN review_library_type VARCHAR(32) NOT NULL DEFAULT 'breakdown' AFTER review_id"))

        if not await _column_exists(conn, "source_documents", "parse_started_time"):
            await conn.execute(text("ALTER TABLE source_documents ADD COLUMN parse_started_time DATETIME NULL AFTER parse_error"))


async def ensure_message_token_count_column():
    async with engine.begin() as conn:
        token_column_result = await conn.execute(
            text(
                """
                SELECT COUNT(*) AS cnt
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'message'
                  AND COLUMN_NAME = 'token_count'
                """
            )
        )
        if int(token_column_result.scalar_one() or 0) == 0:
            await conn.execute(
                text("ALTER TABLE message ADD COLUMN token_count INT NOT NULL DEFAULT 0 AFTER ai_reference_doc_ids")
            )


async def ensure_ai_usage_logs_table():
    async with engine.begin() as conn:
        if not await _table_exists(conn, "ai_usage_logs"):
            await conn.execute(
                text(
                    """
                    CREATE TABLE ai_usage_logs (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NULL,
                        session_id INT NULL,
                        message_id INT NULL,
                        provider VARCHAR(32) NOT NULL DEFAULT 'openai',
                        model VARCHAR(255) NOT NULL DEFAULT '',
                        request_type VARCHAR(64) NOT NULL DEFAULT '',
                        status VARCHAR(32) NOT NULL DEFAULT 'success',
                        input_tokens INT NOT NULL DEFAULT 0,
                        output_tokens INT NOT NULL DEFAULT 0,
                        total_tokens INT NOT NULL DEFAULT 0,
                        prompt_tokens INT NOT NULL DEFAULT 0,
                        completion_tokens INT NOT NULL DEFAULT 0,
                        raw_usage_json TEXT NULL,
                        error_message TEXT NULL,
                        created_time DATETIME NULL,
                        INDEX idx_ai_usage_logs_user_id (user_id),
                        INDEX idx_ai_usage_logs_session_id (session_id),
                        INDEX idx_ai_usage_logs_message_id (message_id),
                        INDEX idx_ai_usage_logs_provider (provider),
                        INDEX idx_ai_usage_logs_model (model),
                        INDEX idx_ai_usage_logs_request_type (request_type),
                        INDEX idx_ai_usage_logs_status (status),
                        INDEX idx_ai_usage_logs_created_time (created_time)
                    )
                    """
                )
            )


async def migrate_legacy_documents_to_breakdown():
    async with engine.begin() as conn:
        if not await _table_exists(conn, "documents"):
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


async def migrate_legacy_tags_to_tag_tables():
    async with AsyncSessionLocal() as db:
        for document_model in (DocumentBreakdown, DocumentKnowledge):
            result = await db.execute(select(document_model).where(document_model.is_deleted == 0))
            documents = result.scalars().all()
            for document in documents:
                raw_tag = getattr(document, "tag", [])
                legacy_tag_names = normalize_tag_names(raw_tag)
                if legacy_tag_names:
                    await set_document_tag_names(db, document, raw_tag, created_by=document.contributor_id)
        await db.commit()

# 自定义 StaticFiles 类，添加 CORS 头，用于跨域
async def migrate_legacy_upload_document_paths_to_source_documents():
    base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    path_fields = [
        ("source_documents", "stored_file_path"),
        ("document_breakdown", "origin_file_dir"),
        ("document_knowledge", "origin_file_dir"),
        ("document_reviews", "origin_file_dir"),
        ("knowledge_document_reviews", "origin_file_dir"),
    ]

    async with engine.begin() as conn:
        for table_name, column_name in path_fields:
            if not await _table_exists(conn, table_name):
                continue
            result = await conn.execute(
                text(
                    f"""
                    SELECT id, {column_name}
                    FROM {table_name}
                    WHERE {column_name} LIKE 'upload/documents/%'
                       OR {column_name} LIKE '/upload/documents/%'
                       OR {column_name} LIKE 'upload\\\\documents\\\\%'
                    """
                )
            )
            for row in result.mappings().all():
                old_path = row[column_name]
                new_path = normalize_upload_path(old_path)
                if not new_path or new_path == old_path:
                    continue
                if not (base_dir / new_path).exists():
                    continue
                await conn.execute(
                    text(f"UPDATE {table_name} SET {column_name} = :new_path WHERE id = :id"),
                    {"new_path": new_path, "id": row["id"]},
                )


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
    await ensure_role_group_schema()
    await ensure_user_api_key_column()
    await ensure_document_tables_for_library_split()
    await ensure_review_library_columns()
    await ensure_source_document_library_columns()
    await ensure_message_token_count_column()
    await ensure_ai_usage_logs_table()
    await migrate_legacy_documents_to_breakdown()
    await migrate_legacy_tags_to_tag_tables()
    await migrate_legacy_upload_document_paths_to_source_documents()

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
app.include_router(tags.router)
app.include_router(conversation.router)
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
app.include_router(tags.router, prefix=API_V1_PREFIX)
app.include_router(conversation.router, prefix=API_V1_PREFIX)
app.include_router(message.chat_router)

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
