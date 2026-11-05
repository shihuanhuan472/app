"""Lossless paragraph-first vector splitting, without model inference."""
import re


def split_text(text, count_tokens, max_tokens=1000):
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    chunks, current = [], ""
    # Preserve separators so concatenation reconstructs the exact source text.
    for paragraph in re.findall(r"[^\n]*\n|[^\n]+$", text or ""):
        if current and count_tokens(current + paragraph) > max_tokens:
            chunks.append(current)
            current = ""
        while count_tokens(paragraph) > max_tokens:
            low, high = 0, len(paragraph)
            while low < high:
                middle = (low + high + 1) // 2
                if count_tokens(paragraph[:middle]) <= max_tokens:
                    low = middle
                else:
                    high = middle - 1
            if not low:
                raise ValueError("One character exceeds the token budget")
            # Prefer a sentence boundary when it does not produce tiny fragments.
            boundary = max(paragraph.rfind(mark, low // 2, low) for mark in "。！？；.!?;")
            cut = boundary + 1 if boundary >= 0 else low
            chunks.append(paragraph[:cut])
            paragraph = paragraph[cut:]
        current = paragraph if not current else current + paragraph
    if current:
        chunks.append(current)
    return chunks
