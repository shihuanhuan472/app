import base64
import json
import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from PIL import Image
from openai import OpenAI
from qwen_token_counter import get_token_count

from models import Document
from utils.ai_endpoint import get_ai_base_url
from utils.error_codes import BizCode


"""
Markdown解释器。直接读取markdown文本，提取其中引用的图片，然后使用和PdfParser/PPTParser相同的本地多模态模型流程进行处理。
Markdown parser. It reads markdown text directly, extracts referenced images,
and then uses the same local multimodal model flow as PdfParser/PPTParser.
"""


class MarkdownParser:
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 3000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.allow_image = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
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

    def get_content(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        text = self.read_markdown(file_path)
        image_urls = []
        image_names = []
        markdown_dir = os.path.dirname(os.path.abspath(file_path))
        output_dir = os.path.join(self.document_base_dir, self.image_dir)
        section_image_indexes = self._empty_section_image_indexes()
        current_section = None
        text_parts = []
        last_pos = 0

        pattern = re.compile(r"!\[[^\]]*]\(([^)]+)\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
        for match in pattern.finditer(text):
            segment = text[last_pos:match.start()]
            if segment:
                detected_section = self._detect_section(segment)
                if detected_section:
                    current_section = detected_section
                text_parts.append(segment)

            raw_src = match.group(1) or match.group(2)
            image_ref = self.clean_markdown_image_src(raw_src) if match.group(1) else raw_src.strip()
            image_path = self.save_image_ref(image_ref, markdown_dir, output_dir) if image_ref else None
            if image_path is not None:
                image_urls.append(image_path)
                image_names.append(os.path.basename(image_path))
                image_index = len(image_urls)
                text_parts.append(f"\n【图片{image_index}】\n")
                if current_section:
                    section_image_indexes[current_section].append(image_index)

            last_pos = match.end()

        tail = text[last_pos:]
        if tail:
            detected_section = self._detect_section(tail)
            if detected_section:
                current_section = detected_section
            text_parts.append(tail)

        return "".join(text_parts), image_urls, image_names, section_image_indexes
    def read_markdown(self, file_path):
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def extract_image_refs(self, text):
        refs = []
        seen = set()

        for match in re.finditer(r"!\[[^\]]*]\(([^)]+)\)", text):
            src = self.clean_markdown_image_src(match.group(1))
            if src and src not in seen:
                refs.append(src)
                seen.add(src)

        for match in re.finditer(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", text, re.IGNORECASE):
            src = match.group(1).strip()
            if src and src not in seen:
                refs.append(src)
                seen.add(src)

        return refs

    def clean_markdown_image_src(self, raw_src):
        src = raw_src.strip()
        if not src:
            return None

        if src.startswith("<") and ">" in src:
            return src[1:src.index(">")].strip()

        if src[0] in ("'", '"'):
            quote = src[0]
            end = src.find(quote, 1)
            if end > 0:
                return src[1:end].strip()

        title_match = re.match(r"(.+?)(?:\s+[\"'].*[\"'])\s*$", src)
        if title_match:
            return title_match.group(1).strip()

        return src

    def save_image_ref(self, image_ref, markdown_dir, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        image_ref = image_ref.strip()

        if image_ref.startswith("data:image/"):
            return self.save_data_uri_image(image_ref, output_dir)

        parsed = urlparse(image_ref)
        if parsed.scheme in ("http", "https"):
            return self.download_image(image_ref, output_dir)

        return self.copy_local_image(image_ref, markdown_dir, output_dir)

    def copy_local_image(self, image_ref, markdown_dir, output_dir):
        parsed = urlparse(image_ref)
        raw_path = unquote(parsed.path or image_ref)
        source_path = Path(raw_path)
        if not source_path.is_absolute():
            source_path = Path(markdown_dir) / source_path
        source_path = source_path.resolve()

        if not source_path.exists() or not source_path.is_file():
            print(f"markdown image not found: {source_path}")
            return None

        ext = source_path.suffix.lower()
        if ext not in self.allow_image:
            print(f"unsupported markdown image format: {source_path}")
            return None

        filename = self.new_image_filename(ext)
        target_path = os.path.join(output_dir, filename)
        shutil.copyfile(source_path, target_path)

        if not self.verify_image(target_path):
            return None
        return target_path

    def download_image(self, url, output_dir):
        try:
            response = requests.get(url, stream=True, timeout=15, allow_redirects=True)
            if response.status_code != 200:
                print(f"markdown image download failed HTTP {response.status_code}: {url}")
                return None

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = mimetypes.guess_extension(content_type) or Path(urlparse(url).path).suffix.lower()
            if ext == ".jpe":
                ext = ".jpg"
            if ext not in self.allow_image:
                print(f"unsupported remote markdown image format: {url}")
                return None

            target_path = os.path.join(output_dir, self.new_image_filename(ext))
            with open(target_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            if not self.verify_image(target_path):
                return None
            return target_path
        except Exception as e:
            print(f"markdown image download error: {url}, {e}")
            return None

    def save_data_uri_image(self, data_uri, output_dir):
        try:
            header, payload = data_uri.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            ext = mimetypes.guess_extension(mime_type) or ".png"
            if ext == ".jpe":
                ext = ".jpg"
            if ext not in self.allow_image:
                print(f"unsupported data uri markdown image format: {mime_type}")
                return None

            target_path = os.path.join(output_dir, self.new_image_filename(ext))
            with open(target_path, "wb") as f:
                f.write(base64.b64decode(payload))

            if not self.verify_image(target_path):
                return None
            return target_path
        except Exception as e:
            print(f"markdown data uri image parse error: {e}")
            return None

    def new_image_filename(self, ext):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{uuid.uuid4().hex}{ext}"

    def verify_image(self, image_path):
        try:
            with Image.open(image_path) as img:
                img.verify()
            return True
        except Exception as e:
            print(f"invalid markdown image removed: {image_path}, {e}")
            if os.path.exists(image_path):
                os.remove(image_path)
            return False

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)):
        if not os.path.exists(image_path):
            raise FileNotFoundError(image_path)

        image = Image.open(image_path).convert("RGB")
        max_length = max(image.width, image.height)
        rate = max_size / max_length
        new_size = (int(image.width * rate), int(image.height * rate))
        resized_image = image.resize(new_size)

        new_image = Image.new("RGB", (max_size, max_size), pad_color)
        x = (max_size - new_size[0]) // 2
        y = (max_size - new_size[1]) // 2
        new_image.paste(resized_image, (x, y))

        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = os.path.join(dir_name, f"{name}_compressed{ext}")
        new_image.save(new_path)
        return new_path

    def _normalize_result_fields(self, result: dict) -> dict:
        text_fields = ["title", "problem_intro", "causes", "evaluation", "inspection", "solutions", "key_points"]
        image_fields = [
            "image_urls_problem_intro", "image_urls_causes", "image_urls_evaluation",
            "image_urls_inspection", "image_urls_solutions", "image_urls_key_points"
        ]

        for key in text_fields:
            value = result.get(key, "")
            if value is None:
                result[key] = ""
            elif isinstance(value, str):
                result[key] = value.strip()
            else:
                result[key] = str(value).strip()

        for key in image_fields:
            value = result.get(key, [])
            if isinstance(value, list):
                result[key] = value
                continue
            if isinstance(value, str):
                nums = re.findall(r"\d+", value)
                result[key] = [int(x) for x in nums] if nums else []
                continue
            result[key] = []

        return result

    def _is_effectively_empty(self, result: dict) -> bool:
        keys = ["title", "problem_intro", "causes", "evaluation", "inspection", "solutions", "key_points"]
        non_empty = 0
        for key in keys:
            value = result.get(key, "")
            if isinstance(value, str) and value.strip():
                non_empty += 1
        return non_empty <= 1

    def _build_fallback_title(self, text: str) -> str:
        lines = [line.strip("# ").strip() for line in (text or "").splitlines() if line.strip()]
        if lines:
            return lines[0][:120]
        return "Markdown解析文档"

    def _build_retry_only_text_message(self, text: str):
        prompt = """请你仅基于下面文档文本，提取结构化信息并输出JSON。
该文档可能是维修案例、部署指南、排障手册，不一定是传统故障案例。
请将内容映射到以下字段，尽量不要返回全空：
- problem_intro: 背景/目的/问题描述/范围
- causes: 原因/前提条件/依赖/风险
- evaluation: 验证方法/测试结果/验收标准
- inspection: 检查步骤/排查步骤/确认方式
- solutions: 解决方案/部署安装配置步骤/修复动作
- key_points: 总结/注意事项/关键结论

严格输出JSON对象，字段必须完整：
{
  "title": "",
  "problem_intro": "",
  "image_urls_problem_intro": [],
  "causes": "",
  "image_urls_causes": [],
  "evaluation": "",
  "image_urls_evaluation": [],
  "inspection": "",
  "image_urls_inspection": [],
  "solutions": "",
  "image_urls_solutions": [],
  "key_points": "",
  "image_urls_key_points": []
}

如果某字段确实无信息可填空字符串，但只要文本中有相关内容就必须填写，不要返回全空。

[文档文本]
{text}
""".format(text=text or "")
        return [{"role": "user", "content": [{"type": "text", "text": prompt}]}]

    def generate_message(self, text: str = None, image_urls=None):
        messages = []
        data = {}
        #print(text)
        ""
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
2. 内容中不包含的信息可以为空，但只要文本中存在相关信息就必须填写，不要返回全空。
3. 内容必须基于我提供的文本和图片，图片编号从1开始，不可杜撰任何信息。
4. 你给出的回答仅包含我要求的JSON格式答案（不要markdown代码块）。
5. 给定图片中可能包含无关图片，请勿放进回答中。
6. 每张图片最多出现在一个字段中。
7. 内容需连贯详细，最大限度使用给定内容，请勿过分精简。
8. 各字段内容请勿大量重复，无关图片不要放入回答。

现在请分析下面的内容：
[文本内容]
{text}

[图片内容由base64给出]""".format(text=text or "")
        prompt += "\n注意：图片归属必须优先依据其在原文中的位置。图片位于哪个小节下，就归入对应 image_urls 字段，不得仅凭图片内容语义移动到其他字段。"

        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = get_token_count(prompt)

        print(f"token1: {token_cnt}")
        for image in image_urls or []:
            print(image)
            if token_cnt >= self.input_token - 258:
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
                    ".bmp": "image/bmp"
                }.get(ext, "image/jpeg")

            image_base64 = self.image_to_base64(compress_image)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
            token_cnt += 258

        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
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
            ans_clean = ans.strip()
            ans_clean = re.sub(r"^```json\s*", "", ans_clean)
            ans_clean = re.sub(r"^```\s*", "", ans_clean)
            ans_clean = re.sub(r"\s*```$", "", ans_clean)

            result = json.loads(ans_clean)
            result = self._normalize_result_fields(result)

            # 第一次结果近似全空，进行一次“仅文本强制抽取”重试
            if self._is_effectively_empty(result):
                retry_messages = self._build_retry_only_text_message(text)
                retry_resp = client.chat.completions.create(
                    model=self.model,
                    messages=retry_messages,
                    max_tokens=self.max_token
                )
                retry_ans = retry_resp.choices[0].message.content.strip()
                retry_ans = re.sub(r"^```json\s*", "", retry_ans)
                retry_ans = re.sub(r"^```\s*", "", retry_ans)
                retry_ans = re.sub(r"\s*```$", "", retry_ans)
                retry_result = json.loads(retry_ans)
                retry_result = self._normalize_result_fields(retry_result)
                if not self._is_effectively_empty(retry_result):
                    result = retry_result

            if not result.get("title"):
                result["title"] = self._build_fallback_title(text)

            result, used_image_indexes = self._apply_section_image_urls(result, section_image_indexes, image_names)

            if not used_image_indexes:
                flag = [0] * len(image_names)
                for key in result.keys():
                    if "image" in key:
                        image_url_content = ""
                        if not isinstance(result[key], list):
                            result[key] = []

                        for image_index in result[key]:
                            try:
                                image_index = int(image_index)
                            except Exception:
                                continue

                            if image_index <= 0 or image_index > len(image_names):
                                continue
                            if flag[image_index - 1] == 1:
                                continue

                            url = image_names[image_index - 1]
                            flag[image_index - 1] = 1
                            url = self.image_dir + "/" + url
                            image_url_content += url + ", "

                        image_url_content = image_url_content.rstrip(", ")
                        if len(result[key]) == 0 or image_url_content == "":
                            image_url_content = None
                        result[key] = image_url_content
                used_image_indexes = {i + 1 for i, used in enumerate(flag) if used == 1}

            document = Document(**result, is_vectorized=0)

            for i in range(len(image_urls)):
                if i + 1 not in used_image_indexes:
                    if os.path.exists(image_urls[i]):
                        os.remove(image_urls[i])
                    dir_name, filename = os.path.split(image_urls[i])
                    name, ext = os.path.splitext(filename)
                    compressed_path = os.path.join(dir_name, f"{name}_compressed{ext}")
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


markdown_parser = MarkdownParser()


if __name__ == "__main__":
    document = markdown_parser.parse(
        r"D:\Maintenance_Assistance_System\datasets\output_guides\2964_1999-2004 Volkswagen Golf Windshield Wipers Replacement.md"
    )
