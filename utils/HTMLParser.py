import json
import mimetypes
import os
import uuid
from datetime import datetime

from openai import OpenAI
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, unquote
from email import policy
from email.parser import BytesParser
import base64
from PIL import Image
from qwen_token_counter import get_token_count
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from models import Document


"""
HTML解析器，用于html和mhtml文件导入解析
本质上是使用beautifulsoup等方法提取文件中的图像和文本
"""

class HTMLParser:
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.decorative_keywords = ['icon', 'logo', 'btn', 'background']
        self.allow_image = ['.jpg', '.jpeg', '.png', '.bmp', '.webp']
        base_url = os.path.join(self.document_base_dir, self.document_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")

    def parse(self, file_path):
        text, image_urls, image_names = self.get_content(file_path)
        document = self.file2document(text, image_urls, image_names)
        return document

    def should_download_image(self, img_tag):
        """
        判断一个 img 标签是否应该被下载（内容图片）。
        其实不保证，但是html往往图片元素太多了，只能初筛一下
        """
        src = img_tag.get('src', '')
        alt = img_tag.get('alt', '').strip()
        # 存在非空 alt 属性
        if alt:
            return True
        # 检查 src 中是否包含无关图片常见关键词
        if any(k in src.lower() for k in self.decorative_keywords):
            return False

        return True

    def get_rendered_html(self, file_path, wait_seconds=3):
        """
        用浏览器把文件完全渲染完，进而得到各元素
        （如果只是靠源码中的src去下载图片，会发现所有重要图片都需要登录权限，访问src会自动跳转登录界面，无法下载）
        所以想要导入html文件，请确保有chrome浏览器
        """
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        driver.get(f"file://{file_path}")
        driver.implicitly_wait(wait_seconds)  # 等待页面加载
        html = driver.page_source
        driver.quit()
        return html

    # async def get_rendered_html(self, file_path, wait_seconds=3):
    #     """异步获取渲染后的 HTML"""
    #     async with async_playwright() as p:
    #         browser = p.chromium.launch(headless=True)
    #         page = browser.new_page()
    #         page.goto(f'file://{file_path}')
    #         page.wait_for_timeout(wait_seconds * 1000)
    #         html = page.content()
    #         browser.close()
    #         return html

    def download_image(self, url, output_dir, filename):
        """
        下载网络图片，并验证内容是否为图片。
        返回 True 表示成功，False 表示失败。
        """
        try:
            print(f"开始下载: {url}")
            # 增加超时和流式传输
            response = requests.get(url, stream=True, timeout=10, allow_redirects=True)
            print(f"响应状态码: {response.status_code}")
            print(f"最终 URL: {response.url}")
            print(f"Content-Type: {response.headers.get('content-type')}")

            # 如果状态码不是 200，直接失败
            if response.status_code != 200:
                print(f"下载失败 (HTTP {response.status_code}): {url}")
                return False

            # 检查 Content-Type 是否为图片
            content_type = response.headers.get('content-type', '').lower()
            if not content_type.startswith('image/'):
                print(f"警告：响应内容不是图片 (Content-Type: {content_type})，跳过保存。URL: {url}")
                return False

            # 确定文件扩展名（优先从 URL 提取，其次从 Content-Type 推断）
            parsed = urlparse(url)
            path = parsed.path
            url_ext = os.path.splitext(path)[1].lower()

            if url_ext in self.allow_image:
                ext = url_ext
            else:
                print(f"图片格式不符合要求，不进行保存")
                return False
            # 保存文件
            file_path = os.path.join(output_dir, f"{filename}{ext}")
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            try:
                with Image.open(file_path) as img:
                    img.verify()  # 快速验证
                print(f"图片验证成功: {file_path}")
                return True
            except Exception as e:
                print(f"图片 {file_path} 已损坏，删除: {e}")
                os.remove(file_path)
                return False

        except requests.exceptions.Timeout:
            print(f"下载超时: {url}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"下载异常 {url}: {e}")
            return False
        except Exception as e:
            print(f"未知错误 {url}: {e}")
            return False

    def _clean_url(self, src):
        """
        清理可能被 quoted-printable 污染的 URL
        问就是导入的时候发现根本下不了，然后发现src都有问题
        """
        if not src:
            return src
        src = re.sub(r'^3D"?', '', src)
        src = src.strip('"\'')
        src = src.lstrip('=')
        src = unquote(src)
        return src

    def get_content(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        # with open(file_path, "r", encoding="utf-8") as f:
        #     html_content = f.read()
        file_ext = file_path.split(".")[-1]
        image_urls = []
        image_names = []
        html_content = self.get_rendered_html(file_path)
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        text_content = [line.strip() for line in text.splitlines() if line.strip()]
        text_content = '\n'.join(text_content)

        # mhtml和html的图片存储方式不一样
        # mhtml会把图片编码存在源码里，因此需要单独处理
        if file_ext == "mhtml":
            image_urls, image_names = self.get_image_from_mhtml_with_filter(file_path, soup)
        else:
            img_origin_urls = []
            for image in soup.find_all("img"):
                if not self.should_download_image(image):
                    continue
                img_url = image.get('src')
                if not img_url:
                    continue
                img_origin_urls.append(self._clean_url(img_url))

            base_url = os.path.join(self.document_base_dir, self.image_dir)
            for img in img_origin_urls:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_filename = f"{timestamp}_{uuid.uuid4().hex}"
                if self.download_image(img, base_url, unique_filename):
                    image_urls.append(os.path.join(base_url, unique_filename))
                    image_names.append(unique_filename)

        # 因千问模型无法输入svg图像，因此svg提取被我删掉了（本来写了，结果问答一直报错）

        return text_content, image_urls, image_names

    def get_image_from_mhtml_with_filter(self, mhtml_url, soup):
        """
        从 MHTML 文件中提取图片，并使用 soup 中的 img 标签进行过滤。
        """
        with open(mhtml_url, 'rb') as fp:
            msg = BytesParser(policy=policy.default).parse(fp)

        # 构建 Content-Location -> 图片数据 的映射
        image_map = {}
        for part in msg.walk():
            if part.get_content_type().startswith('image/'):
                content_location = part.get('Content-Location')
                if not content_location:
                    continue
                payload = part.get_payload(decode=True)
                if payload is None:
                    payload_str = part.get_payload()
                    if part.get('Content-Transfer-Encoding') == 'base64':
                        try:
                            payload = base64.b64decode(payload_str)
                        except Exception:
                            continue
                if payload:
                    image_map[content_location] = payload

        image_urls = []
        image_names = []
        base_url = os.path.join(self.document_base_dir, self.image_dir)

        # 遍历页面中的 img 标签，匹配并保存
        for img_tag in soup.find_all('img'):
            src = img_tag.get('src')
            if not src or src not in image_map:
                continue
            if not self.should_download_image(img_tag):
                continue

            # 确定扩展名（从 MIME 部分获取 Content-Type）
            content_type = None
            for part in msg.walk():
                if part.get('Content-Location') == src:
                    content_type = part.get_content_type()
                    break
            if content_type:
                main_type = content_type.split(';')[0].strip()
                ext = mimetypes.guess_extension(main_type) or f".{main_type.split('/')[-1]}"
            else:
                ext = '.bin'

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{uuid.uuid4().hex}{ext}"
            filepath = os.path.join(base_url, filename)
            with open(filepath, 'wb') as f:
                f.write(image_map[src])
            # 验证图片有效性
            try:
                with Image.open(filepath) as img:
                    img.verify()
                print(f"MHTML图片验证成功: {filepath}")
                image_urls.append(filepath)
                image_names.append(filename)
            except Exception as e:
                print(f"MHTML图片 {filepath} 损坏，删除: {e}")
                os.remove(filepath)
                continue  # 跳过此图片

        return image_urls, image_names

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

    def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)):
        # 图片压缩，最终尺寸（512, 512），是等比压缩，空白部分填充黑色
        # 问就是一次问答的token数是有限的，只有8192
        if not os.path.exists(image_path):
            raise FileNotFoundError()

        try:
            image = Image.open(image_path).convert("RGB")
        except Exception as e:
            print(f"无法打开图片 {image_path}: {e}")
            return None

        try:
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
            new_path = f"{name}_compressed{ext}"
            new_path = os.path.join(dir_name, new_path)

            new_image.save(new_path)
            print(f"压缩成功: {new_path}")
            return new_path
        except Exception as e:
            print(f"压缩图片 {image_path} 失败: {e}")
            return None

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
1. 内容中不包含的信息，对应字段可以为空，若不包含图片，图片为空列表[]。
2. 内容必须基于我提供的文本和图片，图片编号从1开始，不可杜撰任何信息。
3. 你给出的回答仅包含我要求的JSON格式答案。
4. 给定图片中可能包含无关图片，请勿放进回答中。
5. 每张图片最多出现在一个字段中。
6. 内容需连贯详细，最大限度使用给定内容，请勿过分精简。
7. 各字段内容请勿大量重复，无关图片不要放入回答。

现在请分析下面的内容：
[文本内容]
{text}

[图片内容由base64给出]""".format(text=text)
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

    def file2document(self, text, image_urls, image_names) -> Document:
        """
        让ai根据文本和图像，总结成document对应的字段，进而生成Document对象
        """
        try:
            client = OpenAI(
                base_url=f"http://{self.ai}:8000/v1",
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

            # ai回答的是图像的id，从id得到路径，并从服务器中删除无用图片
            for key in result.keys():
                if "image" in key:
                    image_url_content = ""
                    for image_index in result[key]:
                        if image_index > len(image_names) or flag[image_index - 1] == 1:
                            continue
                        url = image_names[image_index - 1]
                        url = self.image_dir + "/" + url
                        image_url_content += url + ", "
                        flag[image_index - 1] = 1
                    image_url_content = image_url_content.rstrip(", ")
                    if len(result[key]) == 0:
                        image_url_content = None
                    result[key] = image_url_content
            document = Document(**result,
                                is_vectorized=0)

            if len(image_urls) > 0:
                for i in range(len(image_urls)):
                    if flag[i] == 0:
                        os.remove(image_urls[i])
                        dir_name, filename = os.path.split(image_urls[i])
                        name, ext = os.path.splitext(filename)
                        new_path = f"{name}_compressed.{ext}"
                        new_path = os.path.join(dir_name, new_path)
                        if os.path.exists(new_path):
                            os.remove(new_path)

            return document

        except Exception as e:
            print(e)
            for image in image_urls:
                if os.path.exists(image):
                    os.remove(image)

html_parser = HTMLParser()

if __name__ == "__main__":
    pass
