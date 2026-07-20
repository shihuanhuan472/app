import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Sequence

from dotenv import load_dotenv
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database import AsyncSessionLocal
from models import DocumentBreakdown, DocumentKnowledge
from utils.VectorService import VectorService


load_dotenv()


def parse_id_list(raw: str) -> List[int]:
    values: List[int] = []
    for part in (raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        values.append(int(text))
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Delete existing Milvus vectors for specific documents and rebuild them from MySQL."
    )
    parser.add_argument(
        "--breakdown-ids",
        default="",
        help="Comma-separated breakdown document ids, for example: 45,46,87",
    )
    parser.add_argument(
        "--knowledge-ids",
        default="",
        help="Comma-separated knowledge document ids",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be rebuilt without changing Milvus or MySQL.",
    )
    return parser


async def rebuild_documents(
    library_type: str,
    ids: Sequence[int],
    dry_run: bool,
) -> List[str]:
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


async def async_main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    breakdown_ids = parse_id_list(args.breakdown_ids)
    knowledge_ids = parse_id_list(args.knowledge_ids)

    if not breakdown_ids and not knowledge_ids:
        parser.error("Provide at least one of --breakdown-ids or --knowledge-ids")

    print("=== Rebuild Vector Plan ===")
    print(f"breakdown ids: {', '.join(str(value) for value in breakdown_ids) if breakdown_ids else '(none)'}")
    print(f"knowledge ids: {', '.join(str(value) for value in knowledge_ids) if knowledge_ids else '(none)'}")
    print(f"dry run: {'yes' if args.dry_run else 'no'}")

    messages: List[str] = []
    messages.extend(await rebuild_documents("breakdown", breakdown_ids, args.dry_run))
    messages.extend(await rebuild_documents("knowledge", knowledge_ids, args.dry_run))

    print("\n=== Rebuild Results ===")
    for message in messages:
        print(message)

    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
