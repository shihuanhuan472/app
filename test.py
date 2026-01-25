import requests
import base64

def image_chat(image_path, prompt):
    url = "http://192.168.246.200:11434/api/chat"

    # 1. 图片转 base64（不要 data:image/... 前缀）
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    # 2. Ollama 正确请求体
    data = {
        "model": "qwen3-vl:8b-instruct",
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_base64]
            }
        ]
    }

    # 3. 调用
    r = requests.post(url, json=data)
    print(r.text)   # 🔥 调试时建议先打印
    r.raise_for_status()

    # 4. 解析结果
    return r.json()["message"]["content"]

# ===== 测试 =====
result = image_chat(
    r"D:\Pycharm\code\Maintenance_Assistance_System\upload\ask\20260122_163526_0eff6409740e4176a458d9a013629d01.png",
    "描述一下这张图片"
)
print(result)