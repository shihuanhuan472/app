# ImageParser.py
import base64
import json
import mimetypes
import os
import uuid
import shutil
from datetime import datetime
from PIL import Image
from openai import OpenAI

from models import Document
from utils.ai_endpoint import get_ai_base_url
from utils.title_utils import normalize_document_title
try:
    from utils.token_counter import get_token_count
except ModuleNotFoundError:
    from token_counter import get_token_count
from utils.error_codes import BizCode


class ImageParser:
    """
    解析单张设备维修相关图片（PNG/JPG/JPEG/WebP/BMP）
    调用 Qwen3-VL 模型分析图片内容，生成结构化JSON文档
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
        """确保必要目录存在"""
        for dir_name in [self.document_dir, self.image_dir]:
            base_path = os.path.join(self.document_base_dir, dir_name)
            if not os.path.exists(base_path):
                os.makedirs(base_path)
                print(f"创建目录: {base_path}")

    def _set_last_error(self, code: int, message: str):
        self.last_error_code = int(code)
        self.last_error_detail = message

    def _is_ai_service_unavailable_error(self, error: Exception) -> bool:
        """判断是否为AI服务不可用错误"""
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
        """验证图片格式和可读性"""
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
        """
        压缩并标准化图片尺寸（保持长宽比，居中填充）
        返回压缩后的图片路径
        """
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
        """将图片转换为base64字符串"""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_prompt(self, user_text: str = None) -> str:
        """构建分析图片的prompt模板"""
        text_hint = f"\n[用户补充说明]\n{user_text}" if user_text and user_text.strip() else ""
        
        return f"""你好，你是一位设备维修问题分析专家。我将给你一张设备维修相关的图片{text_hint}。
请你仔细观察图片内容，按照以下模板提供信息，以JSON格式返回。若内容无法从图片中判断，字段可以为空字符串或空列表，但不可缺失字段。

模板：
标题：<简洁的标题，点明案例名称，必须填写>
问题简介：<可包含定义解释，现象介绍，问题发生频率，后果等内容>
原因：<造成该问题的主要原因，尽量从高频到低频排序>
评估：<评估问题的手段，方法，工具等信息>
检查：<描述维修现场如何进行定位确认，要求详细>
解决方法：<现场的解决措施及根本的解决方案，要求详细>
总结：<总结问题的主要原因，后果及解决方案的关键信息>
相关图片：<与字段相关的图像，每张图像最多出现在一个字段中>

请严格按照如下JSON格式输出：
{{
    "title": "标题", // 案例名称
    "problem_intro": "问题简介文本",
    "image_urls_problem_intro": [1, 3], // 与问题简介相关的图片编号
    "causes": "原因文本",
    "image_urls_causes": [2], // 与原因相关的图片编号
    "evaluation": "评估方法文本",
    "image_urls_evaluation": [4], // 与评估相关的图片编号
    "inspection": "检查方法文本",
    "image_urls_inspection": [5], // 与检查相关的图片编号
    "solutions": "解决方案文本",
    "image_urls_solutions": [6], // 与解决方案相关的图片编号
    "key_points": "总结文本",
    "image_urls_key_points": [7, 8] // 与总结相关的图片编号
}}

注意：
1. 所有内容必须基于图片可见信息，不可杜撰。若图片信息不足，对应字段填""或[]。
2. 你给出的回答仅包含我要求的JSON格式答案，不要有其他解释。
3. 图片编号固定为[1]，因为只传入一张图片。
4. title字段不可为空，必须控制在100个字符以内，请尽量从图片中提取关键信息生成。
5. 内容需专业、连贯、详细，避免过分精简。
6. 检查步骤和解决方案请尽可能具体可操作。

现在请分析这张图片：
[图片内容以base64格式在后续消息中提供]
"""

    def _build_messages(self, image_path: str, user_text: str = None) -> list:
        """构建调用AI的messages列表，含token控制"""
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
            token_cnt += 258  # 估算图片token
            print(f"[ImageParser] total token (with image): ~{token_cnt}")

        return [{"role": "user", "content": msg_content}]

    def _post_process_result(self, result: dict, image_name: str) -> dict:
        """处理AI返回结果，将图片编号映射为实际路径"""
        # 单张图片，编号固定为1
        image_path_str = f"{self.image_dir}/{image_name}"
        
        for key in list(result.keys()):
            if key.startswith("image_urls_"):
                # 无论AI返回什么编号，单张图都映射为实际路径
                result[key] = image_path_str if result[key] else None
        return result

    def _cleanup_temp_files(self, original_image: str, compressed_image: str = None):
        """清理临时生成的压缩图片（保留用户上传的原图）"""
        if compressed_image and os.path.exists(compressed_image):
            try:
                os.remove(compressed_image)
            except:
                pass  # 清理失败不影响主流程

    def parse(self, image_path: str, user_text: str = None) -> Document | None:
        """
        解析单张图片，生成结构化维修文档
        
        Args:
            image_path: 图片文件绝对路径
            user_text: 可选，用户对图片的补充文字说明
            
        Returns:
            Document对象，解析失败返回None，可通过last_error_code获取错误
        """
        self.last_error_code = None
        self.last_error_detail = None
        original_image = None
        compressed_image = None
        
        try:
            # 1. 验证并准备图片
            self._validate_image(image_path)
            
            # 复制图片到系统管理的image_dir，便于后续引用
            filename = os.path.basename(image_path)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_name = f"{timestamp}_{uuid.uuid4().hex}_{filename}"
            target_path = os.path.join(self.document_base_dir, self.image_dir, unique_name)
            shutil.copy2(image_path, target_path)
            original_image = target_path
            
            # 2. 构建请求消息
            messages = self._build_messages(original_image, user_text)
            
            # 3. 调用AI服务
            client = OpenAI(
                base_url=get_ai_base_url(),
                api_key=self.api_key
            )
            print(messages)
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_token,
                temperature=0.1  # 降低随机性，保证输出稳定
            )
            
            ans = response.choices[0].message.content.strip()
            print(f"[ImageParser] AI response:\n{ans}")
            
            # 4. 解析JSON结果
            result = json.loads(ans)
            result = self._post_process_result(result, unique_name)
            result["title"] = normalize_document_title(result.get("title"))
            
            # 5. 创建Document对象
            document = Document(**result, is_vectorized=0)
            return document
            
        except json.JSONDecodeError as e:
            print(f"[ImageParser] JSON解析失败: {e}\n原始响应: {ans if 'ans' in locals() else 'N/A'}")
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
            # 清理临时压缩文件，保留用户上传的原图副本
            if compressed_image and compressed_image != original_image:
                self._cleanup_temp_files(original_image, compressed_image)


# 全局单例实例，与PdfParser风格一致
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
        print("\n✅ 解析成功!")
        print(json.dumps(doc.__dict__, ensure_ascii=False, indent=2))
    else:
        print(f"\n❌ 解析失败: code={image_parser.last_error_code}, detail={image_parser.last_error_detail}")
