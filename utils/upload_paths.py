from pathlib import PurePosixPath


LEGACY_DOCUMENT_UPLOAD_PREFIX = "upload/documents/"
SOURCE_DOCUMENT_UPLOAD_PREFIX = "upload/source_documents/"


def normalize_upload_path(path: str | None) -> str | None:
    if not path:
        return path
    normalized = str(PurePosixPath(str(path).replace("\\", "/").lstrip("/")))
    if normalized.startswith(LEGACY_DOCUMENT_UPLOAD_PREFIX):
        return f"{SOURCE_DOCUMENT_UPLOAD_PREFIX}{normalized[len(LEGACY_DOCUMENT_UPLOAD_PREFIX):]}"
    return normalized
