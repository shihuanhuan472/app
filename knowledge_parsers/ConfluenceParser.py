"""Convert staged Confluence blocks into the existing knowledge section contract."""
import json
import hashlib


def parse_bundle(marker):
    from knowledge_parsers.KnowledgeParser import KnowledgeParsedDocument, KnowledgeSectionData
    payload = json.loads(marker.get_text())
    if payload.get("format") != "confluence-source-v1":
        raise ValueError("无效的 Confluence 源文件")
    title, source = payload["title"], payload["source"]
    source = {**source, "source_type": "confluence"}
    sections, heading_path, pending = [], [], []
    all_images = payload.get("images", [])
    page_path = list(source.get("title_path") or [title])

    children = source.get("child_pages") or []
    child_titles = [str(child.get("title") or "").strip() for child in children]
    child_titles = [child_title for child_title in child_titles if child_title]
    if child_titles:
        sections.append(KnowledgeSectionData(
            section_index=0, section_title=title, section_type="navigation",
            plain_text="页面路径：" + " / ".join(page_path) + "\n页面目录：" + "；".join(child_titles),
            metadata={**source, "unit_type": "directory_index", "navigation_only": True,
                      "heading_path": [], "content_blocks": []}))

    def flush():
        if not pending:
            return
        heading_titles = [t for _, t in heading_path]
        heading_context = " / ".join(page_path + heading_titles)
        body = "\n\n".join(b.get("text", "") for b in pending).strip()
        text = "页面路径：" + heading_context + "\n" + body
        image_urls = list(dict.fromkeys(i["image_url"] for i in all_images
                                       if i.get("image_url") and any(
                                           i["image_url"] in block.get("text", "")
                                           for block in pending)))
        metadata = {**source, "content_hash": payload.get("content_hash"),
                    "heading_path": heading_titles, "unit_type": "page_section",
                    "chunk_strategy": "confluence_heading_v2",
                    "chapter_key": hashlib.sha256(json.dumps([source.get("page_id"), heading_path], ensure_ascii=False).encode()).hexdigest()[:24],
                    "content_blocks": list(pending), "confluence_bundle": True,
                    "source_links": payload.get("links", []), "parse_warnings": payload.get("warnings", [])}
        sections.append(KnowledgeSectionData(
            section_index=len(sections), section_title=heading_titles[-1] if heading_titles else title,
            section_type=str(len(sections) + 1), plain_text=text, image_urls=image_urls, metadata=metadata))
        pending.clear()

    for block in payload["blocks"]:
        if block["type"] == "heading":
            flush()
            level = block["level"]
            heading_path = [(n, t) for n, t in heading_path if n < level] + [(level, block["text"])]
        if block["type"] == "table" and len(block.get("rows", [])) > 25:
            flush()
            header_rows = block.get("header_rows", 0)
            rows = block["rows"][header_rows:]
            for start in range(0, len(rows), 20):
                subset = rows[start:start + 20]
                text = "\n".join("；".join(block["headers"][c] + "：" + value for c, value in enumerate(row)) for row in subset)
                pending.append({**block, "text": text, "row_range": [start + header_rows, start + header_rows + len(subset)]})
                flush()
        else:
            # Keep the complete chapter for display/context; vector subchunks are derived later.
            pending.append(block)
    flush()
    if not pending and not any(section.metadata.get("unit_type") == "page_section" for section in sections):
        sections.append(KnowledgeSectionData(section_index=len(sections), section_title=title,
            plain_text="页面路径：" + " / ".join(page_path),
            metadata={**source, "unit_type": "directory_index", "navigation_only": True}))
    content = "\n\n".join(s.plain_text for s in sections)
    offset = 0
    for section in sections:
        section.char_start = offset
        section.char_end = offset + len(section.plain_text)
        offset = section.char_end + 2
    images = list(dict.fromkeys(url for section in sections for url in section.image_urls))
    return KnowledgeParsedDocument(title=title, summary=title, content=content, image_urls=images, sections=sections)
