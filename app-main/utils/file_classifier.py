import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


ALLOWED_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".pptx",
    ".ppt",
    ".html",
    ".mhtml",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".csv",
    ".xlsx",
    ".xls",
    ".xlsm",
}


EXTENSION_CATEGORY = {
    ".pdf": "pdf",
    ".docx": "word",
    ".ppt": "presentation",
    ".pptx": "presentation",
    ".html": "html",
    ".mhtml": "html",
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".bmp": "image",
    ".csv": "spreadsheet",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".xlsm": "spreadsheet",
}

ZIP_BASED_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xlsm"}
OLE_BASED_EXTENSIONS = {".ppt", ".xls"}
TEXT_LIKE_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".html", ".mhtml"}


def get_file_extension(filename: Optional[str]) -> str:
    return os.path.splitext(filename or "")[-1].lower()


def normalize_uploaded_relative_filename(filename: Optional[str]) -> str:
    basename = Path(str(filename or "").replace("\\", "/")).name.strip()
    safe_name = "".join(char for char in basename if char not in '<>:"/\\|?*').strip()
    if not safe_name or safe_name in {".", ".."}:
        return f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return safe_name


def get_document_category(file_ext: str) -> str:
    return EXTENSION_CATEGORY.get(file_ext.lower(), "other")


def is_allowed_document_extension(file_ext: str) -> bool:
    return file_ext.lower() in ALLOWED_DOCUMENT_EXTENSIONS


def is_upload_content_valid(file_ext: str, contents: bytes) -> bool:
    if not contents:
        return False

    file_ext = file_ext.lower()
    if file_ext in TEXT_LIKE_EXTENSIONS:
        return True
    if file_ext == ".pdf":
        return contents.startswith(b"%PDF")
    if file_ext in ZIP_BASED_EXTENSIONS:
        return contents.startswith(b"PK\x03\x04") or contents.startswith(b"PK\x05\x06") or contents.startswith(b"PK\x07\x08")
    if file_ext in OLE_BASED_EXTENSIONS:
        return contents.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if file_ext in {".jpg", ".jpeg"}:
        return contents.startswith(b"\xff\xd8\xff")
    if file_ext == ".png":
        return contents.startswith(b"\x89PNG\r\n\x1a\n")
    if file_ext == ".webp":
        return len(contents) >= 12 and contents[:4] == b"RIFF" and contents[8:12] == b"WEBP"
    if file_ext == ".bmp":
        return contents.startswith(b"BM")

    return False


def build_document_storage_path(
    document_base_dir: str,
    document_relative_dir: str,
    original_filename: str,
) -> Tuple[str, str, str]:
    file_ext = get_file_extension(original_filename)
    category = get_document_category(file_ext)
    safe_relative_filename = normalize_uploaded_relative_filename(
        original_filename or f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}{file_ext}"
    )
    original_path = Path(safe_relative_filename)
    safe_stem = original_path.stem.strip()
    safe_ext = file_ext or original_path.suffix
    if not safe_stem:
        safe_stem = f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    relative_dir = Path(document_relative_dir) / category
    absolute_dir = Path(document_base_dir) / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{safe_stem}{safe_ext}"
    absolute_path = absolute_dir / stored_filename
    suffix = 1
    while absolute_path.exists():
        stored_filename = f"{safe_stem}({suffix}){safe_ext}"
        absolute_path = absolute_dir / stored_filename
        suffix += 1

    relative_path = (relative_dir / stored_filename).as_posix()
    return str(absolute_path), relative_path, category
