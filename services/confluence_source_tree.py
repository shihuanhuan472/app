"""Build a source-library tree from staged Confluence snapshots."""
import json
from pathlib import Path

from bs4 import BeautifulSoup

from services.knowledge_document_delete import upload_path


def source_metadata(source):
    path = upload_path(source.stored_file_path)
    if not path or not path.is_file():
        return None
    try:
        if path.suffix.lower() == '.html':
            soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            marker = soup.find('script', id='confluence-source-v1')
            if not marker:
                return None
            return json.loads(marker.string or marker.get_text()).get('source')
        sidecar = Path(str(path) + '.source.json')
        if sidecar.is_file():
            return json.loads(sidecar.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return None


def build_source_tree(rows):
    nodes = {}
    order = {}
    def ensure(key, title, parent=None):
        return nodes.setdefault(key, {'key': key, 'title': title, 'parent': parent, 'files': []})
    for source, meta in rows:
        if not meta or meta.get('source_type') != 'confluence' or not meta.get('page_id'):
            continue
        site = str(meta.get('site') or 'Confluence')
        root = json.dumps([site])
        ensure(root, site)
        path = list(meta.get('title_path') or [source.get('origin_file_name') or '未命名页面'])
        ancestors = [str(i) for i in meta.get('ancestor_ids') or []]
        parent = root
        for index, title in enumerate(path[:-1]):
            key = json.dumps([site, ancestors[index]]) if index < len(ancestors) else json.dumps([site, 'path', path[:index + 1]])
            ensure(key, title, parent)
            parent = key
        page = json.dumps([site, str(meta['page_id'])])
        ensure(page, path[-1], parent)
        if not meta.get('attachment_id'):
            order[page] = {json.dumps([site, str(child['page_id'])]): index
                           for index, child in enumerate(meta.get('child_pages') or [])}
            target = page
        else:
            target = json.dumps([site, str(meta['page_id']), 'attachments'])
            ensure(target, '附件', page)
        nodes[target]['files'].append(source)
    def key(node):
        sibling_order = order.get(node['parent'], {})
        return (node['parent'] or '', sibling_order.get(node['key'], len(sibling_order)), node['title'].casefold())
    return sorted(nodes.values(), key=key)
