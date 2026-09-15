# ImageParser.py
import base64
import json
import mimetypes
import os
import uuid
import shutil
from datetime import datetime
from PIL import Image

from models import Document
from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import create_chat_completion, create_openai_client, parse_chat_completion_json
from utils.title_utils import normalize_document_title
try:
    from utils.token_counter import get_token_count
except ModuleNotFoundError:
    from token_counter import get_token_count
from utils.error_codes import BizCode


class ImageParser:
    """
    将单张维修相关图片解析为结构化文档。
    """
    
    SUPPORTED_FORMATS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
    
    def __init__(self):
        self.document_base_dir = os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")
        self.document_dir = os.getenv("DOCUMENT_DIR", "upload/documents")
        self.image_dir = os.getenv("IMAGE_DIR", "upload/images")
        self.api_key = os.getenv("API_KEY", "EMPTY")
        self.model = os.getenv("MODEL_AI", "/models/Qwen3-VL-8B-Instruct")
        self.max_token = int(os.getenv("MAX_TOKEN", 2000))
        self.input_token = int(os.getenv("INPUT_TOKEN", 8000))
        self.last_error_code = None
        self.last_error_detail = None
        
        # 确保目录存在
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保必要目录存在。"""
        for dir_name in [self.document_dir, self.image_dir]:
            base_path = os.path.join(self.document_base_dir, dir_name)
            if not os.path.exists(base_path):
                os.makedirs(base_path)
                print(f"创建目录: {base_path}")

    def _set_last_error(self, code: int, message: str):
        self.last_error_code = int(code)
        self.last_error_detail = message

    def _is_ai_service_unavailable_error(self, error: Exception) -> bool:
        """判断异常是否类似 AI 服务不可用。"""
        name = type(error).__name__
        if name in {"APIConnectionError", "APITimeoutError"}:
            return True
        msg = str(error).lower()
        keywords = [
            "connection", "timed out", "timeout", "refused",
            "temporarily unavailable", "service unavailable",
            "name resolution", "max retries exceeded",
            "502", "503", "504",
        ]
        return any(k in msg for k in keywords)

    def _validate_image(self, image_path: str) -> bool:
        """验证图片格式和可读性。"""
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")
        
        ext = os.path.splitext(image_path)[1].lower()
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"不支持的图片格式: {ext}，支持的格式: {self.SUPPORTED_FORMATS}")
        
        try:
            with Image.open(image_path) as img:
                img.load()  # 验证图片可正常读取
            return True
        except Exception as e:
            raise ValueError(f"图片文件损坏或无法读取: {e}")

    def compress_image(self, image_path: str, max_size=512, pad_color=(0, 0, 0)) -> str:
        """压缩并标准化图片尺寸，同时保持长宽比。"""
        if not os.path.exists(image_path):
            raise FileNotFoundError(image_path)
        
        dir_name, filename = os.path.split(image_path)
        name, ext = os.path.splitext(filename)
        new_path = os.path.join(dir_name, f"{name}_compressed_{max_size}{ext}")
        
        # 如果已存在压缩图，直接返回
        if os.path.exists(new_path):
            return new_path

        image = Image.open(image_path).convert("RGB")
        max_length = max(image.width, image.height)
        rate = max_size / max_length
        new_size = (int(image.width * rate), int(image.height * rate))
        resized_image = image.resize(new_size, Image.Resampling.LANCZOS)

        # 居中填充到正方形
        new_image = Image.new("RGB", (max_size, max_size), pad_color)
        x = (max_size - new_size[0]) // 2
        y = (max_size - new_size[1]) // 2
        new_image.paste(resized_image, (x, y))

        new_image.save(new_path, optimize=True, quality=85)
        return new_path

    def image_to_base64(self, image_path: str) -> str:
        """将图片文件转换为 base64 字符串。"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_prompt(self, user_text: str = None) -> str:
        """构建图片分析提示词。"""
        text_hint = f"\n[用户补充说明]\n{user_text}" if user_text and user_text.strip() else ""

        return f"""你是一位设备维护故障分析专家。请分析提供的图片，并生成一份结构化的故障分析文档。{text_hint}

仅返回一个有效的 JSON 对象。不要包含任何推理过程、Markdown 代码块标记（如 ```json）、解释说明或 <think> 标签内容。
返回内容的第一个字符必须是 {{，最后一个字符必须是 }}。
所有字符串必须使用双引号。当图片中未显示相关信息时，请使用空字符串 "" 或空数组 []。

必需的 JSON 结构如下：
{{
  "title": "案例标题，必填，100个字符以内",
  "problem_intro": "问题简介",
  "image_urls_problem_intro": [],
  "causes": "原因分析",
  "image_urls_causes": [],
  "evaluation": "评估方法或风险评估",
  "image_urls_evaluation": [],
  "inspection": "检查与诊断过程",
  "image_urls_inspection": [],
  "solutions": "解决方案",
  "image_urls_solutions": [],
  "key_points": "关键要点总结",
  "image_urls_key_points": []
}}

图片的 ID 始终为 1。请将该图片 ID 分配到最多一个 image_urls_* 字段中（即不要重复分配）。
"""

    def _build_messages(self, image_path: str, user_text: str = None) -> list:
        """构建带 token 控制的聊天消息。"""
        prompt = self._build_prompt(user_text)
        msg_content = [{"type": "text", "text": prompt}]
        token_cnt = get_token_count(prompt)
        print(f"[ImageParser] prompt token: {token_cnt}")

        # 添加图片（单张）
        if token_cnt < self.input_token - 1000:
            compress_path = self.compress_image(image_path)
            mime_type, _ = mimetypes.guess_type(compress_path)
            if mime_type is None:
                ext = os.path.splitext(compress_path)[1].lower()
                mime_type = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp'
                }.get(ext, 'image/jpeg')
            
            image_base64 = self.image_to_base64(compress_path)
            msg_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}
            })
            token_cnt += 258  # 估算图片 token
            print(f"[ImageParser] total token (with image): ~{token_cnt}")

        return [{"role": "user", "content": msg_content}]

    def _post_process_result(self, result: dict, image_name: str) -> dict:
        """将图片编号字段映射为已存储的图片路径。"""
        # 单张图片，编号固定为 1
        image_path_str = f"{self.image_dir}/{image_name}"
        
        for key in list(result.keys()):
            if key.startswith("image_urls_"):
                # 无论 AI 返回什么编号，单张图都映射为实际路径
                result[key] = image_path_str if result[key] else None
        return result

    def _cleanup_temp_files(self, original_image: str, compressed_image: str = None):
        """清理临时压缩图片文件。"""
        if compressed_image and os.path.exists(compressed_image):
            try:
                os.remove(compressed_image)
            except:
                pass  # 清理失败不影响主流程

    def parse(self, image_path: str, user_text: str = None) -> Document | None:
        """解析单张图片，生成结构化维修文档。"""
        self.last_error_code = None
        self.last_error_detail = None
        original_image = None
        compressed_image = None
        
        try:
            # 1. 验证并准备图片
            self._validate_image(image_path)
            
            # 复制图片到系统管理的 image_dir，便于后续引用
            filename = os.path.basename(image_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_name = f"{timestamp}_{uuid.uuid4().hex}_{filename}"
            target_path = os.path.join(self.document_base_dir, self.image_dir, unique_name)
            shutil.copy2(image_path, target_path)
            original_image = target_path
            
            # 2. 构建请求消息
            messages = self._build_messages(original_image, user_text)
            
            # 3. 调用 AI 服务
            client = create_openai_client(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )
            print(messages)
            response = create_chat_completion(
                client,
                model=self.model,
                messages=messages,
                max_tokens=self.max_token,
                temperature=0.1,
                json_mode=True,
            )
            
            ans = response.choices[0].message.content.strip()
            print(f"[ImageParser] AI response:\n{ans}")
            
            # 4. 解析 JSON 结果
            result = parse_chat_completion_json(response)
            result = self._post_process_result(result, unique_name)
            result["title"] = normalize_document_title(result.get("title"))
            
            # 5. 创建 Document 对象
            document = Document(**result, is_vectorized=0)
            return document
            
        except json.JSONDecodeError as e:
            print(f"[ImageParser] JSON parse failed: {e}\nRaw response: {ans if 'ans' in locals() else 'N/A'}")
            self._set_last_error(BizCode.DOC_PARSE_FAILED, f"AI返回格式错误: {e}")
            return None
        except Exception as e:
            print(f"[ImageParser] 解析异常: {e}")
            if self._is_ai_service_unavailable_error(e):
                self._set_last_error(BizCode.AI_SERVICE_UNAVAILABLE, "AI服务不可用，请稍后重试")
            else:
                self._set_last_error(BizCode.DOC_PARSE_FAILED, str(e))
            return None
        finally:
            # 清理临时压缩文件，保留上传原图副本
            if compressed_image and compressed_image != original_image:
                self._cleanup_temp_files(original_image, compressed_image)


# 全局单例实例，与 PdfParser 风格一致
image_parser = ImageParser()


if __name__ == "__main__":
    # 测试示例
    import sys
    if len(sys.argv) < 2:
        print("用法: python ImageParser.py <图片路径> [可选: 补充说明文字]")
        sys.exit(1)
    
    img_path = sys.argv[1]
    text_hint = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(f"开始解析图片: {img_path}")
    doc = image_parser.parse(img_path, text_hint)
    
    if doc:
        print("\n解析成功!")
        print(json.dumps(doc.__dict__, ensure_ascii=False, indent=2))
    else:
        print(f"\n解析失败: code={image_parser.last_error_code}, detail={image_parser.last_error_detail}")


