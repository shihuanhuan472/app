"""Structure-preserving mindmap chunks; no artificial character limit."""
import hashlib
import json


STRATEGY = "mindmap_path_branch_v1"


def chunk_mindmap(tree, *, source_file="", source_version=None):
    """Return complete leaf paths and branching nodes without inferring roles.

    The caller retains the original tree. Synthetic OCR attachments are excluded
    from diagnostic chains until their connections have been reviewed.
    """
    root = tree.get("root")
    if not isinstance(root, dict):
        raise ValueError("脑图缺少 root 节点")
    tree_id = str(tree.get("tree_id") or "t_" + hashlib.sha256(
        json.dumps(root, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20])
    meta = tree.get("_meta") or {}
    suspect_ids = {f"n_{i}" for i in meta.get("detached", []) + meta.get("orphans", [])}
    chunks, seen = [], set()

    def visit(node, ancestors, positions, uncertain=False):
        if not isinstance(node, dict) or not str(node.get("text") or "").strip():
            raise ValueError("脑图节点必须包含非空 text")
        nid = str(node.get("id") or "position_" + "_".join(map(str, positions)))
        if nid in seen:
            raise ValueError(f"脑图存在重复节点 ID 或循环引用：{nid}")
        seen.add(nid)
        children = node.get("children") or []
        if not isinstance(children, list):
            raise ValueError(f"节点 {nid} 的 children 必须为列表")
        current = {"node_id": nid, "text": str(node["text"]).strip(),
                   "role": node.get("role", "unknown")}
        path = ancestors + [current]
        uncertain = uncertain or nid in suspect_ids or node.get("flag") in {"orphan", "detached"}
        common = {
            "tree_id": tree_id, "node_id": nid,
            "parent_id": ancestors[-1]["node_id"] if ancestors else None,
            "node_ids": [n["node_id"] for n in path],
            "path": [n["text"] for n in path], "path_nodes": path,
            "source_file": source_file, "source_version": source_version,
            "chunk_strategy": STRATEGY,
        }
        heading = f"脑图主题：{str(root['text']).strip()}"
        if not uncertain and not children:
            text = heading + "\n完整链路：\n" + "\n".join(
                f"{i + 1}. {n['text']}" for i, n in enumerate(path))
            chunks.append({**common, "chunk_id": f"{tree_id}:path:{nid}",
                           "chunk_type": "path", "text": text})
        if not uncertain and len(children) > 1:
            branches = []
            for i, child in enumerate(children):
                cid = str(child.get("id") or "position_" + "_".join(map(str, positions + [i])))
                if cid in suspect_ids or child.get("flag") in {"orphan", "detached"}:
                    continue
                branches.append({"node_id": cid, "text": str(child.get("text") or "").strip()})
            if len(branches) > 1:
                text = (heading + "\n上游链路：" + " → ".join(n["text"] for n in path)
                        + "\n当前位置：" + current["text"] + "\n后续分支（分别保留，需确认适用条件）：\n"
                        + "\n".join(f"- {b['text']}" for b in branches))
                chunks.append({**common, "chunk_id": f"{tree_id}:branch:{nid}",
                               "chunk_type": "branch", "branches": branches, "text": text})
        for i, child in enumerate(children):
            visit(child, path, positions + [i], uncertain)

    visit(root, [], [0])
    return chunks
