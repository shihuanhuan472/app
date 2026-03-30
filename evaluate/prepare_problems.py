import requests
import os
import re
import json
import pymupdf
from docx import Document as Docx

"""
根据文档生成问题的
"""

def get_text_by_docx(file_path):
    doc = Docx(file_path)
    text = ""
    # 文本
    for para in doc.paragraphs:
        text = text + str(para.text).strip()
    return text

def get_text_by_pdf(file_path):
    doc = pymupdf.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text().strip()
    # print(text)
    return text

def get_text(file_path):
    if not os.path.exists(file_path):
        return ""

    name, ext = os.path.splitext(file_path)
    if ext == ".docx":
        return get_text_by_docx(file_path)
    elif ext == ".pdf":
        return get_text_by_pdf(file_path)
    else:
        print(f"不支持的文件类型：{file_path}")
        return ""

def get_problem_answer_by_ai(file_path: str):
    print(f"开始分析{file_path}")
    # if not os.path.exists(save_path):
    #     os.makedirs(save_path)

    text = get_text(file_path)
    print(f"内容解析完成{file_path}")
    if text == None or len(text) == 0:
        print("text是None")
    url = "https://spark-api-open.xf-yun.com/v2/chat/completions"
    data = {
        "max_tokens": 1000,
        "top_k": 4,
        "temperature": 0.8,
        "model": "spark-x",
        "messages": [
            {
                "role": "user",
                "content": """请根据以下内容，按照我给定的模板生成2个问答对，要求严格遵循文档内容，不得私自编撰，并使用中文。
【模板】：
[
    {{
        "question": "xxx",
        "ground_truth": "xxx"
    }},
    {{
        "question": "xxx",
        "ground_truth": "xxx"
    }}
]
【内容】：
{text}
【注意】：
1. 严格按照模板格式回答。
2. 问题需准确且包含具体设备名称，需要能从大量文档中定位到该文档。
3. 请勿出现“该”，“这”等词汇，需具体指明设备类型，名称或品牌。
""".format(text=text)
            }
        ],
        "stream": False
    }
    header = {
        "Authorization": "Bearer IeGvgGqUjCvTljREtNTM:cjixgGtnVrsCwPvXwbia"  # 注意此处把“123456”替换为自己的APIPassword
    }
    response = requests.post(url, headers=header, json=data, stream=True)
    if response.status_code == 200:
        try:

            # 流式响应解析示例
            response.encoding = "utf-8"
            # print(response)
            # print("============")
            # print(response.text)

            outer = json.loads(response.text)
            content_str = outer['choices'][0]['message']['content']
            json_str = re.sub(r'^```json\s*|\s*```$', '', content_str, flags=re.MULTILINE).strip()
            inner_data = json.loads(json_str)
            print("-----------")
            print(inner_data)
            for d in inner_data:
                d["filename"] = os.path.basename(file_path)

            return inner_data
        except json.decoder.JSONDecodeError:
            print(response.text)
            return None
    else:
        print(response.text)
        print("ai响应码非200")
        return None

def prepare_problems_answers(save_path, file_dir, file_paths):
    if not os.path.exists(save_path):
        with open(save_path, 'w', encoding='utf-8') as f:
            pass  # 创建空文件，也可以写入初始内容
        print(f"文件已创建: {save_path}")
    with open(save_path, "r", encoding='utf-8') as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                dataset = []
        except json.JSONDecodeError:
            # 文件损坏或为空，重新初始化为空列表
            dataset = []

    print(len(dataset))
    filename_done = [data["filename"] for data in dataset]

    cnt = 0

    for file in os.listdir(file_dir):
        filepath = os.path.join(file_dir, file)
        if filepath not in file_paths:
            print(f"{filepath}未向量化，跳过")
            continue
        if os.path.isfile(filepath):
            filename = os.path.basename(filepath)
            if filename in filename_done:
                print(f"{filename}已解析")
                continue
            data = get_problem_answer_by_ai(filepath)
            if data == None:
                print(f"{filename}解析出来为None")
                continue
            dataset.extend(data)
            cnt += 1
        if cnt % 10 == 0:
            print(f"已解析{cnt}个文档")
            with open(save_path, "w", encoding='utf-8') as f:
                json.dump(dataset, f, ensure_ascii=False, indent=4)

    print(len(dataset))

    with open(save_path, "w", encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":

    url = "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files_data.json"
    with open(url, "r", encoding='utf-8') as f:
        data = json.load(f)
    file_paths = [d["file_path"] for d in data]

    # response = get_problem_answer_by_ai("D:\机密\毕设\开发\数据集\电子产品\PS Vita Slim Unresponsive Black Screen.pdf")
    prepare_problems_answers("D:\Pycharm\code\Maintenance_Assistance_System\datasets\data.json",
                             "D:\机密\毕设\开发\数据集\新能源汽车维修故障案例", file_paths)