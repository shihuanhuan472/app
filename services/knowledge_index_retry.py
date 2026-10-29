"""Resume persisted pending/failed corrections after errors or process restarts."""
import asyncio
import logging
from sqlalchemy import select
from database import AsyncSessionLocal
from models import DocumentKnowledge, KnowledgeDocumentVersion
from services.knowledge_version_service import KnowledgeVersionService, KnowledgeVersionError

logger = logging.getLogger(__name__)


async def retry_knowledge_indexes():
    while True:
        await asyncio.sleep(60)
        try:
            async with AsyncSessionLocal() as db:
                candidates = list((await db.execute(select(
                    KnowledgeDocumentVersion.id, KnowledgeDocumentVersion.document_id
                ).join(DocumentKnowledge, DocumentKnowledge.id == KnowledgeDocumentVersion.document_id).where(
                    DocumentKnowledge.is_deleted == 0,
                    KnowledgeDocumentVersion.workflow_status == 'published',
                    KnowledgeDocumentVersion.validity_status.in_(['active', 'legacy_active']),
                    KnowledgeDocumentVersion.index_status.in_(['pending', 'failed']),
                ).limit(20))).all())
            for version_id, document_id in candidates:
                async with AsyncSessionLocal() as db:
                    # Match the edit lock order, and skip work owned by other workers.
                    document = (await db.execute(select(DocumentKnowledge).where(
                        DocumentKnowledge.id == document_id, DocumentKnowledge.is_deleted == 0
                    ).with_for_update(skip_locked=True))).scalar_one_or_none()
                    if document is None:
                        continue
                    service = KnowledgeVersionService(db)
                    version = await service.get_version(version_id, lock=True)
                    if (version.index_status not in {'pending', 'failed'}
                            or version.workflow_status != 'published'
                            or version.validity_status not in {'active', 'legacy_active'}):
                        continue
                    try:
                        await service.reindex(version_id, version.created_by)
                    except KnowledgeVersionError:
                        logger.warning('Knowledge index retry failed: version %s', version_id)
                    await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('Knowledge index retry cycle failed')
