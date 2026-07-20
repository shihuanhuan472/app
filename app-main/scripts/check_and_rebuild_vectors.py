import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from pymilvus import Collection, connections, utility
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import AsyncSessionLocal
from models import DocumentBreakdown, DocumentKnowledge
from utils.VectorService import VectorService


load_dotenv()


KNOWLEDGE_DOC_ID_OFFSET = 1000000000
DEFAULT_COLLECTION_NAME = "documents_collection_main_chunk"
PAGE_SIZE = 1000


@dataclass
class VectorRow:
    pk_id: int
    vector_doc_id: int
    source_doc_id: Optional[int]
    library_type: str
    title: str
    chunk_id: Optional[int]
    metadata: Dict


def normalize_library_type(value: Optional[str]) -> str:
    return "knowledge" if str(value or "").strip().lower() == "knowledge" else "breakdown"


def decode_doc_id(vector_doc_id: int, library_type: str) -> int:
    return int(vector_doc_id) - KNOWLEDGE_DOC_ID_OFFSET if normalize_library_type(library_type) == "knowledge" else int(vector_doc_id)


def connect_milvus() -> Collection:
    host = os.getenv("MILVUS_HOST", "localhost")
    port = os.getenv("MILVUS_PORT", "19530")
    collection_name = os.getenv("MILVUS_COLLECTION_NAME", DEFAULT_COLLECTION_NAME)

    try:
        connections.disconnect(alias="default")
    except Exception:
        pass

    connections.connect(alias="default", host=host, port=port, timeout=10)
    if not utility.has_collection(collection_name):
        raise RuntimeError(f"Milvus collection not found: {collection_name}")

    collection = Collection(collection_name)
    collection.load()
    return collection


def fetch_all_vector_rows(collection: Collection) -> List[VectorRow]:
    rows: List[VectorRow] = []
    offset = 0
    output_fields = ["id", "doc_id", "chunk_id", "title", "metadata"]

    while True:
        batch = collection.query(
            expr="id >= 0",
            offset=offset,
            limit=PAGE_SIZE,
            output_fields=output_fields,
        )
        if not batch:
            break

        for item in batch:
            metadata_raw = item.get("metadata") or "{}"
            try:
                metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else dict(metadata_raw)
            except Exception:
                metadata = {}

            library_type = normalize_library_type(metadata.get("library_type", "breakdown"))
            vector_doc_id = int(item["doc_id"])
            source_doc_id = metadata.get("source_doc_id")
            if source_doc_id is not None:
                try:
                    source_doc_id = int(source_doc_id)
                except Exception:
                    source_doc_id = None
            if source_doc_id is None:
                source_doc_id = decode_doc_id(vector_doc_id, library_type)

            rows.append(
                VectorRow(
                    pk_id=int(item["id"]),
                    vector_doc_id=vector_doc_id,
                    source_doc_id=source_doc_id,
                    library_type=library_type,
                    title=str(item.get("title") or ""),
                    chunk_id=item.get("chunk_id"),
                    metadata=metadata,
                )
            )

        offset += len(batch)
        if len(batch) < PAGE_SIZE:
            break

    return rows


async def fetch_mysql_documents() -> Dict[Tuple[str, int], str]:
    docs: Dict[Tuple[str, int], str] = {}
    async with AsyncSessionLocal() as session:
        breakdown_result = await session.execute(
            select(DocumentBreakdown.id, DocumentBreakdown.title).where(DocumentBreakdown.is_deleted == 0)
        )
        for doc_id, title in breakdown_result.all():
            docs[("breakdown", int(doc_id))] = str(title or "")

        knowledge_result = await session.execute(
            select(DocumentKnowledge.id, DocumentKnowledge.title).where(DocumentKnowledge.is_deleted == 0)
        )
        for doc_id, title in knowledge_result.all():
            docs[("knowledge", int(doc_id))] = str(title or "")

    return docs


def group_rows(rows: Iterable[VectorRow]) -> Dict[Tuple[str, int], List[VectorRow]]:
    grouped: Dict[Tuple[str, int], List[VectorRow]] = defaultdict(list)
    for row in rows:
        if row.source_doc_id is None:
            continue
        grouped[(row.library_type, int(row.source_doc_id))].append(row)
    return grouped


def find_mixed_title_items(
    grouped_rows: Dict[Tuple[str, int], List[VectorRow]],
    mysql_docs: Dict[Tuple[str, int], str],
) -> List[Dict]:
    items: List[Dict] = []
    for key, rows in grouped_rows.items():
        library_type, doc_id = key
        mysql_title = mysql_docs.get(key)
        if mysql_title is None:
            continue
        vector_titles = sorted({(row.title or "").strip() for row in rows if (row.title or "").strip()})
        if len(vector_titles) <= 1:
            continue
        items.append(
            {
                "library_type": library_type,
                "doc_id": doc_id,
                "chunk_count": len(rows),
                "mysql_title": mysql_title,
                "vector_titles": vector_titles,
                "vector_doc_ids": sorted({int(row.vector_doc_id) for row in rows}),
            }
        )
    return items


def summarize_candidates(items: List[Dict]) -> Dict[str, List[int]]:
    result: Dict[str, List[int]] = {"breakdown": [], "knowledge": []}
    for item in items:
        library_type = str(item["library_type"])
        doc_id = int(item["doc_id"])
        if doc_id not in result[library_type]:
            result[library_type].append(doc_id)
    return {key: value for key, value in result.items() if value}


async def rebuild_documents(library_type: str, ids: Sequence[int], dry_run: bool) -> List[str]:
    if not ids:
        return []

    document_model = DocumentKnowledge if library_type == "knowledge" else DocumentBreakdown
    messages: List[str] = []

    async with AsyncSessionLocal() as session:
        vector_service = VectorService(session)
        for doc_id in ids:
            result = await session.execute(
                select(document_model).where(
                    document_model.id == doc_id,
                    document_model.is_deleted == 0,
                )
            )
            document = result.scalar_one_or_none()
            if not document:
                messages.append(f"{library_type}:{doc_id} skipped - document not found or deleted")
                continue

            if dry_run:
                messages.append(f"{library_type}:{doc_id} would rebuild - title={getattr(document, 'title', '')}")
                continue

            await vector_service.delete_document_from_vector_store(
                doc_id,
                getattr(document, "library_type", library_type),
            )
            document.is_vectorized = 0
            await session.flush()
            await vector_service.add_document_to_vector_store(document, commit=False)
            await session.commit()
            await session.refresh(document)
            messages.append(f"{library_type}:{doc_id} rebuilt - title={getattr(document, 'title', '')}")

    return messages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Milvus/MySQL title consistency and rebuild mixed-title documents."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show which documents would be rebuilt.",
    )
    parser.add_argument(
        "--show-limit",
        type=int,
        default=20,
        help="How many mixed-title items to print.",
    )
    return parser


async def async_main() -> int:
    args = build_parser().parse_args()

    collection = connect_milvus()
    rows = fetch_all_vector_rows(collection)
    mysql_docs = await fetch_mysql_documents()
    grouped_rows = group_rows(rows)
    mixed_title_items = find_mixed_title_items(grouped_rows, mysql_docs)

    print("=== Mixed Title Check ===")
    print(f"Milvus rows: {len(rows)}")
    print(f"Milvus grouped docs: {len(grouped_rows)}")
    print(f"MySQL active docs: {len(mysql_docs)}")
    print(f"Mixed title docs: {len(mixed_title_items)}")
    print(f"dry run: {'yes' if args.dry_run else 'no'}")

    if not mixed_title_items:
        print("\nNo mixed-title documents found.")
        return 0

    print("\n--- Mixed title docs (sample) ---")
    for item in mixed_title_items[: args.show_limit]:
        print(
            f"{item['library_type']}:{item['doc_id']} "
            f"mysql_title={item['mysql_title']!r} vector_titles={item['vector_titles']}"
        )

    candidates = summarize_candidates(mixed_title_items)
    print("\n--- Rebuild candidate IDs ---")
    for library_type, ids in candidates.items():
        print(f"{library_type}: {', '.join(str(value) for value in ids)}")

    messages: List[str] = []
    messages.extend(await rebuild_documents("breakdown", candidates.get("breakdown", []), args.dry_run))
    messages.extend(await rebuild_documents("knowledge", candidates.get("knowledge", []), args.dry_run))

    print("\n=== Rebuild Results ===")
    for message in messages:
        print(message)

    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
