import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from pymilvus import Collection, connections, utility
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import AsyncSessionLocal
from models import DocumentBreakdown, DocumentKnowledge


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


def encode_doc_id(doc_id: int, library_type: str) -> int:
    return int(doc_id) + KNOWLEDGE_DOC_ID_OFFSET if normalize_library_type(library_type) == "knowledge" else int(doc_id)


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


def classify_rows(
    grouped_rows: Dict[Tuple[str, int], List[VectorRow]],
    mysql_docs: Dict[Tuple[str, int], str],
) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
    ok_items: List[Dict] = []
    stale_items: List[Dict] = []
    mismatch_items: List[Dict] = []
    mixed_title_items: List[Dict] = []

    for key, rows in grouped_rows.items():
        library_type, doc_id = key
        mysql_title = mysql_docs.get(key)
        vector_titles = sorted({(row.title or "").strip() for row in rows})
        vector_doc_ids = sorted({int(row.vector_doc_id) for row in rows})
        chunk_count = len(rows)

        item = {
            "library_type": library_type,
            "doc_id": doc_id,
            "mysql_title": mysql_title,
            "vector_titles": vector_titles,
            "vector_doc_ids": vector_doc_ids,
            "chunk_count": chunk_count,
        }

        if mysql_title is None:
            stale_items.append(item)
            continue

        normalized_mysql_title = (mysql_title or "").strip()
        normalized_vector_titles = {title.strip() for title in vector_titles}
        if len(normalized_vector_titles) > 1:
            mixed_title_items.append(item)
        if normalized_mysql_title not in normalized_vector_titles:
            mismatch_items.append(item)
            continue

        if len(normalized_vector_titles) == 1:
            ok_items.append(item)

    return ok_items, stale_items, mismatch_items, mixed_title_items


def delete_stale_vectors(collection: Collection, stale_items: List[Dict]) -> int:
    deleted_groups = 0
    for item in stale_items:
        library_type = item["library_type"]
        doc_id = int(item["doc_id"])
        vector_doc_id = encode_doc_id(doc_id, library_type)
        collection.delete(f"doc_id == {vector_doc_id}")
        deleted_groups += 1
    if deleted_groups:
        collection.flush()
    return deleted_groups


def _format_item(item: Dict) -> List[str]:
    return [
        f"library_type: {item['library_type']}",
        f"doc_id: {item['doc_id']}",
        f"chunk_count: {item['chunk_count']}",
        f"mysql_title: {item['mysql_title']}",
        f"vector_titles: {', '.join(item['vector_titles']) if item['vector_titles'] else '(empty)'}",
        f"vector_doc_ids: {', '.join(str(value) for value in item['vector_doc_ids']) if item['vector_doc_ids'] else '(empty)'}",
    ]


def write_report(path: str, payload: Dict) -> None:
    lines: List[str] = []
    summary = payload["summary"]
    lines.append("=== Vector Consistency Summary ===")
    lines.append(f"Milvus rows: {summary['milvus_row_count']}")
    lines.append(f"Milvus grouped docs: {summary['milvus_doc_group_count']}")
    lines.append(f"MySQL active docs: {summary['mysql_doc_count']}")
    lines.append(f"OK docs: {summary['ok_count']}")
    lines.append(f"Stale docs: {summary['stale_count']}")
    lines.append(f"Title mismatches: {summary['title_mismatch_count']}")
    lines.append(f"Mixed title docs: {summary['mixed_title_count']}")
    lines.append(f"Deleted stale vector groups: {summary['deleted_stale_groups']}")
    lines.append("")

    lines.append("=== Revectorize Candidate IDs ===")
    if payload["revectorize_candidates"]:
        for library_type, ids in payload["revectorize_candidates"].items():
            lines.append(f"{library_type}: {', '.join(str(value) for value in ids)}")
    else:
        lines.append("(none)")
    lines.append("")

    for section_name, items in (
        ("Stale Items", payload["stale_items"]),
        ("Title Mismatch Items", payload["title_mismatch_items"]),
        ("Mixed Title Items", payload["mixed_title_items"]),
        ("OK Items", payload["ok_items"]),
    ):
        lines.append(f"=== {section_name} ({len(items)}) ===")
        if not items:
            lines.append("(none)")
            lines.append("")
            continue
        for index, item in enumerate(items, start=1):
            lines.append(f"[{index}]")
            lines.extend(_format_item(item))
            lines.append("")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")


def build_report(
    ok_items: List[Dict],
    stale_items: List[Dict],
    mismatch_items: List[Dict],
    mixed_title_items: List[Dict],
) -> Dict:
    revectorize_candidates = defaultdict(list)
    for item in stale_items + mismatch_items:
        revectorize_candidates[item["library_type"]].append(int(item["doc_id"]))
    for item in mixed_title_items:
        doc_id = int(item["doc_id"])
        if doc_id not in revectorize_candidates[item["library_type"]]:
            revectorize_candidates[item["library_type"]].append(doc_id)
    normalized_candidates = {
        library_type: sorted(set(ids))
        for library_type, ids in revectorize_candidates.items()
    }
    return {
        "summary": {
            "ok_count": len(ok_items),
            "stale_count": len(stale_items),
            "title_mismatch_count": len(mismatch_items),
            "mixed_title_count": len(mixed_title_items),
        },
        "stale_items": stale_items,
        "title_mismatch_items": mismatch_items,
        "mixed_title_items": mixed_title_items,
        "ok_items": ok_items,
        "revectorize_candidates": normalized_candidates,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check MySQL and Milvus document consistency.")
    parser.add_argument("--report", default="tmp/vector_consistency_report.txt", help="Path to write the TXT report.")
    parser.add_argument("--delete-stale", action="store_true", help="Delete Milvus vectors whose MySQL document no longer exists.")
    parser.add_argument("--show-limit", type=int, default=20, help="How many stale/mismatch items to print.")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    collection = connect_milvus()
    rows = fetch_all_vector_rows(collection)
    mysql_docs = await fetch_mysql_documents()
    grouped_rows = group_rows(rows)
    ok_items, stale_items, mismatch_items, mixed_title_items = classify_rows(grouped_rows, mysql_docs)

    deleted_groups = 0
    if args.delete_stale and stale_items:
        deleted_groups = delete_stale_vectors(collection, stale_items)

    report = build_report(ok_items, stale_items, mismatch_items, mixed_title_items)
    report["summary"]["milvus_row_count"] = len(rows)
    report["summary"]["milvus_doc_group_count"] = len(grouped_rows)
    report["summary"]["mysql_doc_count"] = len(mysql_docs)
    report["summary"]["deleted_stale_groups"] = deleted_groups

    report_path = os.path.abspath(args.report)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    write_report(report_path, report)

    print("=== Vector Consistency Summary ===")
    print(f"Milvus rows: {len(rows)}")
    print(f"Milvus grouped docs: {len(grouped_rows)}")
    print(f"MySQL active docs: {len(mysql_docs)}")
    print(f"OK docs: {len(ok_items)}")
    print(f"Stale docs: {len(stale_items)}")
    print(f"Title mismatches: {len(mismatch_items)}")
    print(f"Mixed title docs: {len(mixed_title_items)}")
    if args.delete_stale:
        print(f"Deleted stale vector groups: {deleted_groups}")
    print(f"Report written to: {report_path}")

    if report["revectorize_candidates"]:
        print("\n--- Revectorize candidate IDs ---")
        for library_type, ids in report["revectorize_candidates"].items():
            print(f"{library_type}: {', '.join(str(value) for value in ids)}")

    if stale_items:
        print("\n--- Stale vectors (sample) ---")
        for item in stale_items[: args.show_limit]:
            print(f"{item['library_type']}:{item['doc_id']} chunks={item['chunk_count']} titles={item['vector_titles']}")

    if mismatch_items:
        print("\n--- Title mismatches (sample) ---")
        for item in mismatch_items[: args.show_limit]:
            print(
                f"{item['library_type']}:{item['doc_id']} "
                f"mysql_title={item['mysql_title']!r} vector_titles={item['vector_titles']}"
            )

    if mixed_title_items:
        print("\n--- Mixed title docs (sample) ---")
        for item in mixed_title_items[: args.show_limit]:
            print(
                f"{item['library_type']}:{item['doc_id']} "
                f"mysql_title={item['mysql_title']!r} vector_titles={item['vector_titles']}"
            )

    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
