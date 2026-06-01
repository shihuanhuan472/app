import base64
import json
import mimetypes
import os
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from PIL import Image
from openai import OpenAI
from qwen_token_counter import get_token_count

# 允许直接运行 utils/TxtParser.py 时，也能找到项目根目录下的 models.py
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models import Document
from utils.ai_endpoint import get_ai_base_url
from utils.error_codes import BizCode


"""
TXT解释器。
TXT 文件本身只能存储纯文本，不能像 Word 一样内嵌图片。
因此这里的图片提取方式是：在 TXT 文本中约定图片路径标记，然后用正则识别这些图片路径。

支持的图片写法示例：
    [image: images/step1.jpg]
    [img: images/step2.png]
    [图片: images/step3.jpg]
    图片：images/step4.jpg
    image: images/step5.jpg

整体流程：
    读取 TXT 文本
    -> 提取 TXT 中的图片路径
    -> 将图片复制到 upload/images
    -> 调用多模态大模型生成结构化 JSON
    -> 转换为 Document 对象
"""


class TxtParser:
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")

        # 本地 OpenAI 兼容接口配置，和你原来的 MarkdownParser 保持一致
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")

        # 建议先不要给太大，避免 JSON 输出被截断
        self.max_token = int(os.getenv("MAX_TOKEN", 3000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.min_output_token = int(os.getenv("MIN_OUTPUT_TOKEN", 128))

        # 可识别的图片格式
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
        text, image_urls, image_names = self.get_content(file_path)
        document = self.file2document(text, image_urls, image_names)
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

    def _truncate_text_by_token_budget(self, text: str, token_budget: int) -> str:
        if not text:
            return ""
        if token_budget <= 0:
            return ""
        try:
            if get_token_count(text) <= token_budget:
                return text
        except Exception:
            approx_len = max(1, token_budget * 2)
            return text[:approx_len]

        left, right = 0, len(text)
        best = ""
        while left <= right:
            mid = (left + right) // 2
            candidate = text[:mid]
            try:
                candidate_tokens = get_token_count(candidate)
            except Exception:
                candidate_tokens = mid // 2

            if candidate_tokens <= token_budget:
                best = candidate
                left = mid + 1
            else:
                right = mid - 1

        if best and len(best) < len(text):
            return best + "\n\n[内容过长，已自动截断]"
        return best or text[: max(1, token_budget)]

    def get_content(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        text = self.read_txt(file_path)
        image_refs = self.extract_image_refs(text)

        image_urls = []
        image_names = []

        txt_dir = os.path.dirname(os.path.abspath(file_path))
        output_dir = os.path.join(self.document_base_dir, self.image_dir)

        for image_ref in image_refs:
            image_path = self.save_image_ref(image_ref, txt_dir, output_dir)
            if image_path is None:
                continue

            image_urls.append(image_path)
            image_names.append(os.path.basename(image_path))

        return text, image_urls, image_names

    def read_txt(self, file_path):
        """
        TXT 常见编码包括 utf-8、utf-8-sig、gbk。
        这里依次尝试，避免中文 TXT 乱码或直接报错。
        """
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    def extract_image_refs(self, text):
        """
        从 TXT 中提取图片路径。

        推荐 TXT 中写法：
            [image: images/a.jpg]
            [img: images/b.png]
            [图片: images/c.jpg]

        也支持单独一行：
            图片：images/d.jpg
            image: images/e.jpg
            img: images/f.jpg
        """
        refs = []
        seen = set()

        # 写法1：[image: xxx] / [img: xxx] / [图片: xxx]
        pattern_bracket = r"\[(?:image|img|图片)\s*[:：]\s*([^\]]+)\]"
        for match in re.finditer(pattern_bracket, text, flags=re.IGNORECASE):
            src = self.clean_txt_image_src(match.group(1))
            if src and src not in seen:
                refs.append(src)
                seen.add(src)

        # 写法2：单独一行 image: xxx / img: xxx / 图片：xxx
        # 要求必须是一整行，避免误识别正文里的普通冒号。
        pattern_line = r"^\s*(?:image|img|图片)\s*[:：]\s*(.+?)\s*$"
        for match in re.finditer(pattern_line, text, flags=re.IGNORECASE | re.MULTILINE):
            src = self.clean_txt_image_src(match.group(1))
            if src and src not in seen:
                refs.append(src)
                seen.add(src)

        return refs

    def clean_txt_image_src(self, raw_src):
        """
        清洗 TXT 中写的图片路径。
        支持：
            images/a.jpg
            "images/a.jpg"
            'images/a.jpg'
            <images/a.jpg>
            images/a.jpg # 注释
            images/a.jpg?x=1
        """
        if raw_src is None:
            return None

        src = raw_src.strip()
        if not src:
            return None

        # 去掉尖括号
        if src.startswith("<") and ">" in src:
            src = src[1:src.index(">")].strip()

        # 去掉首尾引号
        if (src.startswith('"') and '"' in src[1:]) or (src.startswith("'") and "'" in src[1:]):
            quote = src[0]
            end = src.find(quote, 1)
            if end > 0:
                src = src[1:end].strip()

        # 去掉行尾注释：images/a.jpg # xxx
        src = re.sub(r"\s+#.*$", "", src).strip()

        # 如果写成 images/a.jpg "说明文字"，尽量保留前面的图片路径
        title_match = re.match(
            r"(.+?\.(?:jpg|jpeg|png|bmp|webp))\s+[\"'].*[\"']\s*$",
            src,
            flags=re.IGNORECASE
        )
        if title_match:
            src = title_match.group(1).strip()

        # 去掉 query 和 fragment
        src = src.split("#")[0].split("?")[0].strip()

        return src

    def save_image_ref(self, image_ref, txt_dir, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        image_ref = image_ref.strip()

        if image_ref.startswith("data:image/"):
            return self.save_data_uri_image(image_ref, output_dir)

        parsed = urlparse(image_ref)

        if parsed.scheme in ("http", "https"):
            return self.download_image(image_ref, output_dir)

        return self.copy_local_image(image_ref, txt_dir, output_dir)

    def copy_local_image(self, image_ref, txt_dir, output_dir):
        parsed = urlparse(image_ref)

        # 兼容 file:///D:/xxx/a.jpg
        if parsed.scheme == "file":
            raw_path = unquote(parsed.path)
        else:
            raw_path = unquote(parsed.path or image_ref)

        # Windows 下 file:///D:/xxx 可能变成 /D:/xxx
        if re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]

        source_path = Path(raw_path)

        if not source_path.is_absolute():
            source_path = Path(txt_dir) / source_path

        source_path = source_path.resolve()

        if not source_path.exists() or not source_path.is_file():
            print(f"txt image not found: {source_path}")
            return None

        ext = source_path.suffix.lower()
        if ext not in self.allow_image:
            print(f"unsupported txt image format: {source_path}")
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
                print(f"txt image download failed HTTP {response.status_code}: {url}")
                return None

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = mimetypes.guess_extension(content_type) or Path(urlparse(url).path).suffix.lower()

            if ext == ".jpe":
                ext = ".jpg"

            if ext not in self.allow_image:
                print(f"unsupported remote txt image format: {url}")
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
            print(f"txt image download error: {url}, {e}")
            return None

    def save_data_uri_image(self, data_uri, output_dir):
        try:
            header, payload = data_uri.split(",", 1)
            mime_type = header.split(";")[0].replace("data:", "")
            ext = mimetypes.guess_extension(mime_type) or ".png"

            if ext == ".jpe":
                ext = ".jpg"

            if ext not in self.allow_image:
                print(f"unsupported data uri txt image format: {mime_type}")
                return None

            target_path = os.path.join(output_dir, self.new_image_filename(ext))

            with open(target_path, "wb") as f:
                f.write(base64.b64decode(payload))

            if not self.verify_image(target_path):
                return None

            return target_path

        except Exception as e:
            print(f"txt data uri image parse error: {e}")
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
            print(f"invalid txt image removed: {image_path}, {e}")
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

    def generate_message(self, text: str = None, image_urls=None):
        messages = []
        data = {}
        reserved_image_tokens = 258 * len(image_urls or [])
        text_budget = max(256, self.input_token - self.max_token - reserved_image_tokens - 1200)
        safe_text = self._truncate_text_by_token_budget(text or "", text_budget)

        prompt = """你是一位专业的设备维修分析专家。我将给你一段关于设备维修的TXT文本以及若干张相关图片，每张图片都有唯一的编号（从1开始）且只属于一个字段。你的任务是基于这些内容，严格按照以下模板生成JSON格式的总结。请确保所有信息均来源于提供的文本和图片，不得杜撰。对于缺失的信息，对应字段留空，但不可缺失字段（文本为空字符串，图片为空列表）。

模板：
标题：<简洁的标题，点明案例名称，必须填写>
问题简介：<可包含定义解释，现象介绍，问题发生频率，后果等内容>
原因：<造成该问题的主要原因，尽量从高频到低频排序>
评估：<评估问题的手段，方法，工具等信息>
检查：<描述维修现场如何进行定位确认，要求详细>
解决方法：<现场的解决措施及根本的解决方案，要求详细>
总结：<总结问题的主要原因，后果及解决方案的关键信息>
相关图片：<与字段相关的图像，每张图像最多出现在一个字段中>

请严格按照如下JSON格式输出，注意不要输出注释、解释或代码块：
{{
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
}}

注意：
1. 内容中不包含的信息，对应字段可以为空，若不包含图片，图片为空列表[]。
2. 内容必须基于我提供的文本和图片，图片编号从1开始，不可杜撰任何信息。
3. 你给出的回答仅包含我要求的JSON格式答案。
4. 给定图片中可能包含无关图片，请勿放进回答中。
5. 每张图片最多出现在一个字段中。
6. 内容需连贯详细，最大限度使用给定内容，请勿过分精简。
7. 各字段内容请勿大量重复，无关图片不要放入回答。

现在请分析下面的内容：
[TXT文本内容]
{text}

[图片内容由base64给出]""".format(text=safe_text or "")

        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = get_token_count(prompt)

        print(f"token1: {token_cnt}")

        for image in image_urls or []:
            if token_cnt >= self.input_token - 258:
                print(f"token接近上限，停止添加图片: {token_cnt}")
                break

            print(image)

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

        return messages, token_cnt

    def file2document(self, text, image_urls, image_names):
        try:
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )

            messages, token_cnt = self.generate_message(text, image_urls)
            max_output_tokens = min(self.max_token, self.input_token - token_cnt - 16)
            if max_output_tokens < self.min_output_token:
                self._set_last_error(
                    BizCode.DOC_TOKEN_LIMIT_EXCEEDED,
                    f"输入内容过长，剩余可用输出 token 不足: {max_output_tokens}"
                )
                raise ValueError("token budget too small for output")

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_output_tokens
            )

            ans = response.choices[0].message.content

            print(ans)

            ans_clean = ans.strip()
            ans_clean = re.sub(r"^```json\s*", "", ans_clean)
            ans_clean = re.sub(r"^```\s*", "", ans_clean)
            ans_clean = re.sub(r"\s*```$", "", ans_clean)

            result = json.loads(ans_clean)
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

            document = Document(**result, is_vectorized=0)

            # 删除没有被大模型选中的图片，和 MarkdownParser 逻辑保持一致
            for i in range(len(image_urls)):
                if flag[i] == 0:
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
            if self.last_error_code is None:
                if self._is_ai_service_unavailable_error(e):
                    self._set_last_error(BizCode.AI_SERVICE_UNAVAILABLE, "AI服务不可用，请稍后重试")
                else:
                    self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))

            for image in image_urls:
                if os.path.exists(image):
                    os.remove(image)

            return None


txt_parser = TxtParser()


if __name__ == "__main__":
    # 测试时把这里改成你的 TXT 文件路径
    document = txt_parser.parse(
        r"D:\Maintenance_Assistance_System\datasets\output_guides\2964_1999-2004 Volkswagen Golf Windshield Wipers Replacement.txt"
    )

    if document is None:
        print("[ERROR] TXT 解析失败，Document 没有生成")
    else:
        print("[OK] TXT 解析成功")
        print("标题:", document.title)
        print("问题简介:", document.problem_intro)
        print("原因:", document.causes)
        print("评估:", document.evaluation)
        print("检查:", document.inspection)
        print("解决方法:", document.solutions)
        print("总结:", document.key_points)
