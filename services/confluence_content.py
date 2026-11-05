"""Ordered, loss-aware Confluence storage adapter; no model calls or DB access."""
import re
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from services.confluence_language import chinese_blocks, chinese_title


def safe_link(base, value):
    if not value:
        return ""
    url = urljoin(base + "/", value or "")
    return url if urlsplit(url).scheme in {"http", "https"} else ""


def parse_content(page, base_url, image_resolver=None):
    body = page.get("body", {})
    storage = body.get("storage", {}).get("value")
    raw = storage if storage is not None else body.get("view", {}).get("value", "")
    soup = chinese_blocks(BeautifulSoup(raw, "html.parser"))
    # Language macros contain parallel versions, not complementary sections.
    # Remove English before walking so its links/images are not collected either.
    for macro in list(soup.find_all("ac:structured-macro")):
        name = str(macro.get("ac:name") or "").lower() if macro.attrs else ""
        if name == "english" or name.startswith("english-"):
            macro.decompose()
    blocks, warnings = [], []
    links, images = [], []

    def inline(node):
        if isinstance(node, NavigableString):
            return str(node)
        if not isinstance(node, Tag) or node.name in {"script", "style"}:
            return ""
        if node.name in {"img", "ac:image"}:
            return image(node)
        if node.name == "br":
            return "\n"
        if node.name == "ac:link":
            attachment = node.find("ri:attachment")
            target = node.find("ri:page")
            label = node.find(["ac:plain-text-link-body", "ac:link-body"])
            title = (label.get_text(" ", strip=True) if label else "")
            if attachment:
                filename = attachment.get("ri:filename", "")
                links.append({"type": "attachment", "filename": filename,
                              "page_title": (attachment.find("ri:page") or {}).get("ri:content-title")})
                return title or filename
            if target:
                title = chinese_title(title or target.get("ri:content-title", ""))
                target_id = target.get("ri:content-id")
                url = base_url + "/pages/viewpage.action?pageId=" + target_id if target_id else ""
                space = target.get("ri:space-key") or page.get("space", {}).get("key")
                if not url and space:
                    url = base_url + "/pages/viewpage.action?" + urlencode({"spaceKey": space, "title": target.get("ri:content-title", "")})
                links.append({"type": "page", "title": title, "page_id": target_id,
                              "space_key": target.get("ri:space-key"), "url": url})
                return (title + " (" + url + ")") if url else title
        text = "".join(inline(child) for child in node.children)
        if node.name == "a":
            url = safe_link(base_url, node.get("href"))
            links.append({"type": "link", "title": text.strip(), "url": url})
            return text + (" (" + url + ")" if url else "")
        return text

    def image(node):
        attachment = node.find("ri:attachment")
        external = node.find("ri:url")
        ref = {"filename": attachment.get("ri:filename") if attachment else None,
               "url": node.get("src") or (external.get("ri:value") if external else None),
               "page_title": (attachment.find("ri:page") or {}).get("ri:content-title") if attachment else None,
               "alt": node.get("alt") or node.get("ac:alt") or ""}
        local = None
        if image_resolver:
            try:
                local = image_resolver(ref)
            except Exception:
                warnings.append("图片下载失败：" + str(ref["filename"] or ref["url"] or "未知图片"))
        if not local:
            warnings.append("图片未本地化：" + str(ref["filename"] or ref["url"] or "未知图片"))
        images.append({"type": "image", **ref, "image_url": local})
        return "[图片：" + (local or ref["alt"] or ref["filename"] or "未下载") + "]"

    def table(node):
        grid, cells = {}, []
        rows = [r for r in node.find_all("tr") if r.find_parent("table") is node]
        if len(rows) > 2000:
            raise ValueError("表格超过 2000 行，请拆分页面")
        for r, row in enumerate(rows):
            c = 0
            for cell in row.find_all(["td", "th"], recursive=False):
                while (r, c) in grid:
                    c += 1
                rs, cs = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
                if not (1 <= rs <= 2000 and 1 <= cs <= 100 and c + cs <= 100):
                    raise ValueError("表格合并单元格范围异常")
                value = inline(cell).strip()
                cells.append({"row": r, "column": c, "rowspan": rs, "colspan": cs,
                              "header": cell.name == "th", "text": value})
                for rr in range(r, r + rs):
                    for cc in range(c, c + cs):
                        grid[rr, cc] = value
                c += cs
        width = max((c for _, c in grid), default=-1) + 1
        matrix = [[grid.get((r, c), "") for c in range(width)] for r in range(len(rows))]
        # Preserve original cells and generate a row-wise searchable representation.
        header_count = 0
        for row in rows:
            if row.find("th", recursive=False):
                header_count += 1
            else:
                break
        labels = [" / ".join(dict.fromkeys(matrix[r][c] for r in range(header_count) if matrix[r][c]))
                  or "列%d" % (c + 1) for c in range(width)]
        text = "\n".join("；".join(labels[c] + "：" + value for c, value in enumerate(row))
                         for row in matrix[header_count:])
        blocks.append({"type": "table", "cells": cells, "rows": matrix,
                       "headers": labels, "header_rows": header_count, "text": text})

    def walk(node, depth=0, prefix=""):
        if isinstance(node, NavigableString):
            if str(node).strip():
                blocks.append({"type": "paragraph", "text": str(node).strip()})
            return
        if not isinstance(node, Tag) or node.name in {"script", "style", "nav", "footer"}:
            return
        name = node.name
        if name == "table":
            table(node)
        elif name in {"img", "ac:image"}:
            text = image(node)
            blocks.append({**images[-1], "text": text})
        elif re.fullmatch(r"h[1-6]", name):
            blocks.append({"type": "heading", "level": int(name[1]), "text": inline(node).strip()})
        elif name in {"ul", "ol"}:
            for index, li in enumerate(node.find_all("li", recursive=False), 1):
                marker = str(index) + ". " if name == "ol" else "- "
                text = "".join(inline(c) for c in li.children if not (isinstance(c, Tag) and c.name in {"ul", "ol"}))
                blocks.append({"type": "list", "depth": depth, "text": "  " * depth + marker + text.strip()})
                for child in li.find_all(["ul", "ol"], recursive=False):
                    walk(child, depth + 1)
        elif name in {"p", "pre", "a", "ac:link"}:
            text = inline(node).strip()
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif name == "ac:structured-macro":
            macro = node.get("ac:name", "unknown")
            rich = node.find("ac:rich-text-body", recursive=False)
            plain = node.find("ac:plain-text-body", recursive=False)
            if macro != "chinese":
                blocks.append({"type": "macro", "name": macro, "text": "[" + macro + "]"})
            if rich:
                for child in rich.children:
                    walk(child, depth)
            elif plain:
                blocks.append({"type": "paragraph", "text": plain.get_text()})
            else:
                macro_id = node.get("ac:macro-id")
                view = BeautifulSoup(body.get("view", {}).get("value", ""), "html.parser")
                rendered = view.find(attrs={"data-macro-id": macro_id}) if macro_id else None
                if rendered:
                    for child in rendered.children:
                        walk(child, depth)
                else:
                    warnings.append("需核对未展开的宏：" + macro)
        else:
            for child in node.children:
                walk(child, depth)

    for node in soup.children:
        walk(node)
    return {"blocks": blocks, "links": links, "images": images, "warnings": list(dict.fromkeys(warnings))}
