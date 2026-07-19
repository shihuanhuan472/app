TITLE_MAX_LENGTH = 100
DEFAULT_DOCUMENT_TITLE = "未命名文档"


def normalize_document_title(value, fallback=DEFAULT_DOCUMENT_TITLE, max_length=TITLE_MAX_LENGTH) -> str:
    title = str(value or "").strip()
    if not title:
        title = str(fallback or "").strip() or DEFAULT_DOCUMENT_TITLE
    title = " ".join(title.split())
    return title[:max_length]
