import io
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from services.confluence_client import ConfluenceClient, ConfluenceError
from services.confluence_content import parse_content
from services.confluence_import_service import stage_page


BASE = "https://wiki.example.test/confluence"


def test_software_packages_are_never_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_BASE_DIR', str(tmp_path))
    names = ['setup.zip', 'BaseCall.run', 'firmware.bin', 'setup.EXE',
             'installer.msi', 'system.iso', 'archive.tar.gz', 'scripts.7z']
    class Client:
        base_url = BASE
        def page_id(self, value): return value
        def page(self, value): return page('<p>安装包下载说明</p>')
        def children(self, page_id, kind='attachment'):
            if kind == 'page': return []
            return [{'id': str(i), 'title': name, '_links': {'download': '/package'}}
                    for i, name in enumerate(names)]
        def request(self, *args):
            pytest.fail('Software package download must not be requested')
    result = stage_page(Client(), '1', '2', include_attachments=True)
    assert len(result['files']) == 1  # Only the page snapshot.
    assert result['warnings'] == []


def test_parsed_attachment_links_do_not_require_vector_index():
    import asyncio
    from routers.confluence import parsed_attachments

    class DB:
        async def execute(self, statement):
            # Readable parsed documents must not wait for vector indexing.
            assert "is_vectorized" not in str(statement)
            class Rows:
                def all(self):
                    meta = {"source_type": "confluence", "attachment_id": "42",
                            "attachment_title": "风险清单.xlsx"}
                    return [(79, meta), (79, meta)]
            return Rows()

    result = asyncio.run(parsed_attachments(BASE, "2", user=object(), db=DB()))
    assert result.data == [{"document_id": 79, "attachment_id": "42",
                            "title": "风险清单.xlsx"}]


def test_only_chinese_language_version_and_shared_content_are_exported():
    downloads = []
    source = page('<p>公共内容 CAN</p>'
        '<ac:structured-macro ac:name="chinese"><ac:rich-text-body>'
        '<h2>执行步骤</h2><p>下载 DolphinControl 软件</p>'
        '</ac:rich-text-body></ac:structured-macro>'
        '<ac:structured-macro ac:name="english-us"><ac:rich-text-body>'
        '<p>Download software<a href="/english">English link</a>'
        '<ac:image><ri:attachment ri:filename="english.png"/></ac:image></p>'
        '</ac:rich-text-body></ac:structured-macro>')
    parsed = parse_content(source, BASE, lambda ref: downloads.append(ref))
    text = '\n'.join(block['text'] for block in parsed['blocks'])
    assert '公共内容 CAN' in text
    assert '下载 DolphinControl 软件' in text
    assert 'Download software' not in text
    assert '[chinese]' not in text
    assert not parsed['links'] and not parsed['images'] and not downloads


def test_english_macro_is_not_used_as_fallback_when_chinese_is_missing():
    parsed = parse_content(page('<ac:structured-macro ac:name="english-us">'
        '<ac:rich-text-body><p>English only</p></ac:rich-text-body>'
        '</ac:structured-macro>'), BASE)
    assert parsed['blocks'] == []


def test_confluence_import_skips_ocr_and_ai_tags(tmp_path, monkeypatch):
    import asyncio
    import sys
    from types import SimpleNamespace
    from knowledge_parsers.KnowledgeParser import KnowledgeParser, KnowledgeParsedDocument, KnowledgeSectionData
    from utils import automatic_tagging

    def unexpected(*args, **kwargs):
        raise AssertionError("Confluence import must not invoke enrichment")

    monkeypatch.setenv("DOCUMENT_BASE_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "knowledge_parsers.mindmap_parser",
                        SimpleNamespace(parse_mindmap_candidate=unexpected))
    monkeypatch.setattr(automatic_tagging, "classify_document_tag_ids", unexpected)
    section = KnowledgeSectionData(0, "步骤", plain_text="原始步骤",
        image_urls=["upload/images/example.png"], metadata={"source_type": "confluence"})
    document = KnowledgeParsedDocument("页面", "", "原始步骤", section.image_urls, [section])
    KnowledgeParser()._parse_document_mindmaps(document)
    assert "image_parsing" not in section.metadata
    assert section.image_urls == ["upload/images/example.png"]
    tags = [{"id": 1, "name": "设备"}]
    assert asyncio.run(automatic_tagging.resolve_automatic_tag_ids(
        manual_tags=[], file_name="设备.html", document=document, tag_snapshot=tags)) == ([], "unresolved")
    assert asyncio.run(automatic_tagging.resolve_automatic_tag_ids(
        manual_tags=[1], file_name="设备.html", document=document, tag_snapshot=tags)) == ([1], "manual")


def page(body, **extra):
    return {"id": "2", "title": "拆卸说明", "ancestors": [{"id": "1", "title": "维修手册"}],
            "version": {"number": 3}, "body": {"storage": {"value": body}}, **extra}


def test_table_spans_keep_column_associations_and_empty_cells():
    parsed = parse_content(page('<table><tr><th colspan="2">整机配置</th><th>型号</th><th>物料号</th></tr>'
                                '<tr><td rowspan="2">RUO</td><td>国内</td><td>ARS</td><td></td></tr>'
                                '<tr><td>国外</td><td>RS</td><td>123</td></tr></table>'), BASE)
    table = parsed["blocks"][0]
    assert table["rows"][2] == ["RUO", "国外", "RS", "123"]
    assert table["rows"][1][3] == ""
    assert table["cells"][3]["rowspan"] == 2
    assert "型号：RS" in table["text"]
    assert "物料号：" in table["text"]


def test_inline_image_position_lists_and_links_are_not_duplicated():
    parsed = parse_content(page('<h2>操作</h2><p>先断电<ac:image><ri:attachment ri:filename="a.png"/></ac:image>再拆盖</p>'
                                '<ol><li>第一步<ul><li>注意</li></ul></li><li>第二步</li></ol>'
                                '<p><a href="javascript:alert(1)">禁止链接</a></p>'), BASE,
                           lambda ref: "upload/images/a.png")
    assert [b["type"] for b in parsed["blocks"]] == ["heading", "paragraph", "list", "list", "list", "paragraph"]
    assert parsed["blocks"][1]["text"] == "先断电[图片：upload/images/a.png]再拆盖"
    assert "注意" not in parsed["blocks"][2]["text"]
    assert parsed["links"][0]["url"] == ""
    assert len(parsed["images"]) == 1


def test_macros_and_missing_images_report_incomplete_content():
    parsed = parse_content(page('<ac:structured-macro ac:name="include"/>'
                                '<ac:image><ri:attachment ri:filename="missing.png"/></ac:image>'), BASE)
    assert any("include" in w for w in parsed["warnings"])
    assert any("missing.png" in w for w in parsed["warnings"])


def test_client_rejects_external_origin_and_parses_ids():
    client = ConfluenceClient(BASE, "test-user", "test-password")
    assert client.session.auth.username == "test-user"
    assert client.session.auth.password == "test-password"
    assert client.page_id(BASE + "/pages/viewpage.action?pageId=123") == "123"
    assert client.page_id(BASE + "/spaces/X/pages/123/title") == "123"
    for url in ["https://evil.test/a", "//evil.test/a", "http://wiki.example.test/a", "https://user@wiki.example.test/a"]:
        with pytest.raises(ConfluenceError):
            client.safe_url(url)
    client.close()


def test_client_stops_on_rate_limit_without_retry(monkeypatch):
    from routers.confluence import TreeRequest, ImportRequest
    from urllib3.util.retry import Retry

    for request_type in (TreeRequest, ImportRequest):
        assert request_type(root_url="1").max_pages == 500
        assert request_type(root_url="1", max_pages=500).max_pages == 500
        with pytest.raises(ValueError):
            request_type(root_url="1", max_pages=501)
    client = ConfluenceClient(BASE, "test-user", "test-password")
    retry = client.session.get_adapter(BASE).max_retries
    assert isinstance(retry, Retry)
    assert 429 not in retry.status_forcelist

    class Response:
        status_code = 429
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(client.session, "get", lambda *args, **kwargs: Response())
    with pytest.raises(ConfluenceError, match="HTTP 429"):
        client.request(BASE + "/rest/api/content/1")
    client.close()


def test_paginated_tree_includes_parent_body_and_true_leaves(monkeypatch):
    client = ConfluenceClient(BASE, "test-user", "test-password")
    monkeypatch.setattr(client, "page", lambda root: {"id": "1", "title": "root"})
    def response(url, params):
        if url.endswith('/1/child/page'):
            if params["start"] == 0:
                return {"results": [{"id": "2", "title": "a"}], "limit": 1, "_links": {"next": "next"}}
            if params["start"] == 1:
                return {"results": [{"id": "3", "title": "b"}], "limit": 1}
        return {"results": [], "limit": 1}
    monkeypatch.setattr(client, "json", response)
    result = client.tree("1")
    assert [p["page_id"] for p in result["pages"]] == ["1", "2", "3"]
    assert result["pages"][0]["is_leaf"] is False
    assert result["pages"][2]["title_path"] == ["root", "b"]
    with pytest.raises(ConfluenceError, match="数量"):
        client.tree("1", max_pages=2)


def test_tree_can_include_page_content_for_batch_import(monkeypatch):
    client = ConfluenceClient(BASE, "test-user", "test-password")
    root = {"id": "1", "title": "root", "body": {"storage": {"value": "<p>root body</p>"}}}
    child = {"id": "2", "title": "child"}
    monkeypatch.setattr(client, "page", lambda page_id: root if page_id == "1" else {
        **child, "body": {"storage": {"value": "<p>child body</p>"}}})
    monkeypatch.setattr(client, "children", lambda page_id, kind="page": iter([child]) if page_id == "1" and kind == "page" else iter([]))
    result = client.tree("1", include_page_data=True)
    assert [node["page"]["body"]["storage"]["value"] for node in result["pages"]] == [
        "<p>root body</p>", "<p>child body</p>"]
    assert result["pages"][0]["child_pages"] == [child]
    client.close()


def test_failed_children_are_not_reported_as_leaf(monkeypatch):
    client = ConfluenceClient(BASE, "test-user", "test-password")
    monkeypatch.setattr(client, "page", lambda root: {"id": "1", "title": "root"})
    monkeypatch.setattr(client, "json", lambda *a: {})
    with pytest.raises(ConfluenceError, match="results"):
        client.tree("1")


def test_login_html_is_not_accepted(monkeypatch):
    client = ConfluenceClient(BASE, "test-user", "test-password")
    monkeypatch.setattr(client, "request", lambda *args: (b'<html>Log in</html>', 'text/html'))
    with pytest.raises(ConfluenceError, match="JSON"):
        client.page("1")


def test_stage_roundtrip_preserves_source_tables_and_local_images(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_BASE_DIR", str(tmp_path))
    source = page('<h2>配置</h2><table><tr><th>型号</th></tr><tr><td>T1+</td></tr></table>'
                  '<p>拆除<ac:image><ri:attachment ri:filename="a.png"/></ac:image>顶盖</p>')
    image_bytes = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image_bytes, format="PNG")
    class Client:
        base_url = BASE
        def page_id(self, value): return value
        def page(self, value): return source
        def children(self, value, kind):
            if kind == "page": return []
            return [{"id": "10", "title": "a.png", "_links": {"download": "/a.png"}}]
        def request(self, url): return image_bytes.getvalue(), "image/png"
    result = stage_page(Client(), "1", "2")
    path = tmp_path / result["files"][0]["path"]
    from knowledge_parsers.KnowledgeParser import KnowledgeParser
    parser = KnowledgeParser()
    document = parser._parse_html(str(path))
    assert document.title == "拆卸说明"
    assert "型号：T1+" in document.content
    assert len(document.sections) == 1
    assert len(document.image_urls) == 1
    assert (tmp_path / document.image_urls[0]).exists()
    metadata = document.sections[0].metadata
    assert metadata["parent_id"] == "1"
    assert metadata["title_path"] == ["维修手册", "拆卸说明"]
    assert metadata["source_version"] == 3
    assert document.sections[0].plain_text.startswith("页面路径：维修手册 / 拆卸说明")
    assert "<p>拆除" in path.read_text(encoding="utf-8")
    assert "<pre>" not in path.read_text(encoding="utf-8")
    assert not result["warnings"]
    with pytest.raises(ConfluenceError, match="不属于"):
        stage_page(Client(), "99", "2")


def test_hostile_snapshot_text_cannot_close_json_script(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_BASE_DIR", str(tmp_path))
    class Client:
        base_url = BASE
        def page_id(self, value): return value
        def page(self, value): return page('<p>&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;</p>')
        def children(self, *args): return []
    result = stage_page(Client(), "1", "2")
    soup = BeautifulSoup((tmp_path / result["files"][0]["path"]).read_text(encoding="utf-8"), "html.parser")
    assert len(soup.find_all("script")) == 1
    bundle = json.loads(soup.script.get_text())
    assert bundle["blocks"][0]["text"].startswith("</script>")


def test_large_table_sections_repeat_headers_without_losing_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCUMENT_BASE_DIR", str(tmp_path))
    from knowledge_parsers.ConfluenceParser import parse_bundle
    parsed = parse_content(page('<table><tr><th>序号</th><th>型号</th></tr>' + ''.join(
        '<tr><td>%s</td><td>Model%s</td></tr>' % (i, i) for i in range(45)) + '</table>'), BASE)
    payload = {"format": "confluence-source-v1", "title": "型号表", "source": {"page_id": "2"}, **parsed}
    marker = BeautifulSoup('<script type="application/json">' + json.dumps(payload) + '</script>', 'html.parser').script
    document = parse_bundle(marker)
    assert len(document.sections) == 3
    assert '型号：Model0\n' in document.sections[0].plain_text
    assert '型号：Model44' in document.sections[2].plain_text
    assert sum(s.plain_text.count('型号：Model') for s in document.sections) == 45


def test_rendered_macro_fallback_is_used_once():
    source = page('<ac:structured-macro ac:name="include" ac:macro-id="abc"/>')
    source['body']['view'] = {'value': '<div data-macro-id="abc"><p>包含的内容</p></div>'}
    parsed = parse_content(source, BASE)
    assert not parsed['warnings']
    assert sum(b.get('text') == '包含的内容' for b in parsed['blocks']) == 1


def test_attachment_sources_preserve_page_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv('DOCUMENT_BASE_DIR', str(tmp_path))
    class Client:
        base_url = BASE
        def page_id(self, value): return value
        def page(self, value): return page('<p>培训附件</p>')
        def children(self, page_id, kind="attachment"):
            if kind == "page": return []
            return [{'id': '55', 'title': '培训.txt', 'version': {'number': 2}, '_links': {'download': '/text'}}]
        def request(self, *args): return '培训正文'.encode(), 'text/plain'
    result = stage_page(Client(), '1', '2', include_attachments=True)
    attachment = tmp_path / result['files'][1]['path']
    from knowledge_parsers.KnowledgeParser import KnowledgeParser
    parser = KnowledgeParser()
    monkeypatch.setattr(parser, '_parse_document_mindmaps', lambda doc: None)
    document = parser.parse(str(attachment))
    assert document.sections[0].metadata['attachment_id'] == '55'
    assert document.sections[0].metadata['page_id'] == '2'
    assert document.sections[0].metadata['attachment_version'] == 2
    assert document.sections[0].plain_text.startswith("页面路径：维修手册 / 拆卸说明")


def test_confluence_sections_include_page_and_heading_paths_and_directory_index():
    from knowledge_parsers.ConfluenceParser import parse_bundle
    parsed = parse_content(page('<h2>风险清单</h2><p>需要检查针阀</p>'), BASE)
    source = {"page_id": "2", "title_path": ["维修手册", "风险事项"],
              "child_pages": [{"page_id": "3", "title": "注射泵风险事项"}]}
    marker = BeautifulSoup('<script type="application/json">' + json.dumps(
        {"format": "confluence-source-v1", "title": "风险事项", "source": source, **parsed}) + '</script>',
        'html.parser').script
    document = parse_bundle(marker)
    navigation, body = document.sections
    assert navigation.metadata["unit_type"] == "directory_index"
    assert "注射泵风险事项" in navigation.plain_text
    assert "维修手册 / 风险事项 / 风险清单" in body.plain_text
    assert body.metadata["heading_path"] == ["风险清单"]


def test_long_chapter_is_complete_and_vector_split_is_lossless():
    from knowledge_parsers.ConfluenceParser import parse_bundle
    from knowledge_parsers.confluence_chunks import split_text
    text = "测试操作过程。" * 2000 + "最后的关键步骤"
    payload = {"format": "confluence-source-v1", "title": "测试页面",
               "source": {"page_id": "2", "title_path": ["维修", "设备"]},
               "blocks": [{"type": "heading", "level": 2, "text": "执行"},
                          {"type": "paragraph", "text": text}]}
    marker = BeautifulSoup('<script type="application/json">' + json.dumps(payload) + '</script>', 'html.parser').script
    document = parse_bundle(marker)
    assert len(document.sections) == 1
    section = document.sections[0]
    assert section.plain_text.endswith("最后的关键步骤")
    chunks = split_text(section.plain_text, len, max_tokens=600)
    assert "".join(chunks) == section.plain_text
    assert all(len(chunk) <= 600 for chunk in chunks)
    assert section.metadata["chapter_key"]


def test_branch_import_snapshots_user_id_before_rollback(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from routers import confluence
    from services import confluence_refresh
    async def prepare_refresh(db, pages):
        return set(), set(), set(), 0
    monkeypatch.setattr(confluence_refresh, 'prepare_refresh', prepare_refresh)
    user = SimpleNamespace(id=8)
    result = {"files": [{"path": "upload/source_documents/confluence/new/2.html", "name": "页面.html", "size": 10}],
              "image_paths": [], "warnings": [], "content_hash": "b" * 64,
              "source": {"site": BASE, "page_id": "2"}}
    monkeypatch.setattr(confluence, "fetch_branch", lambda request, progress=None: {"root_id": "2", "pages": [result]})
    monkeypatch.setattr(confluence, "discard_staged", lambda *args: None)
    class DB:
        row = None
        async def rollback(self): user.id = None
        async def execute(self, statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self.row))
        def add(self, row): self.row = row; row.id = 17
        async def flush(self): pass
        async def commit(self): pass
    db = DB()
    response = asyncio.run(confluence.import_branch(confluence.ImportRequest(root_url="2"), user, db))
    assert db.row.uploader_id == 8
    assert response.data["page_count"] == 1
    assert response.data["files"][0]["id"] == 17


def test_import_api_requires_admin_and_registers_sources(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace
    from fastapi import HTTPException
    from routers import confluence
    monkeypatch.setattr(confluence, 'has_role', lambda user, role: user.admin)
    with pytest.raises(HTTPException) as error:
        confluence.require_importer(SimpleNamespace(admin=False))
    assert error.value.status_code == 403
    staged = {'files': [{'path': 'upload/source_documents/confluence/new/2.html', 'name': '页面.html', 'size': 100}],
              'source': {'page_id': '2'}, 'warnings': [], 'content_hash': 'a' * 64, 'image_paths': []}
    monkeypatch.setattr(confluence, 'fetch_page', lambda request: staged)
    cleanup = []
    monkeypatch.setattr(confluence, 'discard_staged', lambda files, images: cleanup.extend(files))
    class DB:
        row = None
        async def rollback(self): pass
        async def execute(self, statement):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self.row))
        def add(self, row): self.row = row; row.id = 123
        async def flush(self): pass
        async def commit(self): pass
    db = DB()
    request = confluence.StageRequest(root_id='1', page_id='2')
    result = asyncio.run(confluence.stage(request, SimpleNamespace(id=8), db))
    assert result.data['files'][0]['id'] == 123
    assert db.row.document_library_type == 'knowledge'
    assert db.row.status == 'uploaded'
    second = asyncio.run(confluence.stage(request, SimpleNamespace(id=8), db))
    assert second.data['files'][0]['id'] == 123
    assert len(cleanup) == 1
