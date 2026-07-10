import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Preview query analysis and enhancement.")
    parser.add_argument("query", nargs="?", default="", help="User query to analyze.")
    parser.add_argument("--query-file", default="", help="Read query text from a UTF-8 file.")
    parser.add_argument("--query-images", default="", help="Optional uploaded image path string.")
    args = parser.parse_args()

    query = args.query
    if args.query_file:
        query = Path(args.query_file).read_text(encoding="utf-8-sig").strip()
    if not query:
        parser.error("provide query or --query-file")

    from database import AsyncSessionLocal
    from utils.QueryEnhancer import QueryEnhancer

    async with AsyncSessionLocal() as session:
        plan = await QueryEnhancer(session).build_plan(query, args.query_images or None)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
