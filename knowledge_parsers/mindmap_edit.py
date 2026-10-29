"""Validate editable trees and rebuild derived data before persistence/indexing."""
from copy import deepcopy
from datetime import datetime
import math

from knowledge_parsers.mindmap_chunker import chunk_mindmap


def normalize_image_metadata(metadata, image_urls):
    metadata = deepcopy(metadata or {})
    normalize = lambda url: str(url or '').replace('\\', '/').lstrip('/')
    urls = {normalize(url) for url in image_urls}
    results = []
    for result in metadata.get('image_parsing') or []:
        if normalize(result.get('image_url')) not in urls:
            continue
        if result.get('status') not in {'parsed', 'needs_review'} or not result.get('tree'):
            results.append(result)
            continue
        tree = result['tree']
        seen, nodes, edges, depths, lines = set(), [], [], {}, []
        meta = tree.setdefault('_meta', {})
        def visit(node, parent=None, depth=0):
            if depth > 100 or len(nodes) >= 10000:
                raise ValueError('脑图超过 100 层或 10000 个节点，请拆分后保存')
            if not isinstance(node, dict):
                raise ValueError('脑图节点格式无效')
            nid = node.get('id')
            if not isinstance(nid, str) or not nid or nid in seen:
                raise ValueError('脑图节点 ID 为空或重复')
            seen.add(nid)
            text = node.get('text')
            if not isinstance(text, str) or not text.strip():
                raise ValueError('脑图节点文字不能为空')
            node['text'] = text.strip()
            children = node.setdefault('children', [])
            if not isinstance(children, list):
                raise ValueError('脑图子节点必须为列表')
            box = node.get('box')
            if box is not None and (not isinstance(box, list) or len(box) != 4
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in box)
                    or box[0] >= box[2] or box[1] >= box[3]):
                raise ValueError('脑图文字框坐标无效')
            nodes.append(node)
            depths[nid] = depth
            if parent is not None:
                edges.append([parent, nid])
            lines.append('  ' * depth + '- ' + node['text'])
            for child in children:
                visit(child, nid, depth + 1)
        visit(tree.get('root'))
        meta.update(n_nodes=len(nodes), n_edges=len(edges), root_text=tree['root']['text'],
                    root_child_count=len(tree['root']['children']), edited_edges=edges, edited_depth=depths)
        # Recognition diagnostics describe the original image, not a corrected tree.
        # Preserve coordinates and suspect flags until the user explicitly verifies a node.
        result['markdown'] = '\n'.join(lines)
        result['chunks'] = chunk_mindmap(tree, source_file=result.get('image_url', ''))
        if result.get('human_edited'):
            result['edited_at'] = datetime.now().isoformat()
        results.append(result)
    metadata['image_parsing'] = results
    metadata['image_positions'] = [item for item in metadata.get('image_positions') or []
                                   if normalize(item.get('image_url')) in urls]
    return metadata
