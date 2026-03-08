import base64
import json
import uuid
from datetime import datetime
from models import Document
import pymupdf
import os
from sqlalchemy.orm import Session
from openai import OpenAI
from database import get_db
from utils.VectorService import VectorService


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

    def parse(self, pdf_url: str):
        text = self.get_pdf_text(pdf_url)
        image_urls, image_names = self.get_pdf_images(pdf_url)
        document = self.file2document(text, image_urls, image_names)

        return document

        # self.db.refresh(document)
        #
        # print(document)
        # print(document.image_urls_problem_intro)
        # print(document.image_urls_inspection)


    def get_pdf_text(self, pdf_url: str):
        if not os.path.exists(pdf_url):
            raise FileNotFoundError(pdf_url)
        doc = pymupdf.open(pdf_url)
        text = ""
        for page in doc:
            text += page.get_text().strip()
        # print(text)
        return text

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
            if image_list:
                print(f"在第 {page_index} 页找到 {len(image_list)} 张图片")
            else:
                print(f"第 {page_index} 页未找到图片")

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
        prompt = """你好，你是一位问题分析专家，我将给你一段有关设备维修的文本和几张图片。请你根据内容，按照以下的模板提供信息，以JSON格式返回。除了标题，若内容未提供，字段可以为空。对于图片，请给出图片编号（注意，我提供的图片从1开始编号）。
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
    "title": "标题",
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
4. 我提供的图片编号从1开始，不得编写新的图片。

现在请分析下面的内容：
[文本内容]
{text}

[图片内容由后续base64给出]
""".format(text=text)
        msg_content = [{"type": "text", "text": prompt}]
        for image in image_urls:
            print(image)
            image_base64 = self.image_to_base64(image)
            msg_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
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

            for key in result.keys():
                if "image" in key:
                    image_url_content = ""
                    for image_index in result[key]:
                        url = image_names[image_index - 1]
                        url = self.image_dir + "/" + url
                        image_url_content += url + ", "
                    image_url_content = image_url_content.rstrip(", ")
                    if len(result[key]) == 0:
                        image_url_content = None
                    result[key] = image_url_content
            document = Document(**result,
                                is_vectorized=0)
            return document

        except Exception as e:
            print(e)
            for image in image_urls:
                if os.path.exists(image):
                    os.remove(image)

pdf_parser = PdfParser()

if __name__ == "__main__":
    pdf_parser = PdfParser()
    # text = pdf_parser.get_pdf_text("D:\机密\毕设\文献翻译及开题报告\开题报告.pdf")
    # pdf_parser.get_pdf_images("D:\机密\毕设\文献翻译及开题报告\开题报告.pdf")
    pdf_parser.parse("D:\机密\毕设\开发\知识库文档\T7-结晶问题-TS红宝书.pdf")
