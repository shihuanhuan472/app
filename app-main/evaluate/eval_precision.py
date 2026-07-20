import json
import os

from openai import OpenAI

def generate_message(question, ground_truth, answer):
    messages = [
        {
            "role": "system",
            "content": "你是一个公正的打分裁判，对于模型的回答，若包含正确答案，请给出满分，若完全不包含，请给出0分。"
        },
        {
            "role": "user",
            "content": f"请判断模型答案是否正确。\n问题：{question}\n参考答案：{ground_truth}\n模型回答：{answer}\n\n如果模型答案与参考答案一致，输出 1；否则输出 0。只输出数字，不要有其他内容。"
        }]
    return messages


def eval_precision(file_path: str, save_path: str):
    if not os.path.exists(file_path):
        print("文件不存在！！！")
        return

    client = OpenAI(
        base_url="https://api.chatanywhere.tech/v1",
        api_key="sk-xxxxxxxxxxxxxxxxxxxxxxx"
    )
    model = "gpt-4o-mini"
    max_token = int(os.getenv("MAX_TOKEN", 1000))

    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if os.path.exists(save_path):
        with open(save_path, 'r', encoding='utf-8') as f:
            saved_data = json.load(f)
    else:
        saved_data = []
    print(f"len: {len(saved_data)}")
    cnt = 0
    saved_question = [d["question"] for d in saved_data]
    for d in data:
        if d["question"] in saved_question:
            continue
        messages = generate_message(d["question"], d["ground_truth"], d["answer"])
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_token,
            stream=False
        )
        # print(response)
        score = int(response.choices[0].message.content)
        print(score)
        saved_data.append(
            {
                "question": d["question"],
                "ground_truth": d["ground_truth"],
                "answer": d["answer"],
                "score": score
            }
        )
        cnt += 1
        if cnt % 20 == 0:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
        # if cnt % 50 == 0:
        #     break

    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(saved_data, f, ensure_ascii=False, indent=4)

if __name__ == '__main__':
    eval_precision("D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_result\\fianl_data_without_rerank.json",
                   "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_result\\precision_without_rerank.json")