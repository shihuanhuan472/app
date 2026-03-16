import base64
import json
import uuid
from datetime import datetime
from PIL import Image
from openai import OpenAI

from models import Document
from pptx import Presentation
import os

"""
解析ppt，使用python-pptx提取ppt中的图像和文本
"""

class PPTParser:
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

    def parse(self, file_path: str):
        text, image_urls, image_names = self.get_content(file_path)
        document = self.file2document(text, image_urls, image_names)
        return document

    def get_content(self, file_path):
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        prs = Presentation(file_path)
        text = ""
        image_urls = []
        image_names = []
        base_url = os.path.join(self.document_base_dir, self.image_dir)
        for slide in prs.slides:
            for shape in slide.shapes:
                try:
                    if shape.has_text_frame:
                        for paragraph in shape.text_frame.paragraphs:
                            text += "\n" + paragraph.text
                    if hasattr(shape, "shape_type") and shape.shape_type == 13:
                        image = shape.image
                        image_bytes = image.blob
                        ext = image.ext or "png"

                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        unique_filename = f"{timestamp}_{uuid.uuid4().hex}.{ext}"

                        image_path = os.path.join(base_url, unique_filename)
                        with open(image_path, "wb") as img_file:
                            img_file.write(image_bytes)
                        print(f"已保存: {unique_filename}")
                        image_urls.append(image_path)
                        image_names.append(unique_filename)
                except Exception as e:
                    continue
        return text, image_urls, image_names

    def image_to_base64(self, image: str):
        with open(image, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")
            return image_base64

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

    def generate_message(self, text, image_urls):
        messages = []
        data = {}
        # print(text)
        prompt = """你是一位专业的设备维修分析专家。我将给你一段关于设备维修的文本（包含文字描述）以及若干张相关图片，每张图片都有唯一的编号（从1开始）且只属于一个字段。你的任务是基于这些内容，严格按照以下模板生成JSON格式的总结。请确保所有信息均来源于提供的文本和图片，不得杜撰。对于缺失的信息，对应字段留空（文本为空字符串，图片为空列表）。
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
        # l = len(prompt)
        # print(prompt)
        msg_content = [{"type": "text", "text": prompt}]
        for image in image_urls:
            # print(image)
            compress_image = self.compress_image(image)
            image_base64 = self.image_to_base64(compress_image)
            msg_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}})
            # l += len(image_base64)
        data["role"] = "user"
        data["content"] = msg_content
        messages.append(data)
        # print("len = {}".format(l))
        return messages

    def file2document(self, text, image_urls, image_names) -> Document:
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
            for key in result.keys():
                if "image" in key:
                    image_url_content = ""
                    for image_index in result[key]:
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

ppt_parser = PPTParser()

if __name__ == "__main__":
    pass