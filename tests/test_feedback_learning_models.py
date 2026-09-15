from models import (
    FAULT_CASE_VERIFICATION_STATUSES,
    FEEDBACK_CLAIM_SOURCES,
    FEEDBACK_CLAIM_VERIFICATION_STATUSES,
    FEEDBACK_RECORD_STATUSES,
    FEEDBACK_RECORD_TYPES,
    FEEDBACK_VERIFICATION_RESULTS,
    FEEDBACK_VERIFIER_TYPES,
    RETRIEVAL_PATCH_SCOPES,
    RETRIEVAL_PATCH_TYPES,
    FaultCaseMemory,
    FeedbackAuditLog,
    FeedbackClaim,
    FeedbackEvidence,
    FeedbackRecord,
    FeedbackVerification,
    RetrievalPatch,
    UserReliability,
)


def column_names(model):
    return set(model.__table__.columns.keys())


def index_column_sets(model):
    return {tuple(column.name for column in index.columns) for index in model.__table__.indexes}


def test_feedback_record_schema_and_supported_values():
    assert column_names(FeedbackRecord) >= {
        "id",
        "conversation_id",
        "message_id",
        "user_id",
        "feedback_type",
        "rating",
        "comment",
        "created_at",
        "query_snapshot",
        "answer_snapshot",
        "retrieved_documents",
        "cited_documents",
        "trace_id",
        "status",
    }
    assert FEEDBACK_RECORD_TYPES >= {
        "positive",
        "negative",
        "correction",
        "confirmation",
        "additional_information",
        "irrelevant",
        "suspicious",
    }
    assert FEEDBACK_RECORD_STATUSES >= {"pending", "verified", "rejected", "quarantined", "promoted"}
    indexes = index_column_sets(FeedbackRecord)
    assert ("conversation_id",) in indexes
    assert ("message_id",) in indexes
    assert ("user_id",) in indexes
    assert ("trace_id",) in indexes


def test_feedback_claim_evidence_and_verification_schema():
    assert column_names(FeedbackClaim) >= {
        "id",
        "feedback_id",
        "claim_type",
        "subject",
        "predicate",
        "object",
        "scope",
        "source",
        "confidence",
        "verification_status",
        "evidence_ids",
        "created_at",
    }
    assert FEEDBACK_CLAIM_SOURCES >= {"user", "assistant", "document", "sensor", "maintenance_record", "system"}
    assert FEEDBACK_CLAIM_VERIFICATION_STATUSES >= {
        "unverified",
        "partially_verified",
        "verified",
        "contradicted",
    }
    assert ("feedback_id",) in index_column_sets(FeedbackClaim)
    assert ("feedback_claim_id",) in index_column_sets(FeedbackEvidence)
    assert ("feedback_id",) in index_column_sets(FeedbackVerification)
    assert FEEDBACK_VERIFIER_TYPES >= {"rule", "retrieval", "llm", "cross_case", "maintenance_outcome", "human"}
    assert FEEDBACK_VERIFICATION_RESULTS >= {"support", "contradict", "insufficient", "uncertain"}


def test_user_reliability_uses_decay_ready_fields():
    assert column_names(UserReliability) >= {
        "user_id",
        "total_feedback",
        "verified_feedback",
        "rejected_feedback",
        "correction_feedback",
        "correct_correction_count",
        "reliability_score",
        "decay_factor",
        "recent_verified_weight",
        "recent_rejected_weight",
        "recent_correction_weight",
        "last_updated_at",
    }


def test_fault_case_memory_and_retrieval_patch_schema_boundaries():
    assert column_names(FaultCaseMemory) >= {
        "id",
        "case_key",
        "scope",
        "device",
        "device_type",
        "component",
        "firmware_version",
        "symptoms",
        "metrics",
        "alarm_codes",
        "confirmed_fault",
        "root_cause",
        "actions",
        "outcome",
        "evidence_ids",
        "confidence",
        "verification_status",
        "source_feedback_ids",
        "created_at",
        "updated_at",
    }
    assert FAULT_CASE_VERIFICATION_STATUSES >= {"candidate", "verified", "expert_verified"}
    assert "feedback_claims" not in {fk.column.table.name for fk in FaultCaseMemory.__table__.foreign_keys}
    assert ("scope",) in index_column_sets(FaultCaseMemory)

    assert column_names(RetrievalPatch) >= {
        "id",
        "feedback_id",
        "scope",
        "query_signature",
        "document_id",
        "patch_type",
        "weight",
        "confidence",
        "expires_at",
        "status",
        "created_at",
    }
    assert RETRIEVAL_PATCH_TYPES >= {"boost", "penalty", "exclusion", "preferred_document"}
    assert RETRIEVAL_PATCH_SCOPES >= {"feedback", "user", "conversation", "device", "fault_type", "global"}
    assert ("document_id",) in index_column_sets(RetrievalPatch)
    assert ("scope", "query_signature", "document_id") in index_column_sets(RetrievalPatch)


def test_feedback_audit_log_can_explain_learning_chain():
    assert column_names(FeedbackAuditLog) >= {
        "id",
        "feedback_id",
        "action",
        "before_state",
        "after_state",
        "reason",
        "actor",
        "created_at",
    }
    assert ("feedback_id",) in index_column_sets(FeedbackAuditLog)
