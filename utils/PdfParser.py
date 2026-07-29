import base64
import json
import hashlib
import io
import mimetypes
import re
import uuid
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from html import unescape
from html.parser import HTMLParser as StdHTMLParser
from urllib.parse import unquote
"""
PDF 解析器：普通 PDF 使用 PyMuPDF，扫描 PDF 自动使用 MinerU 提取文本。
需要运行：python -m pip install uv
python -m uv pip install -U "mineru[all]"来安装相关库，还有一个库的版本要求：python -m pip install setuptools==80.9.0
"""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image
try:
    from utils.token_counter import get_token_count
except ModuleNotFoundError:
    from token_counter import get_token_count

from models import Document
from utils.ai_endpoint import get_ai_base_url
from utils.title_utils import normalize_document_title
import pymupdf
from openai import OpenAI

from utils.error_codes import BizCode
"""PDF 解析器：普通 PDF 使用 PyMuPDF，扫描 PDF 自动使用 MinerU 提取文本。"""


class _TableTextParser(StdHTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._current_row = None
        self._current_cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data):
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            cell = " ".join("".join(self._current_cell).split())
            self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class PdfParser:
    def __init__(self):
        # self.db = db
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/source_documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.model_image_max_size = int(os.getenv("MODEL_IMAGE_MAX_SIZE", 1024))

        # =========================
        # MinerU 配置
        # =========================
        # 是否启用 MinerU 自动解析扫描 PDF。
        self.mineru_enabled = _env_bool("MINERU_ENABLED", True)

        # 可以在 .env 中手动指定：
        # MINERU_EXE=C:/xxx/app-main/.venv/Scripts/mineru.exe
        # 不指定时，会自动寻找当前虚拟环境中的 mineru.exe 或 PATH 中的 mineru。
        self.mineru_exe = os.getenv("MINERU_EXE", "").strip()

        # MinerU 3.x 默认 backend 是 hybrid-engine，通常需要 CUDA。
        # 当前系统用于扫描 PDF 的 OCR，默认使用 pipeline + ocr，CPU 环境也能运行。
        self.mineru_backend = os.getenv("MINERU_BACKEND", "pipeline").strip() or "pipeline"
        self.mineru_method = os.getenv("MINERU_METHOD", "ocr").strip() or "ocr"
        self.mineru_lang = os.getenv("MINERU_LANG", "ch").strip() or "ch"
        self.mineru_formula_enable = _env_bool("MINERU_FORMULA_ENABLE", False)
        self.mineru_table_enable = _env_bool("MINERU_TABLE_ENABLE", True)
        self.mineru_temp_dir = os.getenv(
            "MINERU_TEMP_DIR",
            os.path.join(self.document_base_dir, "runtime", "mineru_tmp"),
        ).strip()
        self.mineru_keep_output = _env_bool("MINERU_KEEP_OUTPUT", False)
        self.mineru_output_dir = os.getenv(
            "MINERU_OUTPUT_DIR",
            os.path.join(self.document_base_dir, "runtime", "mineru_output"),
        ).strip()
        self.mineru_llm_max_token = int(os.getenv("MINERU_LLM_MAX_TOKEN", 8000))

        # 扫描件判断参数：
        # 单页有效文字少于该值，认为这一页没有有效文本层。
        self.scan_page_min_chars = int(os.getenv("PDF_SCAN_PAGE_MIN_CHARS", 30))

        # 有效文本页比例低于该值，认为整个 PDF 主要是扫描件。
        self.scan_text_page_ratio = float(os.getenv("PDF_SCAN_TEXT_PAGE_RATIO", 0.5))

        # MinerU 最大执行时间，默认 30 分钟。
        self.mineru_timeout = int(os.getenv("MINERU_TIMEOUT", 1800))
        self.mineru_page_image_dpi = int(os.getenv("MINERU_PAGE_IMAGE_DPI", 144))
        self.mineru_page_image_max_pages = int(os.getenv("MINERU_PAGE_IMAGE_MAX_PAGES", 30))
        self.pdf_skip_decorative_images = _env_bool("PDF_SKIP_DECORATIVE_IMAGES", True)
        self.pdf_min_image_area_ratio = _env_float("PDF_MIN_IMAGE_AREA_RATIO", 0.01)
        self.pdf_min_image_width = _env_int("PDF_MIN_IMAGE_WIDTH", 120)
        self.pdf_min_image_height = _env_int("PDF_MIN_IMAGE_HEIGHT", 80)
        self.pdf_min_foreground_area_ratio = _env_float("PDF_MIN_FOREGROUND_AREA_RATIO", 0.08)
        self.pdf_background_color_tolerance = _env_int("PDF_BACKGROUND_COLOR_TOLERANCE", 18)
        self.pdf_background_image_area_ratio = _env_float("PDF_BACKGROUND_IMAGE_AREA_RATIO", 0.65)
        self.pdf_repeated_image_page_threshold = _env_int("PDF_REPEATED_IMAGE_PAGE_THRESHOLD", 2)
        self.pdf_decorative_text_min_chars = _env_int("PDF_DECORATIVE_TEXT_MIN_CHARS", 30)

        self.last_error_code = None
        self.last_error_detail = None
        base_url = os.path.join(self.document_base_dir, self.document_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")

    def parse(self, pdf_url: str):
        self.last_error_code = None
        self.last_error_detail = None
        text, image_urls, image_names, section_image_indexes = self.get_pdf_layout_content(pdf_url)
        document = self.file2document(text, image_urls, image_names, section_image_indexes)

        return document

    def parse_with_mineru(self, pdf_url: str, include_page_images: bool = False):
        self.last_error_code = None
        self.last_error_detail = None
        if not self.mineru_enabled:
            message = "MINERU_ENABLED=0, MinerU parsing is disabled."
            self._set_last_error(BizCode.DOC_PARSE_FAILED, message)
            raise RuntimeError(message)

        try:
            mineru_text, image_urls, image_names, section_image_indexes = self.parse_pdf_by_mineru_with_assets(
                pdf_url,
                include_page_images=include_page_images,
            )
            return self.file2document(
                mineru_text,
                image_urls,
                image_names,
                section_image_indexes,
            )
        except Exception as e:
            if self.last_error_code is None:
                self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))
            raise

    def _set_last_error(self, code: int, message: str):
        self.last_error_code = int(code)
        self.last_error_detail = message

    def _is_ai_service_unavailable_error(self, error: Exception) -> bool:
        name = type(error).__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            return True
        msg = str(error).lower()
        keywords = [
            "connection",
            "timed out",
            "timeout",
            "refused",
            "temporarily unavailable",
            "service unavailable",
            "name resolution",
            "max retries exceeded",
            "502",
            "503",
            "504",
        ]
        return any(k in msg for k in keywords)

    def get_pdf_text(self, pdf_url: str):
        """使用 PyMuPDF 提取 PDF 自带文本层。"""
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        doc = pymupdf.open(pdf_url)
        try:
            text_parts = []
            for page in doc:
                page_text = page.get_text("text", sort=True).strip()
                if page_text:
                    text_parts.append(page_text)
            return "\n\n".join(text_parts)
        finally:
            doc.close()

    def is_scanned_pdf(self, pdf_url: str) -> bool:
        """
        判断 PDF 是否主要为扫描件。

        判断方式：
        1. 逐页提取文本层；
        2. 单页有效字符数 >= scan_page_min_chars，认为该页存在有效文本；
        3. 有效文本页比例 < scan_text_page_ratio，则认为是扫描 PDF。

        注意：
        这只是工程上的启发式判断，可以通过 .env 调整阈值。
        """
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        doc = pymupdf.open(pdf_url)
        try:
            total_pages = len(doc)
            if total_pages == 0:
                return False

            text_pages = 0
            total_chars = 0

            for page in doc:
                page_text = page.get_text("text", sort=True).strip()
                clean_text = re.sub(r"\s+", "", page_text)
                char_count = len(clean_text)
                total_chars += char_count

                if char_count >= self.scan_page_min_chars:
                    text_pages += 1

            text_page_ratio = text_pages / total_pages

            print(
                f"[PDF类型检测] 总页数={total_pages}, "
                f"有效文本页={text_pages}, "
                f"有效文本页比例={text_page_ratio:.2%}, "
                f"总文本字符数={total_chars}"
            )

            is_scanned = text_page_ratio < self.scan_text_page_ratio

            if is_scanned:
                print("[PDF类型检测] 判定为扫描型 PDF，将优先使用 MinerU。")
            else:
                print("[PDF类型检测] 判定为普通文本型 PDF，使用 PyMuPDF。")

            return is_scanned
        finally:
            doc.close()

    def _image_file_hash(self, image_path: Path) -> str:
        try:
            with open(image_path, "rb") as f:
                return hashlib.sha1(f.read()).hexdigest()
        except Exception:
            return ""

    def _image_file_size(self, image_path: Path):
        try:
            with Image.open(image_path) as image:
                return image.width, image.height
        except Exception:
            return 0, 0

    def _image_foreground_area_ratio(self, image: Image.Image) -> float:
        try:
            image = image.convert("RGB")
            image.thumbnail((256, 256))
            width, height = image.size
            if width <= 0 or height <= 0:
                return 0.0

            corners = [
                image.getpixel((0, 0)),
                image.getpixel((width - 1, 0)),
                image.getpixel((0, height - 1)),
                image.getpixel((width - 1, height - 1)),
            ]

            def bucket(color):
                return tuple(channel // 16 for channel in color)

            buckets = [bucket(color) for color in corners]
            background_bucket = max(set(buckets), key=buckets.count)
            background_candidates = [
                color for color in corners if bucket(color) == background_bucket
            ]
            background = tuple(
                sum(color[index] for color in background_candidates) // len(background_candidates)
                for index in range(3)
            )
            tolerance = max(self.pdf_background_color_tolerance, 0)

            min_x, min_y = width, height
            max_x, max_y = -1, -1
            pixels = image.load()
            for y in range(height):
                for x in range(width):
                    pixel = pixels[x, y]
                    if max(abs(pixel[index] - background[index]) for index in range(3)) <= tolerance:
                        continue
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)

            if max_x < 0 or max_y < 0:
                return 0.0

            foreground_area = (max_x - min_x + 1) * (max_y - min_y + 1)
            return foreground_area / max(width * height, 1)
        except Exception:
            return 1.0

    def _image_file_foreground_area_ratio(self, image_path: Path) -> float:
        try:
            with Image.open(image_path) as image:
                return self._image_foreground_area_ratio(image)
        except Exception:
            return 1.0

    def _pdf_xref_foreground_area_ratio(self, doc, xref: int) -> float:
        try:
            image_info = doc.extract_image(xref)
            image_bytes = image_info.get("image")
            if not image_bytes:
                return 1.0
            with Image.open(io.BytesIO(image_bytes)) as image:
                return self._image_foreground_area_ratio(image)
        except Exception:
            return 1.0

    def _should_keep_image_file(self, image_path: Path, image_hash_counts: dict = None) -> bool:
        if not self.pdf_skip_decorative_images:
            return True

        width, height = self._image_file_size(image_path)
        if width <= 0 or height <= 0:
            return False
        if width < self.pdf_min_image_width or height < self.pdf_min_image_height:
            return False

        aspect_ratio = width / max(height, 1)
        if aspect_ratio > 8 or aspect_ratio < 0.125:
            return False

        if (
            self.pdf_min_foreground_area_ratio > 0
            and self._image_file_foreground_area_ratio(image_path) < self.pdf_min_foreground_area_ratio
        ):
            return False

        image_hash = self._image_file_hash(image_path)
        if (
            image_hash
            and image_hash_counts
            and image_hash_counts.get(image_hash, 0) >= self.pdf_repeated_image_page_threshold
        ):
            return False

        return True

    def _mineru_markdown_image_targets(self, markdown_text: str):
        targets = []
        targets.extend(re.findall(r"!\[[^\]]*\]\(([^\)]+)\)", markdown_text or ""))
        targets.extend(
            re.findall(
                r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
                markdown_text or "",
                flags=re.IGNORECASE,
            )
        )
        return targets

    def _mineru_markdown_image_hash_counts(self, markdown_text: str, markdown_file: str) -> dict:
        counts = {}
        for raw_target in self._mineru_markdown_image_targets(markdown_text):
            image_path = self._resolve_mineru_image_path(markdown_file, raw_target)
            if image_path is None:
                continue
            image_hash = self._image_file_hash(image_path)
            if image_hash:
                counts[image_hash] = counts.get(image_hash, 0) + 1
        return counts

    def _pdf_xref_size(self, doc, xref: int):
        try:
            image_info = doc.extract_image(xref)
            return int(image_info.get("width") or 0), int(image_info.get("height") or 0)
        except Exception:
            return 0, 0

    def _pdf_page_text_chars(self, page) -> int:
        try:
            return len(re.sub(r"\s+", "", page.get_text("text", sort=True) or ""))
        except Exception:
            return 0

    def _should_keep_pdf_layout_image(self, doc, image_item: dict, xref_page_counts: dict) -> bool:
        if not self.pdf_skip_decorative_images:
            return True

        page = doc[image_item["page"]]
        page_area = max(float(page.rect.width) * float(page.rect.height), 1.0)
        x0, y0, x1, y1 = image_item.get("bbox", (0, 0, 0, 0))
        image_area = max(float(x1 - x0), 0.0) * max(float(y1 - y0), 0.0)
        area_ratio = image_area / page_area
        if area_ratio < self.pdf_min_image_area_ratio:
            return False

        width, height = self._pdf_xref_size(doc, image_item["xref"])
        if width < self.pdf_min_image_width or height < self.pdf_min_image_height:
            return False

        aspect_ratio = width / max(height, 1)
        if aspect_ratio > 8 or aspect_ratio < 0.125:
            return False

        if (
            self.pdf_min_foreground_area_ratio > 0
            and self._pdf_xref_foreground_area_ratio(doc, image_item["xref"]) < self.pdf_min_foreground_area_ratio
        ):
            return False

        if len(xref_page_counts.get(image_item["xref"], set())) >= self.pdf_repeated_image_page_threshold:
            return False

        if (
            self._pdf_page_text_chars(page) >= self.pdf_decorative_text_min_chars
            and area_ratio >= self.pdf_background_image_area_ratio
        ):
            return False

        return True

    def _resolve_mineru_executable(self) -> str:
        """
        自动寻找 MinerU 可执行文件。

        查找顺序：
        1. .env 中的 MINERU_EXE；
        2. 当前 Python 环境 Scripts 目录下的 mineru.exe；
        3. 系统 PATH 中的 mineru。
        """
        if self.mineru_exe:
            mineru_path = os.path.abspath(os.path.expanduser(self.mineru_exe))
            if os.path.exists(mineru_path):
                return mineru_path
            raise FileNotFoundError(
                f"MINERU_EXE 指定的文件不存在：{mineru_path}"
            )

        # 当前正在运行的 Python，例如：
        # app-main/.venv/Scripts/python.exe
        python_exe = Path(sys.executable)
        candidates = []

        if os.name == "nt":
            candidates.extend([
                python_exe.parent / "mineru.exe",
                python_exe.parent / "mineru.cmd",
                python_exe.parent / "mineru.bat",
            ])
        else:
            candidates.append(python_exe.parent / "mineru")

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

        path_command = shutil.which("mineru")
        if path_command:
            return path_command

        raise FileNotFoundError(
            "没有找到 MinerU 可执行文件。请确认已在当前虚拟环境安装 MinerU，"
            "或者在 .env 中设置 MINERU_EXE。"
        )

    def _find_mineru_markdown(self, output_dir: str) -> str:
        """在 MinerU 输出目录中寻找生成的 Markdown 文件。"""
        markdown_files = list(Path(output_dir).rglob("*.md"))

        if not markdown_files:
            raise FileNotFoundError(
                f"MinerU 已执行，但在输出目录中没有找到 .md 文件：{output_dir}"
            )

        # 优先选择内容较多的 Markdown，避免误取辅助说明文件。
        markdown_file = max(
            markdown_files,
            key=lambda path: (path.stat().st_size, path.stat().st_mtime)
        )

        print(f"[MinerU] 使用解析结果：{markdown_file}")
        return str(markdown_file)

    def _clean_mineru_markdown(self, markdown_text: str) -> str:
        """
        对 MinerU Markdown 做轻量清洗。

        保留标题、列表、表格和图片位置标记，仅合并过多空行。
        """
        if not markdown_text:
            return ""

        # 合并过多空行
        markdown_text = re.sub(r"\n{4,}", "\n\n\n", markdown_text)

        return markdown_text.strip()

    def _is_mineru_chunked_text(self, text: str) -> bool:
        return (text or "").lstrip().startswith("[MinerU切块保真输入]")

    def _is_mineru_heading_line(self, line: str) -> bool:
        stripped = (line or "").strip()
        if not stripped:
            return False
        if re.match(r"^#{1,6}\s+\S+", stripped):
            return True
        normalized = stripped.lstrip("#").strip()
        if self._section_from_mineru_heading(normalized) is None:
            return False
        compact = "".join(normalized.split())
        has_section_marker = bool(
            re.match(
                r"^(?:第?\s*[一二三四五六七八九十百千万]+\s*[章节、.．)]?|[（(]?\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*[)）]?|[（(]?\s*\d+(?:\.\d+)*\s*[)）.．、]?)\s*",
                normalized,
            )
        )
        return has_section_marker or len(compact) <= 30 or (
            normalized.endswith(("：", ":")) and len(compact) <= 80
        )

    def _is_mineru_table_line(self, line: str) -> bool:
        return bool(re.match(r"^\s*<table\b", line or "", flags=re.IGNORECASE))

    def _normalize_mineru_heading_text(self, text: str) -> str:
        raw_text = (text or "").strip()
        first_line = raw_text.splitlines()[0].strip() if raw_text else ""
        first_line = re.sub(r"^#{1,6}\s*", "", first_line).strip()
        first_line = re.sub(r"<[^>]+>", "", first_line)
        first_line = re.sub(
            r"^(?:第?\s*[一二三四五六七八九十百千万]+\s*[章节、.．)]?|[（(]?\s*[①②③④⑤⑥⑦⑧⑨⑩]\s*[)）]?|[（(]?\s*\d+(?:\.\d+)*\s*[)）.．、]?)\s*",
            "",
            first_line,
        ).strip()
        first_line = re.sub(r"[：:]\s*$", "", first_line)
        return "".join(first_line.split())

    def _section_from_mineru_heading(self, title: str):
        normalized = self._normalize_mineru_heading_text(title)
        if not normalized:
            return None

        if any(keyword in normalized for keyword in ("基本信息", "不合格描述", "问题描述", "问题简介", "问题概述", "故障描述")):
            return "problem_intro"
        if any(keyword in normalized for keyword in ("不合格调查", "原因分析", "问题原因", "故障原因")) or normalized == "原因":
            return "causes"
        if any(keyword in normalized for keyword in ("不合格涉及范围", "影响范围", "故障评估", "问题评估", "评估")):
            return "evaluation"
        if any(keyword in normalized for keyword in ("检查步骤", "检测步骤", "排查步骤", "检查方法", "检测方法", "排查方法")):
            return "inspection"
        if any(keyword in normalized for keyword in ("改善措施", "纠正措施", "处置方案", "处理方案", "解决方案", "维修方案", "问题解决", "解决措施", "跟进处置", "改善结果")):
            return "solutions"
        if any(keyword in normalized for keyword in ("原因分类", "关键要点", "注意事项", "经验总结", "总结", "结论")):
            return "key_points"
        return None

    def _extract_image_position_indexes(self, text: str):
        indexes = []
        for match in re.finditer(r"<image_position\s+indexes?=\"([\d,\s]+)\"\s*/>", text or ""):
            for value in match.group(1).split(","):
                value = value.strip()
                if value.isdigit():
                    index = int(value)
                    if index not in indexes:
                        indexes.append(index)
        return indexes

    def _mineru_chunk_type(self, chunk_text: str) -> str:
        if self._is_mineru_table_line(chunk_text):
            return "table"
        image_indexes = self._extract_image_position_indexes(chunk_text)
        text_without_images = re.sub(r"<image_position\s+indexes?=\"[\d,\s]+\"\s*/>", "", chunk_text or "").strip()
        if image_indexes and text_without_images:
            return "text_with_images"
        if image_indexes:
            return "image"
        return "text"

    def _build_mineru_markdown_chunks(self, markdown_text: str):
        """
        将 MinerU Markdown 转为保真语义块。

        规则：
        - 表格保持原始 HTML table，不拆单元格；
        - 标题、编号段落、带图片说明的段落作为自然边界；
        - <image_position> 绑定到前一个文本块；
        - 长段落只在自然边界处切，不改写原文。
        """
        clean_text = self._clean_mineru_markdown(markdown_text)
        lines = clean_text.splitlines()
        chunks = []
        current_lines = []
        current_title = ""
        current_section = ""
        active_title = ""
        active_section = ""
        current_start_line = None
        target_chars = 1400

        def flush():
            nonlocal current_lines, current_title, current_section, current_start_line
            text = "\n".join(current_lines).strip()
            if not text:
                current_lines = []
                current_title = ""
                current_section = ""
                current_start_line = None
                return
            chunks.append({
                "chunk_index": len(chunks) + 1,
                "title": current_title or active_title,
                "section_hint": current_section or active_section,
                "type": self._mineru_chunk_type(text),
                "text": text,
                "image_indexes": self._extract_image_position_indexes(text),
                "line_start": current_start_line,
                "line_end": current_start_line + len(current_lines) - 1 if current_start_line is not None else None,
                "char_count": len(text),
            })
            current_lines = []
            current_title = ""
            current_section = ""
            current_start_line = None

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line:
                continue

            is_image_position = bool(re.fullmatch(r"<image_position\s+indexes?=\"[\d,\s]+\"\s*/>", line))
            is_table = self._is_mineru_table_line(line)
            is_heading = self._is_mineru_heading_line(line)

            if is_table:
                flush()
                current_lines = [line]
                current_title = active_title
                current_section = active_section
                current_start_line = line_number
                flush()
                continue

            if is_image_position:
                if current_lines:
                    current_lines.append(line)
                elif chunks:
                    chunks[-1]["text"] = chunks[-1]["text"].rstrip() + "\n" + line
                    chunks[-1]["image_indexes"] = self._extract_image_position_indexes(chunks[-1]["text"])
                    chunks[-1]["type"] = self._mineru_chunk_type(chunks[-1]["text"])
                    chunks[-1]["char_count"] = len(chunks[-1]["text"])
                else:
                    current_lines = [line]
                    current_title = active_title
                    current_section = active_section
                    current_start_line = line_number
                continue

            if is_heading:
                flush()
                active_title = line.lstrip("#").strip()
                heading_section = self._section_from_mineru_heading(line)
                if heading_section:
                    active_section = heading_section
                current_lines = [line]
                current_title = active_title
                current_section = active_section
                current_start_line = line_number
                continue

            if not current_lines:
                current_lines = [line]
                current_title = active_title
                current_section = active_section
                current_start_line = line_number
                continue

            if len("\n".join(current_lines)) >= target_chars:
                flush()
                current_lines = [line]
                current_title = active_title
                current_section = active_section
                current_start_line = line_number
            else:
                current_lines.append(line)

        flush()
        return chunks

    def _format_mineru_chunks_for_llm(self, chunks):
        parts = [
            "[MinerU切块保真输入]",
            "说明：以下块来自 MinerU Markdown，是后续 JSON 的唯一原文依据。请按块处理，不要概括压缩。",
            "",
        ]
        for chunk in chunks:
            title = chunk.get("title") or "未命名段落"
            section_hint = chunk.get("section_hint") or "未判定"
            image_indexes = chunk.get("image_indexes") or []
            image_text = ",".join(str(index) for index in image_indexes) if image_indexes else "无"
            parts.extend([
                f"[块 {chunk['chunk_index']:03d}]",
                f"标题: {title}",
                f"章节归属字段: {section_hint}",
                f"类型: {chunk.get('type') or 'text'}",
                f"图片索引: {image_text}",
                "原文:",
                chunk.get("text") or "",
                "[/块]",
                "",
            ])
        return "\n".join(parts).strip()

    def _write_mineru_chunks_debug(self, markdown_file: str, chunks, llm_text: str):
        try:
            output_dir = Path(markdown_file).parent
            with open(output_dir / "mineru_llm_chunks.json", "w", encoding="utf-8") as f:
                json.dump(chunks, f, ensure_ascii=False, indent=2)
            with open(output_dir / "mineru_llm_input.md", "w", encoding="utf-8") as f:
                f.write(llm_text)
        except Exception as e:
            print(f"[MinerU] 写入切块调试文件失败：{e}")

    def _resolve_mineru_image_path(self, markdown_file: str, raw_target: str):
        """将 MinerU Markdown 中的图片引用解析为本地文件路径。"""
        target = (raw_target or "").strip()
        if not target:
            return None
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) and not re.match(r"^[a-zA-Z]:[\\/]", target):
            return None

        match = re.match(
            r"(.+?\.(?:png|jpg|jpeg|webp|bmp|gif))(?:\s+['\"].*['\"])?$",
            target,
            flags=re.IGNORECASE,
        )
        if match:
            target = match.group(1)

        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        image_path = Path(target)
        if not image_path.is_absolute():
            image_path = Path(markdown_file).parent / image_path

        if image_path.exists() and image_path.is_file():
            return image_path
        return None

    def _copy_mineru_image_to_upload(self, source_path: Path, copied_images, image_urls, image_names):
        source_key = str(source_path.resolve())
        if source_key in copied_images:
            return copied_images[source_key]

        ext = source_path.suffix.lower() or ".png"
        unique_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}{ext}"
        upload_dir = Path(self.document_base_dir) / self.image_dir
        upload_dir.mkdir(parents=True, exist_ok=True)
        destination = upload_dir / unique_filename
        shutil.copy2(source_path, destination)

        image_urls.append(str(destination))
        image_names.append(unique_filename)
        image_index = len(image_urls)
        copied_images[source_key] = image_index
        return image_index

    def _prepare_mineru_markdown_assets(self, markdown_text: str, markdown_file: str):
        """
        使用 MinerU 生成的图片作为唯一图片来源。

        Markdown 中出现的图片会复制到 upload/images，并替换成
        <image_position indexes="n" />，让大模型能把文本位置和后续
        base64 图片顺序对应起来。
        """
        image_urls = []
        image_names = []
        copied_images = {}
        image_hash_counts = self._mineru_markdown_image_hash_counts(markdown_text, markdown_file)

        def register_image(raw_target: str):
            source_path = self._resolve_mineru_image_path(markdown_file, raw_target)
            if source_path is None:
                return None
            if not self._should_keep_image_file(source_path, image_hash_counts):
                return None
            return self._copy_mineru_image_to_upload(
                source_path,
                copied_images,
                image_urls,
                image_names,
            )

        def replace_markdown_image(match):
            image_index = register_image(match.group(1))
            if image_index is None:
                return "\n[图片]\n"
            return f"\n{self._format_image_position_hint([image_index])}\n"

        markdown_text = re.sub(
            r"!\[[^\]]*\]\(([^\)]+)\)",
            replace_markdown_image,
            markdown_text,
        )

        def replace_html_image(match):
            image_index = register_image(match.group(1))
            if image_index is None:
                return "\n[图片]\n"
            return f"\n{self._format_image_position_hint([image_index])}\n"

        markdown_text = re.sub(
            r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
            replace_html_image,
            markdown_text,
            flags=re.IGNORECASE,
        )

        return self._clean_mineru_markdown(markdown_text), image_urls, image_names

    def _build_mineru_section_image_indexes(self, markdown_text: str):
        """
        根据 MinerU Markdown 中的标题/小节位置，确定图片属于哪个七段字段。

        MinerU 没有 PyMuPDF 的坐标信息，但 Markdown 顺序是可靠的：
        图片占位符出现在哪个业务小节之后，就归入该小节。
        """
        section_image_indexes = self._empty_section_image_indexes()
        current_section = None

        for raw_line in self._clean_mineru_markdown(markdown_text).splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if self._is_mineru_heading_line(line):
                detected_section = self._section_from_mineru_heading(line)
                if detected_section:
                    current_section = detected_section

            image_indexes = self._extract_image_position_indexes(line)
            if current_section and image_indexes:
                target_indexes = section_image_indexes[current_section]
                for image_index in image_indexes:
                    if image_index not in target_indexes:
                        target_indexes.append(image_index)

        return section_image_indexes

    def _section_from_ppt_page_title(self, title: str):
        normalized = self._normalize_mineru_heading_text(title)
        if not normalized:
            return None
        if any(keyword in normalized for keyword in ("客户反馈", "客诉", "批次信息", "信息描述", "背景", "问题描述")):
            return "problem_intro"
        if any(keyword in normalized for keyword in ("结论", "总结")):
            return "key_points"
        if any(keyword in normalized for keyword in ("改善", "措施", "方案", "处理", "建议")):
            return "solutions"
        if any(keyword in normalized for keyword in ("影响", "风险", "评估", "范围")):
            return "evaluation"
        if any(keyword in normalized for keyword in ("原因", "根因", "异常分析", "分析")):
            return "causes"
        if any(keyword in normalized for keyword in ("上机情况", "使用情况", "测序", "质检", "检测", "排查", "数据", "调查", "验证", "情况")):
            return "inspection"
        return None

    def _infer_ppt_page_sections_from_markdown(self, markdown_text: str):
        page_sections = {}
        for raw_line in (markdown_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = re.match(r"^#{1,6}\s*0*([1-9]\d?)\s*[\.\-、\s]*(.+)$", line)
            if not match:
                continue
            page_number = int(match.group(1))
            section = self._section_from_ppt_page_title(match.group(2))
            if section:
                page_sections[page_number] = section
        return page_sections

    def _render_pdf_pages_to_upload_images(self, pdf_url: str, image_urls, image_names):
        page_images = []
        doc = pymupdf.open(pdf_url)
        try:
            page_count = min(len(doc), max(self.mineru_page_image_max_pages, 0))
            scale = max(self.mineru_page_image_dpi, 72) / 72
            matrix = pymupdf.Matrix(scale, scale)
            upload_dir = Path(self.document_base_dir) / self.image_dir
            upload_dir.mkdir(parents=True, exist_ok=True)

            for page_index in range(page_count):
                page = doc[page_index]
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                unique_filename = (
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_"
                    f"{uuid.uuid4().hex}_page_{page_index + 1}.png"
                )
                image_path = upload_dir / unique_filename
                pix.save(str(image_path))
                pix = None
                image_urls.append(str(image_path))
                image_names.append(unique_filename)
                page_images.append({
                    "page": page_index + 1,
                    "image_index": len(image_urls),
                })
        finally:
            doc.close()
        return page_images

    def _append_pdf_page_image_markers(self, markdown_text: str, page_images):
        if not page_images:
            return markdown_text
        parts = [markdown_text.rstrip(), "", "## 原始页面图"]
        for item in page_images:
            parts.extend([
                "",
                f"第{item['page']}页原始页面图：",
                self._format_image_position_hint([item["image_index"]]),
            ])
        return "\n".join(parts).strip()

    def _apply_pdf_page_images_to_sections(self, markdown_text: str, page_images, section_image_indexes):
        if not page_images:
            return section_image_indexes
        page_sections = self._infer_ppt_page_sections_from_markdown(markdown_text)
        for item in page_images:
            section = page_sections.get(item["page"]) or "inspection"
            target_indexes = section_image_indexes.setdefault(section, [])
            image_index = item["image_index"]
            if image_index not in target_indexes:
                target_indexes.append(image_index)
        return section_image_indexes

    def _create_mineru_output_dir(self, pdf_url: str) -> str:
        """创建可持久保存的 MinerU 输出目录。"""
        os.makedirs(self.mineru_output_dir, exist_ok=True)
        file_stem = Path(pdf_url).stem
        safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", file_stem).strip(" ._")
        if not safe_stem:
            safe_stem = "document"
        safe_stem = safe_stem[:80]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.mineru_output_dir) / f"{timestamp}_{uuid.uuid4().hex[:8]}_{safe_stem}"
        output_dir.mkdir(parents=True, exist_ok=False)
        return str(output_dir)

    def _write_mineru_run_info(
        self,
        output_dir: str,
        command,
        env,
        pdf_url: str,
        status: str,
        stdout: str = "",
        stderr: str = "",
        returncode=None,
    ):
        """保存 MinerU 实际运行命令和关键环境，便于排查输出差异。"""
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            important_env_names = [
                "MODELSCOPE_CACHE",
                "MINERU_TOOLS_CONFIG_JSON",
                "MINERU_MODEL_SOURCE",
                "MINERU_FORMULA_ENABLE",
                "MINERU_TABLE_ENABLE",
                "TMP",
                "TEMP",
                "TMPDIR",
            ]
            run_info = {
                "status": status,
                "returncode": returncode,
                "pdf_url": os.path.abspath(pdf_url),
                "output_dir": str(output_path),
                "command": command,
                "command_line": " ".join(f'"{item}"' if " " in item else item for item in command),
                "env": {name: env.get(name) for name in important_env_names},
                "time": datetime.now().isoformat(timespec="seconds"),
            }
            with open(output_path / "mineru_run_info.json", "w", encoding="utf-8") as f:
                json.dump(run_info, f, ensure_ascii=False, indent=2)
            if stdout:
                with open(output_path / "mineru_stdout.log", "w", encoding="utf-8") as f:
                    f.write(stdout)
            if stderr:
                with open(output_path / "mineru_stderr.log", "w", encoding="utf-8") as f:
                    f.write(stderr)
        except Exception as e:
            print(f"[MinerU] 写入运行信息失败：{e}")

    def parse_pdf_by_mineru_with_assets(self, pdf_url: str, include_page_images: bool = False):
        """
        调用 MinerU CLI 解析扫描 PDF，并返回 Markdown 文本和 MinerU 图片。

        MINERU_KEEP_OUTPUT=1 时，MinerU 原始输出会保存到 MINERU_OUTPUT_DIR；
        否则仍使用临时目录，读取完成后自动删除。
        给大模型使用的图片会复制到 upload/images。
        """
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        mineru_exe = self._resolve_mineru_executable()

        print(f"[MinerU] 可执行文件：{mineru_exe}")
        print(f"[MinerU] 开始解析：{pdf_url}")

        os.makedirs(self.mineru_temp_dir, exist_ok=True)

        temp_output_context = None
        if self.mineru_keep_output:
            temp_output_dir = self._create_mineru_output_dir(pdf_url)
            print(f"[MinerU] 输出目录保留：{temp_output_dir}")
        else:
            temp_output_context = tempfile.TemporaryDirectory(prefix="mineru_", dir=self.mineru_temp_dir)
            temp_output_dir = temp_output_context.name
            print(f"[MinerU] 临时输出目录：{temp_output_dir}")

        try:
            command = [
                mineru_exe,
                "-p",
                os.path.abspath(pdf_url),
                "-o",
                temp_output_dir,
                "-b",
                self.mineru_backend,
            ]
            if self.mineru_backend == "pipeline" or self.mineru_backend.startswith("hybrid"):
                command.extend(["-m", self.mineru_method])
            if self.mineru_backend == "pipeline":
                command.extend(["-l", self.mineru_lang])
            command.extend([
                "-f",
                str(self.mineru_formula_enable).lower(),
                "-t",
                str(self.mineru_table_enable).lower(),
            ])

            print("[MinerU] 执行命令：", " ".join(f'"{item}"' if " " in item else item for item in command))

            try:
                env = os.environ.copy()
                env["TMP"] = self.mineru_temp_dir
                env["TEMP"] = self.mineru_temp_dir
                env["TMPDIR"] = self.mineru_temp_dir
                env["MINERU_FORMULA_ENABLE"] = str(self.mineru_formula_enable).lower()
                env["MINERU_TABLE_ENABLE"] = str(self.mineru_table_enable).lower()
                self._write_mineru_run_info(
                    temp_output_dir,
                    command,
                    env,
                    pdf_url,
                    status="running",
                )
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.mineru_timeout,
                    env=env,
                )
            except subprocess.TimeoutExpired as e:
                self._write_mineru_run_info(
                    temp_output_dir,
                    command,
                    env,
                    pdf_url,
                    status="timeout",
                )
                raise RuntimeError(
                    f"MinerU 解析超时，超过 {self.mineru_timeout} 秒。"
                ) from e
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").strip()
                stdout = (e.stdout or "").strip()
                self._write_mineru_run_info(
                    temp_output_dir,
                    command,
                    env,
                    pdf_url,
                    status="failed",
                    stdout=stdout,
                    stderr=stderr,
                    returncode=e.returncode,
                )
                detail = stderr or stdout or str(e)
                if "os error 1455" in detail.lower():
                    detail = (
                        "Windows 页面文件/虚拟内存不足，MinerU 在加载模型时失败。"
                        "可以增大系统页面文件，或关闭 MINERU_FORMULA_ENABLE 以避免加载 MFR 公式模型。\n"
                        f"{detail}"
                    )
                raise RuntimeError(
                    f"MinerU 解析失败：{detail}"
                ) from e

            if result.stdout:
                print("[MinerU] 输出：")
                print(result.stdout[-3000:])
            self._write_mineru_run_info(
                temp_output_dir,
                command,
                env,
                pdf_url,
                status="success",
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )

            markdown_file = self._find_mineru_markdown(temp_output_dir)

            with open(markdown_file, "r", encoding="utf-8") as f:
                markdown_text = f.read()

            markdown_text, image_urls, image_names = self._prepare_mineru_markdown_assets(
                markdown_text,
                markdown_file,
            )
            section_image_indexes = self._build_mineru_section_image_indexes(markdown_text)
            if include_page_images:
                page_images = self._render_pdf_pages_to_upload_images(
                    pdf_url,
                    image_urls,
                    image_names,
                )
                section_image_indexes = self._apply_pdf_page_images_to_sections(
                    markdown_text,
                    page_images,
                    section_image_indexes,
                )
                markdown_text = self._append_pdf_page_image_markers(markdown_text, page_images)

            if not markdown_text.strip():
                raise RuntimeError("MinerU 解析完成，但提取到的文本为空。")

            try:
                with open(Path(markdown_file).parent / "mineru_llm_input.md", "w", encoding="utf-8") as f:
                    f.write(markdown_text)
            except Exception as e:
                print(f"[MinerU] 写入 LLM Markdown 输入调试文件失败：{e}")

            print(
                f"[MinerU] 解析成功，原文长度：{len(markdown_text)}，"
                f"LLM输入长度：{len(markdown_text)}，图片数：{len(image_urls)}"
            )
            return markdown_text, image_urls, image_names, section_image_indexes
        finally:
            if temp_output_context is not None:
                temp_output_context.cleanup()

    def parse_pdf_by_mineru(self, pdf_url: str) -> str:
        """兼容旧调用：只返回 MinerU Markdown 文本。"""
        markdown_text, _image_urls, _image_names, _section_image_indexes = self.parse_pdf_by_mineru_with_assets(pdf_url)
        return markdown_text

    def _build_image_file(self, doc, xref: int):
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_filename = f"{timestamp}_{uuid.uuid4().hex}.png"
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        image_path = os.path.join(base_url, unique_filename)
        pix.save(image_path)
        pix = None
        return image_path, unique_filename

    def _extract_text_from_block(self, block):
        lines = []
        for line in block.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", [])]
            line_text = "".join(spans).strip()
            if line_text:
                lines.append(line_text)
        return self._clean_image_references("\n".join(lines)).strip()

    def _clean_image_references(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"[【\[]\s*图片\s*\d+\s*[】\]]", "", text)
        text = re.sub(r"<image_position\s+indexes?=\"[\d,\s]+\"\s*/>", "", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    def _detect_section(self, text: str):
        raw_text = (text or "").strip()
        first_line = raw_text.splitlines()[0].strip() if raw_text else ""
        normalized = "".join(first_line.split())
        if not normalized:
            return None
        section_keywords = [
            ("problem_intro", ["问题描述", "问题简介", "问题概述", "故障描述"]),
            ("causes", ["原因分析", "问题原因", "故障原因", "原因"]),
            ("evaluation", ["故障评估", "问题评估", "问题现象", "故障现象", "现象描述", "问题表现", "故障表现", "评估"]),
            ("inspection", ["检查步骤", "检测步骤", "排查步骤", "检查", "检测", "排查"]),
            ("solutions", ["解决方案", "处理方案", "维修方案", "问题解决", "解决措施", "解决"]),
            ("key_points", ["关键要点", "注意事项", "经验总结", "总结"]),
        ]
        for section, keywords in section_keywords:
            if any(normalized == keyword or normalized.startswith(keyword) for keyword in keywords):
                return section
        return None

    def _empty_section_image_indexes(self):
        return {
            "problem_intro": [],
            "causes": [],
            "evaluation": [],
            "inspection": [],
            "solutions": [],
            "key_points": [],
        }

    def _format_image_position_hint(self, image_indexes):
        indexes = ",".join(str(index) for index in image_indexes)
        return f'<image_position indexes="{indexes}" />'

    def _has_forward_image_anchor(self, text: str) -> bool:
        normalized = "".join((text or "").split()).lower()
        if not normalized:
            return False
        if self._has_numbered_image_reference(text):
            return False
        anchor_patterns = [
            "如下图",
            "如下为",
            "如下所示",
            "见下图",
            "下图",
            "offset图",
            "cycle的offset图",
        ]
        return any(pattern.lower() in normalized for pattern in anchor_patterns)

    def _has_numbered_image_reference(self, text: str) -> bool:
        normalized = "".join((text or "").split())
        if not normalized:
            return False
        return bool(re.search(r"(?:如|见)?图\s*[0-9一二三四五六七八九十]+", normalized))

    def _extract_figure_numbers(self, text: str):
        normalized = "".join((text or "").split())
        if not normalized:
            return []
        return re.findall(r"(?:如|见)?图\s*([0-9一二三四五六七八九十]+)", normalized)

    def _is_figure_caption(self, text: str) -> bool:
        normalized = "".join((text or "").split())
        return bool(re.fullmatch(r"图\s*[0-9一二三四五六七八九十]+", normalized or ""))

    def _extract_figure_caption_number(self, text: str):
        normalized = "".join((text or "").split())
        match = re.fullmatch(r"图\s*([0-9一二三四五六七八九十]+)", normalized or "")
        return match.group(1) if match else None

    def _is_section_heading_text(self, text: str) -> bool:
        return self._detect_section(text) is not None and len("".join((text or "").split())) <= 8

    def _consume_nearest_anchor(self, anchors, image_item, max_vertical_gap=260, next_page_top_limit=220):
        if not anchors:
            return None

        image_page = image_item["page"]
        image_x0, image_y0, image_x1, _ = image_item["bbox"]
        image_center_x = (image_x0 + image_x1) / 2
        best_index = None
        best_score = None

        for index, anchor in enumerate(anchors):
            anchor_x0, _, anchor_x1, anchor_y1 = anchor["bbox"]
            page_gap = image_page - anchor["page"]

            if page_gap == 0:
                vertical_gap = image_y0 - anchor_y1
                if vertical_gap < -20 or vertical_gap > max_vertical_gap:
                    continue
                page_penalty = 0
            elif page_gap == 1:
                if image_y0 > next_page_top_limit:
                    continue
                vertical_gap = image_y0
                page_penalty = max_vertical_gap
            else:
                continue

            anchor_center_x = (anchor_x0 + anchor_x1) / 2
            horizontal_gap = abs(image_center_x - anchor_center_x)
            score = page_penalty + vertical_gap + horizontal_gap * 0.15
            if best_score is None or score < best_score:
                best_index = index
                best_score = score

        if best_index is None:
            return None

        return anchors.pop(best_index)["section"]

    def _find_section_from_nearest_text_above(self, text_items, image_item, max_vertical_gap=320):
        image_page = image_item["page"]
        image_x0, image_y0, image_x1, _ = image_item["bbox"]
        image_center_x = (image_x0 + image_x1) / 2
        best_section = None
        best_score = None

        for text_item in text_items:
            section = text_item.get("section")
            if not section:
                continue
            content = text_item.get("content", "")
            if (
                self._is_figure_caption(content)
                or self._is_section_heading_text(content)
                or self._has_numbered_image_reference(content)
            ):
                continue

            text_x0, _, text_x1, text_y1 = text_item["bbox"]
            page_gap = image_page - text_item["page"]

            if page_gap == 0:
                vertical_gap = image_y0 - text_y1
                if vertical_gap < -20 or vertical_gap > max_vertical_gap:
                    continue
                page_penalty = 0
            elif page_gap == 1:
                # Page break fallback: the image may start the next page while
                # its explanatory text ended near the bottom of the previous page.
                if image_y0 > 220:
                    continue
                vertical_gap = image_y0
                page_penalty = max_vertical_gap
            else:
                continue

            text_center_x = (text_x0 + text_x1) / 2
            horizontal_gap = abs(image_center_x - text_center_x)
            score = page_penalty + vertical_gap + horizontal_gap * 0.1
            if best_score is None or score < best_score:
                best_score = score
                best_section = section

        return best_section

    def _find_nearest_image_for_caption(self, image_items, caption_item, max_vertical_gap=160):
        caption_page = caption_item["page"]
        caption_x0, caption_y0, caption_x1, caption_y1 = caption_item["bbox"]
        caption_center_x = (caption_x0 + caption_x1) / 2
        best_image_index = None
        best_score = None

        for image_item in image_items:
            if image_item["page"] != caption_page:
                continue
            image_x0, image_y0, image_x1, image_y1 = image_item["bbox"]
            image_center_x = (image_x0 + image_x1) / 2

            if image_y1 <= caption_y0:
                vertical_gap = caption_y0 - image_y1
            elif image_y0 >= caption_y1:
                vertical_gap = image_y0 - caption_y1
            else:
                vertical_gap = 0

            if vertical_gap > max_vertical_gap:
                continue

            horizontal_gap = abs(caption_center_x - image_center_x)
            score = vertical_gap + horizontal_gap * 0.1
            if best_score is None or score < best_score:
                best_score = score
                best_image_index = image_item["image_index"]

        return best_image_index

    def _apply_numbered_figure_references(self, layout_text_items, image_items, section_image_indexes):
        caption_to_image = {}
        for text_item in layout_text_items:
            figure_number = self._extract_figure_caption_number(text_item.get("content", ""))
            if not figure_number:
                continue
            image_index = self._find_nearest_image_for_caption(image_items, text_item)
            if image_index:
                caption_to_image[figure_number] = image_index

        if not caption_to_image:
            return

        referenced_by_section = self._empty_section_image_indexes()
        for text_item in layout_text_items:
            section = text_item.get("section")
            if not section or self._is_figure_caption(text_item.get("content", "")):
                continue
            for figure_number in self._extract_figure_numbers(text_item.get("content", "")):
                image_index = caption_to_image.get(figure_number)
                if image_index and image_index not in referenced_by_section[section]:
                    referenced_by_section[section].append(image_index)

        for section, referenced_indexes in referenced_by_section.items():
            if not referenced_indexes:
                continue
            existing_indexes = section_image_indexes[section]
            section_image_indexes[section] = referenced_indexes + [
                image_index for image_index in existing_indexes if image_index not in referenced_indexes
            ]

    def get_pdf_layout_content(self, pdf_url: str):
        """
        PDF 自动解析入口。

        普通文本型 PDF：
            PyMuPDF 提取文本 + 图片 + 版面位置。

        扫描型 PDF：
            MinerU 提取 OCR/版面 Markdown 和图片；
            如果 MinerU 失败，则自动回退到 PyMuPDF，避免整个上传流程直接中断。
        """
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        use_mineru = False

        if self.mineru_enabled:
            try:
                use_mineru = self.is_scanned_pdf(pdf_url)
            except Exception as e:
                print(f"[PDF类型检测] 判断失败，继续使用 PyMuPDF：{e}")
        else:
            print("[MinerU] MINERU_ENABLED=0，已禁用 MinerU。")

        # 普通 PDF：完全沿用原来的版面提取逻辑。
        if not use_mineru:
            return self._get_pdf_layout_content_by_pymupdf(pdf_url)

        # 扫描 PDF：
        # MinerU 成功后，文本和图片都只使用 MinerU 输出，避免混用 PyMuPDF 结果。
        try:
            mineru_text, image_urls, image_names, section_image_indexes = self.parse_pdf_by_mineru_with_assets(pdf_url)

            return (
                mineru_text,
                image_urls,
                image_names,
                section_image_indexes,
            )

        except Exception as e:
            print(f"[MinerU] 解析失败，自动回退到 PyMuPDF：{e}")
            return self._get_pdf_layout_content_by_pymupdf(pdf_url)

    def _get_pdf_layout_content_by_pymupdf(self, pdf_url: str):
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        doc = pymupdf.open(pdf_url)
        image_urls = []
        file_names = []
        layout_items = []
        xref_page_counts = {}

        for page_index in range(len(doc)):
            page = doc[page_index]
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                block_text = self._extract_text_from_block(block)
                if not block_text:
                    continue
                x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
                layout_items.append({
                    "type": "text",
                    "page": page_index,
                    "bbox": (x0, y0, x1, y1),
                    "content": block_text,
                })

            for img in page.get_images(full=True):
                xref = img[0]
                rects = page.get_image_rects(xref)
                if not rects:
                    continue
                xref_page_counts.setdefault(xref, set()).add(page_index)
                for rect in rects:
                    layout_items.append({
                        "type": "image",
                        "page": page_index,
                        "bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                        "xref": xref,
                    })

        layout_items.sort(key=lambda item: (item["page"], item["bbox"][1], item["bbox"][0]))

        current_section = None
        section_image_indexes = self._empty_section_image_indexes()
        text_parts = []
        pending_image_indexes = []
        pending_image_anchors = []
        positioned_text_items = []
        layout_text_items = []
        image_items = []

        def flush_pending_images():
            if not pending_image_indexes:
                return
            text_parts.append(self._format_image_position_hint(pending_image_indexes))
            pending_image_indexes.clear()

        for item in layout_items:
            if item["type"] == "text":
                flush_pending_images()
                content = item["content"]
                detected_section = self._detect_section(content)
                if detected_section:
                    current_section = detected_section
                text_parts.append(content)
                if current_section:
                    text_item = {
                        "section": current_section,
                        "page": item["page"],
                        "bbox": item["bbox"],
                        "content": content,
                    }
                    positioned_text_items.append(text_item)
                    layout_text_items.append(text_item)
                if self._has_forward_image_anchor(content) and current_section:
                    pending_image_anchors.append({
                        "section": current_section,
                        "page": item["page"],
                        "bbox": item["bbox"],
                    })
            else:
                if not self._should_keep_pdf_layout_image(doc, item, xref_page_counts):
                    continue
                image_path, image_name = self._build_image_file(doc, item["xref"])
                image_urls.append(image_path)
                file_names.append(image_name)
                image_index = len(image_urls)
                pending_image_indexes.append(image_index)
                image_items.append({
                    "image_index": image_index,
                    "page": item["page"],
                    "bbox": item["bbox"],
                })
                target_section = (
                    self._consume_nearest_anchor(pending_image_anchors, item)
                    or self._find_section_from_nearest_text_above(positioned_text_items, item)
                    or current_section
                )
                if target_section:
                    section_image_indexes[target_section].append(image_index)
        flush_pending_images()
        self._apply_numbered_figure_references(layout_text_items, image_items, section_image_indexes)

        doc.close()
        return "\n".join(text_parts), image_urls, file_names, section_image_indexes

    def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)):
        if not os.path.exists(image_path):
            raise FileNotFoundError()
        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = f"{name}_compressed_{max_size}{ext}"
        new_path = os.path.join(dir_name, new_path)

        if os.path.exists(new_path):
            return new_path

        image = Image.open(image_path).convert("RGB")
        # new_size = (448, 448)
        max_length = max(image.width, image.height)
        rate = max_size / max_length
        new_size = (int(image.width * rate), int(image.height * rate))
        resized_image = image.resize(new_size)

        new_image = Image.new("RGB", (max_size, max_size), pad_color)

        x = (max_size - new_size[0]) // 2
        y = (max_size - new_size[1]) // 2

        new_image.paste(resized_image, (x, y))

        new_image.save(new_path)
        return new_path


    def get_pdf_images(self, pdf_url: str):
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)
        doc = pymupdf.open(pdf_url)
        image_urls = []
        file_names = []
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        for page_index in range(len(doc)):
            page = doc[page_index]
            image_list = page.get_images()
            # if image_list:
            # if image_list:
            # else:
            # else:

            for image_index, img in enumerate(image_list, start=1):  # 遍历图像列表
                xref = img[0]  # 获取图像 XREF
                pix = pymupdf.Pixmap(doc, xref)  

                if pix.n - pix.alpha > 3:  # 如果是 CMYK，先转换为 RGB
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)


                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_filename = f"{timestamp}_{uuid.uuid4().hex}.png"

                url = os.path.join(base_url, unique_filename)

                pix.save(url)  # 保存为 PNG
                pix = None
                image_urls.append(url)
                file_names.append(unique_filename)
        return image_urls, file_names

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

    def generate_message(self, text: str = None, image_urls: str = None):
        messages = []
        prompt = """你好，你是一位设备维修问题分析专家。我将提供一段文档文本和若干张图片。请严格基于文本和图片内容，整理为 JSON，不要补充原文没有的信息。

输出 JSON 格式如下：
{
    "title": "标题",
    "problem_intro": "问题简介文本",
    "image_urls_problem_intro": [1, 3],
    "causes": "原因文本",
    "image_urls_causes": [2],
    "evaluation": "评估方法文本",
    "image_urls_evaluation": [4],
    "inspection": "检查方法文本",
    "image_urls_inspection": [5],
    "solutions": "解决方案文本",
    "image_urls_solutions": [6],
    "key_points": "总结文本",
    "image_urls_key_points": [7, 8]
}

要求：
1. 所有内容必须严格来源于提供的文本和图片。禁止任何形式的脑补、推理、常识补充或添加原文未提及的修饰词与解释。要求最大限度利用文本和图片，文本内容分块后不丢失任何信息。
2. 仅输出 JSON，不要输出 markdown 或其他解释。
3. title 不可为空，且必须控制在 100 个字符以内；其他字段没有内容时用空字符串，图片字段没有内容时用空列表 []。
4. 图片编号从 1 开始，不得编造新的图片编号。
5. 图片归属优先依据原文位置：图片位于哪个小节下，就归入对应 image_urls_* 字段，不要只凭图片语义移动到其他字段。
6. 文本中的 <image_position indexes=\"1,2\" /> 是图片位置标记，表示此处对应第 1、2 张图片。它只能用于填写 image_urls_* 字段，禁止写入 title、problem_intro、causes、evaluation、inspection、solutions、key_points 等正文结果中。
7. 正文字段中不要出现“图片1”“【图片1】”“第1张配图”或 image_position 标记。
8. 这是保真抽取任务，不是摘要任务。不要删掉日期、编号、指标、标准值、实际值、批号、仪器号、人员、结论、措施、表格内容。
9. 不要删掉图表前后的原文引导语，例如“如下”“异常热图如下”“结果如下”“压力曲线排查结果如下”，这些属于正文内容。
10. 内容应按字段组织，保持原文事实和顺序；除删除 image_position 标记外，不要为了简洁压缩原文。

[文本内容]
{text}

[图片内容由后续 base64 给出]
""".replace("{text}", text or "")

        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = get_token_count(prompt)
        print(f"token1: {token_cnt}")
        for image in image_urls or []:
            print(image)
            if token_cnt >= self.input_token - 1000:
                break
            compress_image = self.compress_image(image, max_size=self.model_image_max_size)
            mime_type, _ = mimetypes.guess_type(compress_image)
            if mime_type is None:
                ext = os.path.splitext(compress_image)[1].lower()
                mime_type = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".bmp": "image/bmp",
                }.get(ext, "image/jpeg")
            image_base64 = self.image_to_base64(compress_image)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"},
            })
            token_cnt += 258
        messages.append({"role": "user", "content": msg_content})
        return messages
    def _image_indexes_to_urls(self, image_indexes, image_names):
        urls = []
        for image_index in image_indexes:
            if image_index <= 0 or image_index > len(image_names):
                continue
            urls.append(self.image_dir + "/" + image_names[image_index - 1])
        return ", ".join(urls) if urls else None

    def _apply_section_image_urls(self, result, section_image_indexes, image_names):
        if not section_image_indexes or not any(section_image_indexes.values()):
            return result, set()

        used_indexes = set()
        for section, image_indexes in section_image_indexes.items():
            field = f"image_urls_{section}"
            result[field] = self._image_indexes_to_urls(image_indexes, image_names)
            used_indexes.update(image_indexes)
        return result, used_indexes

    def _append_image_indexes_to_result_field(self, result, field: str, image_indexes, image_names):
        append_value = self._image_indexes_to_urls(image_indexes, image_names)
        if not append_value:
            return
        existing_value = result.get(field)
        if existing_value:
            existing_parts = [part.strip() for part in str(existing_value).split(",") if part.strip()]
            append_parts = [part.strip() for part in append_value.split(",") if part.strip()]
            merged = existing_parts + [part for part in append_parts if part not in existing_parts]
            result[field] = ", ".join(merged)
        else:
            result[field] = append_value

    def _is_mineru_markdown_text(self, text: str) -> bool:
        text = text or ""
        if re.search(r"(?im)^#{1,6}\s+\S+", text):
            return True
        return bool(re.search(r"(?i)<table\b", text))

    def _clean_mineru_markdown_result_line(self, line: str) -> str:
        line = re.sub(r"<image_position\s+indexes?=\"[\d,\s]+\"\s*/>", "", line or "").strip()
        if not line:
            return ""
        if self._is_mineru_table_line(line):
            return self._html_table_to_text(line)
        line = re.sub(r"^#{1,6}\s*", "", line).strip()
        return self._clean_image_references(line)

    def _format_html_table_row(self, cells):
        cells = [unescape(str(cell or "").strip()) for cell in cells if str(cell or "").strip()]
        if not cells:
            return ""
        if len(cells) == 1:
            return cells[0]
        if len(cells) % 2 == 0:
            pairs = []
            for index in range(0, len(cells), 2):
                key = cells[index]
                value = cells[index + 1]
                if key and value:
                    pairs.append(f"{key}：{value}")
                else:
                    pairs.append(f"{key}{value}".strip())
            return "；".join(part for part in pairs if part)
        return "；".join(cells)

    def _strip_form_placeholder_text(self, text: str) -> str:
        text = re.sub(r"[（(](?:需描述|写明|填写|打印时删除).*?[）)]", "", text or "")
        text = re.sub(r"\s+", " ", text)
        return text.strip(" ；;，,")

    def _split_compact_form_fields(self, text: str, labels) -> str:
        clean_text = self._strip_form_placeholder_text(text)
        if not clean_text:
            return ""
        pattern = "|".join(re.escape(label) for label in labels)
        clean_text = re.sub(rf"(?<!^)(?=({pattern})\s*[：:])", "\n", clean_text)
        lines = []
        for item in clean_text.splitlines():
            item = item.strip(" ；;")
            if item:
                lines.append(item)
        return "\n".join(lines)

    def _format_disposal_options(self, text: str) -> str:
        clean_text = self._strip_form_placeholder_text(text)
        option_pattern = r"([☑✓✔√□☐]?\s*(?:挑选|退货|报废|返工|让步|其他)\s*[：:])"
        parts = re.split(option_pattern, clean_text)
        if len(parts) < 3:
            return clean_text

        has_selected = any(mark in clean_text for mark in ("☑", "✓", "✔", "√"))
        lines = []
        for index in range(1, len(parts), 2):
            label_part = parts[index].strip()
            content = parts[index + 1].strip(" ；;") if index + 1 < len(parts) else ""
            mark_match = re.match(r"^([☑✓✔√□☐])?\s*(.*?)\s*[：:]$", label_part)
            if not mark_match:
                continue
            mark = mark_match.group(1) or ""
            label = mark_match.group(2)
            if mark in {"☑", "✓", "✔", "√"}:
                state = "已选"
            elif mark in {"□", "☐"} or has_selected:
                state = "未选"
            else:
                state = ""

            value_parts = [state] if state else []
            if content:
                value_parts.append(content)
            value = "；".join(value_parts) if value_parts else "未填写"
            lines.append(f"- {label}：{value}")
        return "\n".join(lines)

    def _is_form_table_rows(self, rows) -> bool:
        joined = "\n".join(" ".join(row) for row in rows)
        return any(keyword in joined for keyword in ("处置方案", "评审会签", "调查人/日期", "跟进处置和改善结果"))

    def _format_form_table_rows(self, rows) -> str:
        lines = ["表单内容："]
        in_review_table = False
        review_departments = {"工程技术", "QC", "Qc", "qc", "生产", "SQE", "PMC", "采购", "研发", "其他", "其他："}

        for row in rows:
            raw_cells = [unescape(str(cell or "").strip()) for cell in row]
            cells = [cell for cell in raw_cells if cell]
            if not cells:
                continue

            if len(raw_cells) >= 3 and raw_cells[0] == "部门" and raw_cells[1] == "签字" and raw_cells[2] == "日期":
                if not lines or lines[-1] != "评审会签：":
                    lines.append("评审会签：")
                in_review_table = True
                continue

            if in_review_table and raw_cells[0] in review_departments:
                department = raw_cells[0].rstrip("：:")
                department = "QC" if department.lower() == "qc" else department
                signer = raw_cells[1] if len(raw_cells) > 1 else ""
                date = raw_cells[2] if len(raw_cells) > 2 else ""
                if signer or date:
                    parts = []
                    if signer:
                        parts.append(f"签字：{signer}")
                    if date:
                        parts.append(f"日期：{date}")
                    lines.append(f"- {department}：" + "；".join(parts))
                else:
                    lines.append(f"- {department}：未填写")
                continue
            elif in_review_table:
                in_review_table = False

            text = " ".join(cells)
            text = re.sub(r"QA[：:]\s*[\]\|]?\s*", "QA：", text)

            if "调查人/日期" in text:
                split_text = self._split_compact_form_fields(
                    text,
                    ["调查人/日期", "调查部门负责人/日期", "QA/日期"],
                )
                lines.append("调查信息：")
                lines.extend(split_text.splitlines())
                continue

            if text.startswith("注"):
                lines.append(text)
                continue

            if text == "处置方案":
                lines.append("处置方案：")
                continue

            if any(option in text for option in ("挑选", "退货", "报废", "返工", "让步", "其他")) and (
                "复检" in text or "让步分析报告" in text or "退货" in text
            ):
                formatted_options = self._format_disposal_options(text)
                if formatted_options:
                    lines.extend(formatted_options.splitlines())
                continue

            if text == "评审会签":
                lines.append("评审会签：")
                in_review_table = True
                continue

            if text.startswith("批准人"):
                split_text = self._split_compact_form_fields(text, ["批准人", "日期"])
                lines.extend(split_text.splitlines())
                in_review_table = False
                continue

            if text.startswith("受托生产相关"):
                text = re.sub(r"^受托生产相关\s*", "受托生产相关：", text)
                split_text = self._split_compact_form_fields(text, ["受托生产相关", "批准人（委托方）", "日期"])
                lines.extend(split_text.splitlines())
                continue

            if text == "跟进处置和改善结果":
                lines.append("跟进处置和改善结果：")
                continue

            if text.startswith("需跟进点"):
                text = self._strip_form_placeholder_text(text)
                lines.append(text)
                continue

            if text.startswith("QA") and "确认关闭日期" in text:
                split_text = self._split_compact_form_fields(text, ["QA", "确认关闭日期"])
                lines.extend(split_text.splitlines())
                continue

            lines.append(self._format_html_table_row(cells))

        return "\n".join(line for line in lines if line)

    def _format_header_data_table_rows(self, rows):
        if len(rows) < 2:
            return None
        header = [str(cell or "").strip() for cell in rows[0]]
        if len(header) < 2 or any(not cell for cell in header):
            return None
        data_rows = rows[1:]
        if not data_rows or not all(len(row) == len(header) for row in data_rows):
            return None
        if any("：" in cell or ":" in cell for cell in header):
            return None

        lines = []
        for row_index, row in enumerate(data_rows, start=1):
            parts = []
            for key, value in zip(header, row):
                value = str(value or "").strip()
                parts.append(f"{key}：{value}" if value else f"{key}：")
            prefix = f"第{row_index}行：" if len(data_rows) > 1 else ""
            lines.append(prefix + "；".join(parts))
        return "\n".join(lines)

    def _html_table_to_text(self, table_html: str) -> str:
        parser = _TableTextParser()
        try:
            parser.feed(table_html or "")
        except Exception:
            plain_text = re.sub(r"<[^>]+>", " ", table_html or "")
            return " ".join(unescape(plain_text).split())

        if self._is_form_table_rows(parser.rows):
            return self._format_form_table_rows(parser.rows)

        header_data_text = self._format_header_data_table_rows(parser.rows)
        if header_data_text:
            return "表格内容：\n" + header_data_text

        lines = []
        for row in parser.rows:
            line = self._format_html_table_row(row)
            if line:
                lines.append(line)
        if not lines:
            plain_text = re.sub(r"<[^>]+>", " ", table_html or "")
            fallback = " ".join(unescape(plain_text).split())
            return fallback
        return "表格内容：\n" + "\n".join(lines)

    def _normalize_html_tables_in_text(self, text: str) -> str:
        if not text:
            return ""
        return re.sub(
            r"<table\b.*?</table>",
            lambda match: "\n" + self._html_table_to_text(match.group(0)) + "\n",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

    def _build_mineru_markdown_section_texts(self, markdown_text: str):
        section_texts = {section: [] for section in self._empty_section_image_indexes().keys()}
        current_section = None

        for raw_line in self._clean_mineru_markdown(markdown_text).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if re.match(r"^#{1,6}\s*原始页面图\s*$", line):
                current_section = None
                continue
            if re.match(r"^第\d+页原始页面图[:：]?$", line):
                continue

            detected_section = None
            if self._is_mineru_heading_line(line):
                detected_section = self._section_from_mineru_heading(line)
            elif self._is_mineru_table_line(line):
                detected_section = self._section_from_mineru_heading(line)

            if detected_section:
                current_section = detected_section

            clean_line = self._clean_mineru_markdown_result_line(line)
            if current_section and clean_line:
                section_texts[current_section].append(clean_line)

        return {
            section: "\n".join(parts).strip()
            for section, parts in section_texts.items()
            if parts
        }

    def _apply_mineru_markdown_section_texts(self, result, markdown_text: str):
        section_texts = self._build_mineru_markdown_section_texts(markdown_text)
        if not section_texts:
            return result

        forced_texts = []
        for section, section_text in section_texts.items():
            if section_text:
                result[section] = section_text
                forced_texts.append(section_text)

        for field in ("problem_intro", "causes", "evaluation", "inspection", "solutions", "key_points"):
            if field in section_texts:
                continue
            if self._is_duplicate_of_section_texts(result.get(field), forced_texts):
                result[field] = ""

        return result

    def _extract_mineru_input_blocks(self, text: str):
        if not self._is_mineru_chunked_text(text or ""):
            return []

        blocks = []
        pattern = re.compile(r"\[块\s+(\d+)\]\s*(.*?)(?=\n\[块\s+\d+\]|\Z)", re.DOTALL)
        for match in pattern.finditer(text or ""):
            body = match.group(2).strip()
            title_match = re.search(r"^标题:\s*(.*)$", body, flags=re.MULTILINE)
            section_match = re.search(r"^章节归属字段:\s*(.*)$", body, flags=re.MULTILINE)
            image_match = re.search(r"^图片索引:\s*(.*)$", body, flags=re.MULTILINE)
            original_match = re.search(r"原文:\s*\n(.*)", body, flags=re.DOTALL)
            original = original_match.group(1).strip() if original_match else ""
            original = re.sub(r"\n?\[/块\]\s*$", "", original).strip()

            image_indexes = []
            image_text = image_match.group(1).strip() if image_match else ""
            for value in re.findall(r"\d+", image_text):
                index = int(value)
                if index not in image_indexes:
                    image_indexes.append(index)
            for index in self._extract_image_position_indexes(original):
                if index not in image_indexes:
                    image_indexes.append(index)

            blocks.append({
                "chunk_index": int(match.group(1)),
                "title": title_match.group(1).strip() if title_match else "",
                "section": section_match.group(1).strip() if section_match else "",
                "image_indexes": image_indexes,
                "text": original,
            })
        return blocks

    def _clean_mineru_section_text(self, raw_text: str, title: str, section: str) -> str:
        lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
        if lines:
            first_section = self._section_from_mineru_heading(lines[0])
            first_norm = self._normalize_mineru_heading_text(lines[0])
            title_norm = self._normalize_mineru_heading_text(title)
            if first_section == section or (title_norm and first_norm == title_norm):
                lines = lines[1:]
        return self._clean_image_references("\n".join(lines))

    def _normalize_text_for_duplicate_check(self, text: str) -> str:
        return re.sub(r"\W+", "", text or "", flags=re.UNICODE)

    def _is_duplicate_of_section_texts(self, candidate: str, section_texts) -> bool:
        candidate_norm = self._normalize_text_for_duplicate_check(candidate)
        if len(candidate_norm) < 30:
            return False
        for source in section_texts:
            source_norm = self._normalize_text_for_duplicate_check(source)
            if len(source_norm) < 30:
                continue
            if candidate_norm in source_norm or source_norm in candidate_norm:
                return True
        return False

    def _apply_mineru_section_hints(self, result, text: str, image_names):
        blocks = self._extract_mineru_input_blocks(text)
        if not blocks:
            return result, set()

        section_names = set(self._empty_section_image_indexes().keys())
        section_data = {}
        for block in blocks:
            section = block.get("section")
            if section not in section_names:
                continue

            data = section_data.setdefault(section, {"parts": [], "image_indexes": []})
            content = self._clean_mineru_section_text(
                block.get("text") or "",
                block.get("title") or "",
                section,
            )
            if content:
                data["parts"].append(content)
            for image_index in block.get("image_indexes") or []:
                if image_index not in data["image_indexes"]:
                    data["image_indexes"].append(image_index)

        if not section_data:
            return result, set()

        force_sections = {"causes"}
        used_image_indexes = set()
        forced_texts = []
        for section, data in section_data.items():
            should_apply = section in force_sections or not str(result.get(section) or "").strip()
            if data["parts"] and should_apply:
                result[section] = "\n".join(data["parts"])
                forced_texts.append(result[section])
            image_field = f"image_urls_{section}"
            image_urls = self._image_indexes_to_urls(data["image_indexes"], image_names)
            if image_urls and (section in force_sections or not result.get(image_field)):
                result[image_field] = image_urls
                used_image_indexes.update(data["image_indexes"])

        for field in ("problem_intro", "causes", "evaluation", "inspection", "solutions", "key_points"):
            if field in section_data:
                continue
            if self._is_duplicate_of_section_texts(result.get(field), forced_texts):
                result[field] = ""
                image_field = f"image_urls_{field}"
                if image_field in result:
                    result[image_field] = None

        return result, used_image_indexes

    def _coerce_image_field_indexes_to_urls(self, result, image_names, used_image_indexes=None):
        used_indexes = set(used_image_indexes or set())
        for key in list(result.keys()):
            if "image" not in key:
                continue
            value = result.get(key)
            if not isinstance(value, list):
                continue

            urls = []
            for image_index in value:
                if isinstance(image_index, str) and image_index.strip().isdigit():
                    image_index = int(image_index.strip())
                if not isinstance(image_index, int):
                    continue
                if image_index <= 0 or image_index > len(image_names):
                    continue
                if image_index in used_indexes:
                    continue
                urls.append(self.image_dir + "/" + image_names[image_index - 1])
                used_indexes.add(image_index)
            result[key] = ", ".join(urls) if urls else None
        return result, used_indexes

    def _clean_result_text_fields(self, result):
        text_fields = [
            "title",
            "problem_intro",
            "causes",
            "evaluation",
            "inspection",
            "solutions",
            "key_points",
        ]
        for field in text_fields:
            value = result.get(field)
            if isinstance(value, str):
                value = self._normalize_html_tables_in_text(value)
                result[field] = self._clean_image_references(value)
        return result

    def file2document(self, text, image_urls, image_names, section_image_indexes=None):
        try:
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )

            text_value = text or ""
            is_mineru_markdown = self._is_mineru_markdown_text(text_value)
            messages = self.generate_message(text, image_urls)
            has_rich_pdf_input = is_mineru_markdown or "<image_position" in text_value
            max_tokens = max(self.max_token, self.mineru_llm_max_token) if has_rich_pdf_input else self.max_token

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens
            )
            ans = response.choices[0].message.content
            print(ans)
            result = json.loads(ans)
            result["title"] = normalize_document_title(result.get("title"))
            result = self._clean_result_text_fields(result)
            if is_mineru_markdown:
                result = self._apply_mineru_markdown_section_texts(result, text_value)
                result = self._clean_result_text_fields(result)

            used_image_indexes = set()
            result, section_image_used_indexes = self._apply_section_image_urls(result, section_image_indexes, image_names)
            used_image_indexes.update(section_image_used_indexes)

            result, used_image_indexes = self._coerce_image_field_indexes_to_urls(
                result,
                image_names,
                used_image_indexes,
            )

            document = Document(**result,
                                is_vectorized=0)
            if len(image_urls) > 0:
                for i in range(len(image_urls)):
                    if i + 1 not in used_image_indexes:
                        os.remove(image_urls[i])
                        # 删除该原图可能生成的压缩副本。
                        # compress_image() 的命名格式为：
                        # 原文件名_compressed_512.ext
                        dir_name, filename = os.path.split(image_urls[i])
                        name, ext = os.path.splitext(filename)
                        compressed_path = os.path.join(
                            dir_name,
                            f"{name}_compressed_512{ext}"
                        )
                        if os.path.exists(compressed_path):
                            os.remove(compressed_path)

            return document

        except Exception as e:
            print(e)
            if self._is_ai_service_unavailable_error(e):
                self._set_last_error(BizCode.AI_SERVICE_UNAVAILABLE, "AI服务不可用，请稍后重试")
            else:
                self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))
            for image in image_urls:
                if os.path.exists(image):
                    os.remove(image)
            return None

pdf_parser = PdfParser()

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pdf_path = r"C:\Users\exile\xwechat_files\wxid_rgs337i28sad22_66b4\msg\file\2026-07\(其他-TS相关-NCMR报告&让步放行报告)-SJ240605 不合格品报告 20241128.pdf"

    try:
        text = pdf_parser.parse_pdf_by_mineru(pdf_path)

        print("=" * 50)
        print("MinerU解析成功")
        print("=" * 50)

        print(text)

    except Exception as e:
        print("MinerU解析失败：")
        print(e)
