"""
将 RAG 评估结果 JSON 转换为简单的问答对列表。

输入示例（评估结果 JSON 数组）:
[
  {
    "question": "AF模块斜率异常的判断标准是什么？",
    "ground_truth": "AF模块斜率异常的判断标准是超出5±0.25。",
    ... 其他字段
  },
  ...
]

输出示例:
[
  {
    "question": "AF模块斜率异常的判断标准是什么？",
    "ground_truth": "AF模块斜率异常的判断标准是超出5±0.25。"
  },
  ...
]

说明：
1. 仅提取 question 和 ground_truth 字段。
2. 跳过 question 或 ground_truth 为空的条目。
3. 输出文件为 JSON 数组，每个元素包含 question 和 ground_truth。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


CURRENT_DIR = Path(__file__).resolve().parent


@dataclass
class FilePathDefinition:
    # 输入：RAG 评估结果文件（请根据实际文件名修改）
    INPUT_FILE: Path = CURRENT_DIR / "eval_results.json"
    # 输出：精简后的问答对文件
    OUTPUT_FILE: Path = CURRENT_DIR / "qa_pairs.json"


def transform_eval_to_qa(input_file: Path, output_file: Path) -> List[Dict[str, str]]:
    """读取评估结果 JSON，提取 question 和 ground_truth，写入新文件。"""
    with input_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("输入文件必须是 JSON 数组")

    qa_pairs: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        ground_truth = str(item.get("ground_truth") or "").strip()
        if not question or not ground_truth:
            continue
        qa_pairs.append({
            "question": question,
            "ground_truth": ground_truth,
        })

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        json.dump(qa_pairs, f, ensure_ascii=False, indent=2)

    return qa_pairs


if __name__ == "__main__":
    input_path = FilePathDefinition.INPUT_FILE
    output_path = FilePathDefinition.OUTPUT_FILE
    print(f"输入文件: {input_path}")
    print(f"输出文件: {output_path}")

    pairs = transform_eval_to_qa(input_path, output_path)
    print(f"转换完成，共 {len(pairs)} 条问答对")
    if pairs:
        print("示例:", pairs[0])