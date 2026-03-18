import base64
import json
import mimetypes
import uuid
from datetime import datetime
from PIL import Image
from qwen_token_counter import get_token_count

from models import Document
import pymupdf
import os
from openai import OpenAI

"""
解析pdf文件的，使用了pymupdf提取文本和图像
"""

class PdfParser:
    def __init__(self):
        # self.db = db
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.ai = os.getenv("SERVER_IP", "192.168.246.200")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 3000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        base_url = os.path.join(self.document_base_dir, self.document_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        if not os.path.exists(base_url):
            os.makedirs(base_url)
            print(f"创建目录: {base_url}")

    def parse(self, pdf_url: str):
        text = self.get_pdf_text(pdf_url)
        image_urls, image_names = self.get_pdf_images(pdf_url)
        document = self.file2document(text, image_urls, image_names)

        return document

    def get_pdf_text(self, pdf_url: str):
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)
        doc = pymupdf.open(pdf_url)
        text = ""
        for page in doc:
            text += page.get_text().strip()
        # print(text)
        return text

    def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)):
        if not os.path.exists(image_path):
            raise FileNotFoundError()

        image = Image.open(image_path).convert("RGB")
        # new_size = (448, 448)
        max_length = max(image.width, image.height)
        # if short_length < max_size:
        #     return image_path
        # if max_length <= max_size:
        #     return image_path
        rate = max_size / max_length
        new_size = (int(image.width * rate), int(image.height * rate))
        resized_image = image.resize(new_size)

        new_image = Image.new("RGB", (max_size, max_size), pad_color)

        x = (max_size - new_size[0]) // 2
        y = (max_size - new_size[1]) // 2

        new_image.paste(resized_image, (x, y))

        # new_size = (512, 512)
        # print(new_size)

        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = f"{name}_compressed{ext}"
        new_path = os.path.join(dir_name, new_path)
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
            #     print(f"在第 {page_index} 页找到 {len(image_list)} 张图片")
            # else:
            #     print(f"第 {page_index} 页未找到图片")

            for image_index, img in enumerate(image_list, start=1):  # 遍历图像列表
                xref = img[0]  # 获取图像的 XREF
                pix = pymupdf.Pixmap(doc, xref)  # 创建 Pixmap 对象

                if pix.n - pix.alpha > 3:  # 如果是 CMYK 模式，则先转换为 RGB
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)


                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                unique_filename = f"{timestamp}_{uuid.uuid4().hex}.png"

                url = os.path.join(base_url, unique_filename)

                pix.save(url)  # 以 PNG 格式保存图片
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
        data = {}
        # msg_content = []
        prompt = """你好，你是一位问题分析专家，我将给你一段有关设备维修的文本和几张图片。请你最大限度使用内容，按照以下的模板提供信息，以JSON格式返回。若内容未提供，字段可以为空，但不可缺失字段。对于图片，请给出图片编号。
模板：
标题：<简洁的标题，点明核心内容>
问题简介：<可包含定义解释，现象介绍，问题发生频率，后果等内容>
原因：<造成该问题的主要原因，尽量从高频到低频排序>
评估：<评估问题的手段，方法，工具等信息>
检查：<描述维修现场如何进行定位确认>
解决方法：<现场的解决措施及根本的解决方案>
总结：<总结问题的主要原因，后果及解决方案的关键信息>

输出JSON格式如下：
{{
    "title": "标题", // 案例名
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
2. 内容必须基于我提供的文本和图片，不可杜撰任何信息。
3. 你给出的回答仅包含我要求的JSON格式答案。
4. 给定图片编号从1开始，不得编写新的图片。
5. title不可为空，同一张图片尽量不要出现太多次，即一张图片不要出现在2个以上字段中。
6. 内容需连贯详细，最大限度使用给定内容，请勿过分精简。
7. 检查步骤和解决方案字段若有内容相关，请尽可能详细描述。
8. 各字段内容请勿大量重复。

现在请分析下面的内容：
[文本内容]
{text}

[图片内容由后续base64给出]
""".format(text=text)
        msg_content = [{"type": "text", "text": prompt}]
        # encoding = tiktoken.get_encoding("cl100k_base")
        token_cnt = get_token_count(prompt)

        print(f"token1: {token_cnt}")
        for image in image_urls:
            print(image)
            if token_cnt >= self.input_token - 258:
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
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        return messages

    def file2document(self, text, image_urls, image_names):
        try:
            client = OpenAI(
                base_url=f"http://{self.ai}:8000/v1",
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

            flag = [0] * len(image_names)

            for key in result.keys():
                if "image" in key:
                    image_url_content = ""
                    for image_index in result[key]:
                        url = image_names[image_index - 1]
                        flag[image_index - 1] = 1
                        url = self.image_dir + "/" + url
                        image_url_content += url + ", "
                    image_url_content = image_url_content.rstrip(", ")
                    if len(result[key]) == 0:
                        image_url_content = None
                    result[key] = image_url_content
            document = Document(**result,
                                is_vectorized=0)

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

pdf_parser = PdfParser()

if __name__ == "__main__":
    pass
