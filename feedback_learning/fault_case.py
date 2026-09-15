import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from models import FaultCaseMemory
from utils.app_exceptions import AppException
from utils.error_codes import BizCode

from .repository import FeedbackLearningRepository
from .schemas import EvidenceVerificationResult


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)


def _loads_json(value: Any, default: Any):
    if value in (None, "", [], {}):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _listify(value: Any) -> List[Any]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _case_key(parts: List[str]) -> str:
    digest = hashlib.sha1("||".join(p for p in parts if p).encode("utf-8")).hexdigest()
    return f"fault_case_{digest[:20]}"


class CaseCandidateBuilder:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    def build_scope(self, feedback_record: Any, claim_result: Optional[EvidenceVerificationResult], context: Any = None) -> Dict[str, Any]:
        query_snapshot = _loads_json(getattr(feedback_record, "query_snapshot", None), {})
        device_context = query_snapshot.get("device_context") if isinstance(query_snapshot, dict) else {}
        if not isinstance(device_context, dict):
            device_context = {}
        device = device_context.get("active_device")
        component = device_context.get("active_component")
        error_code = device_context.get("active_error_code")
        firmware_version = None
        scope = {
            "device": device,
            "device_type": device_context.get("active_device_type") or device_context.get("device_type"),
            "component": component,
            "firmware_version": firmware_version,
            "fault_type": getattr(feedback_record, "feedback_type", None),
        }
        if context and getattr(context, "active_device", None):
            scope["device"] = getattr(context, "active_device", None) or scope["device"]
        if context and getattr(context, "active_component", None):
            scope["component"] = getattr(context, "active_component", None) or scope["component"]
        if context and getattr(context, "active_error_code", None):
            error_code = getattr(context, "active_error_code", None)
        if error_code:
            scope["fault_type"] = str(error_code)
        if query_snapshot.get("memory_pack"):
            memory_pack = query_snapshot.get("memory_pack")
            if isinstance(memory_pack, dict):
                scope["firmware_version"] = memory_pack.get("firmware_version") or scope["firmware_version"]
        return scope

    def build_case_payload(
        self,
        feedback_record: Any,
        claim_result: Optional[EvidenceVerificationResult],
        context: Any = None,
        maintenance_outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query_snapshot = _loads_json(getattr(feedback_record, "query_snapshot", None), {})
        answer_snapshot = _loads_json(getattr(feedback_record, "answer_snapshot", None), {})
        retrieved_documents = _loads_json(getattr(feedback_record, "retrieved_documents", None), [])
        scope = self.build_scope(feedback_record, claim_result, context)
        claim = claim_result.claim if claim_result else None
        evidence_ids = []
        if claim_result:
            evidence_ids.extend([item.source_id for item in claim_result.supporting_evidence])
            evidence_ids.extend([item.source_id for item in claim_result.contradicting_evidence])
        source_feedback_ids = [str(getattr(feedback_record, "id", None))]
        if claim and claim.evidence_ids:
            evidence_ids.extend([str(x) for x in _listify(claim.evidence_ids)])
        return {
            "case_key": _case_key(
                [
                    str(scope.get("device") or ""),
                    str(scope.get("device_type") or ""),
                    str(scope.get("component") or ""),
                    str(scope.get("firmware_version") or ""),
                    str(scope.get("fault_type") or ""),
                    str(claim.object if claim else ""),
                ]
            ),
            "scope": _json_dumps(scope),
            "device": scope.get("device"),
            "device_type": scope.get("device_type"),
            "component": scope.get("component"),
            "firmware_version": scope.get("firmware_version"),
            "symptoms": _json_dumps(query_snapshot.get("device_context") or {}),
            "metrics": _json_dumps(answer_snapshot),
            "alarm_codes": _json_dumps([scope.get("fault_type")] if scope.get("fault_type") else []),
            "confirmed_fault": claim.object if claim else None,
            "root_cause": claim.object if claim else None,
            "actions": _json_dumps([item.content for item in (claim_result.supporting_evidence if claim_result else []) if item.content]),
            "outcome": maintenance_outcome.get("outcome") if maintenance_outcome else None,
            "evidence_ids": _json_dumps(list(dict.fromkeys([str(item) for item in evidence_ids if item]))),
            "source_feedback_ids": _json_dumps(source_feedback_ids),
            "confidence": float(claim_result.confidence if claim_result else 0.5),
            "verification_status": "candidate",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }


class FaultCaseService:
    def __init__(self, repository: FeedbackLearningRepository, builder: Optional[CaseCandidateBuilder] = None):
        self.repository = repository
        self.builder = builder or CaseCandidateBuilder(repository)

    async def create_candidate_case(
        self,
        feedback_record: Any,
        claim_result: Optional[EvidenceVerificationResult],
        context: Any = None,
        maintenance_outcome: Optional[Dict[str, Any]] = None,
    ) -> FaultCaseMemory:
        if not claim_result:
            raise AppException(400, BizCode.BAD_REQUEST, "不满足创建案例条件")
        payload = self.builder.build_case_payload(feedback_record, claim_result, context, maintenance_outcome)
        if str(claim_result.result) == "contradicted":
            payload["verification_status"] = "rejected"
        existing = await self.repository.get_fault_case_by_key(payload["case_key"])
        if existing:
            return await self._merge_case(existing, payload)
        case = FaultCaseMemory(**payload)
        try:
            return await self.repository.create_fault_case(case)
        except IntegrityError:
            await self.repository.db.rollback()
            existing = await self.repository.get_fault_case_by_key(payload["case_key"])
            if existing:
                return await self._merge_case(existing, payload)
            raise

    async def verify_case(self, case_id: int) -> FaultCaseMemory:
        case = await self.repository.get_fault_case_by_id(case_id)
        if not case:
            raise AppException(404, BizCode.NOT_FOUND, "案例不存在")
        case.verification_status = "verified"
        case.updated_at = datetime.now()
        return await self.repository.save_fault_case(case)

    async def promote_case(self, case_id: int, expert_verified: bool = False) -> FaultCaseMemory:
        case = await self.repository.get_fault_case_by_id(case_id)
        if not case:
            raise AppException(404, BizCode.NOT_FOUND, "案例不存在")
        case.verification_status = "expert_verified" if expert_verified else "verified"
        case.updated_at = datetime.now()
        return await self.repository.save_fault_case(case)

    async def reject_case(self, case_id: int) -> FaultCaseMemory:
        case = await self.repository.get_fault_case_by_id(case_id)
        if not case:
            raise AppException(404, BizCode.NOT_FOUND, "案例不存在")
        case.verification_status = "rejected"
        case.updated_at = datetime.now()
        return await self.repository.save_fault_case(case)

    async def search_similar_cases(
        self,
        device: Optional[str] = None,
        device_type: Optional[str] = None,
        firmware_version: Optional[str] = None,
        component: Optional[str] = None,
        limit: int = 10,
    ) -> List[FaultCaseMemory]:
        return await self.repository.get_fault_cases_by_scope(
            device=device,
            device_type=device_type,
            firmware_version=firmware_version,
            component=component,
            limit=limit,
        )

    async def _merge_case(self, existing: FaultCaseMemory, payload: Dict[str, Any]) -> FaultCaseMemory:
        source_feedback_ids = _listify(_loads_json(existing.source_feedback_ids, []))
        source_feedback_ids.extend(_listify(_loads_json(payload.get("source_feedback_ids"), [])))
        evidence_ids = _listify(_loads_json(existing.evidence_ids, []))
        evidence_ids.extend(_listify(_loads_json(payload.get("evidence_ids"), [])))
        existing.source_feedback_ids = _json_dumps(list(dict.fromkeys([str(x) for x in source_feedback_ids if x])))
        existing.evidence_ids = _json_dumps(list(dict.fromkeys([str(x) for x in evidence_ids if x])))
        existing.confidence = max(float(existing.confidence or 0.0), float(payload.get("confidence") or 0.0))
        existing.updated_at = datetime.now()
        existing.device = existing.device or payload.get("device")
        existing.device_type = existing.device_type or payload.get("device_type")
        existing.component = existing.component or payload.get("component")
        existing.firmware_version = existing.firmware_version or payload.get("firmware_version")
        existing.scope = existing.scope or payload.get("scope")
        existing.confirmed_fault = existing.confirmed_fault or payload.get("confirmed_fault")
        existing.root_cause = existing.root_cause or payload.get("root_cause")
        existing.actions = existing.actions or payload.get("actions")
        existing.outcome = existing.outcome or payload.get("outcome")
        return await self.repository.save_fault_case(existing)


class CaseVerificationService:
    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository
        self.case_service = FaultCaseService(repository)

    async def verify_case(self, case_id: int, maintenance_outcome: Optional[Dict[str, Any]] = None, expert_verified: bool = False) -> FaultCaseMemory:
        case = await self.repository.get_fault_case_by_id(case_id)
        if not case:
            raise AppException(404, BizCode.NOT_FOUND, "案例不存在")
        if maintenance_outcome and maintenance_outcome.get("outcome"):
            case.outcome = maintenance_outcome["outcome"]
            case.confidence = max(float(case.confidence or 0.0), 0.9)
            case.verification_status = "expert_verified"
        elif expert_verified:
            case.verification_status = "expert_verified"
            case.confidence = max(float(case.confidence or 0.0), 0.9)
        elif case.verification_status != "rejected":
            case.verification_status = "verified"
        case.updated_at = datetime.now()
        return await self.repository.save_fault_case(case)
