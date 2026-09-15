import os
from typing import Any, Dict, List, Optional

from feedback_learning.aware_retriever import build_patch_query_context
from feedback_learning.config import feedback_learning_enabled
from feedback_learning.repository import FeedbackLearningRepository
from feedback_learning.retrieval_patch import RetrievalPatchService


def _enabled() -> bool:
    return feedback_learning_enabled()


def _text(value: Any, max_chars: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text[:max_chars].rstrip()


def _loads_json(value: Any, default: Any):
    if isinstance(value, (list, dict)):
        return value
    if not value:
        return default
    try:
        import json

        parsed = json.loads(value)
        return parsed if parsed is not None else default
    except Exception:
        return default


def _query_matches(text: str, query: str) -> bool:
    text = _text(text, 1000).lower()
    terms = [term.lower() for term in _text(query, 240).replace("\n", " ").split() if len(term) >= 2]
    if not terms:
        return True
    return any(term in text for term in terms[:12])


class FeedbackMemoryBuilder:
    """Builds a compact feedback-derived source for the existing MemoryPack."""

    def __init__(self, repository: FeedbackLearningRepository):
        self.repository = repository

    async def build(
        self,
        conversation_id: int,
        query: str,
        user_id: Optional[int] = None,
        memory_pack: Any = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        empty = {
            "verified_corrections": [],
            "relevant_cases": [],
            "unresolved_conflicts": [],
            "applicable_patch_summary": [],
        }
        if not _enabled():
            return empty

        feedbacks = await self.repository.get_feedbacks_for_conversation(
            conversation_id,
            limit=int(os.getenv("FEEDBACK_MEMORY_FEEDBACK_LIMIT", "20")),
        )
        feedbacks = [
            item
            for item in feedbacks
            if str(getattr(item, "feedback_type", "") or "").lower() != "suspicious"
        ]
        claims = await self.repository.get_claims_for_feedback_ids([item.id for item in feedbacks if getattr(item, "id", None)])
        claims_by_feedback: Dict[int, List[Any]] = {}
        for claim in claims:
            claims_by_feedback.setdefault(int(claim.feedback_id), []).append(claim)

        memory = dict(empty)
        for feedback in feedbacks:
            status = str(getattr(feedback, "status", "") or "").lower()
            feedback_type = str(getattr(feedback, "feedback_type", "") or "").lower()
            comment = _text(getattr(feedback, "comment", ""), 180)
            related = _query_matches(comment, query)
            feedback_claims = claims_by_feedback.get(int(feedback.id), [])
            has_verified_claim = any(
                str(getattr(claim, "verification_status", "") or "").lower() in {"verified", "expert_verified", "partially_verified"}
                for claim in feedback_claims
            )
            has_contradicted_claim = any(
                str(getattr(claim, "verification_status", "") or "").lower() == "contradicted"
                for claim in feedback_claims
            )

            if has_contradicted_claim or status in {"rejected", "contradicted"}:
                memory["unresolved_conflicts"].append({
                    "feedback_id": feedback.id,
                    "text": comment or "Contradicted feedback claim requires review.",
                })
                continue

            if not related:
                continue
            if feedback_type in {"negative", "positive"} and not has_verified_claim and status not in {"verified", "promoted"}:
                continue
            if feedback_type not in {"correction", "confirmation", "additional_information"} and not has_verified_claim:
                continue
            if status in {"verified", "promoted"} or has_verified_claim:
                memory["verified_corrections"].append({
                    "feedback_id": feedback.id,
                    "text": comment or self._claim_text(feedback_claims),
                })

        query_context = build_patch_query_context(
            query,
            user_id=user_id,
            session_id=conversation_id,
            memory_pack=memory_pack,
        )
        patches = await RetrievalPatchService(self.repository).get_applicable_patches(query_context)
        for patch in patches[: int(os.getenv("FEEDBACK_MEMORY_PATCH_LIMIT", "3"))]:
            memory["applicable_patch_summary"].append({
                "patch_id": patch.id,
                "summary": f"{patch.patch_type} document {patch.document_id} scope={patch.scope} weight={float(patch.weight or 0.0):.3f}",
            })

        cases = await self.repository.get_verified_fault_cases(
            device=getattr(query_context, "device", None),
            component=getattr(query_context, "component", None),
            alarm_code=getattr(query_context, "fault_type", None),
            keywords=[query],
            limit=int(os.getenv("FEEDBACK_MEMORY_CASE_LIMIT", "3")),
        )
        for case in cases:
            status = str(getattr(case, "verification_status", "") or "").lower()
            priority = 2 if status == "expert_verified" else 1
            memory["relevant_cases"].append({
                "case_id": case.id,
                "summary": _text(
                    " | ".join(
                        part
                        for part in [
                            getattr(case, "confirmed_fault", None),
                            getattr(case, "root_cause", None),
                            getattr(case, "actions", None),
                            getattr(case, "outcome", None),
                        ]
                        if part
                    ),
                    180,
                ),
                "verification_status": status,
                "priority": priority,
            })
        memory["relevant_cases"].sort(key=lambda item: item.get("priority", 0), reverse=True)

        return {
            "verified_corrections": memory["verified_corrections"][:3],
            "relevant_cases": memory["relevant_cases"][:3],
            "unresolved_conflicts": memory["unresolved_conflicts"][:3],
            "applicable_patch_summary": memory["applicable_patch_summary"][:3],
        }

    def _claim_text(self, claims: List[Any]) -> str:
        pieces = []
        for claim in claims[:2]:
            pieces.append(
                _text(
                    " ".join(
                        str(part or "")
                        for part in [
                            getattr(claim, "subject", None),
                            getattr(claim, "predicate", None),
                            getattr(claim, "object", None),
                        ]
                    ),
                    120,
                )
            )
        return " ; ".join(piece for piece in pieces if piece)
