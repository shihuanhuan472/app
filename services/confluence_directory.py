"""Build a browsing tree from source metadata, without changing retrieval scope."""
import json


def build_directory(rows):
    nodes = {}
    child_orders = {}
    attachment_keys = set()
    def ensure(key, title, parent=None):
        return nodes.setdefault(key, {"key": key, "title": title, "parent": parent, "documents": []})
    seen = set()
    for doc_id, doc_title, section_id, section_title, section_index, metadata in rows:
        meta = json.loads(metadata) if isinstance(metadata, str) else metadata or {}
        if meta.get("source_type") != "confluence" or not meta.get("page_id"):
            continue
        site = str(meta.get("site") or "Confluence")
        site_key = json.dumps([site])
        ensure(site_key, site)
        path = list(meta.get("title_path") or [doc_title])
        ancestors = list(meta.get("ancestor_ids") or [])
        parent = site_key
        for index, title in enumerate(path[:-1]):
            page_id = ancestors[index] if index < len(ancestors) else (
                meta.get("parent_id") if index == len(path) - 2 else None)
            key = json.dumps([site, str(page_id)]) if page_id else json.dumps([site, "path", path[:index+1]])
            ensure(key, title, parent)
            parent = key
        page_key = json.dumps([site, str(meta["page_id"])])
        # Only page snapshots own sibling order; attachment snapshots may be stale.
        if not meta.get("attachment_id") and isinstance(meta.get("child_pages"), list):
            try:
                version = int(meta.get("source_version") or 0)
            except (TypeError, ValueError):
                version = 0
            rank = (version, doc_id)
            if page_key not in child_orders or rank > child_orders[page_key][0]:
                order = {}
                for child in meta["child_pages"]:
                    if isinstance(child, dict) and child.get("page_id") is not None:
                        key = json.dumps([site, str(child["page_id"])])
                        order.setdefault(key, len(order))
                child_orders[page_key] = (rank, order)
        node = ensure(page_key, path[-1], parent)
        if meta.get("attachment_id"):
            node = ensure(json.dumps([site, "attachment", str(meta["page_id"]), str(meta["attachment_id"])]),
                          meta.get("attachment_title") or doc_title, page_key)
            attachment_keys.add(node["key"])
        document = next((item for item in node["documents"] if item["id"] == doc_id), None)
        if document is None:
            document = {"id": doc_id, "title": doc_title, "sections": []}
            node["documents"].append(document)
        if (doc_id, section_id) not in seen and not meta.get("navigation_only"):
            document["sections"].append({"id": section_id, "index": section_index, "title": section_title or "正文"})
            seen.add((doc_id, section_id))
    for node in nodes.values():
        node["documents"].sort(key=lambda item: item["id"], reverse=True)
    def sort_key(node):
        order = child_orders.get(node["parent"], (None, {}))[1]
        return (node["parent"] or '', node["key"] in attachment_keys,
                order.get(node["key"], len(order)), node["title"].casefold(), node["key"])
    # Consumers filter this flat list by parent, preserving each sibling's order.
    return sorted(nodes.values(), key=sort_key)
