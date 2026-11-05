"""Bounded text batches for Visualized_BGE; preserve source row ordering."""
import torch


def encode_text_batches(model, texts, batch_size=8, padded_char_budget=8192):
    if not texts:
        return []
    batch_size = max(1, batch_size)
    padded_char_budget = max(1, padded_char_budget)
    output = [None] * len(texts)

    def encode(indices):
        try:
            vectors = model.encode(text=[texts[i] for i in indices], image=None)
            rows = vectors.cpu().numpy().tolist()
        except torch.cuda.OutOfMemoryError:
            if len(indices) == 1:
                raise
            rows = None
        if rows is None:
            # Retry outside the exception handler so failed tensors can be freed.
            torch.cuda.empty_cache()
            middle = len(indices) // 2
            encode(indices[:middle])
            encode(indices[middle:])
            return
        if len(rows) != len(indices):
            raise ValueError("Embedding batch returned an unexpected number of rows")
        for index, vector in zip(indices, rows):
            output[index] = vector

    pending = []
    # Nearby lengths avoid padding every short path to the longest path.
    for index in sorted(range(len(texts)), key=lambda i: len(texts[i])):
        if pending and (len(pending) >= batch_size or
                        len(texts[index]) * (len(pending) + 1) > padded_char_budget):
            encode(pending)
            pending = []
        pending.append(index)
    if pending:
        encode(pending)
    return output
