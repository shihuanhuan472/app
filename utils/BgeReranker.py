"""Optional local BGE cross-encoder reranker."""
import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List


class BgeReranker:
    _instance = None

    def __init__(self, model_path: str):
        from FlagEmbedding import FlagReranker
        try:
            import torch
            cuda_available = torch.cuda.is_available()
        except Exception:
            cuda_available = False
        device = os.getenv("RERANK_DEVICE", "cuda:0" if cuda_available else "cpu")
        use_fp16 = os.getenv("RERANK_USE_FP16", "true").lower() in {"1", "true", "yes", "on"}
        if device.startswith("cpu"):
            use_fp16 = False
        self.model = FlagReranker(model_path, devices=[device], use_fp16=use_fp16)
        self.batch_size = max(1, int(os.getenv("RERANK_BATCH_SIZE", "8")))
        self.max_length = max(128, int(os.getenv("RERANK_MAX_LENGTH", "512")))

    @classmethod
    def get(cls):
        model_path = os.getenv("RERANK_MODEL_PATH", str(Path(__file__).resolve().parents[1] / "model_weights" / "bge-reranker-v2-m3"))
        if not Path(model_path).exists():
            return None
        if cls._instance is None:
            try:
                cls._instance = cls(model_path)
            except Exception as exc:
                print(f"[Reranker] disabled: {exc}")
                cls._instance = False
        return cls._instance or None

    async def score(self, query: str, documents: List[Dict[str, Any]]) -> List[float]:
        pairs = [[query, f"{item.get('title', '')}\n{item.get('content', '')}".strip()] for item in documents]
        def run():
            values = self.model.compute_score(pairs, normalize=True, batch_size=self.batch_size, max_length=self.max_length)
            return [float(values)] if isinstance(values, (float, int)) else [float(v) for v in values]
        return await asyncio.to_thread(run)
