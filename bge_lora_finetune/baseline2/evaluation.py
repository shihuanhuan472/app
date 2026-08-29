#!/usr/bin/env python3
"""
================================================================
BGE LoRA 微调后 Chunk 召回效果评测脚本（独立实现版）
================================================================

完整调用链：
    question
        -> embedding（原始 BGE 或 LoRA 微调 BGE）
        -> Milvus Top-K 检索
        -> retrieved_chunks
        -> retrieval_results_{model}.json        （阶段一产物，可人工检查）
        -> LLM Judge（语义判断）
        -> judge_results_{model}.json           （阶段二产物，可人工检查）
        -> Hit@K 统计
        -> 对比 Original BGE 与 LoRA BGE 的 Hit@K 与 improvement

本脚本与 baseline/simple_bge_retrieval_eval.py 完全独立：
    - 不 import、不继承、不依赖任何旧评测代码。
    - 只复用项目真实存在的底层能力：
        * visual_bge.visual_bge.modeling.Visualized_BGE   （embedding 模型）
        * bge_lora_finetune.finetune_bge_lora 中的 LoRA 注入/加载函数（训练脚本，非评测脚本）
        * pymilvus 的 Collection.search()
        * OpenAI 兼容接口（utils.ai_endpoint.get_ai_base_url）

核心判断依据：
    ground_truth + Milvus 返回 chunk 的 content -> 交给大模型做语义判断，
    不依赖 doc_id、source_doc_id、relevant_docs，也不依赖 Milvus score 阈值。

运行方式（在 E:\\设备维修辅助系统\\app 目录下）：

    1) 只跑检索（阶段一，不调大模型）：
        python bge_lora_finetune\\baseline2\\evaluation.py --model original --stage retrieve
        python bge_lora_finetune\\baseline2\\evaluation.py --model lora --stage retrieve

    2) 只跑 Judge（阶段二，读取已有检索结果，重复评测不用重新检索）：
        python bge_lora_finetune\\baseline2\\evaluation.py --model original --stage judge

    3) 完整跑（检索 + Judge，默认）：
        python bge_lora_finetune\\baseline2\\evaluation.py --model original
        python bge_lora_finetune\\baseline2\\evaluation.py --model lora

    4) 自动对比 Original vs LoRA：
        python bge_lora_finetune\\baseline2\\evaluation.py --compare

常用可选参数：
    --dataset     评测数据集 JSON（question + ground_truth）
    --collection  Milvus collection 名称，默认 documents_collection_main_chunk
    --top-k       检索 Top-K，默认 5
    --adapter-dir LoRA adapter 目录（仅 --model lora 时使用）
================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dotenv import load_dotenv

load_dotenv()
# 离线模式：避免脚本运行时意外触发 Hugging Face 下载。
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 项目根目录：本文件位于 <root>/bge_lora_finetune/baseline2/ 下，
# parents[0]=baseline2, parents[1]=bge_lora_finetune, parents[2]=<root>
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 主动加载项目根目录 .env（若存在），保证直接运行本脚本也能拿到 MODEL_NAME / MODEL_WEIGHT。
load_dotenv(PROJECT_ROOT / ".env")

import torch  # noqa: E402
from pymilvus import Collection, connections, utility  # noqa: E402
from visual_bge.visual_bge.modeling import Visualized_BGE  # noqa: E402

# 复用项目训练脚本里的 LoRA 注入 / 加载函数。
# 注意：这是训练脚本（finetune_bge_lora.py），不是旧评测文件，
# 属于"项目真实存在的 LoRA 加载方式"，与本评测脚本无耦合风险。
from bge_lora_finetune.finetune_bge_lora import (  # noqa: E402
    inject_lora_layers,
    load_lora_adapter,
    parse_target_modules,
)

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    from utils.ai_endpoint import get_ai_base_url
except Exception:  # pragma: no cover
    get_ai_base_url = None


# ----------------------------------------------------------------------
# 默认配置
# ----------------------------------------------------------------------
DEFAULT_COLLECTION = "documents_collection_main_chunk"
DEFAULT_DATASET = "bge_lora_finetune/baseline2/evaluaion_query_groundTruth.json"
DEFAULT_BASE_WEIGHT = os.getenv("MODEL_WEIGHT", str(PROJECT_ROOT / "bge" / "Visualized_m3.pth"))
DEFAULT_MODULE_NAME = os.getenv("MODEL_NAME", str(PROJECT_ROOT / "bge" / "bge-m3"))
# from_pretrained 指向本地 BGE 配置目录（含 config.json / tokenizer），不是 HF id。
DEFAULT_FROM_PRETRAINED = os.getenv("BGE_MODEL_LOCAL_PATH", str(PROJECT_ROOT / "bge" / "bge-m3"))
DEFAULT_ADAPTER_DIR = str(PROJECT_ROOT / "bge_lora_finetune" / "text_only_output" / "lora_adapter")
DEFAULT_OUTPUT_DIR = str(PROJECT_ROOT / "bge_lora_finetune" / "baseline2" / "output" / time.strftime("%Y%m%d_%H%M%S"))

# LLM Judge 配置
DEFAULT_LLM_BASE_URL = os.getenv("AI_BASE_URL")
DEFAULT_LLM_API_KEY = os.getenv("API_KEY")
DEFAULT_LLM_MODEL = os.getenv("MODEL_AI")

# LLM Judge 参数
DEFAULT_MAX_TOKENS = int(os.getenv("MAX_TOKEN", "2000"))

# ----------------------------------------------------------------------
# 路径与通用工具函数
# ----------------------------------------------------------------------
def resolve_path(path_like: Optional[str]) -> Optional[Path]:
    """把可能为相对路径的路径转成项目根目录下的绝对路径。"""
    if not path_like:
        return None
    p = Path(path_like)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def _default_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def read_lora_config(adapter_dir: Path) -> Dict[str, Any]:
    """从 adapter_config.json 读取 LoRA 超参数，保证与训练时一致。"""
    cfg_path = adapter_dir / "adapter_config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def flatten_vector(output: Any) -> List[float]:
    """把模型返回的 embedding 统一转成一维 float 列表，供 Milvus 检索使用。"""
    if output is None:
        raise ValueError("模型输出为空，不能转为向量")
    if hasattr(output, "detach"):
        output = output.detach().cpu().numpy()
    if hasattr(output, "numpy"):
        output = output.numpy()
    if isinstance(output, (list, tuple)):
        flat = list(output)
    else:
        try:
            flat = output.tolist()
        except Exception:
            flat = list(output)
    # shape 可能是 [1, dim] 或 [dim]，统一拉成一维。
    while flat and isinstance(flat[0], (list, tuple)):
        flat = list(flat[0])
    return [float(x) for x in flat]


def _detect_metric_type(collection: Collection) -> str:
    """从 collection 索引中探测实际使用的相似度度量方式，默认 IP。"""
    try:
        for index in collection.indexes:
            params = index.params or {}
            metric = params.get("metric_type")
            if metric:
                return str(metric).upper()
    except Exception:
        pass
    return "IP"


def _ensure_loaded(collection: Collection) -> None:
    """确保 collection 已加载到内存，忽略"已加载"类异常。"""
    try:
        collection.load()
    except Exception as exc:
        if "already loaded" not in str(exc).lower():
            raise


# ----------------------------------------------------------------------
# parse_args()
# ----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BGE LoRA 微调后 Chunk 召回效果评测（独立实现）")

    # 运行模式
    parser.add_argument("--model", choices=["original", "lora"], default="original",
                        help="要评测的模型：original=原始 BGE；lora=LoRA 微调 BGE")
    parser.add_argument("--compare", action="store_true",
                        help="自动分别运行 original 与 lora，并输出对比结果")
    parser.add_argument("--stage", choices=["all", "retrieve", "judge"], default="all",
                        help="all=检索+Judge；retrieve=仅检索并保存结果；judge=仅读取已有检索结果做 LLM 判断")

    # 数据集与 Milvus
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="评测数据集 JSON 路径（question + ground_truth）")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Milvus collection 名称")
    parser.add_argument("--milvus-host", default=os.getenv("MILVUS_HOST", "localhost"), help="Milvus host")
    parser.add_argument("--milvus-port", type=int, default=int(os.getenv("MILVUS_PORT", "19530")), help="Milvus port")
    parser.add_argument("--top-k", type=int, default=5, help="Milvus 返回的 Top-K chunk 数量")

    # 模型路径
    parser.add_argument("--base-weight", default=DEFAULT_BASE_WEIGHT,
                        help="基座 Visualized_BGE 权重文件路径（bge/Visualized_m3.pth）")
    parser.add_argument("--from-pretrained", default=DEFAULT_FROM_PRETRAINED,
                        help="本地 BGE 配置目录（含 config.json / tokenizer）")
    parser.add_argument("--model-name", default="BAAI/bge-m3",
                        help="BGE 模型名（仅用于内部结构判断，保持默认即可）")
    parser.add_argument("--adapter-dir", default=DEFAULT_ADAPTER_DIR,
                        help="LoRA adapter 目录（含 adapter_model.pt，仅 --model lora 时使用）")
    parser.add_argument("--device", default=_default_device(), choices=["cpu", "cuda"], help="运行设备")

    # LLM Judge
    parser.add_argument("--llm-base-url", default=DEFAULT_LLM_BASE_URL, help="LLM OpenAI 兼容接口 base_url，默认读项目配置")
    parser.add_argument("--llm-api-key", default=DEFAULT_LLM_API_KEY, help="LLM API Key，默认读环境变量 API_KEY")
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL, help="LLM 模型名，默认读环境变量 MODEL_AI")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="LLM 最大输出 token")
    parser.add_argument("--chunk-max-chars", type=int, default=1200, help="每个 chunk 送入 LLM 前的最大字符数")

    # 输出
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="检索/Judge 结果输出目录")
    parser.add_argument("--retrieval-results", default=None,
                        help="检索结果 JSON 路径（judge 阶段的输入，默认自动推导）")
    parser.add_argument("--judge-results", default=None,
                        help="Judge 结果 JSON 路径（judge 阶段的输出，默认自动推导）")

    return parser.parse_args()


# ----------------------------------------------------------------------
# load_dataset()：读取 question + ground_truth
# ----------------------------------------------------------------------
def load_dataset(dataset_path: str) -> List[Dict[str, Any]]:
    """
    读取评测数据集，每条至少包含 question 与 ground_truth。
    不假设存在 relevant_docs / source_doc_id / doc_id 等字段。
    """
    p = resolve_path(dataset_path)
    if p is None or not p.exists():
        raise FileNotFoundError(f"评测数据集不存在: {dataset_path}")

    with p.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # 兼容 {"data": [...]} 这类包装格式
        for key in ("data", "items", "samples", "examples"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError(f"评测数据集必须是 JSON 列表: {p}")

    cleaned: List[Dict[str, Any]] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx} 条样本不是 dict: {item}")
        question = str(item.get("question") or "").strip()
        ground_truth = str(item.get("ground_truth") or "").strip()
        if not question:
            raise ValueError(f"第 {idx} 条样本缺少 question 字段: {item}")
        if not ground_truth:
            raise ValueError(f"第 {idx} 条样本缺少 ground_truth 字段: {item}")
        cleaned.append({"question": question, "ground_truth": ground_truth})

    if not cleaned:
        raise ValueError(f"评测数据集为空: {p}")
    return cleaned


# ----------------------------------------------------------------------
# connect_milvus()
# ----------------------------------------------------------------------
def connect_milvus(host: str, port: int) -> None:
    """建立与 Milvus 的连接，已存在连接时先断开再重连，避免重复连接。"""
    try:
        connections.connect(alias="default", host=host, port=port)
    except Exception:
        try:
            connections.disconnect("default")
        except Exception:
            pass
        connections.connect(alias="default", host=host, port=port)


def get_collection(collection_name: str, host: str, port: int) -> Optional[Collection]:
    """读取 Milvus 中已存在的 collection，不存在则返回 None。"""
    connect_milvus(host, port)
    if not utility.has_collection(collection_name):
        return None
    collection = Collection(collection_name)
    _ensure_loaded(collection)

    # 等待 QueryNode 完成时间戳同步，避免 search 时出现
    # "Timestamp lag too large"
    print("[INFO] collection 已加载，等待 Milvus QueryNode 同步...")
    time.sleep(5)
    return collection


# ----------------------------------------------------------------------
# load_embedding_model()：加载原始 BGE 或 LoRA 微调 BGE
# ----------------------------------------------------------------------
def load_embedding_model(kind: str, args: argparse.Namespace) -> Visualized_BGE:
    """
    加载 embedding 模型。
    kind == "original"：直接加载基座 Visualized_BGE 权重；
    kind == "lora"    ：加载基座权重 + 注入 LoRA 层 + 载入 adapter_model.pt。

    这是项目真实使用的加载方式（与训练脚本 / after_finetune_bge_retrieval.py 一致）。
    """
    base_weight = str(resolve_path(args.base_weight))
    if not Path(base_weight).exists():
        raise FileNotFoundError(f"基座权重文件不存在: {base_weight}")

    from_pretrained = str(resolve_path(args.from_pretrained)) if args.from_pretrained else None

    if kind == "original":
        print("[INFO] 加载原始 BGE 模型（不含 LoRA）...")
        model = Visualized_BGE(
            model_name_bge=args.model_name,
            model_weight=base_weight,
            from_pretrained=from_pretrained,
        )
    elif kind == "lora":
        adapter_dir = resolve_path(args.adapter_dir)
        if adapter_dir is None or not (adapter_dir / "adapter_model.pt").exists():
            raise FileNotFoundError(f"LoRA adapter 不存在: {args.adapter_dir}")

        print("[INFO] 加载 LoRA 微调 BGE 模型（基座 + adapter）...")
        cfg = read_lora_config(adapter_dir)
        rank = int(cfg.get("rank", 8))
        alpha = float(cfg.get("alpha", 16.0))
        dropout = float(cfg.get("dropout", 0.1))
        target_modules_raw = cfg.get("target_modules", ["query", "key", "value"])
        if isinstance(target_modules_raw, list):
            target_modules = ",".join(str(x) for x in target_modules_raw)
        else:
            target_modules = str(target_modules_raw)

        model = Visualized_BGE(
            model_name_bge=args.model_name,
            model_weight=base_weight,
            from_pretrained=from_pretrained,
        )

        # 注入 LoRA 层，超参数必须和 adapter_config.json 一致。
        injected = inject_lora_layers(
            model.bge_encoder,
            target_modules=parse_target_modules(target_modules),
            rank=rank,
            alpha=alpha,
            dropout=dropout,
        )
        print(f"[INFO] 注入 LoRA 层数: {len(injected)}，target_modules={target_modules}, "
              f"rank={rank}, alpha={alpha}, dropout={dropout}")

        # 载入 adapter 权重：注意传整个 model，不是 model.bge_encoder。
        load_lora_adapter(model, adapter_dir)
    else:
        raise ValueError(f"未知模型类型: {kind}")

    # 固定 device 并置为 eval 模式。
    # Visualized_BGE 内部 encode() 依赖 self.device，因此显式覆盖一次。
    device_obj = torch.device(args.device)
    model.to(device_obj)
    model.device = device_obj
    model.eval()
    return model


# ----------------------------------------------------------------------
# encode_query()：question -> embedding vector
# ----------------------------------------------------------------------
def encode_query(model: Visualized_BGE, text: str) -> List[float]:
    """
    把一个 question 编码成 embedding vector。
    真实项目 API：model.encode(image=None, text=question)，返回 tensor。
    """
    with torch.inference_mode():
        output = model.encode(image=None, text=text)
    return flatten_vector(output)


# ----------------------------------------------------------------------
# search_milvus()：embedding -> top-k chunks
# ----------------------------------------------------------------------
def search_milvus(collection: Collection, query_vector: Sequence[float], top_k: int) -> List[Dict[str, Any]]:
    """
    在 Milvus collection 中按 embedding 相似度检索 top-k chunk。

    返回每个命中 chunk 的：
        rank      排名（从 1 开始）
        doc_id / chunk_id / title / content   （来自 Milvus 字段）
        score     相似度分数（Milvus 返回）
        metadata  解析后的 metadata 字典（含 source_doc_id / library_type 等，仅作分析用）

    说明：本次"是否召回成功"的核心判断不依赖 doc_id / score，只依赖 content 交给 LLM 语义判断。
    """
    metric_type = _detect_metric_type(collection)
    results = collection.search(
        data=[list(query_vector)],
        anns_field="embedding",
        param={"metric_type": metric_type, "params": {"nprobe": 10}},
        limit=top_k,
        output_fields=["id", "doc_id", "chunk_id", "title", "content", "image_url", "metadata"],
        consistency_level="Bounded",
    )

    hits: List[Dict[str, Any]] = []
    for batch in results:
        for hit in batch:
            entity = hit.entity or {}

            # metadata 在 Milvus 中是 JSON 字符串，解析成 dict。
            raw_metadata = entity.get("metadata")
            if isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except Exception:
                    metadata = {"raw": raw_metadata}
            elif raw_metadata is None:
                metadata = {}
            else:
                metadata = dict(raw_metadata)

            hits.append(
                {
                    "rank": len(hits) + 1,
                    "doc_id": entity.get("doc_id"),
                    "chunk_id": entity.get("chunk_id"),
                    "title": entity.get("title"),
                    "content": entity.get("content"),
                    "score": float(hit.score) if hit.score is not None else None,
                    "metadata": metadata,
                }
            )
    return hits


# ----------------------------------------------------------------------
# retrieve_dataset()：遍历整个数据集完成检索
# ----------------------------------------------------------------------
def retrieve_dataset(
    dataset: List[Dict[str, Any]],
    model: Visualized_BGE,
    collection: Collection,
    top_k: int,
) -> List[Dict[str, Any]]:
    """对数据集中每条 question 执行：embedding -> Milvus Top-K -> 收集 chunk。"""
    retrieval_results: List[Dict[str, Any]] = []
    for idx, item in enumerate(dataset, start=1):
        question = item["question"]
        ground_truth = item["ground_truth"]

        query_vector = encode_query(model, question)
        chunks = search_milvus(collection, query_vector, top_k)

        retrieval_results.append(
            {
                "question": question,
                "ground_truth": ground_truth,
                "retrieved_chunks": chunks,
            }
        )
        print(f"[RETRIEVE] {idx}/{len(dataset)}  top-{top_k} chunks={len(chunks)}  q={question[:40]}")
    return retrieval_results


# ----------------------------------------------------------------------
# save_retrieval_results()：保存阶段一检索结果
# ----------------------------------------------------------------------
def save_retrieval_results(results: List[Dict[str, Any]], output_path: str) -> None:
    """把检索结果保存成 JSON，作为后续 LLM Judge 的输入。"""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] 检索结果已保存: {p}")


# ----------------------------------------------------------------------
# llm_judge()：ground_truth + chunks -> LLM 语义判断
# ----------------------------------------------------------------------
def _resolve_llm_config(args: argparse.Namespace) -> Dict[str, str]:
    """解析 LLM 调用的 base_url / api_key / model。"""
    if args.llm_base_url:
        base_url = args.llm_base_url
    elif get_ai_base_url is not None:
        base_url = get_ai_base_url()
    else:
        base_url = os.getenv("AI_BASE_URL", "http://192.168.246.200:8000/v1")

    api_key = args.llm_api_key or os.getenv("API_KEY", "EMPTY")
    model = args.llm_model or os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    return {"base_url": base_url.rstrip("/"), "api_key": api_key, "model": model}


def _build_judge_prompt(question: str, ground_truth: str, chunks: List[Dict[str, Any]], chunk_max_chars: int) -> str:
    """构建稳定、明确的 Judge Prompt。"""
    lines: List[str] = []
    lines.append("你是一名严谨的检索质量评估专家。请判断下面召回的文档片段(chunk)中，"
                 "是否存在能够支撑【标准答案(ground_truth)】的有效片段。")
    lines.append("")
    lines.append("评估规则：")
    lines.append("1. 只能依据【问题(question)】、【标准答案(ground_truth)】和【召回片段(chunks)】进行判断，"
                 "禁止使用任何外部知识。")
    lines.append("2. 只要 Top-K 片段中存在至少一个片段，能够直接包含、或通过合理语义推理得出 ground_truth 所述内容，"
                 "就判定 hit=true。")
    lines.append("3. 片段即使与问题相关，但无法支撑 ground_truth，也不能判定为 hit。")
    lines.append("4. 不要因为关键词相似就直接判定 hit，必须确认片段确实提供了 ground_truth 所需的信息。")
    lines.append("5. 如果没有任何片段能够支撑 ground_truth，则 hit=false。")
    lines.append("6. matched_chunk_indices 列出所有被判定为有效的片段编号（从 0 开始）。")
    lines.append("7. 只输出一个 JSON 对象，不要输出任何额外文字、解释或 Markdown 代码块。")
    lines.append("")
    lines.append('输出格式：{"hit": true/false, "matched_chunk_indices": [编号...], "reason": "简短说明"}')
    lines.append("")
    lines.append(f"【问题(question)】\n{question}")
    lines.append("")
    lines.append(f"【标准答案(ground_truth)】\n{ground_truth}")
    lines.append("")
    lines.append("【召回片段(chunks)】")
    for i, chunk in enumerate(chunks):
        content = str(chunk.get("content") or "").strip()
        if chunk_max_chars > 0 and len(content) > chunk_max_chars:
            content = content[:chunk_max_chars] + "…"
        lines.append(f"[{i}] {content if content else '(空)'}")
    lines.append("")
    lines.append("请输出 JSON：")
    return "\n".join(lines)


def _extract_json(text: str) -> Dict[str, Any]:
    """从 LLM 输出中稳健地提取 JSON 对象（去掉代码块围栏、只取第一个 {...}）。"""
    text = (text or "").strip()
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"LLM 输出中未找到 JSON 对象: {text[:200]}")
    return json.loads(match.group(0))


def llm_judge(entry: Dict[str, Any], args: argparse.Namespace, max_retries: int = 3) -> Dict[str, Any]:
    """
    对单条样本执行 LLM 语义判断。

    输入：question / ground_truth / retrieved_chunks
    输出：{"hit": bool, "matched_chunk_indices": [int], "reason": str}
    """
    if OpenAI is None:
        raise RuntimeError("当前环境缺少 openai 包，无法调用 LLM Judge。请先安装 openai。")

    question = entry["question"]
    ground_truth = entry["ground_truth"]
    chunks = entry.get("retrieved_chunks") or []

    llm_cfg = _resolve_llm_config(args)
    prompt = _build_judge_prompt(question, ground_truth, chunks, args.chunk_max_chars)
    messages = [{"role": "user", "content": prompt}]

    client = OpenAI(base_url=llm_cfg["base_url"], api_key=llm_cfg["api_key"])

    last_error: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm_cfg["model"],
                messages=messages,
                max_tokens=args.max_tokens,
            )
            content = response.choices[0].message.content or ""
            parsed = _extract_json(content)
            hit = bool(parsed.get("hit", False))
            matched = parsed.get("matched_chunk_indices") or []
            if isinstance(matched, (int, float)):
                matched = [int(matched)]
            else:
                matched = [int(i) for i in matched if str(i).lstrip("-").isdigit()]
            return {
                "hit": hit,
                "matched_chunk_indices": matched,
                "reason": str(parsed.get("reason") or ""),
            }
        except Exception as exc:
            last_error = exc
            print(f"[WARN] LLM Judge 第 {attempt} 次调用失败: {exc}")
            if attempt < max_retries:
                time.sleep(2 * attempt)

    # 重试全部失败后，记录为未命中，保证评测流程不中断。
    print(f"[WARN] LLM Judge 重试 {max_retries} 次仍失败，按 hit=false 处理。q={question[:40]}")
    return {"hit": False, "matched_chunk_indices": [], "reason": f"judge_error: {last_error}"}


# ----------------------------------------------------------------------
# 阶段二：读取检索结果 -> LLM Judge -> 保存 Judge 结果
# ----------------------------------------------------------------------
def run_judge(retrieval_results: List[Dict[str, Any]], args: argparse.Namespace, model_kind: str) -> List[Dict[str, Any]]:
    """对检索结果逐条执行 LLM Judge，并保存 judge 结果。"""
    judge_results: List[Dict[str, Any]] = []
    for idx, entry in enumerate(retrieval_results, start=1):
        judge = llm_judge(entry, args)
        judge_results.append(
            {
                "question": entry.get("question"),
                "ground_truth": entry.get("ground_truth"),
                "retrieved_chunks": entry.get("retrieved_chunks") or [],
                "judge": judge,
            }
        )
        print(f"[JUDGE] {idx}/{len(retrieval_results)}  hit={judge['hit']}  "
              f"matched={judge['matched_chunk_indices']}  q={(entry.get('question') or '')[:40]}")

    judge_path = args.judge_results or str(
        Path(args.output_dir) / f"judge_results_{model_kind}.json"
    )
    p = Path(judge_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(judge_results, f, ensure_ascii=False, indent=2)
    print(f"[SAVE] Judge 结果已保存: {p}")
    return judge_results


# ----------------------------------------------------------------------
# evaluate_judge_results()：根据 LLM 判断结果统计 Hit@K
# ----------------------------------------------------------------------
def evaluate_judge_results(judge_results: List[Dict[str, Any]], top_k: int) -> Dict[str, Any]:
    """
    根据 LLM Judge 结果统计指标。

    核心指标 Hit@K = 至少召回一个有效 chunk 的样本数 / 总样本数。
    额外统计 Top-1 / Top-3 / Top-5 / Top-10 命中率（基于单次 top-k 检索结果推导）。
    """
    total = len(judge_results)
    hit = 0
    ranks: List[Optional[int]] = []

    for item in judge_results:
        judge = item.get("judge") or {}
        is_hit = bool(judge.get("hit"))
        matched = judge.get("matched_chunk_indices") or []
        if is_hit:
            hit += 1
            if matched:
                # 最小匹配编号 -> 1-based 排名（rank 1 即 Top-1 命中）
                ranks.append(min(int(i) for i in matched) + 1)
            else:
                # 判定命中但未给出编号，按最高位兜底。
                ranks.append(1)
        else:
            ranks.append(None)

    candidate_ks = [k for k in (1, 3, 5, 10) if k <= top_k]
    if top_k not in candidate_ks:
        candidate_ks.append(top_k)
    candidate_ks = sorted(set(candidate_ks))

    hit_rates: Dict[int, float] = {}
    for k in candidate_ks:
        k_hits = sum(1 for r in ranks if r is not None and r <= k)
        hit_rates[k] = round(k_hits / total * 100, 2) if total else 0.0

    return {
        "total": total,
        "hit": hit,
        "hit_at_k": round(hit / total * 100, 2) if total else 0.0,
        "top_k": top_k,
        "hit_rates": hit_rates,
        "ranks": ranks,
    }


# ----------------------------------------------------------------------
# print_summary()：输出最终评测结果
# ----------------------------------------------------------------------
def print_summary(model_label: str, metrics: Dict[str, Any]) -> None:
    """打印单个模型的评测摘要。"""
    total = metrics["total"]
    hit = metrics["hit"]
    hit_at_k = metrics["hit_at_k"]
    top_k = metrics["top_k"]

    print()
    print("=" * 48)
    print(f"模型: {model_label}")
    print(f"Top-K: {top_k}")
    print(f"Total: {total}")
    print(f"Hit: {hit}")
    print(f"Hit@{top_k}: {hit_at_k:.2f}%")
    for k, rate in metrics["hit_rates"].items():
        print(f"Hit@{k}: {rate:.2f}%")
    print("=" * 48)


def print_comparison(original_metrics: Dict[str, Any], lora_metrics: Dict[str, Any], top_k: int) -> None:
    """打印 Original BGE 与 LoRA BGE 的对比摘要。"""
    original_hit = original_metrics["hit_at_k"]
    lora_hit = lora_metrics["hit_at_k"]
    improvement = lora_hit - original_hit

    print()
    print("=" * 48)
    print("Dataset 对比结果")
    print(f"Top-K: {top_k}")
    print()
    print("Original BGE")
    print(f"  Total: {original_metrics['total']}")
    print(f"  Hit: {original_metrics['hit']}")
    print(f"  Hit@{top_k}: {original_hit:.2f}%")
    print()
    print("LoRA BGE")
    print(f"  Total: {lora_metrics['total']}")
    print(f"  Hit: {lora_metrics['hit']}")
    print(f"  Hit@{top_k}: {lora_hit:.2f}%")
    print()
    print(f"Improvement: {improvement:+.2f} percentage points")
    print("=" * 48)


def save_metrics(metrics: Dict[str, Any], model_kind: str, args: argparse.Namespace) -> str:
    """将指标结果保存为 JSON 文件，返回保存路径。"""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 文件名可以包含时间戳，避免覆盖历史记录
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    metrics_path = output_dir / f"metrics_{model_kind}_{timestamp}.json"

    # 往 metrics 里补充一些元信息，方便以后识别
    metrics_with_meta = {
        "model_kind": model_kind,
        "top_k": args.top_k,
        "dataset": args.dataset,
        "collection": args.collection,
        "timestamp": timestamp,
        **metrics,
    }

    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(metrics_with_meta, f, ensure_ascii=False, indent=2)

    print(f"[SAVE] 评测指标已保存: {metrics_path}")
    return str(metrics_path)



# ----------------------------------------------------------------------
# 单个模型的完整流程
# ----------------------------------------------------------------------
def run_pipeline(model_kind: str, args: argparse.Namespace, dataset: List[Dict[str, Any]]) -> None:
    """执行"检索 + Judge"或其中某个阶段，并输出对应结果与摘要。"""
    retrieval_path = args.retrieval_results or str(
        Path(args.output_dir) / f"retrieval_results_{model_kind}.json"
    )

    # 阶段一：检索并保存结果
    if args.stage in ("all", "retrieve"):
        model = None
        try:
            model = load_embedding_model(model_kind, args)
            collection = get_collection(args.collection, args.milvus_host, args.milvus_port)
            if collection is None:
                raise RuntimeError(
                    f"Milvus collection '{args.collection}' 不存在。\n"
                    "请确认：1) Milvus 已启动；2) collection 名称正确；3) 文档已写入。"
                )
            print(f"[INFO] 使用 collection: {args.collection}，top_k={args.top_k}")
            retrieval_results = retrieve_dataset(dataset, model, collection, args.top_k)
            save_retrieval_results(retrieval_results, retrieval_path)
        finally:
            if model is not None:
                del model

    # 阶段二：读取检索结果 -> LLM Judge -> 统计
    if args.stage in ("all", "judge"):
        rp = Path(retrieval_path)
        if not rp.exists():
            raise FileNotFoundError(f"检索结果文件不存在，无法执行 Judge: {rp}")
        with rp.open("r", encoding="utf-8") as f:
            retrieval_results = json.load(f)

        judge_results = run_judge(retrieval_results, args, model_kind)
        metrics = evaluate_judge_results(judge_results, args.top_k)
        print_summary(model_kind, metrics)

        save_metrics(metrics, model_kind, args)

        return metrics
    return None


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------
def main() -> None:
    args = parse_args()

    dataset = load_dataset(args.dataset)
    print(f"[INFO] 评测样本数: {len(dataset)}")

    # 确定要运行的模型列表。
    model_kinds: List[str] = ["original", "lora"] if args.compare else [args.model]

    metrics_map: Dict[str, Dict[str, Any]] = {}
    for kind in model_kinds:
        metrics = run_pipeline(kind, args, dataset)
        if metrics is not None:
            metrics_map[kind] = metrics

    # 对比模式：输出 improvement。
    if args.compare and args.stage in ("all", "judge"):
        if "original" in metrics_map and "lora" in metrics_map:
            print_comparison(metrics_map["original"], metrics_map["lora"], args.top_k)

        # 保存对比结果
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        compare_path = output_dir / "comparison_metrics.json"
        compare_data = {
            "top_k": args.top_k,
            "original": metrics_map["original"],
            "lora": metrics_map["lora"],
            "improvement": metrics_map["lora"]["hit_at_k"] - metrics_map["original"]["hit_at_k"],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with compare_path.open("w", encoding="utf-8") as f:
            json.dump(compare_data, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] 对比结果已保存: {compare_path}")


if __name__ == "__main__":
    main()
