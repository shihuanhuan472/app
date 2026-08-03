# schemas.py
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List, TypeVar, Generic
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
    role_group_id: Optional[int] = None
    status: Optional[int] = 1


class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    confirm_password: str = Field(..., min_length=6, max_length=128)
    phone: str = Field(..., min_length=5, max_length=20)
    email: Optional[str] = Field(default=None, max_length=100)
    full_name: str = Field(..., min_length=1, max_length=100)
    department: Optional[str] = Field(default=None, max_length=100)


class RegistrationApproval(BaseModel):
    user_id: int = Field(..., gt=0)
    role_group_id: int = Field(..., gt=0)


class RegistrationRejection(BaseModel):
    user_id: int = Field(..., gt=0)


class UserResponse(BaseModel):
    id: int
    username: str
    phone: str
    email: Optional[str]
    full_name: str
    role: int
    perm: Optional[int] = None
    role_group_id: Optional[int] = None
    role_group_name: Optional[str] = None
    permissions: Optional[List[str]] = None
    status: Optional[int] = None
    registration_status: str = "approved"
    department: Optional[str]
    api_key: Optional[str] = None
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
    role_group_id: Optional[int] = None
    status: Optional[int] = None


class RoleGroupCreate(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    permissions: List[str]


class RoleGroupUpdate(BaseModel):
    id: int
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    is_deleted: Optional[int] = None


class RoleGroupResponse(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str] = None
    permissions: List[str] = []
    is_system: int = 0
    is_deleted: int = 0
    created_time: Optional[datetime] = None
    updated_time: Optional[datetime] = None


class SensitiveTermCreate(BaseModel):
    source: str = Field(..., max_length=200)
    replacement: str = Field(..., max_length=200)


class SensitiveTermUpdate(BaseModel):
    source: str = Field(..., max_length=200)
    new_source: Optional[str] = Field(default=None, max_length=200)
    replacement: Optional[str] = Field(default=None, max_length=200)


class SensitiveTermDelete(BaseModel):
    source: str = Field(..., max_length=200)

class Page(BaseModel):
    page: Optional[int] = 1
    size: Optional[int] = 6
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[Any]] = None


class TagCreate(BaseModel):
    name: str
    description: Optional[str] = None


class TagUpdate(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None


class TagQuery(BaseModel):
    data: Optional[str] = ""
    page: Optional[int] = 1
    size: Optional[int] = 10


class TagResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    document_count: Optional[int] = 0
    created_by: Optional[int] = None
    created_time: Optional[datetime] = None
    updated_time: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    username: str
    password: str


class UserChangePassword(BaseModel):
    old_password: str
    new_password: str


# 文档相关的Schema
class KnowledgeSectionCreate(BaseModel):
    section_index: Optional[int] = None
    section_title: Optional[str] = None
    # 前端展示用章节编号，例如 1、2、1.1、1.1.1。
    section_type: Optional[str] = "1"
    plain_text: Optional[str] = None
    image_urls: Optional[List[str]] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class KnowledgeSectionResponse(KnowledgeSectionCreate):
    id: int

    class Config:
        from_attributes = True


class DocumentCreate(BaseModel):
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[Any]] = None
    title: str
    summary: Optional[str] = None
    content: Optional[str] = None
    sections: Optional[List[KnowledgeSectionCreate]] = None
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
    section_ids: Optional[List[int]] = None
    sections: Optional[List[KnowledgeSectionResponse]] = None
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
    tag: Optional[List[Any]] = None
    sections: Optional[List[KnowledgeSectionCreate]] = None
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


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""

    class DocumentItem(BaseModel):
        """单个文档删除项"""

        id: int
        library_type: str = "breakdown"

    documents: List[DocumentItem]


class DocumentReviewResponse(DocumentResponse):
    review_library_type: Optional[str] = "breakdown"
    sections: Optional[List[KnowledgeSectionCreate]] = None
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
    tag: Optional[List[Any]] = None
    page: Optional[int] = 1
    size: Optional[int] = 6


class DeleteImageRequest(BaseModel):
    image_url: str


class ParseResultItem(BaseModel):
    file_name: str
    file_path: Optional[str] = None
    status: str
    reason: Optional[str] = None
    error_code: Optional[int] = None
    document_id: Optional[int] = None
    document_library_type: Optional[str] = None
    elapsed_seconds: Optional[int] = None


class UploadDocumentResponse(BaseModel):
    success_origin_filename: List[str]
    success_file_url: List[str]
    error_origin_filename: List[str]
    parse_results: Optional[List[ParseResultItem]] = None


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
    parse_started_time: Optional[datetime] = None
    document_id: Optional[int] = None
    document_library_type: Optional[str] = "breakdown"
    review_library_type: Optional[str] = "breakdown"
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
    tag: Optional[List[Any]] = None


class ParseTaskCreate(AnalyzeRequest):
    pass


class ParseTaskItemResponse(BaseModel):
    id: int
    source_document_id: Optional[int] = None
    file_name: str
    file_path: str
    status: str
    error_reason: Optional[str] = None
    error_code: Optional[int] = None
    document_id: Optional[int] = None
    document_library_type: Optional[str] = "breakdown"
    started_time: Optional[datetime] = None
    finished_time: Optional[datetime] = None
    elapsed_seconds: Optional[int] = None

    class Config:
        from_attributes = True


class ParseTaskResponse(BaseModel):
    id: int
    user_id: int
    status: str
    total_count: int
    success_count: int
    failed_count: int
    current_file_name: Optional[str] = None
    submit_for_review: bool = False
    library_type: str = "breakdown"
    tag: Optional[List[Any]] = None
    error_message: Optional[str] = None
    created_time: Optional[datetime] = None
    started_time: Optional[datetime] = None
    finished_time: Optional[datetime] = None
    items: List[ParseTaskItemResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


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
    token_count: Optional[int] = 0
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
T = TypeVar("T")


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
