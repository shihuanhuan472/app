import json
import os.path
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ['HF_HOME'] = os.getenv("MODEL_DOWNLOAD_URL", "D:\Pycharm\code\Maintenance_Assistance_System\\bge\model")
from qwen_token_counter import get_token_count
from openai import OpenAI
from models import Document, Message
from utils.VectorService import VectorService
from database import SessionLocal

db = SessionLocal()
vector_service = VectorService(db)

"""
拿来让ai回答所有问题，生成最终数据集的
"""

def get_prompt(db, document_ids, max_tokens):
    print(document_ids)
    if not document_ids:
        return ""
    tokens = 0
    prompts = []
    for i, document_id in enumerate(document_ids):
        document = db.query(Document).filter(Document.id == document_id).scalar()
        if not document:
            continue
        doc_prompt = f"""【文档{i + 1}：】{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
        """
        token_tmp = get_token_count(doc_prompt)
        if tokens + token_tmp >= max_tokens:
            break
        tokens += token_tmp
        prompts.append(doc_prompt)

    # 添加指令
    if prompts:
        final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
        final_prompt += "\n---\n".join(prompts)
        final_prompt += "\n\n请参考上述文档，并结合你自己的知识库，回答用户的问题。"
        return final_prompt

    return ""

def get_ai_answer(messages):
    """
    获取ai回答.
    流程：获取相关文档id列表（并得到字符串版） -> 生成提示词 -> 生成消息
          -> 消息丢给ai得到回答 -> 返回答案和相关文档id（字符串）
    """
    server_ip = os.getenv("SERVER_IP", "192.168.246.200")
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=f"http://{server_ip}:8000/v1",
        api_key=api_key
    )
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
    max_token = int(os.getenv("MAX_TOKEN", 2000))
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_token
    )

    final_ans = (response.choices[0].message.content
                 .replace("\n---\n", "---")
                 .replace("\n\n", "\n"))

    return final_ans

def generate_messages(db, question, documents_ids, ans):
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    tokens_max -= get_token_count(question)
    prompt = get_prompt(db, documents_ids, tokens_max)
    prompt += "\n" + ans
    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{question}\n要求回答精简一点！"}]
    data = {}
    data["role"] = "user"
    data["content"] = msg_content
    messages = [data]
    return messages

def prepare_all_dataset(question_url: str, save_url):
    with open(question_url, 'r', encoding="utf-8") as f:
        try:
            question_answer = json.load(f)
            if not isinstance(question_answer, list):
                question_answer = []
        except json.decoder.JSONDecodeError:
            question_answer = []

    if not os.path.exists(save_url):
        with open(save_url, 'w', encoding="utf-8") as f:
            pass

    with open(save_url, 'r', encoding="utf-8") as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                dataset = []
        except json.decoder.JSONDecodeError:
            dataset = []

    question_saved = [data["question"] for data in dataset]
    cnt = 0
    for q_a in question_answer:
        if q_a["question"] in question_saved:
            print(f"{q_a['question']}已有回答，跳过")
            continue
        print(f"开始回答问题：{q_a['question']}")
        documents = vector_service.search_similar_documents(q_a["question"], None)
        document_ids = []
        for document in documents:
            print(document["score"])
            document_ids.append(document["doc_id"])
        messages = generate_messages(db, q_a["question"], document_ids, q_a["ground_truth"])
        response = get_ai_answer(messages)
        dataset.append({
            "question": q_a["question"],
            "answer": response,
            "ground_truth": q_a["ground_truth"],
            "context": documents
        })
        cnt += 1
        if cnt % 10 == 0:
            print(f"{cnt} 个问题已回答")
            with open(save_url, "w", encoding="utf-8") as f:
                json.dump(dataset, f, ensure_ascii=False, indent=4)
        if cnt % 100 == 0:
            break
    with open(save_url, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    prepare_all_dataset("D:\Pycharm\code\Maintenance_Assistance_System\datasets\data.json",
                        "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\fianl_data_1.json")

