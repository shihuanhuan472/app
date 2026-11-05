"""Read native XMind topic hierarchies without OCR or layout inference."""
import hashlib
import json
from zipfile import ZipFile
import xml.etree.ElementTree as ET


def _xml_topic(element):
    node = {"id": element.get("id"), "title": element.findtext("title", ""), "children": {}}
    notes = element.find("notes")
    if notes is not None:
        plain = notes.find("plain")
        node["notes"] = {"plain": {"content": "".join((plain if plain is not None else notes).itertext())}}
    node["labels"] = [e.text or "" for e in element.findall("labels/label")]
    node["href"] = element.get("{http://www.w3.org/1999/xlink}href", "")
    for group in element.findall("children/topics"):
        node["children"].setdefault(group.get("type", "attached"), []).extend(
            _xml_topic(t) for t in group.findall("topic"))
    return node


def parse_xmind(file_path):
    """Return one tree per sheet/root, keeping floating topics independent.

    Relationships and summaries are retained as source metadata, never promoted
    to diagnostic parent-child edges. Archive members are never extracted.
    """
    with ZipFile(file_path) as archive:
        names = archive.namelist()
        member = "content.json" if "content.json" in names else "content.xml"
        if member not in names:
            raise ValueError("XMind 缺少 content.json 或 content.xml")
        if archive.getinfo(member).file_size > 32 * 1024 * 1024:
            raise ValueError("XMind 内容超过 32MB 解析限制")
        raw = archive.read(member)
    if member.endswith("json"):
        sheets = json.loads(raw.decode("utf-8-sig"))
    else:
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("XMind XML 不允许 DTD 或实体声明")
        root = ET.fromstring(raw)
        for element in root.iter():
            element.tag = element.tag.rsplit("}", 1)[-1]
        sheets = []
        for sheet in root.findall("sheet"):
            topic = sheet.find("topic")
            if topic is None:
                raise ValueError("XMind 工作表缺少根主题")
            sheets.append({"id": sheet.get("id"), "title": sheet.findtext("title", ""),
                           "rootTopic": _xml_topic(topic),
                           "relationships": [ET.tostring(e, encoding="unicode") for e in sheet.findall("relationships/relationship")]})
    if not isinstance(sheets, list) or not sheets:
        raise ValueError("XMind 没有可解析的工作表")
    trees = []
    digest = hashlib.sha256(raw).hexdigest()[:20]
    for si, sheet in enumerate(sheets):
        pending = [sheet.get("rootTopic")]
        seen = set()
        count = 0

        def convert(topic, depth=0):
            nonlocal count
            if not isinstance(topic, dict) or depth > 200:
                raise ValueError("XMind 主题结构无效或层级超过 200")
            count += 1
            if count > 50000:
                raise ValueError("XMind 节点数超过 50000")
            nid = str(topic.get("id") or "generated_%s" % count)
            if nid in seen:
                raise ValueError("XMind 存在重复主题 ID：" + nid)
            seen.add(nid)
            title = str(topic.get("title") or "").strip()
            node = {"id": nid, "text": title or "（无标题主题）", "role": "unknown",
                    "source_metadata": {k: v for k, v in topic.items() if k != "children"}}
            notes = (topic.get("notes") or {}).get("plain") or {}
            notes = notes.get("content", "") if isinstance(notes, dict) else str(notes)
            if notes:
                node["text"] += "\n备注：" + notes
            if topic.get("labels"):
                node["text"] += "\n标签：" + "、".join(topic["labels"])
            if topic.get("href"):
                node["text"] += "\n链接：" + topic["href"]
            groups = topic.get("children") or {}
            node["children"] = [convert(t, depth + 1) for t in groups.get("attached", [])]
            for kind, topics in groups.items():
                if kind != "attached":
                    pending.extend(topics)
            return node

        ri = 0
        while ri < len(pending):
            node = convert(pending[ri])
            trees.append({"tree_id": f"xmind_{digest}_{si}_{ri}",
                          "tree_name": sheet.get("title") or node["text"], "root": node,
                          "_meta": {"source_format": member, "sheet_id": sheet.get("id"),
                                    "independent_topic": ri > 0,
                                    "relationships": sheet.get("relationships", [])}})
            ri += 1
    return trees


def tree_text(tree):
    lines = []
    def visit(node, depth):
        lines.append("  " * depth + "- " + node["text"].replace("\n", "\n" + "  " * (depth + 1)))
        for child in node.get("children", []):
            visit(child, depth + 1)
    visit(tree["root"], 0)
    return "\n".join(lines)
