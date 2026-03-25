import json
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
from datetime import datetime

from database import SessionLocal
from datasets import Dataset

from utils.HTMLParser import html_parser
from utils.PPTParser import ppt_parser
from utils.PdfParser import pdf_parser
from utils.VectorService import VectorService
from utils.WordParser import word_parser

questions = []
ground_truth = []
answers = []
contexts = []
db = SessionLocal()
vector_service = VectorService(db)

def add_file(file_path):
    try:
        file_ext = file_path.split(".")[-1]
        file_ext = "." + file_ext
        document = None

        # 根据不同的文件类型调用不同的解析器
        if file_ext == ".pdf":
            document = pdf_parser.parse(file_path)
        elif file_ext == ".pptx" or file_ext == ".ppt":
            document = ppt_parser.parse(file_path)
        elif file_ext == ".html" or file_ext == ".mhtml":
            # document = await asyncio.to_thread(html_parser.parse(url))
            document = html_parser.parse(file_path)
        elif file_ext == ".docx":
            document = word_parser.parse(file_path)
        if not document.title:
            if os.path.exists(file_path):
                os.remove(file_path)
            return False
        document.contributor_id = 1
        document.origin_file_name = os.path.basename(file_path)
        # document.origin_file_dir = file
        document.first_edit_date = datetime.now()
        print(document.title)
        db.add(document)
        db.commit()
        db.refresh(document)
        vector_service = VectorService(db)
        vector_service.add_document_to_vector_store(document)
        return True
    except Exception as e:
        print(file_path)
        print(e)
        return False


def analyze_files(file_save: str):
    if not os.path.exists(file_save):
        print(f"dir not found!!! {file_save}")
        return
    dataset = []
    with open(file_save, "r", encoding="utf-8") as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                dataset = []
        except json.JSONDecodeError:
            # 文件损坏或为空，重新初始化为空列表
            dataset = []
    success_files = []
    error_files = []
    for data in dataset:
        if data["is_vectorized"] == 1:
            print(f"{data['filename']} 已向量化，跳过")
            continue
        filepath = data["file_path"]
        if add_file(filepath):
            print(f"{filepath} 添加成功！")
            success_files.append(data["filename"])
            data["is_vectorized"] = 1
        else:
            error_files.append(data["filename"])
        if len(success_files) % 10 == 0:
            print(f"len of success_files: {len(success_files)}")
            with open(file_save, "w", encoding="utf-8") as f:
                json.dump(dataset, f, ensure_ascii=False, indent=4)
        if len(success_files) % 20 == 0:
            break

    with open(file_save, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=4)

def prepare_files(file_dir: str, save_file: str):
    if not os.path.exists(file_dir):
        print(f"dir not found!!! {file_dir}")
        return

    if not os.path.exists(save_file):
        with open(save_file, 'w', encoding='utf-8') as f:
            pass
    dataset = []
    with open(save_file, 'r', encoding='utf-8') as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                dataset = []
        except json.JSONDecodeError:
            # 文件损坏或为空，重新初始化为空列表
            dataset = []
    filenames = [data["filename"] for data in dataset]
    for file in os.listdir(file_dir):
        print(file)
        if file in filenames:
            print(f"{file} 已记录，跳过")
            continue
        filepath = os.path.join(file_dir, file)
        data = {
            "filename": file,
            "file_path": filepath,
            "is_vectorized": 0
        }
        dataset.append(data)

    with open(save_file, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=4)

if __name__ == '__main__':
    # prepare_files("D:\机密\毕设\开发\数据集\电子产品",
    #               "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json")
    # prepare_files("D:\机密\毕设\开发\数据集\家电维修",
    #               "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json")
    # prepare_files("D:\机密\毕设\开发\数据集\汽车维修",
    #               "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json")
    # prepare_files("D:\机密\毕设\开发\数据集\新能源汽车维修故障案例",
    #               "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json")

    analyze_files("D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json")
    # add_file("D:\机密\毕设\开发\知识库文档\T7-结晶问题-TS红宝书.pdf")