"""Live database statistics. Logical documents, source files and reviews stay separate."""
from datetime import datetime

from sqlalchemy import func, select

from models import (
    DocumentBreakdown, DocumentKnowledge, Document_review,
    KnowledgeDocumentReview, SourceDocument,
)
from agents.intent.knowledge_statistics import (
    has_unsupported_statistics_filter, statistics_scope,
)


async def get_knowledge_statistics(db, library_type="all"):
    if library_type not in {"all", "knowledge", "breakdown"}:
        raise ValueError("Unsupported library_type")
    documents = {}
    pending = {}
    sources = {}
    categories = []
    for library, model, review in (
        ("knowledge", DocumentKnowledge, KnowledgeDocumentReview),
        ("breakdown", DocumentBreakdown, Document_review),
    ):
        if library_type not in {"all", library}:
            continue
        rows = (await db.execute(
            select(model.is_vectorized, func.count()).where(model.is_deleted == 0)
            .group_by(model.is_vectorized)
        )).all()
        total = sum(int(count) for _, count in rows)
        indexed = sum(int(count) for state, count in rows if state == 1)
        documents[library] = {"total": total, "vectorized": indexed, "unvectorized": total - indexed}
        pending[library] = int((await db.execute(
            select(func.count()).select_from(review).where(review.status == 0)
        )).scalar_one())
        sources[library] = int((await db.execute(
            select(func.count()).select_from(SourceDocument).where(
                SourceDocument.is_deleted == 0, SourceDocument.document_library_type == library,
            )
        )).scalar_one())
    if library_type in {"all", "knowledge"}:
        rows = (await db.execute(
            select(DocumentKnowledge.category_path, func.count())
            .where(DocumentKnowledge.is_deleted == 0)
            .group_by(DocumentKnowledge.category_path).order_by(DocumentKnowledge.category_path)
        )).all()
        categories = [{"category_path": path or "未分类", "count": int(count)} for path, count in rows]
    return {
        "library_type": library_type,
        "updated_at": datetime.now().astimezone().isoformat(),
        "total_documents": sum(item["total"] for item in documents.values()),
        "documents": documents,
        "pending_reviews": pending,
        "source_files": sources,
        "knowledge_categories": categories,
        "counting_rule": "按未删除的逻辑文档计数；不按章节、向量块或历史版本重复计数。待审核申请及源文件单独统计，不加入文档总数。已向量化状态不等于当前版本可检索。",
    }


async def build_statistics_response(db, question):
    if has_unsupported_statistics_filter(question):
        return (
            "目前支持全系统、知识库或故障库的整体数量统计，以及知识库分类、待审核和向量化状态概览。"
            "暂不支持按设备、时间、指定分类或版本筛选统计。请明确查询上述哪个范围，例如“知识库总共有多少篇文档”。",
            ["statistics_scope_unsupported"], {"statistics_scope_supported": False},
        )
    data = await get_knowledge_statistics(db, statistics_scope(question))
    label = {"all": "全系统", "knowledge": "知识库", "breakdown": "故障库"}[data["library_type"]]
    lines = [f"{label}当前共有 **{data['total_documents']} 篇正式文档**。", ""]
    for library, counts in data["documents"].items():
        name = "知识库" if library == "knowledge" else "故障库"
        lines.append(f"- {name}：{counts['total']} 篇；已向量化 {counts['vectorized']} 篇，未向量化 {counts['unvectorized']} 篇。")
    lines.extend([
        f"- 待审核申请：{sum(data['pending_reviews'].values())} 条（新增、修改、删除申请，不加入文档总数）。",
        f"- 源文件：{sum(data['source_files'].values())} 份（单独统计，不加入文档总数）。",
    ])
    if data["knowledge_categories"]:
        lines.extend(["", "知识库分类分布（按完整分类路径统计）："])
        lines.extend(f"- {row['category_path']}：{row['count']} 篇。" for row in data["knowledge_categories"])
    lines.extend(["", data["counting_rule"], f"统计时间：{data['updated_at']}"])
    return "\n".join(lines), ["query_knowledge_statistics"], {"knowledge_statistics": data}
