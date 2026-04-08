import json
import os
from datetime import datetime

from langchain_rag import BGEM3MultimodalEmbeddings, MultimodalRetriever, MilvusVectorStore, parse_document_to_chunks

def add_documents(file_path: str, save_file: str):
    if not os.path.exists(file_path):
        print("文件路径有误！！！")
        return
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not os.path.exists(save_file):
        with open(save_file, 'w', encoding='utf-8') as f:
            pass
    with open(save_file, 'r', encoding='utf-8') as f:
        data_saved = json.load(f)
    embed_model = BGEM3MultimodalEmbeddings()

    # 初始化 Milvus 向量库
    vector_store = MilvusVectorStore(embed_model, collection_name="multimodal_rag")
    cnt = 0
    data_saved_file_path = [d["file_path"] for d in data_saved]

    print(f"len: {len(data_saved_file_path)}")
    print(f"len: {len(data)}")

    for f in data:
        if f["file_path"] in data_saved_file_path:
            print(f"{f['filename']} 已导入，跳过！")
            continue
        try:
            chunks = parse_document_to_chunks(f["file_path"])
            texts = [chunk["text"] for chunk in chunks]
            image_paths = [chunk["image_path"] for chunk in chunks]
            metadatas = [chunk["metadata"] for chunk in chunks]

            # 批量插入 Milvus
            vector_store.insert(texts, image_paths, metadatas)
            print(f"成功插入 {len(chunks)} 个 chunks")
            cnt += 1
            data_saved.append({
                "filename": f['filename'],
                "file_path": f['file_path'],
                "is_vectorized": 1
            })
            if cnt % 20 == 0:
                print(f"{cnt}数据已插入")
                with open(save_file, 'w', encoding='utf-8') as f:
                    json.dump(data_saved, f, ensure_ascii=False, indent=4)
            if cnt % 100 == 0:
                break
        except Exception as e:
            print(f"{f['filename']}解析报错！！！")
            print(e)
            continue

    with open(save_file, 'w', encoding='utf-8') as f:
        json.dump(data_saved, f, ensure_ascii=False, indent=4)


if __name__ == '__main__':
    add_documents("D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files.json",
                  "D:\Pycharm\code\Maintenance_Assistance_System\datasets\\files_langchain.json")