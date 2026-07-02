import asyncio
import os
from datetime import datetime
from typing import Iterable, List

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import DocumentKnowledge, KnowledgeDocumentSection
from utils.file_cleanup import delete_image_with_variants


def _section_to_model(document_id: int, section, index: int) -> KnowledgeDocumentSection:
    return KnowledgeDocumentSection(
        document_id=document_id,
        document_library_type="knowledge",
        section_index=section.section_index if getattr(section, "section_index", None) is not None else index,
        section_title=getattr(section, "section_title", None),
        section_type=getattr(section, "section_type", None) or str(index + 1),
        plain_text=getattr(section, "plain_text", None),
        image_urls=getattr(section, "image_urls", None) or [],
        char_start=getattr(section, "char_start", None),
        char_end=getattr(section, "char_end", None),
        section_metadata=getattr(section, "metadata", None) or {},
        created_time=datetime.now(),
        updated_time=datetime.now(),
    )


async def replace_knowledge_document_sections(
    db: AsyncSession,
    document: DocumentKnowledge,
    sections: Iterable,
):
    """替换知识库文档的章节记录，仅删除不再被新章节引用的旧图片。

    执行顺序：读取旧章节图片 → 计算差异 → 删除已移除的图片 → 替换章节记录。
    """
    image_base_dir = os.path.join(
        os.getenv("BASE_DIR", "/"),
        os.getenv("IMAGE_DIR", "upload/images").lstrip("/").lstrip("\\"),
    )

    # 1. 收集旧章节的所有图片 URL
    old_sections = await get_knowledge_document_sections(db, document.id)
    old_image_urls: set = set()
    for section in old_sections:
        for url in (section.image_urls or []):
            url_str = str(url).strip()
            if url_str:
                old_image_urls.add(url_str)

    # 2. 收集新章节的所有图片 URL
    new_image_urls: set = set()
    for section in (sections or []):
        section_urls = getattr(section, "image_urls", None) or []
        for url in section_urls:
            url_str = str(url).strip()
            if url_str:
                new_image_urls.add(url_str)

    # 3. 仅删除旧版本有、新版本没有的图片
    removed_urls = old_image_urls - new_image_urls
    for url in removed_urls:
        filename = os.path.basename(url)
        if filename.strip():
            file_path = os.path.join(
                image_base_dir,
                filename.lstrip("/").lstrip("\\"),
            )
            await asyncio.to_thread(delete_image_with_variants, file_path)

    # 4. 替换章节记录
    await db.execute(delete(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document.id))
    section_models = []
    for index, section in enumerate(sections or []):
        section_model = _section_to_model(document.id, section, index)
        section_models.append(section_model)
        db.add(section_model)
    await db.flush()
    if hasattr(document, "section_ids"):
        document.section_ids = [section.id for section in section_models if section.id is not None]
        await db.flush()


async def get_knowledge_document_sections(db: AsyncSession, document_id: int) -> List[KnowledgeDocumentSection]:
    result = await db.execute(
        select(KnowledgeDocumentSection)
        .where(KnowledgeDocumentSection.document_id == document_id)
        .order_by(KnowledgeDocumentSection.section_index.asc(), KnowledgeDocumentSection.id.asc())
    )
    return list(result.scalars().all())


async def delete_section_images_for_document(
    db: AsyncSession,
    document_id: int,
    image_base_dir: str,
) -> int:
    """删除知识库文档所有章节（KnowledgeDocumentSection）关联的图片文件。

    返回实际删除的图片数量。
    """
    sections = await get_knowledge_document_sections(db, document_id)
    deleted_count = 0
    for section in sections:
        image_urls = section.image_urls or []
        for image_url in image_urls:
            if not image_url or not str(image_url).strip():
                continue
            filename = os.path.basename(str(image_url))
            if not filename.strip():
                continue
            url = os.path.join(
                image_base_dir,
                filename.lstrip("/").lstrip("\\"),
            )
            variants = await asyncio.to_thread(delete_image_with_variants, url)
            if variants:
                deleted_count += len(variants)
    return deleted_count
