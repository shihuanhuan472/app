from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


FEEDBACK_TYPES = {
    "positive",
    "negative",
    "correction",
    "confirmation",
    "additional_information",
    "irrelevant",
    "suspicious",
}

FEEDBACK_ANALYSIS_TYPES = {
    "confirmation",
    "correction",
    "additional_information",
    "dissatisfaction",
    "irrelevant",
    "unclear",
    "suspicious",
}

FEEDBACK_RISK_LEVELS = {"low", "medium", "high", "critical"}

FEEDBACK_RECOMMENDED_ACTIONS = {
    "ignore",
    "store_only",
    "verify",
    "create_candidate_case",
    "create_retrieval_patch",
    "require_human_review",
}

FEEDBACK_SCOPE_VALUES = {"feedback", "user", "conversation", "device", "fault_type", "global"}

CLAIM_SOURCE_VALUES = {"user", "assistant", "document", "sensor", "maintenance_record", "system"}

VERIFICATION_STATUS_VALUES = {"unverified", "partially_verified", "verified", "contradicted"}

VERIFIER_TYPE_VALUES = {"rule", "retrieval", "llm", "cross_case", "maintenance_outcome", "human"}

VERIFICATION_RESULT_VALUES = {"support", "contradict", "insufficient", "uncertain"}

EVIDENCE_RESULT_VALUES = {"supported", "contradicted", "insufficient", "uncertain"}

PATCH_TYPE_VALUES = {"boost", "penalty", "preferred_document", "exclusion"}
PATCH_SCOPE_VALUES = {"conversation", "device", "fault_type", "user"}
PATCH_STATUS_VALUES = {"candidate", "active", "revoked", "expired", "rejected"}


class FeedbackClaimPayload(BaseModel):
    claim_type: str = Field(..., min_length=1, max_length=64)
    subject: Optional[str] = Field(default=None, max_length=255)
    predicate: Optional[str] = Field(default=None, max_length=128)
    object: Optional[str] = None
    scope: Optional[str] = Field(default=None, max_length=255)
    source: str = Field(default="user", min_length=1, max_length=32)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_status: str = Field(default="unverified", min_length=1, max_length=32)
    evidence_ids: List[str] = Field(default_factory=list)


class FeedbackEvidenceRequirement(BaseModel):
    evidence_type: str = Field(..., min_length=1, max_length=64)
    source_hint: Optional[str] = Field(default=None, max_length=255)
    reason: Optional[str] = None


class FeedbackAnalysis(BaseModel):
    feedback_type: str = Field(..., min_length=1, max_length=32)
    claims: List[FeedbackClaimPayload] = Field(default_factory=list)
    evidence_requirements: List[FeedbackEvidenceRequirement] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    risk_level: str = Field(..., min_length=1, max_length=16)
    recommended_action: str = Field(..., min_length=1, max_length=32)
    scope: str = Field(..., min_length=1, max_length=32)
    conflict: bool = False
    conflict_reason: Optional[str] = None
    document_preference: Optional[str] = None


class FeedbackVerificationRequest(BaseModel):
    feedback_id: int = Field(..., gt=0)
    user_id: Optional[int] = Field(default=None, gt=0)


class FeedbackVerificationResult(BaseModel):
    analysis: FeedbackAnalysis
    verification_id: Optional[int] = None
    created_claim_ids: List[int] = Field(default_factory=list)
    created_audit_log_ids: List[int] = Field(default_factory=list)
    created_verification: bool = False


class EvidenceItem(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=64)
    source_id: str = Field(..., min_length=1, max_length=255)
    content: Optional[str] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_score: float = Field(default=0.0, ge=0.0, le=1.0)


class EvidenceVerificationResult(BaseModel):
    claim: FeedbackClaimPayload
    supporting_evidence: List[EvidenceItem] = Field(default_factory=list)
    contradicting_evidence: List[EvidenceItem] = Field(default_factory=list)
    evidence_count: int = 0
    support_score: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_score: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    result: str = Field(..., min_length=1, max_length=32)


class PatchDecision(BaseModel):
    patch_type: str = Field(..., min_length=1, max_length=32)
    document_id: Optional[int] = Field(default=None, gt=0)
    query_signature: str = Field(..., min_length=1, max_length=255)
    scope: str = Field(..., min_length=1, max_length=32)
    weight: float = Field(default=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = None
    reason: str = Field(default="", max_length=4000)


class PatchQueryContext(BaseModel):
    query: str
    user_id: Optional[int] = None
    conversation_id: Optional[int] = None
    device: Optional[str] = None
    device_type: Optional[str] = None
    firmware_version: Optional[str] = None
    component: Optional[str] = None
    fault_type: Optional[str] = None


class FeedbackIngestCreate(BaseModel):
    user_id: int = Field(..., gt=0)
    conversation_id: int = Field(..., gt=0)
    message_id: int = Field(..., gt=0)
    feedback_type: str = Field(..., min_length=1, max_length=32)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=4000)


class FeedbackIngestResponse(BaseModel):
    id: int
    conversation_id: int
    message_id: int
    user_id: int
    feedback_type: str
    rating: Optional[int] = None
    comment: Optional[str] = None
    created_at: Optional[datetime] = None
    query_snapshot: Dict[str, Any] = Field(default_factory=dict)
    answer_snapshot: Dict[str, Any] = Field(default_factory=dict)
    retrieved_documents: List[Dict[str, Any]] = Field(default_factory=list)
    cited_documents: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[int] = None
    status: str = "pending"


class FeedbackIngestResult(BaseModel):
    feedback: FeedbackIngestResponse
    created: bool = True
