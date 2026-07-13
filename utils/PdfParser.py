import base64
import json
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
"""
PDF 解析器：普通 PDF 使用 PyMuPDF，扫描 PDF 自动使用 MinerU 提取文本。
需要运行：python -m pip install uv
python -m uv pip install -U "mineru[all]"来安装相关库，还有一个库的版本要求：python -m pip install setuptools==80.9.0
"""

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image
from qwen_token_counter import get_token_count

from models import Document
from utils.ai_endpoint import get_ai_base_url
import pymupdf
from openai import OpenAI

from utils.error_codes import BizCode
"""PDF 解析器：普通 PDF 使用 PyMuPDF，扫描 PDF 自动使用 MinerU 提取文本。"""

class PdfParser:
    def __init__(self):
        # self.db = db
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/source_documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))

        # =========================
        # MinerU 配置
        # =========================
        # 是否启用 MinerU 自动解析扫描 PDF。
        self.mineru_enabled = os.getenv("MINERU_ENABLED", "1").lower() in {"1", "true", "yes", "on"}

        # 可以在 .env 中手动指定：
        # MINERU_EXE=C:/xxx/app-main/.venv/Scripts/mineru.exe
        # 不指定时，会自动寻找当前虚拟环境中的 mineru.exe 或 PATH 中的 mineru。
        self.mineru_exe = os.getenv("MINERU_EXE", "").strip()

        # 扫描件判断参数：
        # 单页有效文字少于该值，认为这一页没有有效文本层。
        self.scan_page_min_chars = int(os.getenv("PDF_SCAN_PAGE_MIN_CHARS", 30))

        # 有效文本页比例低于该值，认为整个 PDF 主要是扫描件。
        self.scan_text_page_ratio = float(os.getenv("PDF_SCAN_TEXT_PAGE_RATIO", 0.5))

        # MinerU 最大执行时间，默认 30 分钟。
        self.mineru_timeout = int(os.getenv("MINERU_TIMEOUT", 1800))

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

        保留标题、列表、表格等结构，仅移除图片 Markdown 语法，
        因为图片仍由当前 PdfParser 的图片提取逻辑单独处理。
        """
        if not markdown_text:
            return ""

        # ![alt](images/xxx.png) -> [图片]
        markdown_text = re.sub(
            r"!\[[^\]]*\]\([^\)]+\)",
            "\n[图片]\n",
            markdown_text
        )

        # HTML img 标签 -> [图片]
        markdown_text = re.sub(
            r"<img\b[^>]*>",
            "\n[图片]\n",
            markdown_text,
            flags=re.IGNORECASE
        )

        # 合并过多空行
        markdown_text = re.sub(r"\n{4,}", "\n\n\n", markdown_text)

        return markdown_text.strip()

    def parse_pdf_by_mineru(self, pdf_url: str) -> str:
        """
        调用 MinerU CLI 解析扫描 PDF，并返回 Markdown 文本。

        MinerU 输出使用临时目录，读取完成后自动删除，
        不会长期占用项目磁盘空间。
        """
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)

        mineru_exe = self._resolve_mineru_executable()

        print(f"[MinerU] 可执行文件：{mineru_exe}")
        print(f"[MinerU] 开始解析：{pdf_url}")

        with tempfile.TemporaryDirectory(prefix="mineru_") as temp_output_dir:
            command = [
                mineru_exe,
                "-p",
                os.path.abspath(pdf_url),
                "-o",
                temp_output_dir,
            ]

            print("[MinerU] 执行命令：", " ".join(f'"{item}"' if " " in item else item for item in command))

            try:
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.mineru_timeout,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"MinerU 解析超时，超过 {self.mineru_timeout} 秒。"
                ) from e
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "").strip()
                stdout = (e.stdout or "").strip()
                detail = stderr or stdout or str(e)
                raise RuntimeError(
                    f"MinerU 解析失败：{detail}"
                ) from e

            if result.stdout:
                print("[MinerU] 输出：")
                print(result.stdout[-3000:])

            markdown_file = self._find_mineru_markdown(temp_output_dir)

            with open(markdown_file, "r", encoding="utf-8") as f:
                markdown_text = f.read()

            markdown_text = self._clean_mineru_markdown(markdown_text)

            if not markdown_text.strip():
                raise RuntimeError("MinerU 解析完成，但提取到的文本为空。")

            print(f"[MinerU] 解析成功，文本长度：{len(markdown_text)}")
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
        text = re.sub(r"<image_position\s+indexes?=\"[\d,]+\"\s*/>", "", text)
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
            MinerU 提取 OCR/版面文本；
            图片仍沿用 PyMuPDF 的原有提取逻辑。
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
        # 1. MinerU 提取 OCR / Markdown 文本；
        # 2. PyMuPDF 继续负责提取 PDF 中能够获取到的图片。
        try:
            mineru_text = self.parse_pdf_by_mineru(pdf_url)

            # 这里仍调用原来的 PyMuPDF 逻辑，是为了尽量保留图片提取能力。
            # 对扫描件来说，PyMuPDF 文本通常很少，但图片仍可能被提取出来。
            (
                _pymupdf_text,
                image_urls,
                image_names,
                section_image_indexes,
            ) = self._get_pdf_layout_content_by_pymupdf(pdf_url)

            # MinerU 的文本是重新 OCR 后的内容，原来的 section_image_indexes
            # 依赖 PyMuPDF 文本位置，对扫描件通常不可靠，因此置空，
            # 后续让多模态大模型根据 MinerU 文本 + 图片自行判断图片归属。
            section_image_indexes = self._empty_section_image_indexes()

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
1. 仅输出 JSON，不要输出 markdown 或其他解释。
2. title 不可为空；其他字段没有内容时用空字符串，图片字段没有内容时用空列表 []。
3. 图片编号从 1 开始，不得编造新的图片编号。
4. 图片归属优先依据原文位置：图片位于哪个小节下，就归入对应 image_urls_* 字段，不要只凭图片语义移动到其他字段。
5. 文本中的 <image_position indexes=\"1,2\" /> 是图片位置标记，表示此处对应第 1、2 张图片。它只能用于填写 image_urls_* 字段，禁止写入 title、problem_intro、causes、evaluation、inspection、solutions、key_points 等正文结果中。
6. 正文字段中不要出现“图片1”“【图片1】”“第1张配图”或 image_position 标记。
7. 内容应连贯、详细，尽量保留文档原意，但不要大量重复。

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
            compress_image = self.compress_image(image)
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
                result[field] = self._clean_image_references(value)
        return result

    def file2document(self, text, image_urls, image_names, section_image_indexes=None):
        try:
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )

            messages = self.generate_message(text, image_urls)

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token
            )
            ans = response.choices[0].message.content
            print(ans)
            result = json.loads(ans)
            result = self._clean_result_text_fields(result)

            result, used_image_indexes = self._apply_section_image_urls(result, section_image_indexes, image_names)

            if not used_image_indexes:
                flag = [0] * len(image_names)
                for key in result.keys():
                    if "image" in key:
                        image_url_content = ""
                        for image_index in result[key]:
                            if image_index > len(image_urls) or flag[image_index - 1] == 1:
                                continue
                            url = image_names[image_index - 1]
                            flag[image_index - 1] = 1
                            url = self.image_dir + "/" + url
                            image_url_content += url + ", "
                        image_url_content = image_url_content.rstrip(", ")
                        if len(result[key]) == 0:
                            image_url_content = None
                        result[key] = image_url_content
                used_image_indexes = {i + 1 for i, used in enumerate(flag) if used == 1}
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
