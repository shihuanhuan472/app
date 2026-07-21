from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import relationship

from database import Base


LargeText = Text().with_variant(MEDIUMTEXT(), "mysql")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(20), unique=True, nullable=False)
    email = Column(String(100), unique=True)
    full_name = Column(String(100), nullable=False)
    status = Column(Integer, default=1)  # 0-disabled, 1-enabled
    role = Column(
        Integer, default=1
    )  # 0-admin, 1-technician, 2-reviewer, 3-maintenance
    perm = Column(Integer, default=1)  # 0-admin, 1-read/write, 2-review, 3-readonly
    role_group_id = Column(Integer, ForeignKey("role_groups.id"), nullable=True, index=True)
    department = Column(String(100))
    api_key = Column(String(128), unique=True, index=True)
    created_time = Column(DateTime)
    last_login = Column(DateTime)

    role_group = relationship("RoleGroup")


class RoleGroup(Base):
    __tablename__ = "role_groups"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    is_system = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    permissions = relationship(
        "RoleGroupPermission",
        back_populates="role_group",
        cascade="all, delete-orphan",
    )


class RoleGroupPermission(Base):
    __tablename__ = "role_group_permissions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    role_group_id = Column(Integer, ForeignKey("role_groups.id"), nullable=False, index=True)
    permission_code = Column(String(64), nullable=False, index=True)

    role_group = relationship("RoleGroup", back_populates="permissions")


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    origin_file_name = Column(String(255), nullable=False)
    stored_file_path = Column(Text, nullable=False)
    file_ext = Column(String(20))
    file_category = Column(String(50), index=True)
    file_size = Column(Integer, default=0)
    uploader_id = Column(Integer, ForeignKey("users.id"), index=True)
    upload_time = Column(DateTime)
    status = Column(String(30), default="uploaded", nullable=False, index=True)
    parse_error = Column(Text)
    parse_started_time = Column(DateTime, nullable=True)
    document_id = Column(Integer, nullable=True, index=True)
    document_library_type = Column(
        String(32), default="breakdown", nullable=False, index=True
    )
    review_id = Column(Integer, nullable=True, index=True)
    review_library_type = Column(
        String(32), default="breakdown", nullable=False, index=True
    )
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    deleted_time = Column(DateTime)

    uploader = relationship("User")


class ParseTask(Base):
    __tablename__ = "parse_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), default="pending", nullable=False, index=True)
    total_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    current_file_name = Column(String(255))
    submit_for_review = Column(Integer, default=0, nullable=False)
    library_type = Column(String(32), default="breakdown", nullable=False)
    tag = Column(JSON, nullable=True)
    error_message = Column(Text)
    created_time = Column(DateTime)
    started_time = Column(DateTime)
    finished_time = Column(DateTime)

    user = relationship("User")
    items = relationship("ParseTaskItem", back_populates="task", cascade="all, delete-orphan")


class ParseTaskItem(Base):
    __tablename__ = "parse_task_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("parse_tasks.id"), nullable=False, index=True)
    source_document_id = Column(Integer, ForeignKey("source_documents.id"), nullable=True, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    status = Column(String(30), default="pending", nullable=False, index=True)
    error_reason = Column(Text)
    error_code = Column(Integer)
    document_id = Column(Integer, nullable=True, index=True)
    document_library_type = Column(String(32), default="breakdown", nullable=False)
    started_time = Column(DateTime)
    finished_time = Column(DateTime)
    elapsed_seconds = Column(Integer)

    task = relationship("ParseTask", back_populates="items")
    source_document = relationship("SourceDocument")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    description = Column(Text)
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    creator = relationship("User")


class DocumentBreakdown(Base):
    __tablename__ = "document_breakdown"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"))
    first_edit_date = Column(DateTime)
    problem_intro = Column(Text)
    image_urls = Column(Text)
    image_urls_problem_intro = Column(Text)
    causes = Column(Text)
    image_urls_causes = Column(Text)
    evaluation = Column(Text)
    image_urls_evaluation = Column(Text)
    inspection = Column(Text)
    image_urls_inspection = Column(Text)
    solutions = Column(Text)
    image_urls_solutions = Column(Text)
    key_points = Column(Text)
    image_urls_key_points = Column(Text)
    is_vectorized = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    vector_update_time = Column(DateTime, nullable=True)
    origin_file_name = Column(String(255))
    origin_file_dir = Column(Text)
    tag = Column(JSON, default=list)
    library_type = "breakdown"

    contributor = relationship("User")


class DocumentKnowledge(Base):
    __tablename__ = "document_knowledge"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    library_type = Column(String(32), default="knowledge", server_default="knowledge", nullable=False, index=True)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"))
    first_edit_date = Column(DateTime)
    image_urls = Column(LargeText)
    section_ids = Column("sections", JSON, default=list)
    is_vectorized = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Integer, default=0, nullable=False, index=True)
    vector_update_time = Column(DateTime, nullable=True)
    origin_file_name = Column(String(255))
    origin_file_dir = Column(Text)
    tag = Column(JSON, default=list)

    contributor = relationship("User")


class KnowledgeDocumentSection(Base):
    __tablename__ = "knowledge_document_sections"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(
        Integer, ForeignKey("document_knowledge.id"), nullable=False, index=True
    )
    document_library_type = Column(
        String(32), default="knowledge", nullable=False, index=True
    )
    section_index = Column(Integer, nullable=False, default=0)
    section_title = Column(String(255))
    section_type = Column(String(64), default="1")
    plain_text = Column(LargeText)
    image_urls = Column(JSON, default=list)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    section_metadata = Column("metadata", JSON, default=dict)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    document = relationship("DocumentKnowledge", back_populates="section_items")


DocumentKnowledge.section_items = relationship(
    "KnowledgeDocumentSection",
    back_populates="document",
    cascade="all, delete-orphan",
    order_by=KnowledgeDocumentSection.section_index,
)


Document = DocumentBreakdown


class Document_review(Base):
    __tablename__ = "document_reviews"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, nullable=True, index=True)
    document_library_type = Column(String(32), default="breakdown", nullable=False)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"), index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    first_edit_date = Column(DateTime)
    reviewed_time = Column(DateTime, nullable=True)
    status = Column(
        Integer, default=0, nullable=False, index=True
    )  # 0-pending, 1-approved, 2-rejected, 3-withdrawn
    problem_intro = Column(Text)
    image_urls = Column(Text)
    image_urls_problem_intro = Column(Text)
    causes = Column(Text)
    image_urls_causes = Column(Text)
    evaluation = Column(Text)
    image_urls_evaluation = Column(Text)
    inspection = Column(Text)
    image_urls_inspection = Column(Text)
    solutions = Column(Text)
    image_urls_solutions = Column(Text)
    key_points = Column(Text)
    image_urls_key_points = Column(Text)
    origin_file_name = Column(String(255))
    origin_file_dir = Column(Text)
    tag = Column(JSON, default=list)
    action_type = Column(Integer, nullable=False)  # 1-create, 2-update, 3-delete
    review_comment = Column(Text)

    # Explicit foreign keys are required because both contributor_id and reviewer_id reference users.id
    contributor = relationship("User", foreign_keys=[contributor_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])


class KnowledgeDocumentReview(Base):
    __tablename__ = "knowledge_document_reviews"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, nullable=True, index=True)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"), index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    first_edit_date = Column(DateTime)
    reviewed_time = Column(DateTime, nullable=True)
    status = Column(
        Integer, default=0, nullable=False, index=True
    )  # 0-pending, 1-approved, 2-rejected, 3-withdrawn
    image_urls = Column(LargeText)
    origin_file_name = Column(String(255))
    origin_file_dir = Column(Text)
    tag = Column(JSON, default=list)
    sections = Column(JSON, nullable=True)
    action_type = Column(Integer, nullable=False)  # 1-create, 2-update, 3-delete
    review_comment = Column(Text)

    contributor = relationship("User", foreign_keys=[contributor_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])


class Conversation(Base):
    __tablename__ = "conversation"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255))
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    user = relationship("User")
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    __tablename__ = "message"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False)
    message_order = Column(Integer, nullable=False)
    role = Column(Integer, nullable=False)  # 0-AI, 1-user
    content_text = Column(Text)
    user_uploaded_images = Column(Text)
    ai_reference_doc_ids = Column(Text)
    token_count = Column(Integer, default=0, nullable=False)
    created_time = Column(DateTime)

    conversation = relationship("Conversation", back_populates="messages")


class AiUsageLog(Base):
    __tablename__ = "ai_usage_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=True, index=True)
    message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    provider = Column(String(32), default="openai", nullable=False, index=True)
    model = Column(String(255), default="", nullable=False, index=True)
    request_type = Column(String(64), default="", nullable=False, index=True)
    status = Column(String(32), default="success", nullable=False, index=True)
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    raw_usage_json = Column(Text)
    error_message = Column(Text)
    created_time = Column(DateTime, index=True)

    user = relationship("User")
    conversation = relationship("Conversation")
    message = relationship("Message")
