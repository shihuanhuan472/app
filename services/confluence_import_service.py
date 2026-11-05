"""Stage source snapshots; existing document parse/review tasks perform publication."""
import hashlib
import html
import io
import json
import os
import re
import uuid
from pathlib import Path

from PIL import Image

from services.confluence_content import parse_content
from services.confluence_client import ConfluenceError
from services.confluence_language import chinese_title

# Web ingestion only downloads readable documents, never software/firmware
# packages. Keep this independent of the general upload allowlist.
WEB_DOCUMENT_EXTENSIONS = frozenset({
    '.pdf', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.xlsm',
    '.csv', '.txt', '.md', '.markdown', '.xmind',
})


def stage_page(client, root_id, page_id, include_attachments=False, page_data=None, child_pages=None):
    page = page_data or client.page(page_id)
    root_id, page_id = client.page_id(root_id), client.page_id(page_id)
    ancestors = page.get("ancestors", [])
    if root_id != page_id and root_id not in {str(a["id"]) for a in ancestors}:
        raise ConfluenceError("所选页面不属于指定根页面")
    title = chinese_title(page.get("title", page_id))
    source_url = client.base_url + "/pages/viewpage.action?pageId=" + page_id
    source = {"source_type": "confluence", "language_policy": "chinese_only_v1", "site": client.base_url, "page_id": page_id,
              "root_id": root_id, "parent_id": str(ancestors[-1]["id"]) if ancestors else None,
              "title_path": [chinese_title(a["title"]) for a in ancestors] + [title],
              "ancestor_ids": [str(a["id"]) for a in ancestors],
              "source_url": source_url, "source_version": page.get("version", {}).get("number"),
              "updated_at": page.get("version", {}).get("when")}
    base = Path(os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")).resolve()
    directory = base / os.getenv("SOURCE_DOCUMENT_DIR", "upload/source_documents") / "confluence" / uuid.uuid4().hex
    image_dir = (base / os.getenv("IMAGE_DIR", "upload/images")).resolve()
    if base not in directory.resolve().parents or base not in image_dir.parents:
        raise ConfluenceError("源文档及图片目录必须位于 DOCUMENT_BASE_DIR 内")
    directory.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    attachments = list(client.children(page_id, "attachment"))
    source["attachment_versions"] = sorted(
        [{"id": str(a["id"]), "version": a.get("version", {}).get("number")}
         for a in attachments], key=lambda a: a["id"])
    source["attachments_included"] = bool(include_attachments)
    child_pages = list(child_pages) if child_pages is not None else list(client.children(page_id, "page"))
    source["child_pages"] = [{"page_id": str(child["id"]), "title": chinese_title(child.get("title", ""))}
                             for child in child_pages]
    attachment_map = {a["title"]: a for a in attachments}
    image_cache = {}

    def download_image(ref):
        if ref.get("page_title") and ref["page_title"] != title:
            raise ConfluenceError("跨页面附件图片需人工核对")
        attachment = attachment_map.get(ref.get("filename"))
        url = attachment.get("_links", {}).get("download") if attachment else ref.get("url")
        if not url:
            raise ConfluenceError("未找到图片附件")
        if url in image_cache:
            return image_cache[url]
        data, mime = client.request(url)
        if not mime.lower().startswith("image/"):
            raise ConfluenceError("图片返回了非图片内容")
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif", "BMP": ".bmp"}.get(image.format)
        if not ext:
            raise ConfluenceError("不支持的图片格式")
        # Unique resources avoid lifecycle deletion of another document's images.
        name = uuid.uuid4().hex + ext
        (image_dir / name).write_bytes(data)
        local = (image_dir / name).relative_to(base).as_posix()
        image_cache[url] = local
        return local

    parsed = parse_content(page, client.base_url, download_image)
    files = []
    if include_attachments:
        from utils.file_classifier import ALLOWED_DOCUMENT_EXTENSIONS, is_upload_content_valid
        for attachment in attachments:
            ext = Path(attachment["title"]).suffix.lower()
            if ext not in WEB_DOCUMENT_EXTENSIONS or ext not in ALLOWED_DOCUMENT_EXTENSIONS:
                continue
            try:
                data, mime = client.request(attachment["_links"]["download"])
                if "text/html" in mime.lower() or not is_upload_content_valid(ext, data[:65536]):
                    raise ConfluenceError("附件格式校验失败")
                path = directory / (str(attachment["id"]) + ext)
                path.write_bytes(data)
                attachment_source = dict(source, attachment_id=str(attachment["id"]),
                                         attachment_version=attachment.get("version", {}).get("number"),
                                         attachment_title=attachment["title"])
                Path(str(path) + ".source.json").write_text(json.dumps(attachment_source, ensure_ascii=False), encoding="utf-8")
                files.append({"path": str(path.relative_to(base)).replace("\\", "/"),
                              "name": attachment["title"], "size": len(data)})
            except Exception:
                parsed["warnings"].append("附件下载失败：" + attachment["title"])
    stable_source = {key: value for key, value in source.items() if key not in {"root_id", "attachments_included"}}
    fingerprint = hashlib.sha256(json.dumps({"body": page.get("body"), "source": stable_source,
                                            "attachments": attachments, "warnings": parsed["warnings"]}, sort_keys=True).encode()).hexdigest()
    bundle = {"format": "confluence-source-v1", "title": title, "source": source,
              "content_hash": fingerprint, **parsed}
    serialized = json.dumps(bundle, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    snapshot = "<!doctype html><html><head><meta charset='utf-8'><title>" + html.escape(title) + "</title></head><body>"
    local_images = set(image_cache.values())
    def preview_text(value):
        parts, offset = [], 0
        value = str(value or "")
        for match in re.finditer(r"\[图片：([^\]]+)\]", value):
            parts.append(html.escape(value[offset:match.start()]).replace("\n", "<br>"))
            image_path = match.group(1)
            parts.append(f'<img src="/{html.escape(image_path, quote=True)}" alt="页面图片" loading="lazy">'
                         if image_path in local_images else html.escape(match.group(0)))
            offset = match.end()
        parts.append(html.escape(value[offset:]).replace("\n", "<br>"))
        return "".join(parts)
    preview = ["<h1>" + html.escape(title) + "</h1>"]
    for block in parsed["blocks"]:
        block_type = block.get("type")
        text = preview_text(block.get("text"))
        if block_type == "heading":
            level = max(2, min(6, int(block.get("level") or 2)))
            preview.append(f"<h{level}>{text}</h{level}>")
        elif block_type == "table":
            rows = []
            for row_index, row in enumerate(block.get("rows") or []):
                cells = []
                for cell in block.get("cells") or []:
                    if cell["row"] != row_index:
                        continue
                    cell_tag = "th" if cell.get("header") else "td"
                    rs = max(1, min(2000, int(cell.get("rowspan", 1))))
                    cs = max(1, min(100, int(cell.get("colspan", 1))))
                    cells.append(f'<{cell_tag} rowspan="{rs}" colspan="{cs}">{preview_text(cell.get("text"))}</{cell_tag}>')
                rows.append("<tr>" + "".join(cells) + "</tr>")
            preview.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
        elif block_type == "image" and block.get("image_url"):
            preview.append(f'<figure><img src="/{html.escape(block["image_url"], quote=True)}" alt="{html.escape(str(block.get("alt") or ""), quote=True)}"></figure>')
        elif block_type == "image":
            preview.append("<p>" + text + "</p>")
        else:
            preview.append("<p>" + text + "</p>")
    snapshot += "<main>" + "".join(preview) + "</main>"
    snapshot += "<style>body{max-width:1100px;margin:32px auto;padding:0 24px;font:16px/1.75 Arial,'Microsoft YaHei',sans-serif;color:#172b4d}table{border-collapse:collapse;max-width:100%;overflow:auto}th,td{border:1px solid #cbd5e1;padding:7px 10px;text-align:left}img{max-width:100%;height:auto}</style>"
    snapshot += '<script type="application/json" id="confluence-source-v1">' + serialized + "</script></body></html>"
    path = directory / (page_id + ".html")
    path.write_text(snapshot, encoding="utf-8")
    # Preserve the API response as a non-executable sidecar for reparsing/audit.
    Path(str(path) + ".raw.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    files.insert(0, {"path": str(path.relative_to(base)).replace("\\", "/"),
                     "name": title + " [" + page_id + "].html", "size": path.stat().st_size})
    return {"files": files, "source": source, "warnings": parsed["warnings"],
            "notices": page.get('_title_notices', []), "content_hash": fingerprint,
            "image_paths": list(image_cache.values())}


def discard_staged(files, images=()):
    """Delete only this call's generated files, never a reused source record."""
    base = Path(os.getenv("DOCUMENT_BASE_DIR", "D:/Pycharm/code/Maintenance_Assistance_System")).resolve()
    for relative in [item["path"] for item in files] + list(images):
        target = (base / relative).resolve()
        if base not in target.parents:
            raise ValueError("源文件路径越界")
        for path in (target, Path(str(target) + ".source.json"), Path(str(target) + ".raw.json")):
            path.unlink(missing_ok=True)
