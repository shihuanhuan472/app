"""
从scripts生成的数据集中转化为微调评测的数据集
"""

from __future__ import annotations

import os
import json
import re
from pathlib import Path
import pandas as pd
from typing import List
from dataclasses import dataclass, field


#项目根目录， 从配置中读取
ROOT_PATH = Path(os.getenv("BASE_DIR") or Path(__file__).resolve().parents[3]).expanduser().resolve()

@dataclass
class FilePathDefinition:
    # 定义数据集文件的路径
    DATASET_FILE = ROOT_PATH / "bge_lora_finetune" / "datasets" / "example1" / "raw_data.json"
    # 定义数据集保存的路径
    DATASET_SAVE_PATH = ROOT_PATH / "bge_lora_finetune" / "datasets" / "example1" / "finetune_dataset.jsonl"


def get_dataset() -> List[dict]:
    """
    从 raw_data.json 中读取每个 chunk 的 content，
    提取为 {"text": "..."} 的格式，并保存到 DATASET_SAVE_PATH。

    返回值:
        List[dict]: 例如 [{"text": "..."}, {"text": "..."}, ...]

    """
    # 1.读取原始json数据
    with open(FilePathDefinition.DATASET_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 2. 结果列表，用来保存最终的 {"text": "..."} 记录
    result: List[dict] = []

    # 3. 遍历每个 item，提取 content 字段为 text 的数据
    for item in data:
        references = item.get("references", [])
        for reference in references:
            chunks = reference.get("chunks", [])
            for chunk in chunks:
                text = chunk.get("content", "").strip()
                if text:
                    result.append({"text": text})

    # 4.确保保存目录存在
    FilePathDefinition.DATASET_SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 5.保存结果
    with open(FilePathDefinition.DATASET_SAVE_PATH, "w", encoding="utf-8") as f:
        for record in result:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return result


if __name__ == "__main__":
    print(f"数据集文件路径: {FilePathDefinition.DATASET_FILE}")
    print(f"数据集保存路径: {FilePathDefinition.DATASET_SAVE_PATH}")
    print(f"保存路径是否存在：{FilePathDefinition.DATASET_SAVE_PATH.exists()}")

    dataset = get_dataset()
    print(f"数据集大小: {len(dataset)}")
    print("数据集前1条记录:", dataset[0] if dataset else "数据集为空")