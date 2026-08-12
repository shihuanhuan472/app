"""Text-only LoRA fine-tuning for the project's Visualized_BGE text encoder.

Dataset format:
    JSONL: {"text": "..."}
    JSON : [{"text": "..."}, ...]

The training objective is unsupervised SimCSE-style contrastive learning:
the same text is encoded twice with dropout as the positive pair, while other
texts in the batch are used as negatives. The dataset itself only needs the
single ``text`` field.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import torch
import torch.nn.functional as F
from dotenv import load_dotenv
from torch.utils.data import DataLoader, Dataset


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from visual_bge.visual_bge.modeling import Visualized_BGE  # noqa: E402
from bge_lora_finetune.finetune_bge_lora import (  # noqa: E402
    count_parameters,
    freeze_all_parameters,
    inject_lora_layers,
    load_lora_adapter,
    merge_lora_layers,
    parse_target_modules,
    resolve_default_model_weight,
    resolve_path,
    save_lora_adapter,
    trainable_parameters,
)


@dataclass
class TextOnlyConfig:
    train_data: str
    output_dir: str = ""
    model_name: str = "BAAI/bge-m3"
    model_weight: str = ""
    from_pretrained: str = ""
    max_length: int = 512
    epochs: int = 1
    batch_size: int = 8
    gradient_accumulation_steps: int = 1
    learning_rate: float = 2e-4
    weight_decay: float = 0.0
    warmup_ratio: float = 0.05
    temperature: float = 0.05
    lora_rank: int = 8
    lora_alpha: float = 16.0
    lora_dropout: float = 0.1
    target_modules: str = "query,key,value"
    seed: int = 42
    logging_steps: int = 10
    save_merged_pth: bool = True
    merged_name: str = "Visualized_m3_text_only_lora_merged.pth"
    resume_adapter_dir: str = ""
    fp16: bool = True
    dry_run: bool = False


class TextOnlyDataset(Dataset):
    def __init__(self, path: Path) -> None:
        texts = read_text_only_items(path)
        seen: set[str] = set()
        self.texts: list[str] = []
        for text in texts:
            normalized = " ".join(str(text).split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            self.texts.append(normalized)

        if len(self.texts) < 2:
            raise ValueError("Text-only contrastive training needs at least 2 unique text rows.")

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, index: int) -> str:
        return self.texts[index]


def read_text_only_items(path: Path) -> list[str]:
    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        return []

    if raw_text.startswith("["):
        data = json.loads(raw_text)
        if not isinstance(data, list):
            raise ValueError("JSON dataset must be a list of objects with only the text field.")
        return [extract_text(item) for item in data]

    texts: list[str] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
        texts.append(extract_text(item))
    return texts


def extract_text(item: object) -> str:
    if not isinstance(item, dict):
        raise ValueError("Each dataset row must be an object like {'text': '...'}")
    keys = set(item.keys())
    if keys != {"text"}:
        raise ValueError(f"Each dataset row must contain only the text field, got: {sorted(keys)}")
    return str(item.get("text", "")).strip()


def collate_texts(batch: list[str]) -> list[str]:
    return batch


def tokenize(tokenizer, texts: list[str], max_length: int, device: torch.device) -> dict[str, torch.Tensor]:
    batch = tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    return {key: value.to(device) for key, value in batch.items()}


def default_output_for_data(data_path: Path) -> Path:
    return data_path.parent / "text_only_output"


def set_hf_cache_from_env() -> None:
    model_cache = os.getenv("MODEL_DOWNLOAD_URL")
    if model_cache and "HF_HOME" not in os.environ:
        os.environ["HF_HOME"] = model_cache


def local_model_dir(path_like: str) -> str | None:
    if not path_like:
        return None
    model_dir = Path(path_like).expanduser()
    if model_dir.is_dir() and (model_dir / "config.json").is_file():
        return str(model_dir)
    return None


def resolve_hf_snapshot_path(model_name: str) -> str | None:
    for configured_path in (
        os.getenv("MODEL_LOCAL_PATH", ""),
        os.getenv("BGE_MODEL_LOCAL_PATH", ""),
        model_name,
    ):
        local_dir = local_model_dir(configured_path)
        if local_dir:
            return local_dir

    if not model_name or "/" not in model_name:
        return None

    cache_root = Path(os.environ.get("HF_HOME", "")).expanduser()
    if not str(cache_root):
        return None

    model_cache_name = f"models--{model_name.replace('/', '--')}"
    for cache_base in (cache_root / "hub", cache_root):
        model_cache_dir = cache_base / model_cache_name
        snapshots_dir = model_cache_dir / "snapshots"
        if not snapshots_dir.is_dir():
            continue

        ref_file = model_cache_dir / "refs" / "main"
        if ref_file.is_file():
            revision = ref_file.read_text(encoding="utf-8").strip()
            snapshot_dir = snapshots_dir / revision
            if (snapshot_dir / "config.json").is_file():
                return str(snapshot_dir)

        snapshots = [
            snapshot
            for snapshot in snapshots_dir.iterdir()
            if snapshot.is_dir() and (snapshot / "config.json").is_file()
        ]
        if snapshots:
            snapshots.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            return str(snapshots[0])

    return None


def save_training_config(output_dir: Path, args: TextOnlyConfig, lora_layers: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = vars(args).copy()
    config["lora_layers"] = lora_layers
    (output_dir / "training_config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def simcse_loss(view_a: torch.Tensor, view_b: torch.Tensor, temperature: float) -> torch.Tensor:
    scores = torch.matmul(view_a, view_b.transpose(0, 1)) / temperature
    labels = torch.arange(scores.size(0), device=scores.device)
    return (F.cross_entropy(scores, labels) + F.cross_entropy(scores.transpose(0, 1), labels)) / 2


def run_training(args: TextOnlyConfig) -> int:
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    train_path = resolve_path(args.train_data)
    output_dir = resolve_path(args.output_dir) if args.output_dir else default_output_for_data(train_path)
    args.train_data = str(train_path)
    args.output_dir = str(output_dir)

    dataset = TextOnlyDataset(train_path)
    print(f"Loaded {len(dataset)} text rows from {train_path}")
    print("Sample text:", dataset[0][:300].replace("\n", " "))
    if args.dry_run:
        return 0

    set_hf_cache_from_env()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    if not args.model_weight:
        args.model_weight = resolve_default_model_weight()
    model_weight = str(resolve_path(args.model_weight))
    args.model_weight = model_weight
    if not Path(model_weight).exists():
        raise FileNotFoundError(f"MODEL_WEIGHT not found: {model_weight}")

    from_pretrained = args.from_pretrained.strip() or resolve_hf_snapshot_path(args.model_name)
    if from_pretrained:
        args.from_pretrained = str(resolve_path(from_pretrained))
        print(f"Using local BGE snapshot: {args.from_pretrained}")
    model = Visualized_BGE(
        model_name_bge=args.model_name,
        model_weight=model_weight,
        from_pretrained=args.from_pretrained or None,
        temperature=args.temperature,
    )
    model.to(device)

    freeze_all_parameters(model)
    target_modules = parse_target_modules(args.target_modules)
    lora_layers = inject_lora_layers(
        model.bge_encoder,
        target_modules=target_modules,
        rank=args.lora_rank,
        alpha=args.lora_alpha,
        dropout=args.lora_dropout,
    )
    if not lora_layers:
        raise RuntimeError(
            f"No Linear layers matched --target-modules={args.target_modules}. "
            "Try --target-modules query,key,value,dense"
        )
    model.to(device)

    if args.resume_adapter_dir:
        resume_adapter_dir = resolve_path(args.resume_adapter_dir)
        args.resume_adapter_dir = str(resume_adapter_dir)
        load_lora_adapter(model, resume_adapter_dir)

    total, trainable = count_parameters(model)
    injected_names = ", ".join(sorted(set(name.rsplit(".", 1)[-1] for name in lora_layers)))
    print(f"Injected LoRA into {len(lora_layers)} layers: {injected_names}")
    print(f"Parameters: trainable={trainable:,} / total={total:,} ({trainable / total:.4%})")

    save_training_config(output_dir, args, lora_layers)
    effective_batch_size = min(max(2, args.batch_size), len(dataset))
    loader = DataLoader(
        dataset,
        batch_size=effective_batch_size,
        shuffle=True,
        collate_fn=collate_texts,
        drop_last=len(dataset) % effective_batch_size == 1,
    )
    print(f"Effective batch size: {effective_batch_size}")

    optimizer = torch.optim.AdamW(trainable_parameters(model), lr=args.learning_rate, weight_decay=args.weight_decay)
    total_update_steps = max(1, math.ceil(len(loader) * args.epochs / args.gradient_accumulation_steps))
    warmup_steps = int(total_update_steps * args.warmup_ratio)

    def lr_lambda(step: int) -> float:
        if warmup_steps > 0 and step < warmup_steps:
            return float(step + 1) / float(max(1, warmup_steps))
        progress = float(step - warmup_steps) / float(max(1, total_update_steps - warmup_steps))
        return max(0.0, 1.0 - progress)

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    scaler = torch.cuda.amp.GradScaler(enabled=args.fp16 and device.type == "cuda")
    loss_log_path = output_dir / "train_loss.jsonl"
    loss_log_path.parent.mkdir(parents=True, exist_ok=True)

    global_step = 0
    optimizer.zero_grad(set_to_none=True)
    model.train()

    with loss_log_path.open("a", encoding="utf-8") as loss_writer:
        for epoch in range(1, args.epochs + 1):
            running_loss = 0.0
            seen_batches = 0
            for batch_index, texts in enumerate(loader, start=1):
                tokens = tokenize(model.tokenizer, texts, args.max_length, device)

                with torch.cuda.amp.autocast(enabled=args.fp16 and device.type == "cuda"):
                    reps_a = model.encode_text(tokens)
                    reps_b = model.encode_text(tokens)
                    loss = simcse_loss(reps_a, reps_b, args.temperature)
                    loss_for_backward = loss / args.gradient_accumulation_steps

                scaler.scale(loss_for_backward).backward()
                running_loss += loss.item()
                seen_batches += 1

                should_step = batch_index % args.gradient_accumulation_steps == 0 or batch_index == len(loader)
                if should_step:
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(trainable_parameters(model), max_norm=1.0)
                    scaler.step(optimizer)
                    scaler.update()
                    optimizer.zero_grad(set_to_none=True)
                    scheduler.step()
                    global_step += 1

                    record = {
                        "epoch": epoch,
                        "step": global_step,
                        "loss": loss.item(),
                        "lr": scheduler.get_last_lr()[0],
                    }
                    loss_writer.write(json.dumps(record, ensure_ascii=False) + "\n")
                    loss_writer.flush()

                    if global_step % args.logging_steps == 0:
                        avg_loss = running_loss / max(1, seen_batches)
                        print(
                            f"epoch={epoch} step={global_step}/{total_update_steps} "
                            f"loss={avg_loss:.6f} lr={scheduler.get_last_lr()[0]:.2e}"
                        )
                        running_loss = 0.0
                        seen_batches = 0

            save_lora_adapter(
                model,
                output_dir,
                config={
                    "model_name": args.model_name,
                    "base_model_weight": model_weight,
                    "rank": args.lora_rank,
                    "alpha": args.lora_alpha,
                    "dropout": args.lora_dropout,
                    "target_modules": sorted(target_modules),
                    "objective": "unsupervised_simcse_text_only",
                },
            )
            print(f"Saved LoRA adapter after epoch {epoch}: {output_dir / 'lora_adapter'}")

    if args.save_merged_pth:
        merged_count = merge_lora_layers(model)
        merged_path = output_dir / args.merged_name
        torch.save(model.state_dict(), merged_path)
        print(f"Merged {merged_count} LoRA layers into full model weight: {merged_path}")

    print("Done.")
    return 0


def parse_args() -> TextOnlyConfig:
    parser = argparse.ArgumentParser(description="Fine-tune Visualized_BGE text encoder with text-only data.")
    parser.add_argument("--train-data", required=True, help="Path to JSONL/JSON dataset. Each row must only contain text.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--model-name", default="BAAI/bge-m3")
    parser.add_argument("--model-weight", default="")
    parser.add_argument("--from-pretrained", default="", help="Optional local Hugging Face snapshot path.")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=0.0)
    parser.add_argument("--warmup-ratio", type=float, default=0.05)
    parser.add_argument("--temperature", type=float, default=0.05)
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--lora-alpha", type=float, default=16.0)
    parser.add_argument("--lora-dropout", type=float, default=0.1)
    parser.add_argument("--target-modules", default="query,key,value")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--merged-name", default="Visualized_m3_text_only_lora_merged.pth")
    parser.add_argument("--resume-adapter-dir", default="")
    parser.add_argument("--no-merged-pth", action="store_true")
    parser.add_argument("--no-fp16", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parser.parse_args()
    return TextOnlyConfig(
        train_data=namespace.train_data,
        output_dir=namespace.output_dir,
        model_name=namespace.model_name,
        model_weight=namespace.model_weight,
        from_pretrained=namespace.from_pretrained,
        max_length=namespace.max_length,
        epochs=namespace.epochs,
        batch_size=namespace.batch_size,
        gradient_accumulation_steps=namespace.gradient_accumulation_steps,
        learning_rate=namespace.learning_rate,
        weight_decay=namespace.weight_decay,
        warmup_ratio=namespace.warmup_ratio,
        temperature=namespace.temperature,
        lora_rank=namespace.lora_rank,
        lora_alpha=namespace.lora_alpha,
        lora_dropout=namespace.lora_dropout,
        target_modules=namespace.target_modules,
        seed=namespace.seed,
        logging_steps=namespace.logging_steps,
        save_merged_pth=not namespace.no_merged_pth,
        merged_name=namespace.merged_name,
        resume_adapter_dir=namespace.resume_adapter_dir,
        fp16=not namespace.no_fp16,
        dry_run=namespace.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(run_training(parse_args()))
