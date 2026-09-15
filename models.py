from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy import Float, Index, UniqueConstraint
from sqlalchemy.orm import relationship

from database import Base


LargeText = Text().with_variant(MEDIUMTEXT(), "mysql")

FEEDBACK_RECORD_TYPES = {
    "positive",
    "negative",
    "correction",
    "confirmation",
    "additional_information",
    "irrelevant",
    "suspicious",
}
FEEDBACK_RECORD_STATUSES = {"pending", "verified", "rejected", "quarantined", "promoted"}
FEEDBACK_CLAIM_SOURCES = {"user", "assistant", "document", "sensor", "maintenance_record", "system"}
FEEDBACK_CLAIM_VERIFICATION_STATUSES = {
    "unverified",
    "partially_verified",
    "verified",
    "contradicted",
}
FEEDBACK_VERIFIER_TYPES = {"rule", "retrieval", "llm", "cross_case", "maintenance_outcome", "human"}
FEEDBACK_VERIFICATION_RESULTS = {"support", "contradict", "insufficient", "uncertain"}
FAULT_CASE_VERIFICATION_STATUSES = {"candidate", "verified", "expert_verified", "rejected"}
RETRIEVAL_PATCH_TYPES = {"boost", "penalty", "exclusion", "preferred_document"}
RETRIEVAL_PATCH_SCOPES = {"feedback", "user", "conversation", "device", "fault_type", "global"}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(20), unique=True, nullable=False)
    email = Column(String(100), unique=True)
    full_name = Column(String(100), nullable=False)
    status = Column(Integer, default=1)  # 0-disabled, 1-enabled
    registration_status = Column(
        String(20), default="approved", nullable=False, index=True
    )  # pending, approved, rejected
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


class ConversationContext(Base):
    __tablename__ = "conversation_contexts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False, unique=True, index=True)
    status = Column(String(32), default="active", nullable=False, index=True)
    active_issue = Column(Text)
    active_device = Column(String(255))
    active_component = Column(String(255))
    active_symptom = Column(Text)
    active_error_code = Column(String(64))
    active_question = Column(Text)
    active_query = Column(Text)
    active_route = Column(String(64), index=True)
    active_reason = Column(String(255))
    active_reference_docs_json = Column(LargeText)
    active_reference_images_json = Column(LargeText)
    slots_json = Column(LargeText)
    summary_text = Column(LargeText)
    last_user_message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    last_ai_message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    last_trace_id = Column(Integer, nullable=True, index=True)
    turn_count = Column(Integer, default=0, nullable=False)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    conversation = relationship("Conversation")


class ConversationContextEvent(Base):
    __tablename__ = "conversation_context_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False, index=True)
    user_message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    ai_message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    route = Column(String(64), index=True)
    reason = Column(String(255))
    source = Column(String(64))
    previous_context_json = Column(LargeText)
    new_context_json = Column(LargeText)
    created_time = Column(DateTime)

    conversation = relationship("Conversation")
    user_message = relationship("Message", foreign_keys=[user_message_id])
    ai_message = relationship("Message", foreign_keys=[ai_message_id])


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False, index=True)
    start_message_order = Column(Integer, default=1, nullable=False)
    end_message_order = Column(Integer, nullable=False, index=True)
    message_count = Column(Integer, default=0, nullable=False)
    token_count = Column(Integer, default=0, nullable=False)
    summary_text = Column(LargeText, nullable=False)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    conversation = relationship("Conversation")


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


class AiMessageTrace(Base):
    __tablename__ = "ai_message_traces"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False, index=True)
    user_message_id = Column(Integer, ForeignKey("message.id"), nullable=False, index=True)
    ai_message_id = Column(Integer, ForeignKey("message.id"), nullable=True, index=True)
    route = Column(String(64), nullable=False, index=True)
    reason = Column(String(255))
    original_question = Column(Text)
    query_rewrite = Column(Text)
    retrieval_query = Column(Text)
    used_previous_refs = Column(Integer, default=0, nullable=False)
    reference_docs_json = Column(LargeText)
    reference_images_json = Column(LargeText)
    actions_json = Column(LargeText)
    validation_json = Column(LargeText)
    answer_preview = Column(Text)
    model_name = Column(String(255))
    input_tokens = Column(Integer, default=0, nullable=False)
    output_tokens = Column(Integer, default=0, nullable=False)
    latency_ms = Column(Integer, default=0, nullable=False)
    status = Column(String(32), default="success", nullable=False, index=True)
    error_message = Column(Text)
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    conversation = relationship("Conversation")
    user_message = relationship("Message", foreign_keys=[user_message_id])
    ai_message = relationship("Message", foreign_keys=[ai_message_id])


class FeedbackRecord(Base):
    __tablename__ = "feedback_records"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "message_id",
            "user_id",
            "feedback_type",
            name="uq_feedback_records_conversation_message_user_type",
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversation.id"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("message.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    feedback_type = Column(String(32), nullable=False, index=True)
    rating = Column(Integer, nullable=True)
    comment = Column(Text)
    created_at = Column(DateTime, index=True)
    query_snapshot = Column(LargeText)
    answer_snapshot = Column(LargeText)
    retrieved_documents = Column(LargeText)
    cited_documents = Column(LargeText)
    trace_id = Column(Integer, ForeignKey("ai_message_traces.id"), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)

    conversation = relationship("Conversation")
    message = relationship("Message")
    user = relationship("User")
    trace = relationship("AiMessageTrace")
    claims = relationship("FeedbackClaim", back_populates="feedback")
    verifications = relationship("FeedbackVerification", back_populates="feedback")
    retrieval_patches = relationship("RetrievalPatch", back_populates="feedback")
    audit_logs = relationship("FeedbackAuditLog", back_populates="feedback")


class FeedbackClaim(Base):
    __tablename__ = "feedback_claims"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    feedback_id = Column(Integer, ForeignKey("feedback_records.id"), nullable=False, index=True)
    claim_type = Column(String(64), nullable=False, index=True)
    subject = Column(String(255), nullable=True, index=True)
    predicate = Column(String(128), nullable=True, index=True)
    object = Column(Text)
    scope = Column(String(255), nullable=True, index=True)
    source = Column(String(32), nullable=False, default="user", index=True)
    confidence = Column(Float, default=0.0, nullable=False)
    verification_status = Column(String(32), nullable=False, default="unverified", index=True)
    evidence_ids = Column(LargeText)
    created_at = Column(DateTime, index=True)

    feedback = relationship("FeedbackRecord", back_populates="claims")
    evidence = relationship("FeedbackEvidence", back_populates="claim")


class FeedbackEvidence(Base):
    __tablename__ = "feedback_evidence"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    feedback_claim_id = Column(Integer, ForeignKey("feedback_claims.id"), nullable=False, index=True)
    evidence_type = Column(String(64), nullable=False, index=True)
    source_id = Column(String(255), nullable=True, index=True)
    content = Column(LargeText)
    relevance_score = Column(Float, default=0.0, nullable=False)
    support_score = Column(Float, default=0.0, nullable=False)
    contradiction_score = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime, index=True)

    claim = relationship("FeedbackClaim", back_populates="evidence")


class FeedbackVerification(Base):
    __tablename__ = "feedback_verifications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    feedback_id = Column(Integer, ForeignKey("feedback_records.id"), nullable=False, index=True)
    verifier_type = Column(String(32), nullable=False, index=True)
    verification_result = Column(String(32), nullable=False, index=True)
    confidence = Column(Float, default=0.0, nullable=False)
    reason = Column(Text)
    evidence_summary = Column(LargeText)
    created_at = Column(DateTime, index=True)

    feedback = relationship("FeedbackRecord", back_populates="verifications")


class UserReliability(Base):
    __tablename__ = "user_reliability"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    total_feedback = Column(Integer, default=0, nullable=False)
    verified_feedback = Column(Integer, default=0, nullable=False)
    rejected_feedback = Column(Integer, default=0, nullable=False)
    correction_feedback = Column(Integer, default=0, nullable=False)
    correct_correction_count = Column(Integer, default=0, nullable=False)
    reliability_score = Column(Float, default=0.5, nullable=False, index=True)
    decay_factor = Column(Float, default=0.98, nullable=False)
    recent_verified_weight = Column(Float, default=0.0, nullable=False)
    recent_rejected_weight = Column(Float, default=0.0, nullable=False)
    recent_correction_weight = Column(Float, default=0.0, nullable=False)
    last_updated_at = Column(DateTime, index=True)

    user = relationship("User")


class FaultCaseMemory(Base):
    __tablename__ = "fault_case_memory"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    case_key = Column(String(255), nullable=False, unique=True, index=True)
    scope = Column(String(255), nullable=True, index=True)
    device = Column(String(255), nullable=True, index=True)
    device_type = Column(String(255), nullable=True, index=True)
    component = Column(String(255), nullable=True, index=True)
    firmware_version = Column(String(128), nullable=True, index=True)
    symptoms = Column(LargeText)
    metrics = Column(LargeText)
    alarm_codes = Column(LargeText)
    confirmed_fault = Column(Text)
    root_cause = Column(Text)
    actions = Column(LargeText)
    outcome = Column(Text)
    evidence_ids = Column(LargeText)
    confidence = Column(Float, default=0.0, nullable=False)
    verification_status = Column(String(32), nullable=False, default="candidate", index=True)
    source_feedback_ids = Column(LargeText)
    created_at = Column(DateTime, index=True)
    updated_at = Column(DateTime, index=True)


class RetrievalPatch(Base):
    __tablename__ = "retrieval_patches"
    __table_args__ = (
        Index("idx_retrieval_patches_scope_query_doc", "scope", "query_signature", "document_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    feedback_id = Column(Integer, ForeignKey("feedback_records.id"), nullable=False, index=True)
    scope = Column(String(32), nullable=False, default="feedback", index=True)
    query_signature = Column(String(255), nullable=False, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    patch_type = Column(String(32), nullable=False, index=True)
    weight = Column(Float, default=0.0, nullable=False)
    confidence = Column(Float, default=0.0, nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, index=True)

    feedback = relationship("FeedbackRecord", back_populates="retrieval_patches")


class FeedbackAuditLog(Base):
    __tablename__ = "feedback_audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    feedback_id = Column(Integer, ForeignKey("feedback_records.id"), nullable=False, index=True)
    action = Column(String(64), nullable=False, index=True)
    before_state = Column(LargeText)
    after_state = Column(LargeText)
    reason = Column(Text)
    actor = Column(String(64), nullable=False, default="system", index=True)
    created_at = Column(DateTime, index=True)

    feedback = relationship("FeedbackRecord", back_populates="audit_logs")


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
