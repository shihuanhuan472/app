import base64
import os
import requests
from openai import OpenAI
from utils.ai_endpoint import get_ai_base_url

def image_to_base64(image: str, dir: str = None):
    if dir is not None:
        image = os.path.join(dir, image)
    with open(image, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")
        return image_base64


# def get_new_title_by_ai(content):
#     ai_url: str = "http://192.168.246.200:11434/api/chat"
#     model = "qwen3-vl:8b-instruct"
#     message = [{"role": "user", "content": f"请根据下面的内容，生成一个10字以内的对话标题。内容：{content}"}]
#     data = {"model": model, "messages": message, "stream": False}
#     result = requests.post(ai_url, json=data)
#     print("new_title: ", result.json())
#     print("message: ", message)
#     new_title = result.json()["message"]["content"]
#     if len(new_title) > 15 or len(new_title) == 0:
#         new_title = "新标题"
#
#     return new_title

def get_ai_answer():
    # ai_reference_document_ids = get_reference_documents(db, message_now.content_text)
    # ai_reference_document_ids_str = get_ai_reference_document_ids_str(ai_reference_document_ids)
    # prompt = get_prompt(db, ai_reference_document_ids)
    # messages = generate_messages(db, session_id, message_now, prompt)
    # print(messages)
    # ai_url: str = os.getenv("AI_API")
    # model = os.getenv("MODEL")
    # data = {"model": model, "messages": messages, "stream": False}
    # result = requests.post(ai_url, json=data)
    # image_base64 = image_to_base64("D:\Pycharm\code\Maintenance_Assistance_System\\test_image.png")
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "对于国内的985，211高校，你了解多少"},
                # {
                #     "type": "image_url",
                #     "image_url": {
                #         "url": f"data:image/jpeg;base64,{image_base64}"
                #     }
                # }
            ],
        }
    ]
    #
    api_key = os.getenv("API_KEY", "EMPTY")
    client = OpenAI(
        base_url=get_ai_base_url(),
        api_key=api_key
    )
    model = os.getenv("MODEL_AI", "/models/Qwen3-VL-4B-Instruct")
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=7000
    )

    # response = client.chat.completions.create(
    #     model="/models/Qwen3-VL-4B-Instruct",
    #     messages=[
    #         {
    #             "role": "user",
    #             "content": [
    #                 {"type": "text", "text": "你好，请描述一下图片中的内容"},
    #                 {
    #                     "type": "image_url",
    #                     "image_url": {
    #                         "url": f"data:image/jpeg;base64,{image_base64}"
    #                     }
    #                 }
    #             ],
    #         }
    #     ],
    #     max_tokens=7000
    # )

    print(response)
    print(response.choices[0].message.content)
    # return result.json()["message"]["content"], ai_reference_document_ids_str
    return response.choices[0].message.content

if __name__ == "__main__":
    get_ai_answer()
    # get_new_title_by_ai("你好，我的电脑风扇转动有明显齿轮声，请给我分析一下原因")


# scripts/test_milvus_connection.py
# import sys
# import os
#
# sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#
# from pymilvus import connections, utility
#
#
# def test_milvus_connection():
#     """测试Milvus连接"""
#     print("测试Milvus连接...")
#
#     try:
#         # 1. 连接Milvus
#         connections.connect(
#             alias="default",
#             host="localhost",
#             port="19530"
#         )
#         print("✓ 成功连接到Milvus")
#
#         # 2. 获取服务器版本
#         version = utility.get_server_version()
#         print(f"✓ Milvus版本: {version}")
#
#         # 3. 列出所有集合
#         collections = utility.list_collections()
#         print(f"✓ 现有集合: {collections}")
#
#         # 4. 测试连接状态
#         connected = connections.has_connection("default")
#         print(f"✓ 连接状态: {connected}")
#
#         return True
#
#     except Exception as e:
#         print(f"✗ 连接失败: {e}")
#         import traceback
#         traceback.print_exc()
#         return False


# if __name__ == "__main__":
    # 配置日志
    # if test_milvus_connection():
    #     print("\n" + "=" * 50)
    #     print("✓ Milvus连接测试通过！")
    #     print("=" * 50)
    #     sys.exit(0)
    # else:
    #     print("\n" + "=" * 50)
    #     print("✗ Milvus连接测试失败")
    #     print("=" * 50)
    #     sys.exit(1)
