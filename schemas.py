# schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict, Optional, List, TypeVar, Generic
from datetime import datetime

PHONE_PATTERN = r"^1[3-9]\d{9}$"
USERNAME_PATTERN = r"^[a-zA-Z0-9_]+$"
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 50


# 用户相关的Schema
class UserCreate(BaseModel):
    username: str = Field(
        ...,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
    )
    phone: str = Field(..., min_length=11, max_length=11, pattern=PHONE_PATTERN)
    email: Optional[str] = None
    full_name: str
    department: Optional[str] = None
    role: Optional[int] = 1
    perm: Optional[int] = 1
    role_group_id: Optional[int] = None
    status: Optional[int] = 1


class UserRegister(BaseModel):
    username: str = Field(
        ...,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
    )
    password: str = Field(..., min_length=6, max_length=128)
    confirm_password: str = Field(..., min_length=6, max_length=128)
    phone: str = Field(..., min_length=11, max_length=11, pattern=PHONE_PATTERN)
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
    phone: Optional[str] = Field(default=None, min_length=11, max_length=11, pattern=PHONE_PATTERN)
    email: Optional[str] = None
    full_name: Optional[str] = None
    department: Optional[str] = None


class UserUpdateByAdmin(BaseModel):
    id: int
    username: Optional[str] = Field(
        default=None,
        min_length=USERNAME_MIN_LENGTH,
        max_length=USERNAME_MAX_LENGTH,
        pattern=USERNAME_PATTERN,
    )
    phone: Optional[str] = Field(default=None, min_length=11, max_length=11, pattern=PHONE_PATTERN)
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
    tag: Optional[List[int]] = None


def _validate_match_aliases(aliases):
    if aliases is None:
        return None
    normalized = []
    seen = set()
    for alias in aliases:
        value = str(alias or "").strip()
        if not value:
            continue
        if any(separator in value for separator in (",", "，", "\r", "\n")):
            raise ValueError("每个识别别名必须单独添加，不能包含逗号或换行")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


class TagCreate(BaseModel):
    name: str
    description: Optional[str] = None
    match_aliases: Optional[List[str]] = None

    @field_validator("match_aliases")
    @classmethod
    def validate_match_aliases(cls, aliases):
        return _validate_match_aliases(aliases)


class TagUpdate(BaseModel):
    id: int
    name: Optional[str] = None
    description: Optional[str] = None
    match_aliases: Optional[List[str]] = None

    @field_validator("match_aliases")
    @classmethod
    def validate_match_aliases(cls, aliases):
        return _validate_match_aliases(aliases)


class TagQuery(BaseModel):
    data: Optional[str] = ""
    page: Optional[int] = 1
    size: Optional[int] = 10


class TagResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    match_aliases: Optional[List[str]] = None
    document_count: Optional[int] = 0
    pending_review_count: Optional[int] = 0
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


class ImageGraphNodeUpdate(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    text: Optional[str] = Field(default=None, min_length=1, max_length=500)
    parent_node_id: Optional[str] = Field(default=None, max_length=64)
    bbox: Optional[List[int]] = Field(default=None, min_length=4, max_length=4)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, bbox):
        if bbox is None:
            return None
        if bbox[0] < 0 or bbox[1] < 0 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError("bbox 必须是有效的 [x0, y0, x1, y1]")
        return bbox


class ImageGraphReviewUpdate(BaseModel):
    nodes: List[ImageGraphNodeUpdate] = Field(..., min_length=1)
    review_comment: Optional[str] = Field(default=None, max_length=2000)
    approved: bool = True


class DocumentCreate(BaseModel):
    library_type: Optional[str] = "breakdown"
    tag: Optional[List[int]] = None
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
    tag: List[int] = Field(default_factory=list)
    tag_names: List[str] = Field(default_factory=list)
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
    tag: Optional[List[int]] = None
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


class SourceBatchDeleteRequest(BaseModel):
    ids: List[int]


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
    tag: Optional[List[int]] = None
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
    upload_results: Optional[List[ParseResultItem]] = None
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
    tag: Optional[List[int]] = None


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
    tag: Optional[List[int]] = None
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
    knowledge_version_id: Optional[int] = Field(default=None, ge=1)
    hardware_version: Optional[str] = Field(default=None, max_length=128)


class MessageCreate(BaseModel):
    session_id: int
    content_text: str = None
    user_uploaded_images: Optional[str] = None
    stream: bool = True
    knowledge_version_id: Optional[int] = Field(default=None, ge=1)
    hardware_version: Optional[str] = Field(default=None, max_length=128)


class MessageResponse(BaseModel):
    id: int
    session_id: int
    message_order: int
    role: int
    content_text: Optional[str]
    user_uploaded_images: Optional[str]
    ai_reference_doc_ids: Optional[str]
    feedback_eligible: bool = False
    token_count: Optional[int] = 0
    created_time: Optional[datetime]

    class Config:
        from_attributes = True


class FeedbackIngestCreate(BaseModel):
    user_id: int = Field(..., gt=0)
    conversation_id: int = Field(..., gt=0)
    message_id: int = Field(..., gt=0)
    feedback_type: str = Field(..., min_length=1, max_length=32)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=4000)


class FeedbackRecordResponse(BaseModel):
    id: int
    conversation_id: int
    message_id: int
    user_id: int
    feedback_type: str
    rating: Optional[int] = None
    comment: Optional[str] = None
    created_at: Optional[datetime] = None
    query_snapshot: Optional[Dict[str, Any]] = None
    answer_snapshot: Optional[Dict[str, Any]] = None
    retrieved_documents: List[Dict[str, Any]] = Field(default_factory=list)
    cited_documents: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[int] = None
    status: str

    class Config:
        from_attributes = True


class UserQueryByPage(BaseModel):
    data: str
    page: Optional[int] = 1
    size: Optional[int] = 6


# 知识库版本管理（仅适用于 library_type=knowledge）
class KnowledgeVersionSectionInput(BaseModel):
    stable_section_key: Optional[str] = Field(default=None, max_length=255)
    parent_section_key: Optional[str] = Field(default=None, max_length=255)
    section_index: Optional[int] = None
    section_title: Optional[str] = Field(default=None, max_length=255)
    section_type: Optional[str] = Field(default="1", max_length=64)
    plain_text: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class KnowledgeVersionFileInput(BaseModel):
    file_role: str = Field(default="attachment", pattern=r"^(primary|attachment|image|video|reference)$")
    original_name: str = Field(..., min_length=1, max_length=255)
    storage_path: str = Field(..., min_length=1)
    mime_type: Optional[str] = Field(default=None, max_length=128)
    file_size: Optional[int] = Field(default=None, ge=0)
    content_hash: Optional[str] = Field(default=None, max_length=64)


class KnowledgeApplicabilityInput(BaseModel):
    machine_model_id: Optional[int] = None
    machine_revision_id: Optional[int] = None
    serial_number_from: Optional[str] = Field(default=None, max_length=128)
    serial_number_to: Optional[str] = Field(default=None, max_length=128)
    firmware_min: Optional[str] = Field(default=None, max_length=128)
    firmware_max: Optional[str] = Field(default=None, max_length=128)
    region_code: Optional[str] = Field(default=None, max_length=64)
    site_id: Optional[int] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    priority: int = 100
    applicability_status: str = Field(default="active", pattern=r"^(active|excluded|inactive)$")
    exclusion_reason: Optional[str] = None


class KnowledgeVersionCreate(BaseModel):
    based_on_version_id: Optional[int] = None
    hardware_version: Optional[str] = Field(default=None, max_length=128)
    planned_publish_mode: str = Field(default="replace", pattern=r"^(replace|coexist)$")
    planned_supersedes_version_ids: List[int] = Field(default_factory=list)
    change_category: str = Field(default="minor", pattern=r"^(minor|major|correction|rollback|emergency)$")
    change_summary: str = Field(..., min_length=1)
    change_reason: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=255)
    tag: Optional[List[int]] = None
    image_urls: Optional[str] = None
    origin_file_name: Optional[str] = Field(default=None, max_length=255)
    origin_file_dir: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    sections: Optional[List[KnowledgeVersionSectionInput]] = None
    files: Optional[List[KnowledgeVersionFileInput]] = None
    applicability: Optional[List[KnowledgeApplicabilityInput]] = None


class KnowledgeVersionUpdate(BaseModel):
    revision: int = Field(..., ge=1)
    hardware_version: Optional[str] = Field(default=None, max_length=128)
    planned_publish_mode: Optional[str] = Field(default=None, pattern=r"^(replace|coexist)$")
    planned_supersedes_version_ids: Optional[List[int]] = None
    change_category: Optional[str] = Field(default=None, pattern=r"^(minor|major|correction|rollback|emergency)$")
    change_summary: Optional[str] = Field(default=None, min_length=1)
    change_reason: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=255)
    tag: Optional[List[int]] = None
    image_urls: Optional[str] = None
    origin_file_name: Optional[str] = Field(default=None, max_length=255)
    origin_file_dir: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    sections: Optional[List[KnowledgeVersionSectionInput]] = None
    files: Optional[List[KnowledgeVersionFileInput]] = None
    applicability: Optional[List[KnowledgeApplicabilityInput]] = None


class KnowledgeVersionDecision(BaseModel):
    comment: Optional[str] = None


class KnowledgeVersionPublish(BaseModel):
    hardware_version: Optional[str] = Field(default=None, max_length=128)
    existing_hardware_versions: Dict[int, str] = Field(default_factory=dict)
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    publish_mode: str = Field(default="replace", pattern=r"^(replace|coexist)$")
    supersedes_version_ids: List[int] = Field(default_factory=list)


class KnowledgeVersionInvalidate(BaseModel):
    reason: str = Field(..., min_length=1)
    new_default_version_id: Optional[int] = None


class KnowledgeVersionRollback(BaseModel):
    source_version_id: int
    change_summary: str = Field(..., min_length=1)
    change_reason: Optional[str] = None


class KnowledgeDocumentMetadataUpdate(BaseModel):
    document_code: Optional[str] = Field(default=None, max_length=128)
    document_type: Optional[str] = Field(default=None, max_length=64)
    category_id: Optional[int] = Field(default=None, ge=1)
    category_path: Optional[str] = Field(default=None, max_length=512)
    lifecycle_status: Optional[str] = Field(default=None, pattern=r"^(active|archived)$")
    applicability_mode: Optional[str] = Field(default=None, pattern=r"^(global|conditional|reference_only)$")


class KnowledgeCategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    parent_id: Optional[int] = Field(default=None, ge=1)
    sort_order: int = 0


class KnowledgeCategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    parent_id: Optional[int] = Field(default=None, ge=1)
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


class MachineModelInput(BaseModel):
    model_code: str = Field(..., min_length=1, max_length=128)
    model_name: str = Field(..., min_length=1, max_length=255)
    lifecycle_status: str = Field(default="active", pattern=r"^(active|discontinued|retired)$")
    production_start: Optional[datetime] = None
    production_end: Optional[datetime] = None
    service_end: Optional[datetime] = None
    aliases: List[str] = Field(default_factory=list)


class MachineRevisionInput(BaseModel):
    revision_code: str = Field(..., min_length=1, max_length=128)
    hardware_version: Optional[str] = Field(default=None, max_length=128)
    software_version_min: Optional[str] = Field(default=None, max_length=128)
    software_version_max: Optional[str] = Field(default=None, max_length=128)
    serial_number_from: Optional[str] = Field(default=None, max_length=128)
    serial_number_to: Optional[str] = Field(default=None, max_length=128)
    production_start: Optional[datetime] = None
    production_end: Optional[datetime] = None


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
