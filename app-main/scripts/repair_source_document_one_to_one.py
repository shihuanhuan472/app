from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.upload_paths import normalize_upload_path


load_dotenv()

PROTECTED_SOURCE_STATUSES = {"review_pending", "parsing"}


@dataclass(frozen=True)
class DocRef:
    library_type: str
    id: int
    path: str
    title: str
    origin_file_name: str
    first_edit_date: Optional[datetime]
    document: object

    @property
    def key(self) -> str:
        return f"{self.library_type}#{self.id}"


@dataclass(frozen=True)
class SourceRepair:
    source: SourceDocument
    keep: DocRef
    old_status: str
    old_document_id: Optional[int]
    old_library_type: str


def normalize_path(value: Optional[str]) -> str:
    normalized = normalize_upload_path(value)
    return str(normalized or "").strip().replace("\\", "/")


def doc_to_dict(doc: DocRef) -> dict:
    return {
        "library_type": doc.library_type,
        "id": doc.id,
        "key": doc.key,
        "path": doc.path,
        "title": doc.title,
        "origin_file_name": doc.origin_file_name,
        "first_edit_date": doc.first_edit_date.isoformat() if doc.first_edit_date else None,
    }


def source_to_dict(source: SourceDocument) -> dict:
    return {
        "id": source.id,
        "origin_file_name": source.origin_file_name,
        "stored_file_path": normalize_path(source.stored_file_path),
        "status": source.status,
        "document_id": source.document_id,
        "document_library_type": source.document_library_type,
        "review_id": source.review_id,
    }


def choose_source(sources: Sequence[SourceDocument]) -> Optional[SourceDocument]:
    if not sources:
        return None
    return sorted(
        sources,
        key=lambda source: (
            str(source.status or "") == "vectorized",
            source.document_id is not None,
            source.id or 0,
        ),
        reverse=True,
    )[0]


def is_protected_source(source: SourceDocument) -> bool:
    return str(source.status or "").strip().lower() in PROTECTED_SOURCE_STATUSES


def protected_sources(sources: Sequence[SourceDocument]) -> List[SourceDocument]:
    return [source for source in sources if is_protected_source(source)]


def choose_keep_doc(
    docs: Sequence[DocRef],
    source: Optional[SourceDocument],
    fallback_keep: str,
) -> Tuple[DocRef, str]:
    if source and source.document_id is not None:
        source_library_type = (
            "knowledge"
            if str(source.document_library_type or "").strip().lower() == "knowledge"
            else "breakdown"
        )
        for doc in docs:
            if doc.library_type == source_library_type and doc.id == source.document_id:
                return doc, "source_document_link"

    if fallback_keep == "oldest":
        return min(docs, key=lambda doc: doc.id), "fallback_oldest_id"
    return max(docs, key=lambda doc: doc.id), "fallback_latest_id"


async def load_active_documents(session) -> Tuple[Dict[str, List[DocRef]], List[DocRef]]:
    from sqlalchemy import select

    from models import DocumentBreakdown, DocumentKnowledge

    document_models = {
        "breakdown": DocumentBreakdown,
        "knowledge": DocumentKnowledge,
    }

    docs_by_path: Dict[str, List[DocRef]] = defaultdict(list)
    docs_without_path: List[DocRef] = []

    for library_type, model in document_models.items():
        result = await session.execute(select(model).where(model.is_deleted == 0))
        for document in result.scalars().all():
            path = normalize_path(getattr(document, "origin_file_dir", None))
            doc = DocRef(
                library_type=library_type,
                id=int(document.id),
                path=path,
                title=getattr(document, "title", "") or "",
                origin_file_name=getattr(document, "origin_file_name", "") or "",
                first_edit_date=getattr(document, "first_edit_date", None),
                document=document,
            )
            if path:
                docs_by_path[path].append(doc)
            else:
                docs_without_path.append(doc)

    return docs_by_path, docs_without_path


async def load_active_sources(session) -> Dict[str, List[SourceDocument]]:
    from sqlalchemy import select

    from models import SourceDocument

    sources_by_path: Dict[str, List[SourceDocument]] = defaultdict(list)
    result = await session.execute(select(SourceDocument).where(SourceDocument.is_deleted == 0))
    for source in result.scalars().all():
        path = normalize_path(source.stored_file_path)
        if path:
            sources_by_path[path].append(source)
    return sources_by_path


def source_needs_repair(source: SourceDocument, keep: DocRef) -> bool:
    return (
        source.status != "vectorized"
        or source.document_id != keep.id
        or source.document_library_type != keep.library_type
        or source.review_id is not None
        or source.parse_error is not None
        or getattr(source, "parse_started_time", None) is not None
    )


def build_plan(
    docs_by_path: Dict[str, List[DocRef]],
    docs_without_path: Sequence[DocRef],
    sources_by_path: Dict[str, List[SourceDocument]],
    fallback_keep: str,
    only_path: str = "",
    limit_groups: int = 0,
    include_protected_sources: bool = False,
) -> dict:
    duplicate_groups = []
    delete_docs: List[DocRef] = []
    kept_by_path: Dict[str, DocRef] = {}
    protected_source_groups = []

    paths = sorted(docs_by_path.keys())
    if only_path:
        wanted = normalize_path(only_path)
        paths = [path for path in paths if path == wanted]

    processed_duplicate_groups = 0
    for path in paths:
        docs = sorted(docs_by_path[path], key=lambda doc: (doc.library_type, doc.id))
        sources = sources_by_path.get(path, [])
        path_protected_sources = protected_sources(sources)
        source = choose_source(sources)
        if len(docs) > 1:
            if path_protected_sources and not include_protected_sources:
                protected_source_groups.append(
                    {
                        "path": path,
                        "docs": [doc_to_dict(doc) for doc in docs],
                        "sources": [source_to_dict(source) for source in path_protected_sources],
                        "reason": "source document is review_pending or parsing",
                    }
                )
                continue
            if limit_groups and processed_duplicate_groups >= limit_groups:
                continue
            keep, keep_reason = choose_keep_doc(docs, source, fallback_keep)
            to_delete = [doc for doc in docs if doc.key != keep.key]
            delete_docs.extend(to_delete)
            duplicate_groups.append(
                {
                    "path": path,
                    "doc_count": len(docs),
                    "keep": doc_to_dict(keep),
                    "keep_reason": keep_reason,
                    "delete_candidates": [doc_to_dict(doc) for doc in to_delete],
                    "source": source_to_dict(source) if source else None,
                    "active_source_count": len(sources_by_path.get(path, [])),
                }
            )
            kept_by_path[path] = keep
            processed_duplicate_groups += 1
        elif docs and (not limit_groups or only_path):
            kept_by_path[path] = docs[0]

    source_repairs: List[SourceRepair] = []
    formal_without_source = []
    multiple_source_rows = []

    for path, keep in kept_by_path.items():
        sources = sources_by_path.get(path, [])
        if not sources:
            formal_without_source.append(doc_to_dict(keep))
            continue
        if len(sources) > 1:
            multiple_source_rows.append(
                {
                    "path": path,
                    "keep": doc_to_dict(keep),
                    "sources": [source_to_dict(source) for source in sources],
                }
            )

        source = choose_source(sources)
        if source and source_needs_repair(source, keep):
            source_repairs.append(
                SourceRepair(
                    source=source,
                    keep=keep,
                    old_status=source.status,
                    old_document_id=source.document_id,
                    old_library_type=source.document_library_type,
                )
            )

    source_without_formal = []
    kept_paths = set(kept_by_path.keys())
    for path, sources in sorted(sources_by_path.items()):
        if path in kept_paths:
            continue
        for source in sources:
            if source.status == "vectorized" or source.document_id is not None:
                source_without_formal.append(source_to_dict(source))

    return {
        "duplicate_groups": duplicate_groups,
        "delete_docs": delete_docs,
        "source_repairs": source_repairs,
        "formal_without_source": formal_without_source,
        "source_without_formal": source_without_formal,
        "multiple_source_rows": multiple_source_rows,
        "protected_source_groups": protected_source_groups,
        "docs_without_path": [doc_to_dict(doc) for doc in docs_without_path],
    }


def serialize_plan(plan: dict) -> dict:
    return {
        "duplicate_group_count": len(plan["duplicate_groups"]),
        "delete_document_count": len(plan["delete_docs"]),
        "source_repair_count": len(plan["source_repairs"]),
        "formal_without_source_count": len(plan["formal_without_source"]),
        "source_without_formal_count": len(plan["source_without_formal"]),
        "multiple_source_path_count": len(plan["multiple_source_rows"]),
        "protected_source_group_count": len(plan["protected_source_groups"]),
        "docs_without_path_count": len(plan["docs_without_path"]),
        "duplicate_groups": plan["duplicate_groups"],
        "delete_documents": [doc_to_dict(doc) for doc in plan["delete_docs"]],
        "source_repairs": [
            {
                "source_id": repair.source.id,
                "path": repair.keep.path,
                "old_status": repair.old_status,
                "old_document_id": repair.old_document_id,
                "old_library_type": repair.old_library_type,
                "new_status": "vectorized",
                "new_document_id": repair.keep.id,
                "new_library_type": repair.keep.library_type,
            }
            for repair in plan["source_repairs"]
        ],
        "formal_without_source": plan["formal_without_source"],
        "source_without_formal": plan["source_without_formal"],
        "multiple_source_rows": plan["multiple_source_rows"],
        "protected_source_groups": plan["protected_source_groups"],
        "docs_without_path": plan["docs_without_path"],
    }


async def delete_vectors(session, docs: Iterable[DocRef], skip_vector_delete: bool) -> List[str]:
    messages = []
    if skip_vector_delete:
        return [f"{doc.key} skipped vector deletion by flag" for doc in docs]

    from utils.VectorService import VectorService

    vector_service = VectorService(session)
    for doc in docs:
        await vector_service.delete_document_from_vector_store(doc.id, doc.library_type)
        messages.append(f"{doc.key} vector deleted")
    return messages


async def apply_plan(session, plan: dict, skip_vector_delete: bool, hard_delete: bool) -> List[str]:
    from sqlalchemy import delete

    from models import KnowledgeDocumentSection

    messages = []
    delete_docs: List[DocRef] = plan["delete_docs"]
    source_repairs: List[SourceRepair] = plan["source_repairs"]

    messages.extend(await delete_vectors(session, delete_docs, skip_vector_delete))

    now = datetime.now()
    for doc in delete_docs:
        if hard_delete:
            if doc.library_type == "knowledge":
                await session.execute(
                    delete(KnowledgeDocumentSection).where(
                        KnowledgeDocumentSection.document_id == doc.id,
                        KnowledgeDocumentSection.document_library_type == "knowledge",
                    )
                )
            await session.delete(doc.document)
            messages.append(f"{doc.key} hard deleted")
        else:
            doc.document.is_deleted = 1
            if hasattr(doc.document, "is_vectorized"):
                doc.document.is_vectorized = 0
            if hasattr(doc.document, "vector_update_time"):
                doc.document.vector_update_time = now
            messages.append(f"{doc.key} soft deleted")

    for repair in source_repairs:
        source = repair.source
        source.status = "vectorized"
        source.document_id = repair.keep.id
        source.document_library_type = repair.keep.library_type
        source.review_id = None
        source.parse_error = None
        if hasattr(source, "parse_started_time"):
            source.parse_started_time = None
        messages.append(
            f"source#{source.id} linked to {repair.keep.key} "
            f"(was {repair.old_library_type}#{repair.old_document_id}, status={repair.old_status})"
        )

    await session.commit()
    return messages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Repair historical one-source-to-many-document data. The script keeps one formal "
            "document per origin_file_dir, deletes duplicate formal documents, deletes "
            "their vectors, and repairs source_documents links. Dry-run is the default."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update MySQL and delete vectors. Without this flag, only prints a dry-run plan.",
    )
    parser.add_argument(
        "--fallback-keep",
        choices=["latest", "oldest"],
        default="latest",
        help="When source_documents does not point to a document in the duplicate group, keep latest or oldest id.",
    )
    parser.add_argument(
        "--skip-vector-delete",
        action="store_true",
        help="Do not delete vectors for duplicate formal documents. Not recommended except for emergency DB-only repair.",
    )
    parser.add_argument(
        "--hard-delete",
        action="store_true",
        help="Physically delete duplicate formal document rows. Requires --apply to take effect. Source files are not deleted.",
    )
    parser.add_argument(
        "--include-protected-sources",
        action="store_true",
        help="Also repair paths whose source document is review_pending or parsing. Default skips them.",
    )
    parser.add_argument(
        "--only-path",
        default="",
        help="Only repair one normalized origin_file_dir/stored_file_path.",
    )
    parser.add_argument(
        "--limit-groups",
        type=int,
        default=0,
        help="Limit duplicate groups processed. Useful for a small apply test.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Path for JSON report. Defaults to tmp/repair_source_document_one_to_one_<timestamp>.json.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every duplicate group and repair action.",
    )
    return parser


def default_report_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return PROJECT_ROOT / "tmp" / f"repair_source_document_one_to_one_{timestamp}.json"


def print_summary(serialized: dict, apply: bool, skip_vector_delete: bool, hard_delete: bool, verbose: bool) -> None:
    print("=== Source Document One-to-One Repair Plan ===")
    print(f"mode: {'APPLY' if apply else 'DRY RUN'}")
    print(f"document deletion: {'hard delete' if hard_delete else 'soft delete'}")
    print(f"vector deletion: {'skipped' if skip_vector_delete else 'enabled'}")
    print(f"duplicate groups: {serialized['duplicate_group_count']}")
    print(f"formal documents to delete: {serialized['delete_document_count']}")
    print(f"source links to repair: {serialized['source_repair_count']}")
    print(f"formal docs without active source row: {serialized['formal_without_source_count']}")
    print(f"vectorized sources without active formal doc: {serialized['source_without_formal_count']}")
    print(f"paths with multiple active source rows: {serialized['multiple_source_path_count']}")
    print(f"paths skipped for review_pending/parsing source: {serialized['protected_source_group_count']}")
    print(f"active formal docs without origin_file_dir: {serialized['docs_without_path_count']}")

    if serialized["duplicate_groups"]:
        print("\nDuplicate group preview:")
        groups = serialized["duplicate_groups"] if verbose else serialized["duplicate_groups"][:10]
        for group in groups:
            delete_keys = ", ".join(item["key"] for item in group["delete_candidates"])
            print(
                f"- path={group['path']} keep={group['keep']['key']} "
                f"reason={group['keep_reason']} delete=[{delete_keys}]"
            )
        if not verbose and len(serialized["duplicate_groups"]) > 10:
            print(f"... {len(serialized['duplicate_groups']) - 10} more groups in the JSON report")

    if serialized["source_repairs"]:
        print("\nSource repair preview:")
        repairs = serialized["source_repairs"] if verbose else serialized["source_repairs"][:10]
        for repair in repairs:
            print(
                f"- source#{repair['source_id']} "
                f"{repair['old_library_type']}#{repair['old_document_id']} -> "
                f"{repair['new_library_type']}#{repair['new_document_id']}"
            )
        if not verbose and len(serialized["source_repairs"]) > 10:
            print(f"... {len(serialized['source_repairs']) - 10} more repairs in the JSON report")


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    from database import AsyncSessionLocal

    if args.apply and args.skip_vector_delete:
        print("WARNING: --skip-vector-delete leaves duplicate vectors in Milvus/vector store.")
    if args.apply and args.hard_delete:
        print("WARNING: --hard-delete will physically delete duplicate formal document rows.")

    async with AsyncSessionLocal() as session:
        docs_by_path, docs_without_path = await load_active_documents(session)
        sources_by_path = await load_active_sources(session)
        plan = build_plan(
            docs_by_path=docs_by_path,
            docs_without_path=docs_without_path,
            sources_by_path=sources_by_path,
            fallback_keep=args.fallback_keep,
            only_path=args.only_path,
            limit_groups=max(args.limit_groups, 0),
            include_protected_sources=args.include_protected_sources,
        )
        serialized = serialize_plan(plan)

        report_path = Path(args.report_path) if args.report_path else default_report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_payload = {
            "generated_at": datetime.now().isoformat(),
            "mode": "apply" if args.apply else "dry_run",
            "fallback_keep": args.fallback_keep,
            "skip_vector_delete": bool(args.skip_vector_delete),
            "hard_delete": bool(args.hard_delete),
            "include_protected_sources": bool(args.include_protected_sources),
            "only_path": args.only_path,
            "limit_groups": args.limit_groups,
            "plan": serialized,
        }
        report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print_summary(serialized, args.apply, args.skip_vector_delete, args.hard_delete, args.verbose)
        print(f"\nJSON report: {report_path}")

        if not args.apply:
            print("\nDRY RUN complete. No MySQL or vector-store changes were made.")
            print("Run again with --apply after reviewing the plan.")
            return 0

        if not plan["delete_docs"] and not plan["source_repairs"]:
            print("\nNothing to apply.")
            return 0

        messages = await apply_plan(session, plan, args.skip_vector_delete, args.hard_delete)
        print("\n=== Applied Changes ===")
        for message in messages:
            print(message)
        print("\nApply complete. Run this script again without --apply to verify duplicate groups are gone.")
        return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
