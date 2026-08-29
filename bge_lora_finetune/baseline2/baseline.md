# BGE LoRA 微调后 Chunk 召回效果评测脚本使用指南

> 本文介绍 `bge_lora_finetune/baseline2/evaluation.py` 的用法。
> 该脚本评测**原始 BGE** 与 **LoRA 微调 BGE** 在 Milvus 文档 chunk 检索任务上的效果，核心指标为 **Hit@K**。

## 目录

- [1. 脚本概述](#1-脚本概述)
- [2. 前置条件](#2-前置条件)
- [3. 运行方式](#3-运行方式)
- [4. 命令行参数](#4-命令行参数)
- [5. 输出文件说明](#5-输出文件说明)
- [6. 运行逻辑详解](#6-运行逻辑详解)
- [7. Hit@K 计算方式](#7-hitk-计算方式)
- [8. 注意事项](#8-注意事项)
- [9. 推荐运行流程](#9-推荐运行流程)
- [10. 快速开始](#10-快速开始)

---

## 1. 脚本概述

**完整调用链**

```C
flowchart TD
    Q[question] --> E[embedding<br/>原始 BGE 或 LoRA BGE]
    E --> M[Milvus Top-K 检索]
    M --> R[retrieved_chunks]
    R --> F1[retrieval_results_{model}.json]
    F1 --> J[LLM Judge 语义判断]
    J --> F2[judge_results_{model}.json]
    F2 --> H[Hit@K 统计]
    H --> C[对比 Original 与 LoRA 的 Hit@K 及 improvement]
```

**核心判断依据**

- 将 `ground_truth` 与召回的 chunk `content` 交给大模型做语义判断：Top-K 结果中是否存在能支撑 `ground_truth` 的 chunk。
- **不依赖**以下字段：
  - `doc_id`
  - `source_doc_id`
  - `relevant_docs`
  - Milvus score 阈值

**与旧评测代码的关系**

本脚本与旧评测代码完全独立（不 import、不继承），仅复用项目真实存在的底层能力：

- `Visualized_BGE` 模型
- LoRA 注入 / 加载函数
- PyMilvus 检索
- OpenAI 兼容接口

---

## 2. 前置条件

### 2.1 Python 环境

依赖：`torch`、`pymilvus`、`openai`、`python-dotenv`

### 2.2 Milvus

Milvus 已启动，且 collection `documents_collection_main_chunk` 已写入 chunk 数据，建议至少包含字段：

`embedding`、`doc_id`、`chunk_id`、`title`、`content`、`metadata`

### 2.3 `.env` 配置

在项目根目录配置 `.env`：

| 变量                     | 示例值                             | 说明                |
| ------------------------ | ---------------------------------- | ------------------- |
| `MODEL_WEIGHT`         | `bge/Visualized_m3.pth`          | BGE 基座权重路径    |
| `MODEL_NAME`           | `BAAI/bge-m3`                    | BGE 模型名称        |
| `BGE_MODEL_LOCAL_PATH` | `bge/bge-m3`                     | 本地 BGE 配置目录   |
| `API_KEY`              | `your_api_key`                   | LLM API Key（必填） |
| `AI_BASE_URL`          | `http://192.168.246.200:8000/v1` | LLM 服务地址        |
| `MODEL_AI`             | `your_llm_model`                 | LLM 模型名称        |

### 2.4 LoRA Adapter

评测 LoRA 模型时需确保 adapter 已训练并保存。默认路径：

```
bge_lora_finetune/text_only_output/lora_adapter/
```

目录中应至少包含 `adapter_model.pt` 与 `adapter_config.json`。

### 2.5 评测数据集

默认数据集：`bge_lora_finetune/baseline2/evaluaion_query_groundTruth.json`

格式为 JSON 数组，每条至少包含非空的 `question` 与 `ground_truth`（脚本会校验）：

```json
[
  {
    "question": "如何更换液压油滤芯？",
    "ground_truth": "关闭电源，拆卸旧滤芯，安装新滤芯并检查密封性。"
  }
]
```

---

## 3. 运行方式

所有命令建议在项目根目录 `E:\设备维修辅助系统\app` 下执行。

### 3.1 仅检索（不调用 LLM）

只执行 `question → embedding → Milvus Top-K → 保存检索结果`。

```bash
# 原始 BGE
python bge_lora_finetune\baseline2\evaluation.py --model original --stage retrieve --output-dir bge_lora_finetune\baseline2\output\original

# LoRA BGE
python bge_lora_finetune\baseline2\evaluation.py --model lora --stage retrieve --output-dir bge_lora_finetune\baseline2\output\lora
```

完成后可人工检查 `retrieval_results_original.json`、`retrieval_results_lora.json`。

### 3.2 仅 Judge（读取已有检索结果）

读取已有的 `retrieval_results_{model}.json`，调用 LLM 做语义判断，**不重新检索**。

```bash
python bge_lora_finetune\baseline2\evaluation.py --model original --stage judge --output-dir bge_lora_finetune\baseline2\output\original
python bge_lora_finetune\baseline2\evaluation.py --model lora --stage judge --output-dir bge_lora_finetune\baseline2\output\lora
```

不传 `--retrieval-results` 时，脚本自动寻找 `{output_dir}/retrieval_results_{model}.json`；也可手动指定：

```bash
python bge_lora_finetune\baseline2\evaluation.py --model original --stage judge --retrieval-results bge_lora_finetune\baseline2\output\original\retrieval_results_original.json --output-dir bge_lora_finetune\baseline2\output\original
```

### 3.3 完整运行（检索 + Judge，默认）

依次完成：检索 → 保存 `retrieval_results` → LLM Judge → 保存 `judge_results` → 计算 Hit@K。

```bash
python bge_lora_finetune\baseline2\evaluation.py --model original --output-dir bge_lora_finetune\baseline2\output\original
python bge_lora_finetune\baseline2\evaluation.py --model lora --output-dir bge_lora_finetune\baseline2\output\lora
```

### 3.4 自动对比 Original vs LoRA

自动完成两个模型的「检索 → Judge → Hit@K」，并计算 `improvement`。

```bash
python bge_lora_finetune\baseline2\evaluation.py --compare --output-dir bge_lora_finetune\baseline2\output\compare
```

最终生成 `comparison_metrics.json`，记录 Original / LoRA 的 Hit@K 与 improvement。

---

## 4. 命令行参数

| 参数                  | 默认值                                                           | 说明                                                 |
| --------------------- | ---------------------------------------------------------------- | ---------------------------------------------------- |
| `--dataset`         | `bge_lora_finetune/baseline2/evaluaion_query_groundTruth.json` | 评测数据集                                           |
| `--collection`      | `documents_collection_main_chunk`                              | Milvus collection 名称                               |
| `--top-k`           | `5`                                                            | 检索 Top-K 数量                                      |
| `--adapter-dir`     | `bge_lora_finetune/text_only_output/lora_adapter`              | LoRA adapter 目录（仅`--model lora` 时使用）       |
| `--base-weight`     | 从`.env` 读取，或 `bge/Visualized_m3.pth`                    | BGE 基座权重路径                                     |
| `--from-pretrained` | 从`.env` 读取，或 `bge/bge-m3`                               | 本地 BGE 配置目录（含`config.json`、tokenizer 等） |
| `--llm-base-url`    | 从`.env` 读取，或 `http://192.168.246.200:8000/v1`           | LLM 服务地址                                         |
| `--llm-api-key`     | 从`.env` 读取 `API_KEY`                                      | LLM API Key                                          |
| `--llm-model`       | 从`.env` 读取 `MODEL_AI`                                     | LLM 模型名称                                         |
| `--max-tokens`      | `2000`                                                         | LLM 最大输出 token 数                                |
| `--chunk-max-chars` | `1200`                                                         | 每个 chunk 送入 LLM 前的最大字符数                   |
| `--output-dir`      | 带时间戳的默认目录                                               | 所有输出文件的保存目录                               |

> 其余参数（`--model`、`--stage`、`--compare` 等）见上文运行方式。

---

## 5. 输出文件说明

### 5.1 检索结果 `retrieval_results_{model}.json`

包含 `question`、`ground_truth`、`retrieved_chunks`（每个 chunk 含 `rank`、`doc_id`、`chunk_id`、`title`、`content`、`score`、`metadata`）。

```json
[
  {
    "question": "如何更换液压油滤芯？",
    "ground_truth": "关闭电源，拆卸旧滤芯，安装新滤芯并检查密封性。",
    "retrieved_chunks": [
      {
        "rank": 1,
        "doc_id": "doc_123",
        "chunk_id": "chunk_001",
        "title": "液压系统维护手册",
        "content": "更换滤芯步骤：1. 关闭电源；2. 拆卸旧滤芯...",
        "score": 0.82,
        "metadata": { "source_doc_id": "doc_123", "library_type": "manual" }
      }
    ]
  }
]
```

### 5.2 Judge 结果 `judge_results_{model}.json`

```json
[
  {
    "question": "如何更换液压油滤芯？",
    "ground_truth": "关闭电源，拆卸旧滤芯，安装新滤芯并检查密封性。",
    "retrieved_chunks": [],
    "judge": {
      "hit": true,
      "matched_chunk_indices": [0],
      "reason": "chunk[0]明确描述了更换滤芯的完整步骤，与ground_truth一致。"
    }
  }
]
```

| 字段                      | 含义                                                           |
| ------------------------- | -------------------------------------------------------------- |
| `hit`                   | 该问题是否命中                                                 |
| `matched_chunk_indices` | 被 LLM 判定为能支撑`ground_truth` 的 chunk 编号（从 0 开始） |
| `reason`                | LLM 的判断理由                                                 |

### 5.3 指标 `metrics_{model}_{timestamp}.json`

```json
{
  "model_kind": "original",
  "top_k": 5,
  "total": 100,
  "hit": 78,
  "hit_at_k": 78.0,
  "hit_rates": { "1": 45.0, "3": 68.0, "5": 78.0, "10": 82.0 },
  "ranks": [1, null, 3, 1]
}
```

### 5.4 对比结果 `comparison_metrics.json`

在 `--compare` 模式下生成，记录 Original / LoRA 的 Hit@K 与 improvement：

$$
\text{improvement} = \text{LoRA Hit@K} - \text{Original Hit@K}
$$

单位为**百分点**。例如 Original 78.0%、LoRA 82.0%，则 `improvement = +4.0` 个百分点。

---

## 6. 运行逻辑详解

### 6.1 阶段一：Retrieve

1. **加载模型**
   - Original：`Visualized_BGE` → 加载基座权重
   - LoRA：`Visualized_BGE` → 加载基座权重 → 依据 `adapter_config.json` 注入 LoRA → 加载 `adapter_model.pt`
2. **连接 Milvus**：连接 `documents_collection_main_chunk` 并确保已加载到内存；检索前等待 QueryNode 同步。
3. **编码 question**：`question → BGE / LoRA BGE → query vector`
4. **Milvus 检索**：`query vector → Milvus → Top-K chunks`
5. **保存**：写入 `retrieval_results_{model}.json`

### 6.2 阶段二：LLM Judge

读取 `retrieval_results_{model}.json`，对每个 question 构建 Judge Prompt，核心问题：

> Top-K 召回的 chunk 中，是否存在至少一个能直接支撑 `ground_truth` 的片段？

例：Question「如何更换液压油滤芯？」、Ground Truth「关闭电源，拆卸旧滤芯，安装新滤芯并检查密封性。」，若 chunk 完整列出更换步骤，则判定：

```json
{ "hit": true, "matched_chunk_indices": [0], "reason": "该chunk完整描述了ground_truth中的更换步骤。" }
```

### 6.3 Judge 重试机制

单个样本最多重试 **3** 次；3 次全部失败时按 `hit = false` 处理，保证整个评测流程不因单个 LLM 请求失败而中断。

---

## 7. Hit@K 计算方式

$$
\text{Hit@K} = \frac{\text{至少召回一个有效 chunk 的样本数}}{\text{总样本数}} \times 100\%
$$

例如：总样本 100、命中 78，则 $\text{Hit@5} = 78 / 100 \times 100\% = 78\%$。

### 7.1 Hit@1 / Hit@3 / Hit@5 / Hit@10

- `--top-k` 为 5 时主要统计 **Hit@1 / Hit@3 / Hit@5**；Top-K 足够大时再统计 **Hit@10**。
- 取最早命中的 chunk 排名：`min(matched_chunk_indices) + 1`。

例如 `matched_chunk_indices = [2, 4]`，即 `chunk[2]`（rank 3）、`chunk[4]`（rank 5），最早命中 `rank = 3`，因此：

| 指标  | 结果   |
| ----- | ------ |
| Hit@1 | 不命中 |
| Hit@3 | 命中   |
| Hit@5 | 命中   |

---

## 8. 注意事项

| 事项       | 说明                                                                                                                                           |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| 路径       | 默认路径均相对项目根目录`E:\设备维修辅助系统\app`，建议先进入该目录                                                                          |
| Milvus     | 检索前脚本等待约 5 秒同步 QueryNode；若报`Timestamp lag too large`，可增加等待时间、检查 Milvus 状态 / collection 是否加载 / Docker 容器状态 |
| LLM 服务   | 确保`API_KEY`、`AI_BASE_URL`、`MODEL_AI` 配置正确且服务可访问                                                                            |
| 离线模式   | 脚本默认设置`TRANSFORMERS_OFFLINE=1` 等，需保证本地模型文件完整                                                                              |
| 输出目录   | 建议每次显式指定`--output-dir`，避免不同运行的时间戳目录混淆                                                                                 |
| 数据集格式 | 每条必须包含非空的`question` 与 `ground_truth`                                                                                             |

---

## 9. 推荐运行流程

首次运行时建议按以下顺序：

1. **测试 Original BGE 检索**，检查 `output\original\retrieval_results_original.json`
   ```bash
   python bge_lora_finetune\baseline2\evaluation.py --model original --stage retrieve --output-dir output\original
   ```
2. **测试 LoRA BGE 检索**，检查 `output\lora\retrieval_results_lora.json`
   ```bash
   python bge_lora_finetune\baseline2\evaluation.py --model lora --stage retrieve --output-dir output\lora
   ```
3. **运行 Original Judge**，检查 `output\original\judge_results_original.json`
   ```bash
   python bge_lora_finetune\baseline2\evaluation.py --model original --stage judge --output-dir output\original
   ```
4. **运行 LoRA Judge**，检查 `output\lora\judge_results_lora.json`
   ```bash
   python bge_lora_finetune\baseline2\evaluation.py --model lora --stage judge --output-dir output\lora
   ```
5. **查看指标**：`output\original\metrics_original_*.json`、`output\lora\metrics_lora_*.json`，关注 Hit@1 / Hit@3 / Hit@5 / Hit@10
6. **自动对比**：`python bge_lora_finetune\baseline2\evaluation.py --compare --output-dir output\compare`，查看 `comparison_metrics.json`

---

## 10. 快速开始

只需以下三个命令即可完成评测与对比：

```bash
# Original BGE
python bge_lora_finetune\baseline2\evaluation.py --model original --output-dir output\original

# LoRA BGE
python bge_lora_finetune\baseline2\evaluation.py --model lora --output-dir output\lora

# 自动对比
python bge_lora_finetune\baseline2\evaluation.py --compare --output-dir output\compare
```

最终查看 `output\compare\comparison_metrics.json`，即可得到 Original vs LoRA 的 Hit@K 与 improvement。
