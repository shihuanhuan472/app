from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from agents.memory.schemas import MemoryPack
from feedback_learning.repository import PatchRepository
from feedback_learning.retrieval_patch import PatchApplier, RetrievalPatchService
from feedback_learning.schemas import PatchQueryContext
from feedback_learning.config import feedback_learning_enabled


def _active_context_value(active_context: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = active_context.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def build_patch_query_context(
    query: str,
    user_id: Optional[int] = None,
    session_id: Optional[int] = None,
    memory_pack: Optional[MemoryPack] = None,
) -> PatchQueryContext:
    active_context = memory_pack.active_context if memory_pack and memory_pack.active_context else {}
    working_memory = memory_pack.working_memory if memory_pack and memory_pack.working_memory else {}
    memory_context = working_memory.get("memory_pack") if isinstance(working_memory.get("memory_pack"), dict) else {}

    return PatchQueryContext(
        query=query or "",
        user_id=user_id,
        conversation_id=session_id,
        device=_active_context_value(active_context, "active_device", "device"),
        device_type=_active_context_value(active_context, "active_device_type", "device_type"),
        component=_active_context_value(active_context, "active_component", "component"),
        firmware_version=(
            _active_context_value(active_context, "firmware_version", "active_firmware_version")
            or _active_context_value(working_memory, "firmware_version")
            or _active_context_value(memory_context, "firmware_version")
        ),
        fault_type=_active_context_value(
            active_context,
            "fault_type",
            "active_fault_type",
            "active_error_code",
        ),
    )


class FeedbackAwareRetriever:
    """Adapter that applies feedback-derived retrieval patches after base RAG retrieval."""

    def __init__(
        self,
        db: AsyncSession,
        patch_service: Optional[RetrievalPatchService] = None,
        patch_applier: Optional[PatchApplier] = None,
        enabled: Optional[bool] = None,
    ):
        self.db = db
        self.enabled = feedback_learning_enabled() if enabled is None else bool(enabled)
        self.patch_service = patch_service or RetrievalPatchService(PatchRepository(db))
        self.patch_applier = patch_applier or PatchApplier()

    async def apply_feedback_layer(
        self,
        base_results: Sequence[Dict[str, Any]],
        query: str,
        user_id: Optional[int] = None,
        session_id: Optional[int] = None,
        memory_pack: Optional[MemoryPack] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.enabled:
            return list(base_results), {
                "feedback_learning_enabled": False,
                "feedback_patch_count": 0,
                "applied_patch_ids": [],
            }

        query_context = build_patch_query_context(
            query,
            user_id=user_id,
            session_id=session_id,
            memory_pack=memory_pack,
        )
        patches = await self.patch_service.get_applicable_patches(query_context)
        if not patches:
            return list(base_results), {
                "feedback_learning_enabled": True,
                "feedback_patch_count": 0,
                "applied_patch_ids": [],
            }

        patched_results = self.patch_applier.apply_patches(base_results, patches)
        applied_patch_ids = []
        adjusted_count = 0
        for doc in patched_results:
            patch_ids = [entry.get("patch_id") for entry in doc.get("patches", []) if entry.get("patch_id") is not None]
            if patch_ids:
                adjusted_count += 1
                applied_patch_ids.extend(patch_ids)
                doc["applied_patch_ids"] = patch_ids
            elif "applied_patch_ids" not in doc:
                doc["applied_patch_ids"] = []

        if adjusted_count == 0:
            return list(base_results), {
                "feedback_learning_enabled": True,
                "feedback_patch_count": len(patches),
                "applied_patch_ids": [],
            }

        return patched_results, {
            "feedback_learning_enabled": True,
            "feedback_patch_count": len(patches),
            "feedback_adjusted_document_count": adjusted_count,
            "applied_patch_ids": sorted(set(applied_patch_ids)),
        }
