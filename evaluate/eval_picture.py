import base64
import json
import os
from typing import List, Set, Dict, Any
from database import SessionLocal
from models import Document
from utils.VectorService import VectorService
from openai import OpenAI

db = SessionLocal()
vector_service = VectorService(db)

"""
data.json：文本检索时，根据文件提问题记录
document_images.json：数据库中所有图片提取记录
document_images_retrieve.json：初步分块检索结果
document_images_retrieve1.json：好像和前一个一样，我忘了是干嘛的了
document_images_retrieve_new.json：若图片单独分块，检索数据库中图片结果（好到爆炸了属于是）
final_data.json：文本问答时模型回答结果及上下文检索结果，因prompt未提及答案精炼，所以答案口语化严重，过于冗余，未采用
final_data_1.json：文本检索的最终数据集
final_data_2.json：未使用RAG检索的模型回答，作为对比
files.json：拿来记录文档的
files_data.json：记录文档导入流程的
files_langchain.json：拿来记录langchain导入文档的流程
final_data_langchain.json：langchain的最终数据集
images_data.json：标注图片路由和相关文档的
images_retrieve.json：图像检索，初始分块，top-8，阈值0.4
image_retrieve_main_chunk.json：仅增加main_chunk（略微提升）
images_retrieve_main_chunk_vision.json：增加图像ai提取内容（初代prompt）
images_retrieve_new.json：图像检索，图片单独分块，top-8，阈值0.4
precision_1.json：文本检索答案正确率
precision_2.json：无RAG，文本检索答案正确率
precision_langchain.json：langchain，文本检索答案正确率
"""


def precision_at_k(retrieved_ids: List[Any], relevant_ids: Set[Any], k: int) -> float:
    """计算Precision@k"""
    if k <= 0:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for doc_id in top_k if doc_id in relevant_ids)
    return hits / k

def recall_at_k(retrieved_ids: List[Any], relevant_ids: Set[Any], k: int) -> float:
    """计算Recall@k"""
    if not relevant_ids:
        return 0.0  # 如果没有相关文档，召回率无定义，通常按0处理
    top_k = retrieved_ids[:k]
    hits = sum(1 for doc_id in top_k if doc_id in relevant_ids)
    return hits / len(relevant_ids)

def f1_at_k(retrieved_ids: List[Any], relevant_ids: Set[Any], k: int) -> float:
    """计算F1@k"""
    p = precision_at_k(retrieved_ids, relevant_ids, k)
    r = recall_at_k(retrieved_ids, relevant_ids, k)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)

def average_precision(retrieved_ids: List[Any], relevant_ids: Set[Any]) -> float:
    """计算单个查询的Average Precision (AP)"""
    if not relevant_ids:
        return 0.0
    hits = 0
    sum_precisions = 0.0
    for i, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_ids:
            hits += 1
            precision_at_i = hits / i
            sum_precisions += precision_at_i
    return sum_precisions / len(relevant_ids)

def mean_reciprocal_rank(retrieved_ids_list: List[List[Any]], ground_truth: List[Set[Any]]) -> float:
    """计算MRR (Mean Reciprocal Rank)"""
    assert len(retrieved_ids_list) == len(ground_truth)
    rr_sum = 0.0
    for retrieved_ids, relevant_ids in zip(retrieved_ids_list, ground_truth):
        for rank, doc_id in enumerate(retrieved_ids, start=1):
            if doc_id in relevant_ids:
                rr_sum += 1.0 / rank
                break
    return rr_sum / len(retrieved_ids_list)

def mean_average_precision(retrieved_ids_list: List[List[Any]], ground_truth: List[Set[Any]]) -> float:
    """计算MAP (Mean Average Precision)"""
    assert len(retrieved_ids_list) == len(ground_truth)
    ap_sum = 0.0
    for retrieved_ids, relevant_ids in zip(retrieved_ids_list, ground_truth):
        ap_sum += average_precision(retrieved_ids, relevant_ids)
    return ap_sum / len(retrieved_ids_list)

def exact_picture(save_url: str):
    if not os.path.exists(save_url):
        with open(save_url, 'w', encoding='utf-8') as f:
            pass
    pic_attrs = ["image_urls_problem_intro", "image_urls_causes", "image_urls_evaluation",
                 "image_urls_inspection", "image_urls_solutions", "image_urls_key_points"]

    documents = db.query(Document).filter(Document.id < 451).all()

    data = []

    for document in documents:
        for attr in pic_attrs:
            v = getattr(document, attr)
            if v is not None:
                images = v.split(",")
                for image in images:
                    if len(image.strip()) > 1:
                        data.append({
                            "image_url": image.strip(),
                            "document_id": document.id,
                            "document_filename": document.origin_file_name
                        })

    print(f"len: {len(data)}")

    with open(save_url, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def retrieve(pic_file: str, save_url: str):
    with open(pic_file, 'r', encoding='utf-8') as f:
        pictures = json.load(f)

    saved_data = []

    with open(save_url, 'r', encoding='utf-8') as f:
        try:
            saved_data = json.load(f)
            if not isinstance(saved_data, list):
                saved_data = []
        except json.decoder.JSONDecodeError:
            saved_data = []
        # saved_data = json.load(f)

    saved_image = [d['image_url'] for d in saved_data]
    cnt = 0
    for pic in pictures:
        if pic['image_url'] in saved_image:
            print(f"{pic['image_url']}已检索，跳过")
            continue
        print(f"开始检索{pic['image_url']}")
        documents = vector_service.search_similar_documents("", pic["image_url"])
        saved_data.append({
            "image_url": pic['image_url'],
            "document_id": pic['document_id'],
            "document_filename": pic['document_filename'],
            "context": documents
        })
        cnt += 1
        if cnt % 10 == 0:
            print(f"已检索{cnt}个图像")
            with open(save_url, 'w', encoding='utf-8') as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            # break
        # if cnt % 100 == 0:
        #     break

    with open(save_url, 'w', encoding='utf-8') as f:
        json.dump(saved_data, f, ensure_ascii=False, indent=4)


def retrieve_new(pic_file: str, save_url: str):
    with open(pic_file, 'r', encoding='utf-8') as f:
        pictures = json.load(f)

    saved_data = []

    with open(save_url, 'r', encoding='utf-8') as f:
        try:
            saved_data = json.load(f)
            if not isinstance(saved_data, list):
                saved_data = []
        except json.decoder.JSONDecodeError:
            saved_data = []
        # saved_data = json.load(f)

    saved_image = [d['image_url'] for d in saved_data]
    cnt = 0
    for pic in pictures:
        if pic['image_url'] in saved_image:
            print(f"{pic['image_url']}已检索，跳过")
            continue
        print(f"开始检索{pic['image_url']}")

        vision_data = get_vision(pic['image_url'])
        if vision_data is not None:
            documents = vector_service.search_similar_documents(f"【图像信息】：{vision_data}", pic["image_url"])
        else:
            documents = vector_service.search_similar_documents(f"", pic["image_url"])
        saved_data.append({
            "image_url": pic['image_url'],
            "document_id": pic['document_id'],
            "context": documents
        })
        cnt += 1
        if cnt % 5 == 0:
            print(f"已检索{cnt}个图像")
            with open(save_url, 'w', encoding='utf-8') as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            # break
        # if cnt % 100 == 0:
        #     break

    with open(save_url, 'w', encoding='utf-8') as f:
        json.dump(saved_data, f, ensure_ascii=False, indent=4)

def tmp_check(save_url: str):
    with open(save_url, 'r', encoding='utf-8') as f:
        data = json.load(f)

    t = 0
    t_rank = 0

    for d in data:
        truth = d['document_id']
        pred = [d_tmp['doc_id'] for d_tmp in d['context']]
        print(truth, pred)
        for i, doc in enumerate(pred):
            if doc == truth:
                t += 1
                t_rank += i + 1
                break
    print(f"true: {t}，false: {len(data) - t}，t_rank: {t_rank / t}")

def image_to_base64(image: str, dir: str = None):
    if dir is not None:
        # print(dir, image)
        image = os.path.join(dir, image)
    with open(image, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
        return image_base64


def get_vision(pic: str):
    base_dir = os.getenv("BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")

    image_base64 = image_to_base64(pic, base_dir)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请详细描述图像信息，重点包含设备信息和故障信息。\n仅返回答案，不要任何markdown渲染。"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}"
                    }
                }
            ],
        }
    ]
    #
    # print(111)
    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=f"http://{server_ip}:8000/v1",
        api_key=api_key
    )
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    # print(222)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1000
        )
        # print(response)
        print(response.choices[0].message.content)
        print("\n================================\n")
        # return result.json()["message"]["content"], ai_reference_document_ids_str
        return response.choices[0].message.content
    except Exception as e:
        print("ai提取失败！！！")
        return None

def eval(pic_file: str, save_url: str):
    if not os.path.exists(pic_file):
        print("未检索到图像记录文件！")
        exact_picture(pic_file)

    if not os.path.exists(save_url):
        with open(save_url, 'w', encoding='utf-8') as f:
            pass

    with open(pic_file, 'r', encoding='utf-8') as f:
        pictures = json.load(f)

    with open(save_url, 'r', encoding='utf-8') as f:
        try:
            saved_data = json.load(f)
            if not isinstance(saved_data, list):
                saved_data = []
        except json.decoder.JSONDecodeError:
            saved_data = []

    if len(saved_data) < len(pictures):
        print(f"存在未检索的图像，进入检索")
        # retrieve(pic_file, save_url)
        retrieve_new(pic_file, save_url)

    with open(save_url, 'r', encoding='utf-8') as f:
        try:
            saved_data = json.load(f)
            if not isinstance(saved_data, list):
                saved_data = []
        except json.decoder.JSONDecodeError:
            saved_data = []

    print("===============")
    retrieve_doc = []
    ground_truth = []
    for data in saved_data:
        context = data['context']
        doc_id = [c['doc_id'] for c in context]
        retrieve_doc.append(doc_id)
        # ground_truth.append([data['document_id']])
        ground_truth.append(data['document_id'])

    for k in range(8):

        precisions = [precision_at_k(ret, rel, k + 1) for ret, rel in zip(retrieve_doc, ground_truth)]
        recalls = [recall_at_k(ret, rel, k + 1) for ret, rel in zip(retrieve_doc, ground_truth)]
        f1s = [f1_at_k(ret, rel, k + 1) for ret, rel in zip(retrieve_doc, ground_truth)]

        avg_precision_k = sum(precisions) / len(precisions)
        avg_recall_k = sum(recalls) / len(recalls)
        avg_f1_k = sum(f1s) / len(f1s)

        mrr = mean_reciprocal_rank(retrieve_doc, ground_truth)
        map_score = mean_average_precision(retrieve_doc, ground_truth)

        print(f"Precision@{k + 1}: {avg_precision_k:.4f}")
        print(f"Recall@{k + 1}: {avg_recall_k:.4f}")
        print(f"F1@{k + 1}: {avg_f1_k:.4f}")
        print(f"MRR: {mrr:.4f}")
        print(f"MAP: {map_score:.4f}")
        print("-------------------")

def parse_filename(base_name: str):
    # base_name = os.path.splitext(filename)[0]
    parts = base_name.split('_')
    numbers = []
    for part in parts:
        # 用 '-' 分割，取第一部分
        if '-' in part:
            prefix = part.split('-')[0]
            # 确保前缀是数字（避免非数字字符）
            if prefix.isdigit():
                numbers.append(int(prefix))
    return numbers

def pre_pictures(image_dir: str, save_path: str):
    if not os.path.isdir(image_dir):
        print(f"错误：文件夹 '{image_dir}' 不存在")
        return

    if not os.path.exists(save_path):
        with open(save_path, 'w', encoding='utf-8'):
            pass

    with open(save_path, 'r', encoding='utf-8') as f:
        try:
            saved_data = json.load(f)
            if not isinstance(saved_data, list):
                saved_data = []
        except json.decoder.JSONDecodeError:
            saved_data = []

    saved_url = [d['image_url'] for d in saved_data]
    cnt = 0
    for item in os.listdir(image_dir):
        full_path = os.path.join(image_dir, item)

        if os.path.isfile(full_path):
            filename = os.path.basename(full_path)
            # base_name, ext = os.path.splitext(filename)
            url = "eval_images/" + filename

            if url in saved_url:
                print(f"{url}已解析，跳过")
                continue

            numbers = parse_filename(os.path.splitext(filename)[0])
            saved_data.append({
                "image_url": url,
                "document_id": numbers
            })
            cnt += 1
            if cnt % 10 == 0:
                print(f"已解析{cnt}个图片")
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(saved_data, f, ensure_ascii=False, indent=4)
                # break
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(saved_data, f, ensure_ascii=False, indent=4)





if __name__ == '__main__':

    # documents = vector_service.batch_vectorize_existing_documents(150)
    #
    # print(documents)

    # exact_picture("D:\Pycharm\code\Maintenance_Assistance_System\datasets\document_images.json")

    # eval("D:\Pycharm\code\Maintenance_Assistance_System\datasets\document_images.json",
    #          "D:\Pycharm\code\Maintenance_Assistance_System\datasets\document_images_retrieve1.json")

    eval("D:\Pycharm\code\Maintenance_Assistance_System\datasets\images_data.json",
             "D:\Pycharm\code\Maintenance_Assistance_System\datasets\images_retrieve_main_chunk_vision_new_prompt1.json")

    # tmp_check("D:\Pycharm\code\Maintenance_Assistance_System\datasets\document_images_retrieve.json")

    # pre_pictures("D:\Pycharm\code\Maintenance_Assistance_System\eval_images",
    #              "D:\Pycharm\code\Maintenance_Assistance_System\datasets\images_data.json")