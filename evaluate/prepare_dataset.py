import json
from database import SessionLocal
from datasets import Dataset
import os
import pandas as pd
from models import Document

db = SessionLocal()

"""
生成最后RAGAS跑的数据集
"""

def get_context(doc_id):
    document = db.query(Document).filter(Document.id == doc_id).first()
    if not document:
        print(f"未查询到文档{doc_id}!!!")
        return None
    doc_context = f"""【文档】{document.title}
问题描述：{document.problem_intro}
原因分析：{document.causes}
评估建议：{document.evaluation}
检查步骤：{document.inspection}
解决方案：{document.solutions}
关键要点：{document.key_points}
"""
    return doc_context

def get_chunk(context: list):
    context_return = []
    # print(context)
    for context_tmp in context:
        # print("---------")
        # print(context_tmp)
        chunks = context_tmp["chunks"]
        for chunk in chunks:
            context_return.append(chunk["content"])
    return context_return

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

        doc_ids = [c_tmp["doc_id"] for c_tmp in context_tmp]
        # doc_context = get_chunk(context_tmp)
        doc_context = []
        for doc_id in doc_ids:
            doc_context_tmp = get_context(doc_id)
            if doc_context_tmp is not None:
                doc_context.append(doc_context_tmp)
        context.append(doc_context)
        cnt += 1
        if cnt % 110 == 0:
            break

    final_data = {
        "question": question,
        "reference": ground_truth,
        "contexts": context,
        "answer": answer
    }
    for tmp in final_data:
        print(final_data[tmp])
    return Dataset.from_dict(final_data)

if __name__ == '__main__':
    pass

    # dataset = prepare_dataset("D:\Pycharm\code\Maintenance_Assistance_System\datasets\\fianl_data.json",
    #                           "D:\Pycharm\code\Maintenance_Assistance_System\evaluate\\result.xlsx")
    # print(dataset)