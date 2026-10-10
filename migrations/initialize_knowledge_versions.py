"""把现有 knowledge 文档幂等初始化为已发布 v1.0。

先执行 20260925_knowledge_document_versioning.sql，再运行：
    python migrations/initialize_knowledge_versions.py
"""
import asyncio

from sqlalchemy import select

from database import AsyncSessionLocal
from models import DocumentKnowledge
from services.knowledge_version_service import KnowledgeVersionService


async def main():
    async with AsyncSessionLocal() as db:
        documents = (await db.execute(
            select(DocumentKnowledge).where(DocumentKnowledge.is_deleted == 0).order_by(DocumentKnowledge.id)
        )).scalars().all()
        initialized = 0
        for document in documents:
            if not document.contributor_id:
                print(f"skip document {document.id}: contributor_id is empty")
                continue
            await KnowledgeVersionService(db).initialize_legacy_document(document.id, document.contributor_id)
            initialized += 1
        await db.commit()
        print(f"initialized {initialized} knowledge documents")


if __name__ == "__main__":
    asyncio.run(main())
