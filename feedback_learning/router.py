from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import get_current_active_user
from schemas import FeedbackIngestCreate, ResultNew

from .repository import FeedbackLearningRepository
from .config import feedback_enabled
from .schemas import FeedbackIngestResponse
from .service import FeedbackIngestionService, _loads_json
from .learning_loop import FeedbackLearningEvaluationService, FeedbackLearningJob
from utils.app_exceptions import AppException
from utils.error_codes import BizCode


def _ensure_feedback_enabled() -> None:
    if not feedback_enabled():
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "反馈功能已关闭")


router = APIRouter(prefix="/feedback", tags=["feedback-learning"])


def _record_to_response(record) -> FeedbackIngestResponse:
    return FeedbackIngestResponse(
        id=record.id,
        conversation_id=record.conversation_id,
        message_id=record.message_id,
        user_id=record.user_id,
        feedback_type=record.feedback_type,
        rating=record.rating,
        comment=record.comment,
        created_at=record.created_at,
        query_snapshot=_loads_json(record.query_snapshot, {}),
        answer_snapshot=_loads_json(record.answer_snapshot, {}),
        retrieved_documents=_loads_json(record.retrieved_documents, []),
        cited_documents=_loads_json(record.cited_documents, []),
        trace_id=record.trace_id,
        status=record.status,
    )


@router.post("/ingest", summary="反馈入库")
async def ingest_feedback(
    payload: FeedbackIngestCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    service = FeedbackIngestionService(FeedbackLearningRepository(db))
    record, created = await service.ingest_feedback(payload, current_user)
    response = _record_to_response(record)
    return ResultNew.result(0, "created" if created else "duplicate", {
        "created": created,
        "feedback": response.model_dump() if hasattr(response, "model_dump") else response.dict(),
    })


@router.post("/jobs/process-pending", summary="process pending feedback learning jobs")
async def process_pending_feedback_jobs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    _ensure_feedback_enabled()
    results = await FeedbackLearningJob(FeedbackLearningRepository(db)).process_pending(limit=limit)
    await db.commit()
    return ResultNew.result(0, "success", {"results": results})


@router.get("/{feedback_id}/evaluation", summary="feedback learning evaluation")
async def feedback_learning_evaluation(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    _ensure_feedback_enabled()
    service = FeedbackLearningEvaluationService(FeedbackLearningRepository(db))
    return ResultNew.result(0, "success", {
        "correction_lag": await service.correction_lag(feedback_id),
        "post_feedback_performance": await service.post_feedback_performance(feedback_id),
    })
