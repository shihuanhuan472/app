import os
import uuid
from datetime import datetime
from playwright.sync_api import sync_playwright
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, unquote

class HTMLParser:
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 3000))
        base_url = os.path.join(self.document_base_dir, self.document_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")

    def parse(self, file_path):
        pass

    def get_rendered_html(self, file_path, wait_seconds=3):
        with sync_playwright() as p:
            # 启动 Chromium（headless 默认开启）
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            # 加载本地文件
            page.goto(f'file://{file_path}')
            # 等待指定时间，或等待某个元素出现
            page.wait_for_timeout(wait_seconds * 1000)
            # 或者更精确地等待 SVG 出现：
            # page.wait_for_selector('svg', state='attached')
            html = page.content()
            browser.close()
            return html

    def download_image(self, url, output_dir, filename):
        """下载网络图片，智能判断扩展名"""
        try:
            print(url)
            response = requests.get(url, stream=True, timeout=10)
            print("Content-Type:", response.headers.get('content-type'))
            print("Content-Length:", response.headers.get('content-length'))
            print("URL:", response.url)  # 检查是否有重定向
            if response.status_code == 200:
                # 1. 从 URL 路径中提取扩展名（优先）
                parsed_url = urlparse(url)
                path = parsed_url.path
                url_ext = os.path.splitext(path)[1].lower()  # 例如 '.png'
                # 有效图片扩展名列表
                valid_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'}
                if url_ext in valid_exts:
                    ext = url_ext
                else:
                    # 2. 从 Content-Type 推断
                    content_type = response.headers.get('content-type', '').lower()
                    if 'png' in content_type:
                        ext = '.png'
                    elif 'jpeg' in content_type or 'jpg' in content_type:
                        ext = '.jpg'
                    elif 'gif' in content_type:
                        ext = '.gif'
                    elif 'webp' in content_type:
                        ext = '.webp'
                    elif 'svg' in content_type:
                        ext = '.svg'
                    elif 'bmp' in content_type:
                        ext = '.bmp'
                    else:
                        # 后备：使用通用二进制扩展名
                        ext = '.bin'
                        print(f"警告：未知图片类型，保存为 {ext}，URL: {url}")

                file_path = os.path.join(output_dir, f"{filename}{ext}")
                with open(file_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"已下载网络图片: {file_path}")
                return True
            else:
                print(f"下载失败 (HTTP {response.status_code}): {url}")
                return False
        except Exception as e:
            print(f"下载异常 {url}: {e}")
            return False  # 异常时返回 False，不要返回 True 掩盖错误

    def _clean_url(self, src):
        """清理可能被 quoted-printable 污染的 URL"""
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

        html_content = self.get_rendered_html(file_path)
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        text_content = [line.strip() for line in text.splitlines() if line.strip()]
        text_content = '\n'.join(text_content)

        img_content = soup.find_all("img")

        img_origin_urls = []

        image_urls = []
        image_names = []

        for image in img_content:
            img_url = image.get('src')
            if not img_url:
                continue
            img_origin_urls.append(self._clean_url(img_url))
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        for img in img_origin_urls:
            # ext = img.split("?")[0].split(".")[-1] or "png"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{uuid.uuid4().hex}"

            if self.download_image(img, base_url, unique_filename):
                image_urls.append(os.path.join(base_url, unique_filename))
                image_names.append(unique_filename)

        svg_images = soup.find_all("svg")
        print(len(svg_images))
        for svg_image in svg_images:
            try:
                svg_content = svg_image.prettify()

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_filename = f"{timestamp}_{uuid.uuid4().hex}.svg"
                url = os.path.join(base_url, unique_filename)
                with open(url, "w", encoding="utf-8") as f:
                    f.write(svg_content)
                image_urls.append(url)
                image_names.append(unique_filename)
            except Exception as e:
                print(e)
                continue

        return text_content, image_urls, image_names

if __name__ == "__main__":
    html_parser = HTMLParser()
    text_content, image_urls, image_names = html_parser.get_content("D:\机密\毕设\开发\知识库文档\二链Q30下降快 -  - MGI KMS.mhtml")
    print(text_content)
    print(len(image_names))
    #
    # """
    # src="https://confluence.mgi-tech.com/download/attachments/84446478/image-2024-5-21_19-22-20.png?version=1&modificationDate=1716290540697&api=v2"
    # """