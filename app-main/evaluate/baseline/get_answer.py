import json
import os
from langchain_rag import MilvusVectorStore, BGEM3MultimodalEmbeddings
from qwen_token_counter import get_token_count
from openai import OpenAI
from utils.ai_endpoint import get_ai_base_url

def get_prompt(docs, max_tokens):
    contexts = []
    total_len = 0
    for i, doc in enumerate(docs, 1):
        content = doc.page_content.strip()
        if not content:
            continue
        # 简单按字符数截断（实际可按token数）
        token_tmp = get_token_count(content)
        if total_len + token_tmp > max_tokens:
            break
        contexts.append(content)
        total_len += token_tmp

    if contexts:
        final_prompt = "以下是一些相关的知识文档，供你参考：\n\n"
        final_prompt += "\n---\n".join(contexts)
        final_prompt += "\n\n请参考上述文档，回答用户的问题。严格按照文档内容回答，不要添加额外信息！"
        return final_prompt

    return ""

def generate_message(docs, question):
    tokens_max = int(os.getenv("MESSAGE_MAX_TOKEN", 8000)) - int(os.getenv("MAX_TOKEN", 2000))
    tokens_max -= get_token_count(question)
    prompt = get_prompt(docs, tokens_max)
    msg_content = [{"type": "text", "text": f"{prompt}\n问题：{question}\n要求回答精简一点！"}]
    data = {}
    data["role"] = "user"
    data["content"] = msg_content
    messages = [data]
    return messages

def get_ai_answer(messages):
    """
    获取ai回答.
    流程：获取相关文档id列表（并得到字符串版） -> 生成提示词 -> 生成消息
          -> 消息丢给ai得到回答 -> 返回答案和相关文档id（字符串）
    """
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=get_ai_base_url(),
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

def serialize_docs(docs):
    return [{"content": doc.page_content, "score": doc.metadata.get("score")} for doc in docs]

def get_answer(question_path: str, save_path: str):
    if not os.path.exists(question_path):
        print("路径不存在！！！")
        return

    if not os.path.exists(save_path):
        with open(save_path, 'w', encoding='utf-8') as f:
            pass

    saved_data = []
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        with open(save_path, 'r', encoding='utf-8') as f:
            try:
                saved_data = json.load(f)
            except json.JSONDecodeError:
                print(f"警告：{save_path} 内容损坏，将重新初始化")
                saved_data = []

    saved_question = [d['question'] for d in saved_data]

    with open(question_path, 'r', encoding='utf-8') as f:
        questions = json.load(f)

    embed_model = BGEM3MultimodalEmbeddings()
    vector_store = MilvusVectorStore(embed_model, collection_name="multimodal_rag")
    cnt = 0
    for q_a in questions:
        if q_a['question'] in saved_question:
            print(f"问题 {q_a['question']} 已回答，跳过")
            continue
        print(f"开始回答{q_a['question']}")
        docs = vector_store.search_with_threshold(q_a['question'], score_threshold=0.6)
        messages = generate_message(docs, q_a['question'])

        response = get_ai_answer(messages)
        saved_data.append({
            "question": q_a["question"],
            "answer": response,
            "ground_truth": q_a["ground_truth"],
            "context": serialize_docs(docs)
        })
        cnt += 1
        if cnt % 10 == 0:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
            print(f"{cnt}个问题已回答")

        # if cnt % 100 == 0:
        #     break
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(saved_data, f, ensure_ascii=False, indent=4)



if __name__ == "__main__":
    get_answer("D:\Pycharm\code\Maintenance_Assistance_System\datasets\data.json",
               "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_result\\fianl_data_langchain.json")
