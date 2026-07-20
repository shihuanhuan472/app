"""根据本地文档生成问答对。

使用方式：直接修改文件底部 if __name__ == "__main__" 里的路径，然后运行本文件。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests
from docx import Document as Docx

try:
    import pymupdf
except ImportError:
    import fitz as pymupdf


AI_BASE_URL = "https://wcode.net/api/gpt/v1"
AI_MODEL = "qwen3.6-plus"
API_KEY = os.getenv("WCODE_API_KEY", "")


def is_temporary_file(file_path: str | Path) -> bool:
    filename = Path(file_path).name
    return filename.startswith(("~$", ".~lock."))


def get_text_by_docx(file_path: str | Path) -> str:
    doc = Docx(file_path)
    texts = []

    for para in doc.paragraphs:
        text = str(para.text).strip()
        if text:
            texts.append(text)

    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                texts.append(" | ".join(cells))

    return "\n".join(texts)


def get_text_by_pdf(file_path: str | Path) -> str:
    doc = pymupdf.open(file_path)
    texts = []

    for page in doc:
        text = page.get_text().strip()
        if text:
            texts.append(text)

    return "\n".join(texts)


def get_text_by_txt(file_path: str | Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read().strip()
        except UnicodeDecodeError:
            continue

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read().strip()


def get_text(file_path: str | Path) -> str:
    file_path = Path(file_path)

    if not file_path.exists():
        print(f"文件不存在：{file_path}")
        return ""

    if is_temporary_file(file_path):
        print(f"跳过临时文件：{file_path.name}")
        return ""

    ext = file_path.suffix.lower()

    if ext == ".docx":
        return get_text_by_docx(file_path)
    if ext == ".pdf":
        return get_text_by_pdf(file_path)
    if ext in [".txt", ".md"]:
        return get_text_by_txt(file_path)

    print(f"不支持的文件类型：{file_path}")
    return ""


def get_ai_base_url() -> str:
    return AI_BASE_URL.rstrip("/")


def parse_json_array(content: str) -> list[dict]:
    content = content.strip()
    content = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        content,
        flags=re.MULTILINE | re.IGNORECASE,
    ).strip()

    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end > start:
        content = content[start : end + 1]

    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("模型返回内容不是 JSON 数组")

    return data


def get_problem_answer_by_ai(file_path: str | Path, questions_count: int = 2):
    if not API_KEY:
        raise ValueError("请先设置环境变量 WCODE_API_KEY")

    file_path = Path(file_path)
    print(f"开始分析：{file_path}")

    text = get_text(file_path)
    print(f"内容解析完成：{file_path}")

    if not text:
        print("text为空")
        return None

    url = f"{get_ai_base_url()}/chat/completions"
    data = {
        "model": AI_MODEL,
        "max_tokens": 2000,
        "temperature": 0.2,
        "messages": [
            {
                "role": "user",
                "content": f"""请根据以下完整文档内容，生成 {questions_count} 个高质量问答对，要求严格遵循文档内容，不得私自编撰，并使用中文。

【模板】：
[
    {{
        "question": "xxx",
        "ground_truth": "xxx"
    }},
    {{
        "question": "xxx",
        "ground_truth": "xxx"
    }}
]

【内容】：
{text}

【注意】：
1. 严格按照模板格式回答，只输出 JSON 数组，不要输出 Markdown 或解释。
2. 问题需准确且包含具体设备名称、型号、参数、故障现象或专业名词，需要能从大量文档中定位到该文档。
3. 请勿使用“该”“这”等指代词，需具体指明设备类型、名称、型号或品牌。
4. 答案必须来自文档内容，不得补充文档之外的信息。
5. 对 T7、16S、18S、ITS、Q30、RFID、Offset 等专业名词、型号和缩写必须原样保留。
""",
            }
        ],
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, json=data, timeout=180)

    if response.status_code != 200:
        print(response.text)
        print("AI响应码非200")
        return None

    outer = response.json()
    content = outer["choices"][0]["message"]["content"]
    qa_list = parse_json_array(content)

    print("-----------")
    print(qa_list)

    for item in qa_list:
        item["filename"] = file_path.name
        item["source_file"] = file_path.name
        item["source_path"] = str(file_path.resolve())
        item["answer"] = item.get("answer") or item.get("ground_truth", "")
        item["source_text"] = text

    return qa_list


def list_document_files(file_dir: str | Path) -> list[Path]:
    file_dir = Path(file_dir)

    if not file_dir.is_dir():
        raise NotADirectoryError(f"文件夹不存在：{file_dir}")

    supported_extensions = {".pdf", ".docx", ".txt", ".md"}
    file_paths = []

    for file_path in sorted(file_dir.iterdir()):
        if not file_path.is_file():
            continue

        if is_temporary_file(file_path):
            print(f"跳过临时文件：{file_path.name}")
            continue

        if file_path.suffix.lower() not in supported_extensions:
            print(f"不支持的文件类型，跳过：{file_path}")
            continue

        file_paths.append(file_path)

    return file_paths


def load_saved_dataset(save_path: str | Path) -> list[dict]:
    save_path = Path(save_path)

    if save_path.exists() and save_path.is_dir():
        raise IsADirectoryError(f"save_path 必须是 JSON 文件，不能是文件夹：{save_path}")

    save_path.parent.mkdir(parents=True, exist_ok=True)

    if not save_path.exists():
        save_dataset([], save_path)
        print(f"文件已创建：{save_path}")
        return []

    with save_path.open("r", encoding="utf-8") as f:
        try:
            dataset = json.load(f)
            return dataset if isinstance(dataset, list) else []
        except json.JSONDecodeError:
            print(f"原有 JSON 文件格式错误，将从空列表开始：{save_path}")
            return []


def save_dataset(dataset: list[dict], save_path: str | Path) -> None:
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=4)

    os.replace(tmp_path, save_path)


def get_processed_filenames(dataset: list[dict]) -> set[str]:
    return {
        str(item.get("filename"))
        for item in dataset
        if isinstance(item, dict) and item.get("filename")
    }


def get_pending_files(file_dir: str | Path, filename_done: set[str], max_files: int) -> list[Path]:
    all_files = list_document_files(file_dir)
    pending_files = [
        file_path
        for file_path in all_files
        if file_path.name not in filename_done
    ]

    print(f"目录中支持的文档数量：{len(all_files)}")
    print(f"已处理文档数量：{len(filename_done)}")
    print(f"尚未处理文档数量：{len(pending_files)}")

    if max_files > 0:
        pending_files = pending_files[:max_files]

    print(f"本次准备读取文档数量：{len(pending_files)}")
    return pending_files


def prepare_problems_answers(
    save_path: str | Path,
    file_dir: str | Path,
    max_files: int = 0,
    questions_count: int = 2,
):
    dataset = load_saved_dataset(save_path)
    print(f"已有问答对数量：{len(dataset)}")

    filename_done = get_processed_filenames(dataset)
    file_paths = get_pending_files(file_dir, filename_done, max_files)

    success_count = 0
    failure_count = 0

    for index, filepath in enumerate(file_paths, start=1):
        filename = filepath.name
        print()
        print(f"开始处理 {index}/{len(file_paths)}：{filename}")

        try:
            data = get_problem_answer_by_ai(filepath, questions_count=questions_count)
        except KeyboardInterrupt:
            print("检测到 Ctrl+C，正在保存已有数据……")
            save_dataset(dataset, save_path)
            raise
        except Exception as exc:
            failure_count += 1
            print(f"{filename} 处理失败，已跳过")
            print(f"具体错误：{exc}")
            continue

        if not data:
            failure_count += 1
            print(f"{filename} 解析出来为 None，已跳过")
            continue

        dataset.extend(data)
        filename_done.add(filename)
        success_count += 1

        save_dataset(dataset, save_path)
        print(f"{filename} 处理成功，已立即保存")
        print(f"当前问答对数量：{len(dataset)}")

    save_dataset(dataset, save_path)

    print()
    print("任务结束")
    print(f"成功处理文档数量：{success_count}")
    print(f"失败文档数量：{failure_count}")
    print(f"最终问答对数量：{len(dataset)}")
    print(f"保存位置：{save_path}")


if __name__ == "__main__":
    save_path = r"C:\Users\exile\Desktop\data\source_documents\source_documents\data1.json"
    file_dir = r"C:\Users\exile\Desktop\data\source_documents\source_documents\word"
    max_files = 9
    questions_count = 2

    prepare_problems_answers(save_path, file_dir, max_files, questions_count)
