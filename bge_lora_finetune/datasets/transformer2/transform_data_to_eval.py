"""
将 raw_data.json 转换为检索评测需要的格式。

输入示例（raw_data.json 中的每一条）:
{
  "question": "...",
  "references": [{"doc_id": 12, "library_type": "breakdown"}, ...],
  "source_doc_id": 12,
  "source_library_type": "breakdown"
}

输出示例:
[
  {
    "question": "主板供电电路的常见故障现象有哪些？",
    "relevant_docs": [
      {"doc_id": 12, "library_type": "breakdown"},
      {"doc_id": 17, "library_type": "breakdown"}
    ]
  }
]

说明：
1. 优先从 references 提取 doc_id + library_type。
2. 如果 references 为空或无有效值，则回退到 source_doc_id + source_library_type。
3. 对 relevant_docs 去重，保证同一个 (doc_id, library_type) 只保留一次。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


CURRENT_DIR = Path(__file__).resolve().parent


@dataclass
class FilePathDefinition:
    # 输入：原始评测结果文件
    DATASET_FILE: Path = CURRENT_DIR / "raw_data.json"
    # 输出：用于 simple_bge_retrieval_eval.py 的评测文件
    DATASET_SAVE_PATH: Path = CURRENT_DIR / "lora_retrieval_eval_sample.json"


def _normalize_library_type(value: Any) -> str:
    text = str(value or "").strip()
    return text or "breakdown"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _extract_relevant_docs(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从单条 raw_data 中提取 relevant_docs。
    优先读取 references；若没有有效 references，则回退 source_doc_id/source_library_type。
    """
    refs: Sequence[Any] = item.get("references") or []
    pairs: List[Tuple[int, str]] = []

    for ref in refs:
        if not isinstance(ref, dict):
            continue
        doc_id = _safe_int(ref.get("doc_id"))
        library_type = _normalize_library_type(ref.get("library_type"))
        if doc_id is None:
            continue
        pairs.append((doc_id, library_type))

    # 兼容没有 references 或 references 不完整的情况
    if not pairs:
        fallback_doc_id = _safe_int(item.get("source_doc_id"))
        fallback_library_type = _normalize_library_type(item.get("source_library_type"))
        if fallback_doc_id is not None:
            pairs.append((fallback_doc_id, fallback_library_type))

    dedup_pairs: List[Tuple[int, str]] = []
    seen = set()
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        dedup_pairs.append(pair)

    return [{"doc_id": doc_id, "library_type": library_type} for doc_id, library_type in dedup_pairs]


def transform_to_eval_dataset() -> List[Dict[str, Any]]:
    """
    读取 raw_data.json，生成检索评测格式：
    [{"question": "...", "relevant_docs": [{"doc_id": 1, "library_type": "breakdown"}, ...]}, ...]
    """
    with FilePathDefinition.DATASET_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("raw_data.json 必须是 JSON 列表")

    result: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        question = str(item.get("question") or "").strip()
        if not question:
            continue

        relevant_docs = _extract_relevant_docs(item)
        if not relevant_docs:
            continue

        result.append({
            "question": question,
            "relevant_docs": relevant_docs,
        })

    FilePathDefinition.DATASET_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FilePathDefinition.DATASET_SAVE_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    print(f"输入文件: {FilePathDefinition.DATASET_FILE}")
    print(f"输出文件: {FilePathDefinition.DATASET_SAVE_PATH}")

    dataset = transform_to_eval_dataset()
    print(f"转换完成，样本数: {len(dataset)}")
    print("前1条样本:", dataset[0] if dataset else "转换结果为空")