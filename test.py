# import os
# import requests
#
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
#
# if __name__ == "__main__":
#     get_new_title_by_ai("你好，我的电脑风扇转动有明显齿轮声，请给我分析一下原因")


# scripts/test_milvus_connection.py
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymilvus import connections, utility


def test_milvus_connection():
    """测试Milvus连接"""
    print("测试Milvus连接...")

    try:
        # 1. 连接Milvus
        connections.connect(
            alias="default",
            host="localhost",
            port="19530"
        )
        print("✓ 成功连接到Milvus")

        # 2. 获取服务器版本
        version = utility.get_server_version()
        print(f"✓ Milvus版本: {version}")

        # 3. 列出所有集合
        collections = utility.list_collections()
        print(f"✓ 现有集合: {collections}")

        # 4. 测试连接状态
        connected = connections.has_connection("default")
        print(f"✓ 连接状态: {connected}")

        return True

    except Exception as e:
        print(f"✗ 连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 配置日志
    if test_milvus_connection():
        print("\n" + "=" * 50)
        print("✓ Milvus连接测试通过！")
        print("=" * 50)
        sys.exit(0)
    else:
        print("\n" + "=" * 50)
        print("✗ Milvus连接测试失败")
        print("=" * 50)
        sys.exit(1)