from typing import Optional

from pydantic import BaseModel
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
    status = Column(Integer, default=1)  # 0-禁用，1-启用
    role = Column(Integer, default=1)  # 0-管理员，1-技术人员
    department = Column(String(100))
    created_time = Column(DateTime)
    last_login = Column(DateTime)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    contributor_id = Column(Integer, ForeignKey("users.id"))
    first_edit_date = Column(DateTime)
    problem_intro = Column(Text, nullable=False)
    image_urls = Column(Text)
    causes = Column(Text, nullable=False)
    evaluation = Column(Text, nullable=False)
    inspection = Column(Text, nullable=False)
    solutions = Column(Text, nullable=False)
    key_points = Column(Text, nullable=False)

    # 关系
    contributor = relationship("User")


class Conversation(Base):
    __tablename__ = "conversation"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255))
    created_time = Column(DateTime)
    updated_time = Column(DateTime)

    # 关系
    user = relationship("User")
    messages = relationship("Message", back_populates="conversation")


class Message(Base):
    __tablename__ = "message"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("conversation.id"), nullable=False)
    message_order = Column(Integer, nullable=False)
    role = Column(Integer, nullable=False)  # 0-AI，1-用户
    content_text = Column(Text)
    user_uploaded_images = Column(Text)
    ai_reference_doc_ids = Column(Text)
    created_time = Column(DateTime)

    # 关系
    conversation = relationship("Conversation", back_populates="messages")