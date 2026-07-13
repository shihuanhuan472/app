import base64
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image
from openai import OpenAI
from qwen_token_counter import get_token_count

from models import Document
from utils.ai_endpoint import get_ai_base_url_alt
from utils.error_codes import BizCode
import os

"""
使用 MinerU 提取 PPTX 中的文本和图片。
"""

class PPTParser:
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.mineru_exe = os.getenv("MINERU_EXE", "").strip()
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

    def parse(self, file_path: str):
        self.last_error_code = None
        self.last_error_detail = None
        text, image_urls, image_names, section_image_indexes = self.get_content(file_path)
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

    def _detect_section(self, text: str):
        normalized = "".join((text or "").split())
        section_keywords = [
            ("problem_intro", ["问题描述", "问题简介"]),
            ("causes", ["原因分析", "原因"]),
            ("evaluation", ["故障评估", "评估"]),
            ("inspection", ["检查步骤", "检查"]),
            ("solutions", ["解决方案", "问题解决", "解决"]),
            ("key_points", ["关键要点", "总结"]),
        ]
        for section, keywords in section_keywords:
            if any(keyword in normalized for keyword in keywords):
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

    def _resolve_mineru_executable(self) -> str:
        if self.mineru_exe:
            mineru_path = os.path.abspath(os.path.expanduser(self.mineru_exe))
            if os.path.exists(mineru_path):
                return mineru_path
            raise FileNotFoundError(f"MINERU_EXE 指定的文件不存在：{mineru_path}")

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

    def _find_mineru_markdown(self, output_dir: str) -> Path:
        markdown_files = list(Path(output_dir).rglob("*.md"))
        if not markdown_files:
            raise FileNotFoundError(
                f"MinerU 已执行，但在输出目录中没有找到 .md 文件：{output_dir}"
            )
        return max(
            markdown_files,
            key=lambda path: (path.stat().st_size, path.stat().st_mtime),
        )

    def _copy_mineru_images(self, markdown_text: str, markdown_dir: Path):
        image_urls = []
        image_names = []
        image_indexes = {}
        target_dir = Path(self.document_base_dir) / self.image_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        markdown_root = markdown_dir.resolve()

        def copy_image(raw_path: str) -> int | None:
            relative_path = unquote(raw_path.strip().strip('"\''))
            relative_path = relative_path.split("#", 1)[0].split("?", 1)[0]
            source_path = (markdown_dir / relative_path).resolve()

            try:
                source_path.relative_to(markdown_root)
            except ValueError:
                return None
            if not source_path.is_file():
                return None

            source_key = str(source_path).lower()
            if source_key in image_indexes:
                return image_indexes[source_key]

            extension = source_path.suffix.lower() or ".png"
            unique_filename = f"{uuid.uuid4().hex}{extension}"
            target_path = target_dir / unique_filename
            shutil.copy2(source_path, target_path)

            image_urls.append(str(target_path))
            image_names.append(unique_filename)
            image_index = len(image_urls)
            image_indexes[source_key] = image_index
            print(f"[MinerU] 已保存 PPT 图片：{unique_filename}")
            return image_index

        markdown_image_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

        def replace_markdown_image(match: re.Match) -> str:
            image_index = copy_image(match.group(1))
            return f"\n【图片{image_index}】\n" if image_index else "\n[图片]\n"

        markdown_text = markdown_image_pattern.sub(replace_markdown_image, markdown_text)

        html_image_pattern = re.compile(
            r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
            flags=re.IGNORECASE,
        )

        def replace_html_image(match: re.Match) -> str:
            image_index = copy_image(match.group(1))
            return f"\n【图片{image_index}】\n" if image_index else "\n[图片]\n"

        markdown_text = html_image_pattern.sub(replace_html_image, markdown_text)
        markdown_text = re.sub(r"\n{4,}", "\n\n\n", markdown_text).strip()
        return markdown_text, image_urls, image_names

    def get_content(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        extension = Path(file_path).suffix.lower()
        if extension != ".pptx":
            raise ValueError("MinerU 仅支持 .pptx，不支持旧版 .ppt；请先将文件另存为 .pptx。")

        mineru_exe = self._resolve_mineru_executable()
        print(f"[MinerU] 开始解析 PPTX：{file_path}")

        with tempfile.TemporaryDirectory(prefix="mineru_ppt_") as temp_output_dir:
            command = [
                mineru_exe,
                "-p",
                os.path.abspath(file_path),
                "-o",
                temp_output_dir,
            ]
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
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"MinerU 解析 PPTX 超时，超过 {self.mineru_timeout} 秒。"
                ) from error
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or error.stdout or str(error)).strip()
                raise RuntimeError(f"MinerU 解析 PPTX 失败：{detail}") from error

            if result.stdout:
                print("[MinerU] 输出：")
                print(result.stdout[-3000:])

            try:
                markdown_file = self._find_mineru_markdown(temp_output_dir)
            except FileNotFoundError as error:
                detail = (result.stderr or result.stdout or "").strip()
                if detail:
                    detail = f"\nMinerU 输出：{detail[-3000:]}"
                raise RuntimeError(f"{error}{detail}") from error
            markdown_text = markdown_file.read_text(encoding="utf-8")
            markdown_text, image_urls, image_names = self._copy_mineru_images(
                markdown_text,
                markdown_file.parent,
            )
            if not markdown_text:
                raise RuntimeError("MinerU 解析 PPTX 完成，但提取到的文本为空。")

            print(
                f"[MinerU] PPTX 解析成功，文本长度：{len(markdown_text)}，"
                f"图片数量：{len(image_urls)}"
            )
            return (
                markdown_text,
                image_urls,
                image_names,
                self._empty_section_image_indexes(),
            )
    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

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

    def _remove_image_files(self, image_path: str, max_size=512):
        if os.path.exists(image_path):
            os.remove(image_path)
        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        compressed_path = os.path.join(
            dir_name,
            f"{name}_compressed_{max_size}{ext}",
        )
        if os.path.exists(compressed_path):
            os.remove(compressed_path)

    def generate_message(self, text, image_urls):
        messages = []
        data = {}
        # print(text)
        prompt = """你是一位专业的设备维修分析专家。我将给你一段关于设备维修的文本（包含文字描述）以及若干张相关图片，每张图片都有唯一的编号（从1开始）且只属于一个字段。你的任务是基于这些内容，严格按照以下模板生成JSON格式的总结。请确保所有信息均来源于提供的文本和图片，不得杜撰。对于缺失的信息，对应字段留空，但不可缺失字段（文本为空字符串，图片为空列表）。
模板：
标题：<简洁的标题，点明案例名称，必须填写>
问题简介：<可包含定义解释，现象介绍，问题发生频率，后果等内容>
原因：<造成该问题的主要原因，尽量从高频到低频排序>
评估：<评估问题的手段，方法，工具等信息>
检查：<描述维修现场如何进行定位确认，要求详细>
解决方法：<现场的解决措施及根本的解决方案，要求详细>
总结：<总结问题的主要原因，后果及解决方案的关键信息>
相关图片：<与字段相关的图像，每张图像最多出现在一个字段中>

请严格按照如下JSON格式输出：
{{
    "title": "标题", // 案例名称
    "problem_intro": "问题简介文本",
    "image_urls_problem_intro": [1, 3], // 与问题简介相关的图片编号
    "causes": "原因文本",
    "image_urls_causes": [2], // 与原因相关的图片编号
    "evaluation": "评估方法文本",
    "image_urls_evaluation": [4], // 与评估相关的图片编号
    "inspection": "检查方法文本",
    "image_urls_inspection": [5], // 与检查相关的图片编号
    "solutions": "解决方案文本",
    "image_urls_solutions": [6], // 与解决方案相关的图片编号
    "key_points": "总结文本",
    "image_urls_key_points": [7, 8] // 与总结相关的图片编号
}}

注意：
1.所有内容必须严格来源于提供的文本和图片。禁止任何形式的脑补、推理、常识补充或添加原文未提及的修饰词与解释，并且最大限度利用文本。
2. 内容中不包含的信息，对应字段可以为空，若不包含图片，图片为空列表[]。
3. 内容必须基于我提供的文本和图片，图片编号从1开始，不可杜撰任何信息。
4. 你给出的回答仅包含我要求的JSON格式答案。
5. 给定图片中可能包含无关图片，请勿放进回答中。
6. 每张图片最多出现在一个字段中。
7. 内容需连贯详细，最大限度使用给定内容，请勿过分精简。
8. 各字段内容请勿大量重复，无关图片不要放入回答。

现在请分析下面的内容：
[文本内容]
{text}

[图片内容由base64给出]""".format(text=text)
        prompt += "\n注意：图片归属必须优先依据其在原文中的位置。图片位于哪个小节下，就归入对应 image_urls 字段，不得仅凭图片内容语义移动到其他字段。"
        msg_content = [{"type": "text", "text": prompt}]
        # encoding = tiktoken.get_encoding("cl100k_base")
        token_cnt = get_token_count(prompt)

        print(f"token1: {token_cnt}")
        for image in image_urls:
            print(image)
            if token_cnt >= self.input_token - 1000:
                break
            compress_image = self.compress_image(image)
            mime_type, _ = mimetypes.guess_type(compress_image)
            if mime_type is None:
                ext = os.path.splitext(compress_image)[1].lower()
                mime_type = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp'
                }.get(ext, 'image/jpeg')
            image_base64 = self.image_to_base64(compress_image)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
            token_cnt += 258
            # l += len(image_base64)
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        # print("len = {}".format(l))
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
            result[f"image_urls_{section}"] = self._image_indexes_to_urls(image_indexes, image_names)
            used_indexes.update(image_indexes)
        return result, used_indexes
    def file2document(self, text, image_urls, image_names, section_image_indexes=None) -> Document:
        try:
            client = OpenAI(
                base_url=get_ai_base_url_alt(),
                api_key=self.api_key
            )

            messages = self.generate_message(text, image_urls)
            # print(messages)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token
            )
            # print(response)
            ans = response.choices[0].message.content
            print(ans)
            result = json.loads(ans)
            flag = [0] * len(image_urls)
            for key in result.keys():
                if "image" in key:
                    image_url_content = ""
                    for image_index in result[key]:
                        if image_index > len(image_urls) or flag[image_index - 1] == 1:
                            continue
                        url = image_names[image_index - 1]
                        url = self.image_dir + "/" + url
                        image_url_content += url + ", "
                        flag[image_index - 1] = 1
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
                        self._remove_image_files(image_urls[i])

            return document

        except Exception as e:
            print(e)
            if self._is_ai_service_unavailable_error(e):
                self._set_last_error(BizCode.AI_SERVICE_UNAVAILABLE, "AI服务不可用，请稍后重试")
            else:
                self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))
            for image in image_urls:
                self._remove_image_files(image)
            return None

ppt_parser = PPTParser()

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    pdf_path = r"C:\Users\exile\xwechat_files\wxid_rgs337i28sad22_66b4\msg\file\2026-07\E15芯片无trackline-芯片工程排查.pptx"

    try:
        # 执行完整 PDF 解析流程：
        # 1. 判断普通 PDF / 扫描 PDF
        # 2. PyMuPDF / MinerU 提取内容
        # 3. 提取图片
        # 4. 调用大模型进行结构化
        # 5. 转换为 Document 对象
        document = ppt_parser.parse(pdf_path)

        if document is None:
            print("=" * 50)
            print("PPT 结构化失败")
            print("=" * 50)
            print("错误码：", ppt_parser.last_error_code)
            print("错误信息：", ppt_parser.last_error_detail)

        else:
            print("=" * 50)
            print("大模型结构化结果")
            print("=" * 50)

            # SQLAlchemy Document 对象转成字典
            result = {
                "title": document.title,
                "problem_intro": document.problem_intro,
                "image_urls_problem_intro": document.image_urls_problem_intro,
                "causes": document.causes,
                "image_urls_causes": document.image_urls_causes,
                "evaluation": document.evaluation,
                "image_urls_evaluation": document.image_urls_evaluation,
                "inspection": document.inspection,
                "image_urls_inspection": document.image_urls_inspection,
                "solutions": document.solutions,
                "image_urls_solutions": document.image_urls_solutions,
                "key_points": document.key_points,
                "image_urls_key_points": document.image_urls_key_points,
                "is_vectorized": document.is_vectorized,
            }

            # 格式化输出 JSON
            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=4
                )
            )

    except Exception as e:
        print("=" * 50)
        print("PPT 解析失败")
        print("=" * 50)
        print(type(e).__name__)
        print(e)