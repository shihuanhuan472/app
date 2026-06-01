from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    phone = Column(String(20), unique=True, nullable=False)
    email = Column(String(100), unique=True)
    full_name = Column(String(100), nullable=False)
    status = Column(Integer, default=1)  # 0-disabled, 1-enabled
    role = Column(Integer, default=1)  # 0-admin, 1-technician, 2-reviewer, 3-maintenance
    perm = Column(Integer, default=1)  # 0-admin, 1-read/write, 2-review, 3-readonly
    department = Column(String(100))
    created_time = Column(DateTime)
    last_login = Column(DateTime)


class Document(Base):
    __tablename__ = "documents"

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

    contributor = relationship("User")


class Document_review(Base):
    __tablename__ = "document_reviews"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"), index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    first_edit_date = Column(DateTime)
    reviewed_time = Column(DateTime, nullable=True)
    status = Column(Integer, default=0, nullable=False, index=True)  # 0-pending, 1-approved, 2-rejected, 3-withdrawn
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
    action_type = Column(Integer, nullable=False)  # 1-create, 2-update, 3-delete
    review_comment = Column(Text)

    # Explicit foreign keys are required because both contributor_id and reviewer_id reference users.id
    contributor = relationship("User", foreign_keys=[contributor_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    document = relationship("Document", foreign_keys=[document_id])


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
    created_time = Column(DateTime)

    conversation = relationship("Conversation", back_populates="messages")
