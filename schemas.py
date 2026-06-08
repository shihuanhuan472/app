# schemas.py
from pydantic import BaseModel
from typing import Optional, List, TypeVar, Generic
from datetime import datetime


# 用户相关的Schema
class UserCreate(BaseModel):
    username: str
    phone: str
    email: Optional[str] = None
    full_name: str
    department: Optional[str] = None
    role: Optional[int] = 1
    perm: Optional[int] = 1
    status: Optional[int] = 1


class UserResponse(BaseModel):
    id: int
    username: str
    phone: str
    email: Optional[str]
    full_name: str
    role: int
    perm: Optional[int] = None
    department: Optional[str]
    created_time: Optional[datetime]
    last_login: Optional[datetime]

    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None

class UserUpdateByAdmin(BaseModel):
    id: int
    phone: Optional[str] = None
    email: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None
    role: Optional[int] = None
    perm: Optional[int] = None
    status: Optional[int] = None

class Page(BaseModel):
    page: Optional[int] = 1
    size: Optional[int] = 6
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None

class UserLogin(BaseModel):
    username: str
    password: str
    role: str

class UserChangePassword(BaseModel):
    old_password: str
    new_password: str

# 文档相关的Schema
class DocumentCreate(BaseModel):
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None
    title: str
    problem_intro: Optional[str] = None
    image_urls: Optional[str] = None
    causes: Optional[str] = None
    evaluation: Optional[str] = None
    inspection: Optional[str] = None
    solutions: Optional[str] = None
    key_points: Optional[str] = None

    image_urls_problem_intro: Optional[str] = None
    image_urls_causes: Optional[str] = None
    image_urls_evaluation: Optional[str] = None
    image_urls_inspection: Optional[str] = None
    image_urls_solutions: Optional[str] = None
    image_urls_key_points: Optional[str] = None

class DocumentResponse(BaseModel):
    id: int
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None
    title: str
    contributor_id: Optional[int]
    contributor_name: Optional[str]
    first_edit_date: Optional[datetime]
    problem_intro: Optional[str]
    image_urls: Optional[str]
    causes: Optional[str]
    evaluation: Optional[str]
    inspection: Optional[str]
    solutions: Optional[str]
    key_points: Optional[str]
    origin_file_name: Optional[str] = None
    origin_file_dir: Optional[str] = None

    image_urls_problem_intro: Optional[str] = None
    image_urls_causes: Optional[str] = None
    image_urls_evaluation: Optional[str] = None
    image_urls_inspection: Optional[str] = None
    image_urls_solutions: Optional[str] = None
    image_urls_key_points: Optional[str] = None

    class Config:
        from_attributes = True


class DocumentReviewRequest(BaseModel):
    # action_type: 1-create, 2-update, 3-delete
    action_type: int
    document_id: Optional[int] = None
    document_library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None
    review_comment: Optional[str] = None

    title: Optional[str] = None
    problem_intro: Optional[str] = None
    image_urls: Optional[str] = None
    causes: Optional[str] = None
    evaluation: Optional[str] = None
    inspection: Optional[str] = None
    solutions: Optional[str] = None
    key_points: Optional[str] = None
    origin_file_name: Optional[str] = None
    origin_file_dir: Optional[str] = None

    image_urls_problem_intro: Optional[str] = None
    image_urls_causes: Optional[str] = None
    image_urls_evaluation: Optional[str] = None
    image_urls_inspection: Optional[str] = None
    image_urls_solutions: Optional[str] = None
    image_urls_key_points: Optional[str] = None


class DocumentReviewResponse(DocumentResponse):
    document_id: Optional[int] = None
    reviewer_id: Optional[int] = None
    reviewer_name: Optional[str] = None
    reviewed_time: Optional[datetime] = None
    # status: 0-pending, 1-approved, 2-rejected, 3-withdrawn
    status: int
    # action_type: 1-create, 2-update, 3-delete
    action_type: int
    review_comment: Optional[str] = None

    class Config:
        from_attributes = True

class DocumentQuery(BaseModel):
    data: str
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None
    page: Optional[int] = 1
    size: Optional[int] = 6

class DeleteImageRequest(BaseModel):
    image_url: str

class UploadDocumentResponse(BaseModel):
    success_origin_filename: List[str]
    success_file_url: List[str]
    error_origin_filename: List[str]

class UploadDocumentRequestNew(BaseModel):
    name: str
    size: int
    type: str
    location: str
    create: datetime


class SourceDocumentResponse(BaseModel):
    id: int
    origin_file_name: str
    stored_file_path: str
    file_ext: Optional[str] = None
    file_category: Optional[str] = None
    file_size: Optional[int] = 0
    uploader_id: Optional[int] = None
    uploader_name: Optional[str] = None
    upload_time: Optional[datetime] = None
    status: str
    parse_error: Optional[str] = None
    document_id: Optional[int] = None
    document_library_type: Optional[str] = "breakdown"
    review_id: Optional[int] = None

    class Config:
        from_attributes = True


class DeleteDocumentRequestNew(BaseModel):
    ids: List[int]

class AnalyzeRequest(BaseModel):
    file_list: List[str]
    file_name: List[str]
    submit_for_review: Optional[bool] = False
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[str]] = None

# 对话相关的Schema
class ConversationCreate(BaseModel):
    user_id: int
    title: Optional[str] = None

class ConversationResponse(BaseModel):
    id: int
    user_id: int
    title: Optional[str]
    created_time: Optional[datetime]
    updated_time: Optional[datetime]

    class Config:
        from_attributes = True

class ConversationCreateNew(BaseModel):
    name: str
    user_id: Optional[str] = None

class ConversationResponseNew(BaseModel):
    code: int
    data: Optional[dict] = None
    message: Optional[str] = None

class ConversationDeleteRequest(BaseModel):
    ids: List[int]

# 消息相关的Schema
class MessageCreateNew(BaseModel):
    question: str
    session_id: int
    stream: bool
    user_uploaded_images: Optional[str] = None

class MessageCreate(BaseModel):
    session_id: int
    content_text: str = None
    user_uploaded_images: Optional[str] = None
    stream: bool = True


class MessageResponse(BaseModel):
    id: int
    session_id: int
    message_order: int
    role: int
    content_text: Optional[str]
    user_uploaded_images: Optional[str]
    ai_reference_doc_ids: Optional[str]
    created_time: Optional[datetime]

    class Config:
        from_attributes = True

class UserQueryByPage(BaseModel):
    data: str
    page: Optional[int] = 1
    size: Optional[int] = 6

class TokenData(BaseModel):
    """Token 数据模型"""
    user_id: Optional[str] = None
    username: Optional[str] = None


# 1. 使用标准库的 TypeVar 定义泛型
T = TypeVar('T')

# 2. 继承 BaseModel 并使用 Generic
class Result(BaseModel, Generic[T]):
    """统一响应模型 (Pydantic v2 语法)"""
    code: int = 1
    msg: str = "success"
    data: Optional[T] = None

    @classmethod
    def success(cls):
        """增删改成功，无数据返回"""
        return cls(code=1, msg="success", data=None)

    @classmethod
    def success_with_data(cls, data: T):
        """查询成功，携带数据"""
        return cls(code=1, msg="success", data=data)

    @classmethod
    def error(cls, msg: str):
        """操作失败"""
        return cls(code=0, msg=msg, data=None)

class ResultNew(BaseModel, Generic[T]):
    """统一响应模型 (Pydantic v2 语法)"""
    code: int = 1
    message: Optional[str] = None
    data: Optional[T] = None

    @classmethod
    def result(cls, code: int, msg: str = None, data: Optional[T] = None):
        return cls(code=code, message=msg, data=data)
