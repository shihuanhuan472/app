"""Centralized token budgets for model workloads."""
import os


def _positive(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


MODEL_CONTEXT_WINDOW = _positive("MODEL_CONTEXT_WINDOW", 131072)
CHAT_MAX_OUTPUT_TOKENS = _positive("CHAT_MAX_OUTPUT_TOKENS", 8192)
CHAT_CONTEXT_MARGIN_TOKENS = _positive("CHAT_CONTEXT_MARGIN_TOKENS", 2048)
CHAT_MAX_INPUT_TOKENS = _positive("CHAT_MAX_INPUT_TOKENS", MODEL_CONTEXT_WINDOW - CHAT_MAX_OUTPUT_TOKENS - CHAT_CONTEXT_MARGIN_TOKENS)
DOCUMENT_MAX_INPUT_TOKENS = _positive("DOCUMENT_MAX_INPUT_TOKENS", 64512)
DOCUMENT_MAX_OUTPUT_TOKENS = _positive("DOCUMENT_MAX_OUTPUT_TOKENS", 64512)
DOCUMENT_CONTEXT_MARGIN_TOKENS = _positive("DOCUMENT_CONTEXT_MARGIN_TOKENS", 2048)
IMAGE_MAX_INPUT_TOKENS = _positive("IMAGE_MAX_INPUT_TOKENS", 24576)
IMAGE_MAX_OUTPUT_TOKENS = _positive("IMAGE_MAX_OUTPUT_TOKENS", 4096)
IMAGE_CONTEXT_MARGIN_TOKENS = _positive("IMAGE_CONTEXT_MARGIN_TOKENS", 1024)
IMAGE_INPUT_TOKENS = _positive("IMAGE_INPUT_TOKENS", 1500)


def validate_token_budgets() -> None:
    for name, input_tokens, output_tokens, margin in (
        ("chat", CHAT_MAX_INPUT_TOKENS, CHAT_MAX_OUTPUT_TOKENS, CHAT_CONTEXT_MARGIN_TOKENS),
        ("document", DOCUMENT_MAX_INPUT_TOKENS, DOCUMENT_MAX_OUTPUT_TOKENS, DOCUMENT_CONTEXT_MARGIN_TOKENS),
        ("image", IMAGE_MAX_INPUT_TOKENS, IMAGE_MAX_OUTPUT_TOKENS, IMAGE_CONTEXT_MARGIN_TOKENS),
    ):
        if input_tokens + output_tokens + margin > MODEL_CONTEXT_WINDOW:
            raise RuntimeError(f"{name} token budget exceeds MODEL_CONTEXT_WINDOW")


validate_token_budgets()
