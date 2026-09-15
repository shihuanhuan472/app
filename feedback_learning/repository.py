import json
from datetime import datetime
from typing import Optional

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    AiMessageTrace,
    Conversation,
    ConversationContext,
    FeedbackAuditLog,
    FeedbackClaim,
    FeedbackEvidence,
    FeedbackRecord,
    FeedbackVerification,
    FaultCaseMemory,
    RetrievalPatch,
    UserReliability,
    Message,
)
from .config import feedback_enabled


class FeedbackLearningRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_conversation(self, conversation_id: int) -> Optional[Conversation]:
        result = await self.db.execute(select(Conversation).where(Conversation.id == conversation_id))
        return result.scalar_one_or_none()

    async def get_message(self, message_id: int) -> Optional[Message]:
        result = await self.db.execute(select(Message).where(Message.id == message_id))
        return result.scalar_one_or_none()

    async def get_latest_trace_for_message(
        self,
        message_id: int,
        conversation_id: int,
    ) -> Optional[AiMessageTrace]:
        result = await self.db.execute(
            select(AiMessageTrace)
            .where(
                AiMessageTrace.ai_message_id == message_id,
                AiMessageTrace.session_id == conversation_id,
            )
            .order_by(desc(AiMessageTrace.updated_time), desc(AiMessageTrace.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def is_feedback_eligible(
        self,
        message_id: int,
        conversation_id: int,
    ) -> bool:
        if not feedback_enabled():
            return False
        trace = await self.get_latest_trace_for_message(message_id, conversation_id)
        message = await self.get_message(message_id)
        raw_message_documents = getattr(message, "ai_reference_doc_ids", None)
        if not trace:
            # Historical RAG answers may have reference IDs but no persisted trace.
            # Keep them eligible instead of silently hiding the feedback control.
            has_message_documents = bool(str(raw_message_documents or "").strip())
            print(
                f"[FeedbackEligibility] message_id={message_id} conversation_id={conversation_id} "
                f"trace=None raw_message_documents={raw_message_documents!r} "
                f"result={has_message_documents} reason={'historical_message_documents' if has_message_documents else 'no_trace_no_documents'}",
                flush=True,
            )
            return has_message_documents

        try:
            actions = json.loads(trace.actions_json or "[]")
        except Exception:
            actions = []
        try:
            documents = json.loads(trace.reference_docs_json or "[]")
        except Exception:
            documents = []
        try:
            message_documents = json.loads(raw_message_documents or "[]")
        except Exception:
            message_documents = []

        # Historical rows use several formats: JSON arrays, JSON objects,
        # comma-separated IDs, or a non-empty reference payload string.
        if isinstance(message_documents, dict):
            message_documents = [message_documents]
        elif isinstance(message_documents, (str, int, float)) and str(message_documents).strip():
            message_documents = [message_documents]
        elif not isinstance(message_documents, list) and raw_message_documents:
            message_documents = [str(raw_message_documents).strip()]

        route = str(getattr(trace, "route", "") or "").lower()
        if route != "knowledge_search" and not (isinstance(message_documents, list) and message_documents):
            print(
                f"[FeedbackEligibility] message_id={message_id} conversation_id={conversation_id} "
                f"trace_id={getattr(trace, 'id', None)} route={route!r} "
                f"actions={actions!r} trace_documents={len(documents) if isinstance(documents, list) else 'invalid'} "
                f"message_documents={len(message_documents) if isinstance(message_documents, list) else 'invalid'} "
                f"raw_message_documents={raw_message_documents!r} "
                "result=False reason=not_knowledge_search_and_no_message_documents",
                flush=True,
            )
            return False

        has_rag_action = any(
            str(action).startswith(("rag_search", "reuse_previous_references", "react_context_retrieve"))
            for action in (actions if isinstance(actions, list) else [])
        )
        has_trace_documents = isinstance(documents, list) and bool(documents)
        has_message_documents = isinstance(message_documents, list) and bool(message_documents)
        used_previous_refs = bool(getattr(trace, "used_previous_refs", 0))

        # Older traces may not contain actions_json, but a knowledge_search trace
        # with persisted reference documents is still a real RAG answer.
        result = bool(
            (has_rag_action or used_previous_refs or has_trace_documents or has_message_documents)
            and (has_trace_documents or has_message_documents)
        )
        print(
            f"[FeedbackEligibility] message_id={message_id} conversation_id={conversation_id} "
            f"trace_id={getattr(trace, 'id', None)} route={route!r} "
            f"actions={actions!r} used_previous_refs={getattr(trace, 'used_previous_refs', None)!r} "
            f"trace_documents={len(documents) if isinstance(documents, list) else 'invalid'} "
            f"message_documents={len(message_documents) if isinstance(message_documents, list) else 'invalid'} "
            f"raw_message_documents={raw_message_documents!r} "
            f"has_rag_action={has_rag_action} has_trace_documents={has_trace_documents} "
            f"has_message_documents={has_message_documents} result={result}",
            flush=True,
        )
        return result

    async def get_trace_by_id(self, trace_id: int) -> Optional[AiMessageTrace]:
        result = await self.db.execute(select(AiMessageTrace).where(AiMessageTrace.id == trace_id))
        return result.scalar_one_or_none()

    async def get_active_context(self, conversation_id: int) -> Optional[ConversationContext]:
        result = await self.db.execute(
            select(ConversationContext).where(ConversationContext.session_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def get_existing_feedback(
        self,
        user_id: int,
        conversation_id: int,
        message_id: int,
        feedback_type: str,
    ) -> Optional[FeedbackRecord]:
        result = await self.db.execute(
            select(FeedbackRecord).where(
                FeedbackRecord.user_id == user_id,
                FeedbackRecord.conversation_id == conversation_id,
                FeedbackRecord.message_id == message_id,
                FeedbackRecord.feedback_type == feedback_type,
            )
        )
        return result.scalar_one_or_none()

    async def create_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        self.db.add(feedback)
        await self.db.flush()
        await self.db.refresh(feedback)
        return feedback

    async def get_feedback_record(self, feedback_id: int) -> Optional[FeedbackRecord]:
        result = await self.db.execute(select(FeedbackRecord).where(FeedbackRecord.id == feedback_id))
        return result.scalar_one_or_none()

    async def create_claim(self, claim: FeedbackClaim) -> FeedbackClaim:
        self.db.add(claim)
        await self.db.flush()
        await self.db.refresh(claim)
        return claim

    async def create_verification(self, verification: FeedbackVerification) -> FeedbackVerification:
        self.db.add(verification)
        await self.db.flush()
        await self.db.refresh(verification)
        return verification

    async def create_audit_log(self, audit_log: FeedbackAuditLog) -> FeedbackAuditLog:
        self.db.add(audit_log)
        await self.db.flush()
        await self.db.refresh(audit_log)
        return audit_log

    async def create_evidence(self, evidence: FeedbackEvidence) -> FeedbackEvidence:
        self.db.add(evidence)
        await self.db.flush()
        await self.db.refresh(evidence)
        return evidence

    async def get_verified_fault_cases(
        self,
        device: Optional[str] = None,
        component: Optional[str] = None,
        alarm_code: Optional[str] = None,
        keywords: Optional[list[str]] = None,
        limit: int = 5,
    ) -> list[FaultCaseMemory]:
        conditions = [FaultCaseMemory.verification_status.in_(["verified", "expert_verified"])]
        if device:
            conditions.append(FaultCaseMemory.device.ilike(f"%{device}%"))
        if component:
            conditions.append(FaultCaseMemory.component.ilike(f"%{component}%"))
        if alarm_code:
            conditions.append(FaultCaseMemory.alarm_codes.ilike(f"%{alarm_code}%"))
        if keywords:
            keyword_filters = []
            for keyword in keywords:
                if keyword:
                    keyword_filters.append(
                        or_(
                            FaultCaseMemory.symptoms.ilike(f"%{keyword}%"),
                            FaultCaseMemory.metrics.ilike(f"%{keyword}%"),
                            FaultCaseMemory.alarm_codes.ilike(f"%{keyword}%"),
                            FaultCaseMemory.root_cause.ilike(f"%{keyword}%"),
                            FaultCaseMemory.actions.ilike(f"%{keyword}%"),
                            FaultCaseMemory.outcome.ilike(f"%{keyword}%"),
                        )
                    )
            if keyword_filters:
                conditions.append(or_(*keyword_filters))
        result = await self.db.execute(
            select(FaultCaseMemory).where(*conditions).order_by(FaultCaseMemory.updated_at.desc(), FaultCaseMemory.id.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_reliability(self, user_id: int) -> Optional[UserReliability]:
        result = await self.db.execute(select(UserReliability).where(UserReliability.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_or_create_user_reliability(self, user_id: int) -> UserReliability:
        row = await self.get_user_reliability(user_id)
        if row:
            return row
        row = UserReliability(user_id=user_id)
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def save_user_reliability(self, row: UserReliability) -> UserReliability:
        self.db.add(row)
        await self.db.flush()
        await self.db.refresh(row)
        return row

    async def get_feedback_by_id(self, feedback_id: int) -> Optional[FeedbackRecord]:
        return await self.get_feedback_record(feedback_id)

    async def get_feedback_verifications(self, feedback_id: int) -> list[FeedbackVerification]:
        result = await self.db.execute(
            select(FeedbackVerification)
            .where(FeedbackVerification.feedback_id == feedback_id)
            .order_by(FeedbackVerification.created_at.desc(), FeedbackVerification.id.desc())
        )
        return list(result.scalars().all())

    async def get_latest_feedback_verification(self, feedback_id: int) -> Optional[FeedbackVerification]:
        verifications = await self.get_feedback_verifications(feedback_id)
        return verifications[0] if verifications else None

    async def get_recent_feedbacks_for_user(self, user_id: int, limit: int = 20) -> list[FeedbackRecord]:
        result = await self.db.execute(
            select(FeedbackRecord)
            .where(FeedbackRecord.user_id == user_id)
            .order_by(FeedbackRecord.created_at.desc(), FeedbackRecord.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_feedbacks_for_conversation(
        self,
        conversation_id: int,
        limit: int = 20,
    ) -> list[FeedbackRecord]:
        result = await self.db.execute(
            select(FeedbackRecord)
            .where(FeedbackRecord.conversation_id == conversation_id)
            .order_by(desc(FeedbackRecord.created_at), desc(FeedbackRecord.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_claims_for_feedback_ids(self, feedback_ids: list[int]) -> list[FeedbackClaim]:
        if not feedback_ids:
            return []
        result = await self.db.execute(
            select(FeedbackClaim)
            .where(FeedbackClaim.feedback_id.in_(feedback_ids))
            .order_by(desc(FeedbackClaim.created_at), desc(FeedbackClaim.id))
        )
        return list(result.scalars().all())

    async def get_feedback_audit_log(self, feedback_id: int, action: str) -> Optional[FeedbackAuditLog]:
        result = await self.db.execute(
            select(FeedbackAuditLog)
            .where(FeedbackAuditLog.feedback_id == feedback_id, FeedbackAuditLog.action == action)
            .order_by(FeedbackAuditLog.created_at.desc(), FeedbackAuditLog.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_fault_case_by_id(self, case_id: int) -> Optional[FaultCaseMemory]:
        result = await self.db.execute(select(FaultCaseMemory).where(FaultCaseMemory.id == case_id))
        return result.scalar_one_or_none()

    async def get_fault_case_by_key(self, case_key: str) -> Optional[FaultCaseMemory]:
        result = await self.db.execute(select(FaultCaseMemory).where(FaultCaseMemory.case_key == case_key))
        return result.scalar_one_or_none()

    async def get_fault_cases_by_scope(
        self,
        device: Optional[str] = None,
        device_type: Optional[str] = None,
        firmware_version: Optional[str] = None,
        component: Optional[str] = None,
        limit: int = 20,
    ) -> list[FaultCaseMemory]:
        conditions = []
        if device:
            conditions.append(FaultCaseMemory.device.ilike(f"%{device}%"))
        if device_type:
            conditions.append(FaultCaseMemory.device_type.ilike(f"%{device_type}%"))
        if firmware_version:
            conditions.append(FaultCaseMemory.firmware_version.ilike(f"%{firmware_version}%"))
        if component:
            conditions.append(FaultCaseMemory.component.ilike(f"%{component}%"))
        result = await self.db.execute(
            select(FaultCaseMemory)
            .where(*conditions)
            .order_by(FaultCaseMemory.updated_at.desc(), FaultCaseMemory.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_fault_case(self, case: FaultCaseMemory) -> FaultCaseMemory:
        self.db.add(case)
        await self.db.flush()
        await self.db.refresh(case)
        return case

    async def save_fault_case(self, case: FaultCaseMemory) -> FaultCaseMemory:
        self.db.add(case)
        await self.db.flush()
        await self.db.refresh(case)
        return case

    async def create_retrieval_patch(self, patch: RetrievalPatch) -> RetrievalPatch:
        self.db.add(patch)
        await self.db.flush()
        await self.db.refresh(patch)
        return patch

    async def save_retrieval_patch(self, patch: RetrievalPatch) -> RetrievalPatch:
        self.db.add(patch)
        await self.db.flush()
        await self.db.refresh(patch)
        return patch

    async def get_retrieval_patch_by_id(self, patch_id: int) -> Optional[RetrievalPatch]:
        result = await self.db.execute(select(RetrievalPatch).where(RetrievalPatch.id == patch_id))
        return result.scalar_one_or_none()

    async def get_retrieval_patch_by_signature(
        self,
        query_signature: str,
        scope: Optional[str] = None,
        document_id: Optional[int] = None,
    ) -> list[RetrievalPatch]:
        conditions = [RetrievalPatch.query_signature == query_signature]
        if scope:
            conditions.append(RetrievalPatch.scope == scope)
        if document_id is not None:
            conditions.append(RetrievalPatch.document_id == document_id)
        result = await self.db.execute(
            select(RetrievalPatch)
            .where(*conditions)
            .order_by(RetrievalPatch.created_at.desc(), RetrievalPatch.id.desc())
        )
        return list(result.scalars().all())

    async def get_active_retrieval_patches(self) -> list[RetrievalPatch]:
        result = await self.db.execute(
            select(RetrievalPatch)
            .where(
                RetrievalPatch.status.in_(["active", "candidate"]),
            )
            .order_by(RetrievalPatch.created_at.desc(), RetrievalPatch.id.desc())
        )
        return list(result.scalars().all())

    async def get_pending_feedbacks(self, limit: int = 20) -> list[FeedbackRecord]:
        result = await self.db.execute(
            select(FeedbackRecord)
            .where(FeedbackRecord.status.in_([
                "pending",
                "retry",
                "analysis",
                "claim_extraction",
                "evidence_verification",
                "reliability_update",
                "patch_decision",
                "case_decision",
                "audit",
            ]))
            .order_by(FeedbackRecord.created_at.asc(), FeedbackRecord.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        self.db.add(feedback)
        await self.db.flush()
        await self.db.refresh(feedback)
        return feedback


class PatchRepository(FeedbackLearningRepository):
    """Repository boundary for retrieval patch lifecycle and audit operations."""

    pass
