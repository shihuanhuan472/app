import json
import os

import pandas as pd
from datasets import Dataset


def prepare_dataset(data_url: str, saved_url: str):
    if not os.path.exists(data_url):
        print("文件不存在！")
        return None
    with open(data_url, 'r', encoding="utf-8") as f:
        try:
            dataset = json.load(f)
            if not isinstance(dataset, list):
                dataset = []
        except Exception as e:
            dataset = []

    if not os.path.exists(saved_url):
        print("目前没有已评估的问题")
        saved_question = []
    else:
        df = pd.read_excel(saved_url, engine='openpyxl')
        saved_question = df["user_input"].tolist()

    print(f"总数据集长度：{len(dataset)}")
    print(f"已评估问题：{len(saved_question)}")
    question = []
    context = []
    ground_truth = []
    answer = []
    cnt = 0
    print(saved_question)
    for data in dataset:

        if data["question"] in saved_question:
            print(f"{data['question']} 已评估，跳过")
            continue

        question.append(data["question"])
        ground_truth.append(data["ground_truth"])
        answer.append(data["answer"])

        context_tmp = data["context"]
        doc_context = [c_tmp['content'] for c_tmp in context_tmp]
        context.append(doc_context)
        cnt += 1
        if cnt % 50 == 0:
            break

    final_data = {
        "question": question,
        "reference": ground_truth,
        "contexts": context,
        "answer": answer
    }
    # for tmp in final_data:
    #     print(final_data[tmp])
    return Dataset.from_dict(final_data)

if __name__ == '__main__':
    # pass

    dataset = prepare_dataset("D:\Pycharm\code\Maintenance_Assistance_System\datasets\\final_data_langchain.json",
                              "D:\Pycharm\code\Maintenance_Assistance_System\evaluate\\result_langchain.xlsx")
    print(dataset)