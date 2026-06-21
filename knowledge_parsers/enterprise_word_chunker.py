import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from docx import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph


@dataclass
class WordBlock:
    block_type: str
    text: str = ""
    style_name: str = ""
    heading_level: Optional[int] = None
    rows: List[List[str]] = field(default_factory=list)
    image_urls: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class WordChunk:
    title: str
    section_type: str
    plain_text: str
    image_urls: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class EnterpriseWordChunker:
    """DOCX parser and chunker tuned for enterprise RAG ingestion.

    Design goals:
    - Preserve table semantics for manuals, error-code sheets and SOPs.
    - Avoid directory-only imports by falling back when heading matching fails.
    - Produce chunks compatible with KnowledgeSectionData-like storage.
    - Keep dependencies light: python-docx only.
    """

    def __init__(
        self,
        save_image_blob: Callable[[bytes, str], str],
        max_chunk_chars: int = 1800,
        chunk_overlap_chars: int = 180,
        max_table_rows_per_chunk: int = 18,
        min_section_chars: int = 80,
    ):
        self.save_image_blob = save_image_blob
        self.max_chunk_chars = max_chunk_chars
        self.chunk_overlap_chars = chunk_overlap_chars
        self.max_table_rows_per_chunk = max_table_rows_per_chunk
        self.min_section_chars = min_section_chars

    def parse(self, file_path: str) -> List[WordChunk]:
        doc = DocxDocument(file_path)
        blocks = self._extract_blocks(doc)
        blocks = self._filter_blocks(blocks)
        sections = self._blocks_to_sections(blocks, Path(file_path).stem)
        chunks = self._sections_to_display_chunks(sections)
        return self._normalize_chunks(chunks, Path(file_path).stem)

    def _extract_blocks(self, doc) -> List[WordBlock]:
        blocks: List[WordBlock] = []
        for index, item in enumerate(self._iter_block_items(doc)):
            if isinstance(item, Paragraph):
                text = self._normalize_text(item.text)
                style_name = item.style.name if item.style else ""
                heading_level = self._heading_level(item, text)
                image_urls = self._save_images_from_element(doc, item._element)
                if text:
                    blocks.append(
                        WordBlock(
                            "text",
                            text=text,
                            style_name=style_name,
                            heading_level=heading_level,
                            image_urls=image_urls,
                            metadata={
                                "block_index": index,
                                "field_instruction": self._field_instruction_text(item._element),
                                "has_field_instruction": self._has_field_instruction(item._element),
                            },
                        )
                    )
                elif image_urls:
                    blocks.append(
                        WordBlock(
                            "image",
                            image_urls=image_urls,
                            metadata={"block_index": index},
                        )
                    )
            elif isinstance(item, Table):
                rows = self._table_rows(item)
                table_text = self._table_to_document_text(item)
                image_urls = self._save_images_from_element(doc, item._element)
                if table_text:
                    blocks.append(
                        WordBlock(
                            "table",
                            text=table_text,
                            rows=rows,
                            image_urls=image_urls,
                            metadata={"block_index": index},
                        )
                    )
                elif image_urls:
                    blocks.append(
                        WordBlock(
                            "image",
                            image_urls=image_urls,
                            metadata={"block_index": index},
                        )
                    )
        return blocks

    def _iter_block_items(self, parent):
        if isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:
            parent_elm = parent.element.body

        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    def _save_images_from_element(self, doc, element) -> List[str]:
        rel_ids = []
        for xpath in (".//a:blip/@r:embed", ".//a:blip/@r:link"):
            try:
                rel_ids.extend(element.xpath(xpath))
            except Exception:
                continue

        image_urls = []
        seen = set()
        for rel_id in rel_ids:
            if rel_id in seen or rel_id not in doc.part.rels:
                continue
            seen.add(rel_id)
            rel = doc.part.rels[rel_id]
            if "image" not in rel.target_ref:
                continue
            image_part = rel.target_part
            ext = image_part.content_type.split("/")[-1]
            image_urls.append(self.save_image_blob(image_part.blob, ext))
        return image_urls

    def _field_instruction_text(self, element) -> str:
        texts = []
        try:
            for node in element.xpath(".//w:instrText"):
                if node.text:
                    texts.append(node.text)
        except Exception:
            return ""
        return " ".join(texts)

    def _has_field_instruction(self, element) -> bool:
        return bool(self._field_instruction_text(element).strip())

    def _table_rows(self, table: Table) -> List[List[str]]:
        rows: List[List[str]] = []
        active_vertical_values: Dict[int, str] = {}
        for row in table.rows:
            cells = []
            seen_cell_ids = set()
            for column_index, cell in enumerate(row.cells):
                text = self._cell_plain_text(cell)
                cell_id = id(cell._tc)
                if cell_id in seen_cell_ids:
                    cells.append("")
                    continue
                seen_cell_ids.add(cell_id)
                merge_state = self._vertical_merge_state(cell)
                if merge_state == "restart":
                    active_vertical_values[column_index] = text
                elif merge_state == "continue":
                    text = text or active_vertical_values.get(column_index, "")
                else:
                    if text:
                        active_vertical_values[column_index] = text
                    elif column_index in active_vertical_values:
                        active_vertical_values.pop(column_index, None)
                cells.append(text)
            if any(cells):
                rows.append(cells)
        return rows

    def _vertical_merge_state(self, cell: _Cell) -> Optional[str]:
        try:
            tc_pr = cell._tc.tcPr
            v_merge = tc_pr.vMerge if tc_pr is not None else None
            if v_merge is None:
                return None
            value = v_merge.val
            return "continue" if value is None else str(value)
        except Exception:
            return None

    def _cell_plain_text(self, cell: _Cell) -> str:
        parts: List[str] = []
        for item in self._iter_block_items(cell):
            if isinstance(item, Paragraph):
                text = self._normalize_text(item.text)
                if text:
                    parts.append(text)
        return "<br>".join(part for part in parts if part).strip()

    def _table_has_nested_table(self, table: Table) -> bool:
        for row in table.rows:
            for cell in row.cells:
                for item in self._iter_block_items(cell):
                    if isinstance(item, Table):
                        return True
        return False

    def _table_to_document_text(self, table: Table) -> str:
        if not self._table_has_nested_table(table):
            return self._table_to_markdown(self._table_rows(table))

        parts: List[str] = []
        seen_cells = set()
        for row in table.rows:
            for cell in row.cells:
                cell_key = id(cell._tc)
                if cell_key in seen_cells:
                    continue
                seen_cells.add(cell_key)
                for item in self._iter_block_items(cell):
                    if isinstance(item, Paragraph):
                        text = self._normalize_text(item.text)
                        if text:
                            parts.append(text)
                    elif isinstance(item, Table):
                        table_text = self._table_to_document_text(item)
                        if table_text:
                            parts.append(table_text)
        return "\n".join(part for part in parts if part).strip()

    def _dedupe_merged_cells(self, rows: List[List[str]]) -> List[List[str]]:
        cleaned = []
        for row in rows:
            previous = None
            cleaned_row = []
            for cell in row:
                if cell and cell == previous:
                    cleaned_row.append("")
                else:
                    cleaned_row.append(cell)
                previous = cell
            cleaned.append(cleaned_row)
        return cleaned

    def _filter_blocks(self, blocks: List[WordBlock]) -> List[WordBlock]:
        filtered = []
        for block in blocks:
            if block.block_type == "text":
                if self._is_field_toc_block(block):
                    continue
                if self._is_noise_text(block.text):
                    continue
                if self._is_toc_text(block.text):
                    continue
            if block.block_type == "table" and self._is_toc_table(block.rows):
                continue
            filtered.append(block)
        return filtered

    def _is_field_toc_block(self, block: WordBlock) -> bool:
        instruction = str((block.metadata or {}).get("field_instruction") or "").upper()
        text = str(block.text or "").strip()
        if "TOC" in instruction or "PAGEREF" in instruction or "_TOC" in instruction:
            return True
        if (block.metadata or {}).get("has_field_instruction") and self._looks_like_toc_entry(text):
            return True
        return False

    def _looks_like_toc_entry(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        if stripped in {"目录", "Table of Contents", "Contents"}:
            return True
        return bool(re.match(r"^(?:\d+(?:[.．]\d+)*[、.．]?|[一二三四五六七八九十]+[、.．]?)?.{1,80}(?:\s+\d{1,4})?$", stripped))

    def _blocks_to_sections(self, blocks: List[WordBlock], fallback_title: str) -> List[Dict]:
        sections: List[Dict] = []
        current = self._new_section(fallback_title, "1")
        has_content = False

        for block in blocks:
            if block.block_type == "text" and self._is_heading_block(block):
                if has_content:
                    sections.append(current)
                current = self._new_section(block.text, self._section_type_from_heading(block))
                current["blocks"].append(block)
                has_content = True
                continue

            current["blocks"].append(block)
            if block.text or block.image_urls:
                has_content = True

        if has_content:
            sections.append(current)

        if not sections:
            fallback = self._new_section(fallback_title, "1")
            fallback["blocks"] = blocks
            sections = [fallback]

        return self._merge_short_sections(sections)

    def _new_section(self, title: str, section_type: str) -> Dict:
        return {
            "title": (title or "未命名章节").strip()[:255],
            "section_type": section_type or "",
            "blocks": [],
            "section_id": str(uuid.uuid4()),
        }

    def _merge_short_sections(self, sections: List[Dict]) -> List[Dict]:
        if not sections:
            return []

        merged: List[Dict] = []
        for section in sections:
            text_len = len("".join(self._section_text(section).split()))
            if merged and text_len < self.min_section_chars and not self._section_has_table(section):
                merged[-1]["blocks"].extend(section["blocks"])
                if section["title"] and section["title"] not in merged[-1]["title"]:
                    merged[-1]["title"] = f"{merged[-1]['title']} / {section['title']}"[:255]
            else:
                merged.append(section)
        return merged

    def _sections_to_display_chunks(self, sections: List[Dict]) -> List[WordChunk]:
        chunks: List[WordChunk] = []
        for section_index, section in enumerate(sections):
            parts = []
            image_urls: List[str] = []
            image_positions = []
            table_count = 0
            table_summaries = []

            for block in section["blocks"]:
                if block.block_type == "text":
                    if block.text:
                        parts.append(block.text)
                elif block.block_type == "table":
                    table_count += 1
                    table_text = block.text or self._table_to_markdown(block.rows)
                    if table_text:
                        parts.append(table_text)
                        table_summaries.append(
                            {
                                "table_index": table_count,
                                "row_count": max(0, len(block.rows) - 1),
                                "header": block.rows[0] if block.rows else [],
                            }
                        )
                elif block.block_type == "image":
                    pass

                for image_url in block.image_urls:
                    image_urls.append(image_url)
                    marker = f"【图片{len(image_urls)}】"
                    parts.append(marker)
                    image_positions.append(
                        {
                            "image_url": image_url,
                            "paragraph_index": max(len(parts) - 2, 0),
                            "nearby_text_before": parts[-2][:200] if len(parts) >= 2 else "",
                        }
                    )

            text = "\n".join(part for part in parts if part).strip()
            if not text and not image_urls:
                continue

            metadata = self._base_metadata(section, section_index, "section")
            metadata.update(
                {
                    "image_positions": image_positions,
                    "table_count": table_count,
                    "tables": table_summaries,
                    "split_method": "enterprise_docx_display_section",
                    "chunk_strategy": "enterprise_docx_title_section_v2",
                    "section_role": "body",
                    "vector_hint": {
                        "split_tables_by_rows": True,
                        "max_table_rows_per_chunk": self.max_table_rows_per_chunk,
                        "max_chunk_chars": self.max_chunk_chars,
                    },
                }
            )
            chunks.append(
                WordChunk(
                    title=section["title"],
                    section_type=section["section_type"],
                    plain_text=text,
                    image_urls=image_urls,
                    metadata=metadata,
                )
            )
        return chunks

    def _split_textual_blocks(self, section: Dict, blocks: List[WordBlock], section_index: int) -> List[WordChunk]:
        parts = []
        image_urls: List[str] = []
        image_positions = []
        for block in blocks:
            if block.text:
                parts.append(block.text)
            for image_url in block.image_urls:
                image_urls.append(image_url)
                marker = f"【图片{len(image_urls)}】"
                parts.append(marker)
                image_positions.append(
                    {
                        "image_url": image_url,
                        "paragraph_index": max(len(parts) - 2, 0),
                        "nearby_text_before": parts[-2][:200] if len(parts) >= 2 else "",
                    }
                )

        text = "\n".join(part for part in parts if part).strip()
        if not text and not image_urls:
            return []

        subtexts = self._split_long_text(text)
        chunks = []
        for sub_index, subtext in enumerate(subtexts):
            metadata = self._base_metadata(section, section_index, "section_text")
            metadata.update(
                {
                    "subchunk_index": sub_index,
                    "image_positions": image_positions if sub_index == 0 else [],
                    "split_method": "enterprise_docx_text",
                }
            )
            chunks.append(
                WordChunk(
                    title=section["title"],
                    section_type=section["section_type"],
                    plain_text=subtext,
                    image_urls=image_urls if sub_index == 0 else [],
                    metadata=metadata,
                )
            )
        return chunks

    def _split_table_block(
        self,
        section: Dict,
        table_block: WordBlock,
        section_index: int,
        table_index: int,
    ) -> List[WordChunk]:
        rows = table_block.rows or []
        if not rows:
            return []

        header = rows[0]
        body_rows = rows[1:] if len(rows) > 1 else []
        row_groups = self._table_row_groups(body_rows)
        if not row_groups:
            row_groups = [[]]

        chunks = []
        for group_index, group in enumerate(row_groups):
            group_rows = [header] + group if group else [header]
            text = self._table_to_markdown(group_rows)
            title = f"{section['title']} - 表格{table_index}"
            metadata = self._base_metadata(section, section_index, "table")
            metadata.update(
                {
                    "table_index": table_index,
                    "table_group_index": group_index,
                    "row_start": group_index * self.max_table_rows_per_chunk + 1,
                    "row_end": group_index * self.max_table_rows_per_chunk + len(group),
                    "table_header": header,
                    "split_method": "enterprise_docx_table_rows",
                }
            )
            chunks.append(
                WordChunk(
                    title=title[:255],
                    section_type=section["section_type"],
                    plain_text=text,
                    image_urls=table_block.image_urls if group_index == 0 else [],
                    metadata=metadata,
                )
            )
        return chunks

    def _table_row_groups(self, rows: List[List[str]]) -> List[List[List[str]]]:
        groups = []
        current: List[List[str]] = []
        current_chars = 0
        for row in rows:
            row_chars = len("".join(row))
            if current and (
                len(current) >= self.max_table_rows_per_chunk
                or current_chars + row_chars >= self.max_chunk_chars
            ):
                groups.append(current)
                current = []
                current_chars = 0
            current.append(row)
            current_chars += row_chars
        if current:
            groups.append(current)
        return groups

    def _split_long_text(self, text: str) -> List[str]:
        if len(text) <= self.max_chunk_chars:
            return [text]

        paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
        chunks = []
        current = ""
        for paragraph in paragraphs:
            if current and len(current) + len(paragraph) + 1 > self.max_chunk_chars:
                chunks.append(current.strip())
                overlap = current[-self.chunk_overlap_chars:].strip() if self.chunk_overlap_chars > 0 else ""
                current = f"{overlap}\n{paragraph}" if overlap else paragraph
            else:
                current = f"{current}\n{paragraph}" if current else paragraph
        if current.strip():
            chunks.append(current.strip())
        return chunks or [text[: self.max_chunk_chars]]

    def _normalize_chunks(self, chunks: List[WordChunk], fallback_title: str) -> List[WordChunk]:
        normalized = []
        counter = 0
        for chunk in chunks:
            if not chunk.plain_text.strip() and not chunk.image_urls:
                continue
            counter += 1
            metadata = dict(chunk.metadata or {})
            metadata.setdefault("section_role", "body")
            metadata.setdefault("chunk_strategy", "enterprise_docx_v1")
            metadata.setdefault("word_chunk_index", counter - 1)
            section_type = chunk.section_type or str(counter)
            normalized.append(
                WordChunk(
                    title=chunk.title or fallback_title,
                    section_type=section_type,
                    plain_text=chunk.plain_text.strip(),
                    image_urls=chunk.image_urls,
                    metadata=metadata,
                )
            )
        if normalized:
            return normalized
        return [
            WordChunk(
                title=fallback_title or "未命名文档",
                section_type="1",
                plain_text="",
                metadata={"section_role": "body", "chunk_strategy": "enterprise_docx_v1"},
            )
        ]

    def _base_metadata(self, section: Dict, section_index: int, content_type: str) -> Dict:
        return {
            "source_section_id": section.get("section_id"),
            "source_section_title": section.get("title"),
            "source_section_index": section_index,
            "content_type": content_type,
        }

    def _section_text(self, section: Dict) -> str:
        return "\n".join(block.text for block in section.get("blocks", []) if block.text)

    def _section_has_table(self, section: Dict) -> bool:
        return any(block.block_type == "table" for block in section.get("blocks", []))

    def _table_to_markdown(self, rows: List[List[str]]) -> str:
        cleaned_rows = []
        for row in rows:
            cleaned = [str(cell or "").replace("|", " ").strip() for cell in row]
            if any(cleaned):
                cleaned_rows.append(cleaned)
        if not cleaned_rows:
            return ""

        max_cols = max(len(row) for row in cleaned_rows)
        normalized_rows = [row + [""] * (max_cols - len(row)) for row in cleaned_rows]

        def render(row: List[str]) -> str:
            return "| " + " | ".join(row) + " |"

        return "\n".join(
            [render(normalized_rows[0]), render(["---"] * max_cols)]
            + [render(row) for row in normalized_rows[1:]]
        )

    def _heading_level(self, paragraph: Paragraph, text: str) -> Optional[int]:
        style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
        style_match = re.search(r"(?:heading|标题)\s*([1-6])", style_name)
        if style_match:
            return int(style_match.group(1))

        try:
            p_pr = paragraph._element.pPr
            if p_pr is not None and p_pr.outlineLvl is not None:
                return int(p_pr.outlineLvl.val) + 1
        except Exception:
            pass

        if self._looks_like_heading(text):
            marker = self._section_marker_from_text(text)
            return max(1, min(6, marker.count(".") + 1)) if marker else 1
        return None

    def _is_heading_block(self, block: WordBlock) -> bool:
        if block.heading_level is not None:
            return True
        return self._looks_like_heading(block.text)

    def _section_type_from_heading(self, block: WordBlock) -> str:
        marker = self._section_marker_from_text(block.text)
        if marker:
            return marker
        if block.heading_level:
            return f"level_{block.heading_level}"
        return ""

    def _looks_like_heading(self, text: str) -> bool:
        stripped = (text or "").strip()
        if not stripped or len(stripped) > 90:
            return False
        if self._is_toc_text(stripped):
            return False
        if re.search(r"[。；;]$", stripped):
            return False
        patterns = [
            r"^\d+(?:[.．]\d+)*[、.．\s]+.+",
            r"^第\s*[一二三四五六七八九十百千万0-9]+\s*[章节篇部分][、.．\s]*.*",
            r"^[一二三四五六七八九十]+[、.．\s]+.+",
        ]
        return any(re.match(pattern, stripped) for pattern in patterns)

    def _section_marker_from_text(self, text: str) -> str:
        stripped = (text or "").strip()
        match = re.match(r"^(\d+(?:[.．]\d+)*)[、.．\s]", stripped)
        if match:
            return match.group(1).replace("．", ".").strip(".")
        return ""

    def _is_toc_table(self, rows: List[List[str]]) -> bool:
        if not rows:
            return False
        flat = [cell for row in rows for cell in row if cell]
        if not flat:
            return False
        toc_like = sum(1 for cell in flat if self._is_toc_text(cell))
        return toc_like >= max(2, len(flat) // 2)

    def _is_toc_text(self, text: str) -> bool:
        stripped = (text or "").strip()
        normalized = re.sub(r"\s+", "", stripped).lower()
        if normalized in {"目录", "目錄", "contents", "tableofcontents", "图目录", "表目录"}:
            return True
        if re.match(r"^toc\s", normalized):
            return True
        # Word TOC entries often look like "2.1 开箱 ........ 11".
        return bool(
            re.match(
                r"^(?:\d+(?:[.．]\d+)*|第\s*[一二三四五六七八九十百千万0-9]+\s*[章节]|[一二三四五六七八九十]+[、.．])?.{1,120}"
                r"(?:[.·•…\-–—_ ]{2,}|\t|\s{2,})\d{1,4}$",
                stripped,
            )
        )

    def _is_noise_text(self, text: str) -> bool:
        stripped = (text or "").strip()
        normalized = re.sub(r"\s+", "", stripped)
        if not normalized:
            return True
        if re.fullmatch(r"第?\s*\d+\s*页?|[-–—]?\s*\d+\s*[-–—]?", stripped):
            return True
        if re.fullmatch(r"page\s*\d+(\s*/\s*\d+)?", stripped, flags=re.IGNORECASE):
            return True
        if re.search(r"机密|保密|confidential", normalized, flags=re.IGNORECASE) and re.search(
            r"仅适用于|服务工程师|授权服务提供商|请勿外传|donotdistribute|internaluse",
            normalized,
            flags=re.IGNORECASE,
        ):
            return True
        return False

    def _normalize_text(self, text: str) -> str:
        lines = []
        for raw_line in str(text or "").replace("\r", "\n").splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                lines.append(line)
        return "\n".join(lines).strip()
