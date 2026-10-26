import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    DocumentKnowledge,
    KnowledgeDocumentSection,
    KnowledgeDocumentVersion,
    KnowledgeDocumentVersionFile,
    KnowledgeDocumentVersionSection,
    KnowledgeVersionApplicability,
    KnowledgeVersionAuditLog,
    KnowledgeVersionRelation,
)


EDITABLE_STATUSES = {"draft", "rejected"}
PUBLISHED_VALIDITY = {"active", "legacy_active"}


class KnowledgeVersionError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now()


def _validate_hardware_coexistence(new_hardware: str, remaining_versions):
    if not remaining_versions:
        return
    if not new_hardware:
        raise KnowledgeVersionError("保留并存版本时，新版本必须填写设备硬件版本号")
    for label, old_hardware in remaining_versions:
        if not old_hardware:
            raise KnowledgeVersionError(f"v{label} 没有硬件版本号，不能与新版本并存")
        if old_hardware.casefold() == new_hardware.casefold():
            raise KnowledgeVersionError(f"硬件版本号与 v{label} 相同，不能并存")


def _sha256(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", ".", (value or "").strip().lower()).strip(".")
    return text[:180]


def _section_key(section: Any, index: int) -> str:
    supplied = getattr(section, "stable_section_key", None)
    if not supplied:
        metadata = getattr(section, "section_metadata", None) or getattr(section, "metadata", None)
        if isinstance(metadata, dict):
            supplied = metadata.get("stable_section_key")
    if supplied:
        return str(supplied).strip()
    title = getattr(section, "section_title", None) or "section"
    return f"{_slug(title) or 'section'}.{index + 1}"


def _section_payload(section: Any, index: int) -> dict:
    section_metadata = getattr(section, "section_metadata", None)
    if section_metadata is None:
        candidate = getattr(section, "metadata", None)
        section_metadata = candidate if isinstance(candidate, dict) else {}
    return {
        "stable_section_key": _section_key(section, index),
        "parent_section_key": getattr(section, "parent_section_key", None),
        "section_index": getattr(section, "section_index", None) if getattr(section, "section_index", None) is not None else index,
        "section_title": getattr(section, "section_title", None),
        "section_type": getattr(section, "section_type", None) or str(index + 1),
        "plain_text": getattr(section, "plain_text", None) or "",
        "image_urls": getattr(section, "image_urls", None) or [],
        "section_metadata": section_metadata or {},
    }


def _scopes_overlap(left, right):
    """空条件为通配；区间不能证明互斥时保守地视为重叠。"""
    for field in ("machine_model_id", "machine_revision_id", "region_code", "site_id"):
        a, b = getattr(left, field, None), getattr(right, field, None)
        if a is not None and b is not None and a != b:
            return False
    for start, end in (("effective_from", "effective_to"),):
        a_start, a_end = getattr(left, start, None), getattr(left, end, None)
        b_start, b_end = getattr(right, start, None), getattr(right, end, None)
        if (a_end and b_start and a_end <= b_start) or (b_end and a_start and b_end <= a_start):
            return False
    # 序列号和固件并无统一可排序格式，不能靠字符串比较允许并存。
    return True


def _explicit_scope(scope):
    return any(getattr(scope, name, None) for name in (
        "machine_model_id", "machine_revision_id", "serial_number_from", "serial_number_to",
        "firmware_min", "firmware_max", "region_code", "site_id",
    ))


class KnowledgeVersionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _audit(self, document_id: int, version_id: Optional[int], action: str, operator_id: int, payload=None):
        self.db.add(KnowledgeVersionAuditLog(
            document_id=document_id,
            version_id=version_id,
            action=action,
            operator_id=operator_id,
            payload_json=payload or {},
            created_at=_now(),
        ))

    async def get_document(self, document_id: int, lock: bool = False) -> DocumentKnowledge:
        stmt = select(DocumentKnowledge).where(DocumentKnowledge.id == document_id, DocumentKnowledge.is_deleted == 0)
        if lock:
            stmt = stmt.with_for_update()
        document = (await self.db.execute(stmt)).scalar_one_or_none()
        if not document:
            raise KnowledgeVersionError("知识文档不存在")
        return document

    async def get_version(self, version_id: int, lock: bool = False) -> KnowledgeDocumentVersion:
        stmt = select(KnowledgeDocumentVersion).where(KnowledgeDocumentVersion.id == version_id)
        if lock:
            stmt = stmt.with_for_update()
        version = (await self.db.execute(stmt)).scalar_one_or_none()
        if not version:
            raise KnowledgeVersionError("文档版本不存在")
        return version

    async def list_versions(self, document_id: int):
        await self.get_document(document_id)
        result = await self.db.execute(
            select(KnowledgeDocumentVersion)
            .where(KnowledgeDocumentVersion.document_id == document_id)
            .order_by(KnowledgeDocumentVersion.version_major.desc(), KnowledgeDocumentVersion.version_minor.desc(), KnowledgeDocumentVersion.id.desc())
        )
        return list(result.scalars().all())

    async def _source_sections(self, version_id: int):
        result = await self.db.execute(
            select(KnowledgeDocumentVersionSection)
            .where(KnowledgeDocumentVersionSection.version_id == version_id)
            .order_by(KnowledgeDocumentVersionSection.section_index, KnowledgeDocumentVersionSection.id)
        )
        return list(result.scalars().all())

    async def _source_files(self, version_id: int):
        result = await self.db.execute(select(KnowledgeDocumentVersionFile).where(KnowledgeDocumentVersionFile.version_id == version_id))
        return list(result.scalars().all())

    async def _source_applicability(self, version_id: int):
        result = await self.db.execute(select(KnowledgeVersionApplicability).where(KnowledgeVersionApplicability.version_id == version_id))
        return list(result.scalars().all())

    async def detail(self, version_id: int) -> dict:
        version = await self.get_version(version_id)
        return {
            "version": version,
            "sections": await self._source_sections(version_id),
            "files": await self._source_files(version_id),
            "applicability": await self._source_applicability(version_id),
        }

    async def _next_number(self, document_id: int, category: str, base: Optional[KnowledgeDocumentVersion]):
        versions = await self.list_versions(document_id)
        released = [v for v in versions if v.workflow_status in {"approved", "published", "superseded", "invalidated"}]
        anchor = base or (released[0] if released else None)
        if not anchor:
            return 1, 0
        if category == "major":
            return anchor.version_major + 1, 0
        return anchor.version_major, anchor.version_minor + 1

    async def _replace_children(self, version: KnowledgeDocumentVersion, sections=None, files=None, applicability=None):
        if sections is not None:
            await self.db.execute(delete(KnowledgeDocumentVersionSection).where(KnowledgeDocumentVersionSection.version_id == version.id))
            seen = set()
            for index, raw in enumerate(sections):
                payload = _section_payload(raw, index)
                key = payload["stable_section_key"]
                if key in seen:
                    raise KnowledgeVersionError(f"章节稳定键重复：{key}")
                seen.add(key)
                payload["content_hash"] = _sha256(payload)
                self.db.add(KnowledgeDocumentVersionSection(version_id=version.id, created_at=_now(), **payload))
        if files is not None:
            await self.db.execute(delete(KnowledgeDocumentVersionFile).where(KnowledgeDocumentVersionFile.version_id == version.id))
            primary_count = sum(1 for item in files if item.file_role == "primary")
            if primary_count > 1:
                raise KnowledgeVersionError("一个版本最多只能有一个主文件")
            for item in files:
                digest = item.content_hash or _sha256(f"{item.storage_path}:{item.file_size or 0}")
                self.db.add(KnowledgeDocumentVersionFile(
                    version_id=version.id, file_role=item.file_role, original_name=item.original_name,
                    storage_path=item.storage_path, mime_type=item.mime_type, file_size=item.file_size,
                    content_hash=digest, created_at=_now(),
                ))
        if applicability is not None:
            await self.db.execute(delete(KnowledgeVersionApplicability).where(KnowledgeVersionApplicability.version_id == version.id))
            for item in applicability:
                if item.effective_from and item.effective_to and item.effective_from >= item.effective_to:
                    raise KnowledgeVersionError("适用范围的失效时间必须晚于生效时间")
                fields = (
                    "machine_model_id", "machine_revision_id", "serial_number_from", "serial_number_to",
                    "firmware_min", "firmware_max", "region_code", "site_id", "effective_from", "effective_to",
                    "priority", "applicability_status", "exclusion_reason",
                )
                payload = item.model_dump() if hasattr(item, "model_dump") else {name: getattr(item, name, None) for name in fields}
                self.db.add(KnowledgeVersionApplicability(version_id=version.id, **payload))

    async def create_draft(self, document_id: int, request, operator_id: int) -> KnowledgeDocumentVersion:
        document = await self.get_document(document_id, lock=True)
        base = await self.get_version(request.based_on_version_id) if request.based_on_version_id else None
        if base and base.document_id != document_id:
            raise KnowledgeVersionError("基础版本不属于当前文档")
        major, minor = await self._next_number(document_id, request.change_category, base)
        duplicate = (await self.db.execute(select(KnowledgeDocumentVersion).where(
            KnowledgeDocumentVersion.document_id == document_id,
            KnowledgeDocumentVersion.version_major == major,
            KnowledgeDocumentVersion.version_minor == minor,
        ))).scalar_one_or_none()
        if duplicate:
            raise KnowledgeVersionError(f"版本 {major}.{minor} 已存在，请先处理现有草稿")

        source_sections = await self._source_sections(base.id) if base else []
        source_files = await self._source_files(base.id) if base else []
        source_scope = await self._source_applicability(base.id) if base else []
        sections = request.sections if request.sections is not None else source_sections
        files = request.files if request.files is not None else source_files
        scopes = request.applicability if request.applicability is not None else source_scope
        title = request.title or (base.title_snapshot if base else document.title)
        tags = request.tag if request.tag is not None else (base.tag_snapshot if base else document.tag or [])
        image_urls = request.image_urls if request.image_urls is not None else (base.image_urls_snapshot if base else document.image_urls)
        origin_name = request.origin_file_name if request.origin_file_name is not None else (base.origin_file_name if base else document.origin_file_name)
        origin_dir = request.origin_file_dir if request.origin_file_dir is not None else (base.origin_file_dir if base else document.origin_file_dir)
        now = _now()
        version = KnowledgeDocumentVersion(
            document_id=document_id, version_major=major, version_minor=minor, version_label=f"{major}.{minor}",
            hardware_version=(request.hardware_version or (base.hardware_version if base else None)),
            planned_publish_mode=request.planned_publish_mode,
            planned_supersedes_version_ids=request.planned_supersedes_version_ids,
            draft_number=1, based_on_version_id=base.id if base else None, workflow_status="draft",
            validity_status="inactive", change_category=request.change_category,
            change_summary=request.change_summary, change_reason=request.change_reason,
            effective_from=request.effective_from, effective_to=request.effective_to,
            title_snapshot=title, tag_snapshot=tags, image_urls_snapshot=image_urls,
            origin_file_name=origin_name, origin_file_dir=origin_dir,
            content_hash=_sha256({"title": title, "tags": tags, "sections": [str(getattr(s, "plain_text", "")) for s in sections]}),
            index_status="pending", revision=1, created_by=operator_id, created_at=now, updated_at=now,
        )
        self.db.add(version)
        await self.db.flush()
        await self._replace_children(version, sections, files, scopes)
        await self._audit(document_id, version.id, "create_draft", operator_id, {"based_on_version_id": version.based_on_version_id})
        await self.db.flush()
        return version

    async def update_draft(self, version_id: int, request, operator_id: int):
        version = await self.get_version(version_id, lock=True)
        if version.workflow_status not in EDITABLE_STATUSES:
            raise KnowledgeVersionError("只有草稿或已驳回版本可以编辑")
        if version.revision != request.revision:
            raise KnowledgeVersionError("版本已被其他人修改，请刷新后重试")
        for request_name, model_name in {
            "change_category": "change_category", "change_summary": "change_summary", "change_reason": "change_reason",
            "hardware_version": "hardware_version", "planned_publish_mode": "planned_publish_mode",
            "planned_supersedes_version_ids": "planned_supersedes_version_ids",
            "title": "title_snapshot", "tag": "tag_snapshot", "image_urls": "image_urls_snapshot",
            "origin_file_name": "origin_file_name", "origin_file_dir": "origin_file_dir",
            "effective_from": "effective_from", "effective_to": "effective_to",
        }.items():
            value = getattr(request, request_name)
            if value is not None:
                setattr(version, model_name, value)
        if version.effective_from and version.effective_to and version.effective_from >= version.effective_to:
            raise KnowledgeVersionError("失效时间必须晚于生效时间")
        await self._replace_children(version, request.sections, request.files, request.applicability)
        sections = await self._source_sections(version.id)
        version.content_hash = _sha256({"title": version.title_snapshot, "tags": version.tag_snapshot, "sections": [s.content_hash for s in sections]})
        version.revision += 1
        version.workflow_status = "draft"
        version.updated_at = _now()
        await self._audit(version.document_id, version.id, "update_draft", operator_id, {"revision": version.revision})
        return version

    async def transition(self, version_id: int, action: str, operator_id: int, comment: Optional[str] = None):
        version = await self.get_version(version_id, lock=True)
        transitions = {
            "submit": ({"draft", "rejected"}, "pending"),
            "approve": ({"pending"}, "approved"),
            "reject": ({"pending"}, "rejected"),
            "withdraw": ({"draft", "pending", "rejected"}, "withdrawn"),
        }
        allowed, target = transitions[action]
        if version.workflow_status not in allowed:
            raise KnowledgeVersionError(f"当前状态 {version.workflow_status} 不允许执行 {action}")
        if action == "submit":
            await self._validate_submission(version)
        version.workflow_status = target
        version.updated_at = _now()
        if action in {"approve", "reject"}:
            version.reviewed_by = operator_id
            version.reviewed_at = _now()
        await self._audit(version.document_id, version.id, action, operator_id, {"comment": comment})
        return version

    async def _validate_submission(self, version: KnowledgeDocumentVersion):
        files = await self._source_files(version.id)
        sections = await self._source_sections(version.id)
        if not any(item.file_role == "primary" for item in files) and not (version.origin_file_name and version.origin_file_dir):
            raise KnowledgeVersionError("提交审核前必须上传主文件")
        if not sections or not any((item.plain_text or "").strip() for item in sections):
            raise KnowledgeVersionError("提交审核前必须保存至少一个有正文的章节")
        if not (version.change_summary or "").strip():
            raise KnowledgeVersionError("提交审核前必须填写变更摘要")
        hardware = (version.hardware_version or "").strip()
        if not hardware:
            raise KnowledgeVersionError("提交审核前必须填写设备硬件版本号")
        current = (await self.db.execute(select(KnowledgeDocumentVersion).where(
            KnowledgeDocumentVersion.document_id == version.document_id,
            KnowledgeDocumentVersion.workflow_status == "published",
            KnowledgeDocumentVersion.validity_status.in_(PUBLISHED_VALIDITY),
            KnowledgeDocumentVersion.id != version.id,
        ))).scalars().all()
        targets = set(version.planned_supersedes_version_ids or [])
        current_ids = {item.id for item in current}
        if not targets.issubset(current_ids):
            raise KnowledgeVersionError("草稿中计划替代的版本已失效，请重新选择发布目标")
        if version.planned_publish_mode == "replace":
            if current and not targets:
                raise KnowledgeVersionError("替代发布必须选择要替代的当前版本")
            for item in current:
                if item.id in targets and (item.hardware_version or "").strip().casefold() != hardware.casefold():
                    raise KnowledgeVersionError(f"只能替代相同硬件版本的 v{item.version_label}")
        elif targets:
            raise KnowledgeVersionError("按设备并存不能选择替代版本")
        remaining = [item for item in current if item.id not in targets]
        _validate_hardware_coexistence(hardware, [
            (item.version_label, str(item.hardware_version or "").strip()) for item in remaining
        ])

    async def _validate_publish_conflicts(self, version: KnowledgeDocumentVersion):
        if version.effective_from and version.effective_to and version.effective_from >= version.effective_to:
            raise KnowledgeVersionError("失效时间必须晚于生效时间")
        scopes = await self._source_applicability(version.id)
        for scope in scopes:
            if scope.applicability_status != "active":
                continue
            conditions = [
                    KnowledgeDocumentVersion.document_id == version.document_id,
                    KnowledgeDocumentVersion.id != version.id,
                    KnowledgeDocumentVersion.workflow_status == "published",
                    KnowledgeDocumentVersion.validity_status.in_(PUBLISHED_VALIDITY),
                    KnowledgeVersionApplicability.applicability_status == "active",
                    KnowledgeVersionApplicability.priority == scope.priority,
                    KnowledgeVersionApplicability.machine_model_id == scope.machine_model_id,
                    KnowledgeVersionApplicability.machine_revision_id == scope.machine_revision_id,
            ]
            if version.effective_from is not None:
                conditions.append(or_(KnowledgeVersionApplicability.effective_to.is_(None), KnowledgeVersionApplicability.effective_to > version.effective_from))
            if version.effective_to is not None:
                conditions.append(or_(KnowledgeVersionApplicability.effective_from.is_(None), KnowledgeVersionApplicability.effective_from < version.effective_to))
            stmt = (
                select(KnowledgeVersionApplicability, KnowledgeDocumentVersion)
                .join(KnowledgeDocumentVersion, KnowledgeDocumentVersion.id == KnowledgeVersionApplicability.version_id)
                .where(*conditions)
            )
            if (await self.db.execute(stmt)).first():
                raise KnowledgeVersionError("存在相同设备范围、优先级和重叠有效期的已发布版本")

    async def publish(self, version_id: int, request, operator_id: int):
        version = await self.get_version(version_id, lock=True)
        if version.workflow_status != "approved":
            raise KnowledgeVersionError("只有审核通过的版本可以发布")
        if request.effective_from is not None:
            version.effective_from = request.effective_from
        if request.effective_to is not None:
            version.effective_to = request.effective_to
        if version.effective_from and version.effective_to and version.effective_from >= version.effective_to:
            raise KnowledgeVersionError("失效时间必须晚于生效时间")
        document = await self.get_document(version.document_id, lock=True)
        previous = (await self.db.execute(select(KnowledgeDocumentVersion).where(
            KnowledgeDocumentVersion.document_id == version.document_id,
            KnowledgeDocumentVersion.workflow_status == "published",
            KnowledgeDocumentVersion.validity_status.in_(PUBLISHED_VALIDITY),
            KnowledgeDocumentVersion.id != version.id,
        ))).scalars().all()
        selected_ids = set(version.planned_supersedes_version_ids or [])
        previous_ids = {item.id for item in previous}
        if not selected_ids.issubset(previous_ids):
            raise KnowledgeVersionError("只能替代当前文档的已发布有效版本")
        if version.planned_publish_mode == "coexist" and selected_ids:
            raise KnowledgeVersionError("并存发布不能选择替代版本")
        if previous and version.planned_publish_mode == "replace" and not selected_ids:
            raise KnowledgeVersionError("请选择要替代的版本")
        remaining = [item for item in previous if item.id not in selected_ids]
        hardware_version = str(version.hardware_version or "").strip()
        if version.planned_publish_mode == "coexist" and not hardware_version:
            raise KnowledgeVersionError("按设备并存必须填写设备硬件版本号")
        if remaining:
            for item in remaining:
                supplied_hardware = str(request.existing_hardware_versions.get(item.id, "")).strip()
                if supplied_hardware:
                    item.hardware_version = supplied_hardware
            _validate_hardware_coexistence(hardware_version, [
                (item.version_label, str(item.hardware_version or "").strip()) for item in remaining
            ])
        version.hardware_version = hardware_version or None
        # Index first. An indexing failure must leave the previous release usable.
        try:
            from utils.VectorService import VectorService
            vector_service = VectorService(self.db)
            await vector_service.add_knowledge_version_to_vector_store(version)
        except Exception as exc:
            raise KnowledgeVersionError(f"新版本向量化失败，旧版本保持有效：{str(exc)[:500]}")
        now = _now()
        for item in previous:
            if item.id not in selected_ids:
                continue
            item.workflow_status = "superseded"
            item.validity_status = "superseded"
            item.effective_to = item.effective_to or version.effective_from or now
            self.db.add(KnowledgeVersionRelation(
                source_version_id=item.id, target_version_id=version.id, relation_type="supersedes",
                reason=version.change_reason, created_by=operator_id, created_at=now,
            ))
        version.workflow_status = "published"
        version.validity_status = "active"
        version.published_by = operator_id
        version.published_at = now
        version.updated_at = now
        version.index_status = "indexed"
        version.index_error = None
        version.indexed_at = now
        if document.default_version_id is None or document.default_version_id in selected_ids:
            document.default_version_id = version.id
        document.title = version.title_snapshot
        document.tag = version.tag_snapshot or []
        document.image_urls = version.image_urls_snapshot
        document.origin_file_name = version.origin_file_name
        document.origin_file_dir = version.origin_file_dir
        document.first_edit_date = now
        document.updated_time = now
        document.is_vectorized = 0
        await self._sync_current_sections(document, version.id)
        for old_id in selected_ids:
            try:
                await vector_service.delete_knowledge_version_vectors(old_id)
            except Exception as exc:
                await self._audit(version.document_id, old_id, "vector_cleanup_failed", operator_id,
                                  {"error": str(exc)[:2000]})
        await self._audit(version.document_id, version.id, "publish", operator_id, {"publish_mode": version.planned_publish_mode, "supersedes_version_ids": sorted(selected_ids), "hardware_version": version.hardware_version})
        return version

    async def edit_current(self, document, request, sections, operator_id: int, version_id=None):
        """Correct the selected published version in place, retaining its release number."""
        target_id = version_id or document.default_version_id
        version = (await self.get_version(target_id, lock=True)
                   if target_id else
                   await self.initialize_legacy_document(document.id, operator_id))
        if (version.document_id != document.id or version.workflow_status != "published"
                or version.validity_status not in PUBLISHED_VALIDITY):
            raise KnowledgeVersionError("只能直接编辑当前有效的已发布版本")
        before = {"content_hash": version.content_hash, "revision": version.revision}
        fields = request.model_fields_set if hasattr(request, "model_fields_set") else request.__fields_set__
        if "title" in fields:
            version.title_snapshot = request.title
        if "tag" in fields:
            version.tag_snapshot = request.tag or []
        if "image_urls" in fields:
            version.image_urls_snapshot = request.image_urls
        if "sections" in fields and request.sections is not None:
            await self._replace_children(version, sections=sections)
        await self.db.flush()
        rows = await self._source_sections(version.id)
        version.content_hash = _sha256({"title": version.title_snapshot,
            "tags": version.tag_snapshot, "images": version.image_urls_snapshot,
            "sections": [row.content_hash for row in rows]})
        version.revision = (version.revision or 0) + 1
        version.updated_at = _now()
        if document.default_version_id == version.id:
            document.title = version.title_snapshot
            document.tag = version.tag_snapshot or []
            document.image_urls = version.image_urls_snapshot
            document.updated_time = version.updated_at
            document.first_edit_date = version.updated_at
            await self._sync_current_sections(document, version.id)
        await self.reindex(version.id, operator_id)
        if document.default_version_id == version.id:
            document.is_vectorized = 1
            document.vector_update_time = version.indexed_at
        await self._audit(document.id, version.id, "edit_current", operator_id,
            {"before": before, "content_hash": version.content_hash, "revision": version.revision})
        return version

    async def _sync_current_sections(self, document: DocumentKnowledge, version_id: int):
        source = await self._source_sections(version_id)
        await self.db.execute(delete(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document.id))
        current = []
        now = _now()
        for item in source:
            row = KnowledgeDocumentSection(
                document_id=document.id, document_library_type="knowledge", section_index=item.section_index,
                section_title=item.section_title, section_type=item.section_type, plain_text=item.plain_text,
                image_urls=item.image_urls or [], section_metadata={**(item.section_metadata or {}), "version_id": version_id,
                    "version": (await self.get_version(version_id)).version_label, "stable_section_key": item.stable_section_key},
                created_time=now, updated_time=now,
            )
            self.db.add(row)
            current.append(row)
        await self.db.flush()
        document.section_ids = [row.id for row in current]

    async def invalidate(self, version_id: int, reason: str, operator_id: int, new_default_version_id: Optional[int] = None):
        version = await self.get_version(version_id, lock=True)
        if version.workflow_status not in {"published", "superseded"}:
            raise KnowledgeVersionError("只有已发布或已替代版本可以作废")
        document = await self.get_document(version.document_id, lock=True)
        if document.default_version_id == version.id:
            if not new_default_version_id:
                raise KnowledgeVersionError("作废默认展示版本前，必须选择新的默认版本")
            replacement = await self.get_version(new_default_version_id, lock=True)
            if replacement.document_id != version.document_id or replacement.id == version.id or replacement.workflow_status != "published" or replacement.validity_status not in PUBLISHED_VALIDITY:
                raise KnowledgeVersionError("新的默认版本必须是当前文档的其他有效发布版本")
            document.default_version_id = replacement.id
        elif new_default_version_id:
            raise KnowledgeVersionError("只有作废默认展示版本时才需要指定新的默认版本")
        version.workflow_status = "invalidated"
        version.validity_status = "invalidated"
        version.updated_at = _now()
        try:
            from utils.VectorService import VectorService
            await VectorService(self.db).delete_knowledge_version_vectors(version.id)
        except Exception as exc:
            await self._audit(version.document_id, version.id, "vector_cleanup_failed", operator_id,
                              {"error": str(exc)[:2000]})
        await self._audit(version.document_id, version.id, "invalidate", operator_id, {"reason": reason})
        return version

    async def set_default(self, version_id: int, operator_id: int):
        version = await self.get_version(version_id, lock=True)
        if version.workflow_status != "published" or version.validity_status not in PUBLISHED_VALIDITY:
            raise KnowledgeVersionError("只有当前有效的已发布版本可以设为默认展示")
        document = await self.get_document(version.document_id, lock=True)
        previous_version_id = document.default_version_id
        document.default_version_id = version.id
        document.updated_time = _now()
        await self._audit(version.document_id, version.id, "set_default", operator_id, {
            "previous_version_id": previous_version_id,
            "default_version_id": version.id,
        })
        return version

    async def reindex(self, version_id: int, operator_id: int):
        version = await self.get_version(version_id, lock=True)
        if version.workflow_status != "published" or version.validity_status not in PUBLISHED_VALIDITY:
            raise KnowledgeVersionError("只有当前有效的已发布版本可以重建索引")
        version.index_status = "indexing"
        version.index_error = None
        await self.db.flush()
        try:
            from utils.VectorService import VectorService
            await VectorService(self.db).add_knowledge_version_to_vector_store(version)
            version.index_status = "indexed"
            version.indexed_at = _now()
        except Exception as exc:
            version.index_status = "failed"
            version.index_error = str(exc)[:2000]
            raise KnowledgeVersionError(f"版本索引失败：{version.index_error}")
        await self._audit(version.document_id, version.id, "reindex", operator_id, {
            "hardware_version": version.hardware_version,
        })
        return version

    async def rollback(self, document_id: int, request, operator_id: int):
        source = await self.get_version(request.source_version_id)
        if source.document_id != document_id or source.workflow_status not in {"published", "superseded", "invalidated"}:
            raise KnowledgeVersionError("只能基于当前文档的历史发布版本创建回滚草稿")
        draft_request = type("RollbackRequest", (), {
            "based_on_version_id": source.id, "change_category": "rollback", "change_summary": request.change_summary,
            "hardware_version": source.hardware_version, "planned_publish_mode": "replace",
            "planned_supersedes_version_ids": [source.id] if source.workflow_status == "published" else [],
            "change_reason": request.change_reason, "title": None, "tag": None, "image_urls": None,
            "origin_file_name": None, "origin_file_dir": None, "effective_from": None, "effective_to": None,
            "sections": None, "files": None, "applicability": None,
        })()
        draft = await self.create_draft(document_id, draft_request, operator_id)
        self.db.add(KnowledgeVersionRelation(
            source_version_id=source.id, target_version_id=draft.id, relation_type="rollback_of",
            reason=request.change_reason, created_by=operator_id, created_at=_now(),
        ))
        return draft

    async def initialize_legacy_document(self, document_id: int, operator_id: int):
        document = await self.get_document(document_id, lock=True)
        existing = (await self.db.execute(select(KnowledgeDocumentVersion.id).where(KnowledgeDocumentVersion.document_id == document_id).limit(1))).scalar_one_or_none()
        if existing:
            return await self.get_version(existing)
        sections = (await self.db.execute(select(KnowledgeDocumentSection).where(KnowledgeDocumentSection.document_id == document_id).order_by(KnowledgeDocumentSection.section_index))).scalars().all()
        wrappers = []
        for s in sections:
            wrappers.append(type("LegacySection", (), {
                "stable_section_key": (s.section_metadata or {}).get("stable_section_key"), "parent_section_key": None,
                "section_index": s.section_index, "section_title": s.section_title, "section_type": s.section_type,
                "plain_text": s.plain_text, "image_urls": s.image_urls or [], "metadata": s.section_metadata or {},
            })())
        legacy_files = []
        if document.origin_file_name and document.origin_file_dir:
            source_path = Path(document.origin_file_dir)
            legacy_files.append(type("LegacyFile", (), {
                "file_role": "primary", "original_name": document.origin_file_name,
                "storage_path": document.origin_file_dir, "mime_type": None,
                "file_size": source_path.stat().st_size if source_path.is_file() else None,
                "content_hash": _sha256(f"{document.origin_file_dir}:legacy"),
            })())
        request = type("LegacyRequest", (), {
            "based_on_version_id": None, "change_category": "minor", "change_summary": "现有知识库数据初始化为 v1.0",
            "hardware_version": None, "planned_publish_mode": "replace", "planned_supersedes_version_ids": [],
            "change_reason": "版本管理上线迁移", "title": document.title, "tag": document.tag or [],
            "image_urls": document.image_urls, "origin_file_name": document.origin_file_name, "origin_file_dir": document.origin_file_dir,
            "effective_from": document.first_edit_date, "effective_to": None, "sections": wrappers, "files": legacy_files, "applicability": [],
        })()
        version = await self.create_draft(document_id, request, operator_id)
        version.workflow_status = "published"
        version.validity_status = "active"
        version.published_by = operator_id
        version.published_at = _now()
        version.index_status = "indexed" if document.is_vectorized else "pending"
        version.indexed_at = document.vector_update_time
        document.default_version_id = version.id
        if not document.document_code:
            document.document_code = f"KB-{document.id:06d}"
        await self._audit(document_id, version.id, "initialize_legacy", operator_id)
        return version
