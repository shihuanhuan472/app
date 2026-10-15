"""Resolve device aliases and use current database tags for both recall routes."""
import json

from sqlalchemy import select

from models import DocumentBreakdown, DocumentKnowledge, Tag


def matching_device_tag(device, tags):
    key = str(device or "").strip().casefold()
    if not key:
        return None
    matches = set()
    for tag in tags:
        aliases = tag.match_aliases or []
        if isinstance(aliases, str):
            aliases = [aliases]
        names = [tag.name, *aliases]
        if any(str(name).strip().casefold() == key for name in names):
            matches.add(tag.id)
    return next(iter(matches)) if len(matches) == 1 else None


def document_matches_device(tags, tag_id):
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            return False
    return not tags or str(tag_id) in {str(value) for value in tags}


async def device_document_scope(db, device):
    if not str(device or "").strip():
        return None
    tags = (await db.execute(select(Tag).where(Tag.is_deleted == 0))).scalars().all()
    tag_id = matching_device_tag(device, tags)
    if tag_id is None:
        return None
    scope = {}
    for model, library in ((DocumentBreakdown, "breakdown"), (DocumentKnowledge, "knowledge")):
        rows = (await db.execute(select(model.id, model.tag).where(model.is_deleted == 0))).all()
        scope[library] = {int(doc_id) for doc_id, tags in rows if document_matches_device(tags, tag_id)}
    return scope
