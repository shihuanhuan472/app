"""Read-only Confluence Data Center client. Credentials never leave configured origin."""
import os
import re
import threading
import time
from collections import deque
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class ConfluenceError(ValueError):
    pass


class ConfluenceClient:
    _request_lock = threading.Lock()
    _next_request_at = 0.0
    REQUEST_INTERVAL_SECONDS = 2.0

    def __init__(self, base_url=None, username=None, password=None, session=None):
        self.base_url = (base_url or os.getenv("CONFLUENCE_BASE_URL", "")).rstrip("/")
        username = username or ""
        password = password or ""
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
            raise ConfluenceError("请配置 HTTPS CONFLUENCE_BASE_URL（可包含 /confluence 上下文路径）")
        if not username or not password:
            raise ConfluenceError("请填写 Confluence 用户名和密码")
        self.origin = (parsed.scheme, parsed.hostname, parsed.port)
        self.session = session or requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update({"Accept": "application/json"})
        self.session.headers.update({"Accept-Language": "zh-CN,zh;q=0.9"})
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=1, backoff_factor=1, status_forcelist=[502, 503, 504],
            allowed_methods=["GET"], respect_retry_after_header=True)))

    def close(self):
        self.session.close()

    def safe_url(self, value):
        url = urljoin(self.base_url + "/", value)
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.hostname, parsed.port) != self.origin or parsed.username or parsed.password:
            raise ConfluenceError("拒绝向站外地址发送 Confluence 凭据")
        return url

    def page_id(self, value):
        if str(value).isdigit():
            return str(value)
        parsed = urlsplit(self.safe_url(value))
        ids = parse_qs(parsed.query).get("pageId", [])
        match = re.search(r"/pages/(\d+)(?:/|$)", parsed.path)
        page_id = ids[0] if ids else match.group(1) if match else ""
        if not page_id.isdigit():
            raise ConfluenceError("请输入包含 pageId 的页面链接或数字页面 ID")
        return page_id

    def request(self, url, params=None, max_bytes=20 * 1024 * 1024):
        safe_url = self.safe_url(url)
        if getattr(self, 'progress_callback', None):
            # Query parameters may contain signed credentials; show only the path.
            self.progress_callback(url=safe_url.split('?')[0])
        try:
            with self._request_lock:
                wait = self._next_request_at - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                type(self)._next_request_at = time.monotonic() + self.REQUEST_INTERVAL_SECONDS
            with self.session.get(safe_url, params=params, timeout=(10, 60),
                                  allow_redirects=False, stream=True) as response:
                if response.status_code in (403, 429):
                    raise ConfluenceError("Confluence 返回 HTTP %s，已停止采集；请稍后再试并检查账号权限" % response.status_code)
                if response.status_code != 200:
                    raise ConfluenceError("Confluence 请求失败（HTTP %s），请检查权限或登录配置" % response.status_code)
                result = bytearray()
                for chunk in response.iter_content(65536):
                    result.extend(chunk)
                    if len(result) > max_bytes:
                        raise ConfluenceError("Confluence 响应超过大小限制")
                return bytes(result), response.headers.get("Content-Type", "")
        except requests.RequestException:
            raise ConfluenceError("Confluence 网络请求失败，请检查服务器网络和证书") from None

    def json(self, url, params=None):
        import json
        body, mime = self.request(url, params)
        if "json" not in mime.lower():
            raise ConfluenceError("未返回 JSON，可能跳转到了 SSO 登录页")
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except ValueError:
            raise ConfluenceError("Confluence 返回无效 JSON") from None

    def page(self, page_id):
        page_id = self.page_id(page_id)
        if getattr(self, 'progress_callback', None):
            self.progress_callback(page_id=page_id, title='', phase='遍历页面')
        page = self.json(self.base_url + "/rest/api/content/" + page_id,
                         {"expand": "body.storage,body.view,version,ancestors,space"})
        from services.confluence_language import rendered_title, chinese_title
        page['title'] = chinese_title(page.get('title', page_id))
        # The REST title may be English while the language plugin renders Chinese.
        if not re.search(r'[\u4e00-\u9fff]', page['title']):
            failure = '页面未提供可识别的中文标题，可能仅有英文标题'
            try:
                body, mime = self.request(self.base_url + '/pages/viewpage.action',
                                          {'pageId': page_id, 'language': 'zh_CN'})
                if 'html' in mime.lower():
                    page['title'] = rendered_title(body, page['title'])
                else:
                    failure = '页面返回的内容不是 HTML'
            except ConfluenceError as exc:
                failure = str(exc)
            if not re.search(r'[\u4e00-\u9fff]', page['title']):
                page['_title_notices'] = [f"页面 {page_id} 中文标题获取失败：{failure}；暂用原标题 {page['title']}，不影响继续导入。"]
        if getattr(self, 'progress_callback', None):
            self.progress_callback(title=page['title'])
        return page

    def children(self, page_id, kind="page"):
        if kind not in {"page", "attachment"}:
            raise ConfluenceError("不支持的子内容类型")
        url = self.base_url + "/rest/api/content/" + self.page_id(page_id) + "/child/" + kind
        start, seen = 0, set()
        for _ in range(10000):
            data = self.json(url, {"start": start, "limit": 100, "expand": "version"})
            rows = data.get("results")
            if not isinstance(rows, list):
                raise ConfluenceError("子内容响应缺少 results，无法确认树结构完整")
            if not rows:
                if data.get("_links", {}).get("next"):
                    raise ConfluenceError("子内容分页异常")
                return
            for row in rows:
                key = str(row["id"])
                if not key.isdigit():
                    raise ConfluenceError("子内容 ID 无效")
                if key in seen:
                    raise ConfluenceError("子内容分页重复，已停止以避免遗漏")
                seen.add(key)
                yield row
            start += len(rows)
            # An explicit next link or a full server-sized page requires another request.
            if not data.get("_links", {}).get("next") and len(rows) < int(data.get("limit", 100)):
                return
        raise ConfluenceError("子内容分页超过限制")

    def tree(self, root, max_pages=500, include_page_data=False, allow_partial=False):
        root_id = self.page_id(root)
        page = self.page(root_id)
        queue = deque([(page, None, [])])
        seen, nodes, failures = set(), [], []
        while queue:
            node, parent, path = queue.pop()
            key = str(node["id"])
            if key in seen:
                continue
            if len(seen) >= max_pages:
                raise ConfluenceError("页面数量超过限制，请选择更小的根页面")
            seen.add(key)
            body_failed = False
            if include_page_data and "body" not in node:
                try:
                    node = self.page(key)
                except ConfluenceError as exc:
                    if not allow_partial:
                        raise
                    if any(code in str(exc) for code in ('403', '429', '401', '网络请求失败')):
                        failures.append({'page_id': key, 'title': node['title'], 'error': str(exc) + '；本次剩余遍历已暂停', 'scope': 'remaining'})
                        break
                    body_failed = True
                    failures.append({'page_id': key, 'title': node['title'], 'error': str(exc), 'scope': 'page'})
            path = path + [node["title"]]
            try:
                children = list(self.children(key))
            except ConfluenceError as exc:
                if not allow_partial:
                    raise
                if any(code in str(exc) for code in ('403', '429', '401', '网络请求失败')):
                    failures.append({'page_id': key, 'title': node['title'], 'error': str(exc) + '；本次剩余遍历已暂停', 'scope': 'remaining'})
                    break
                failures.append({'page_id': key, 'title': node['title'], 'error': str(exc), 'scope': 'subtree'})
                # Do not publish a snapshot that incorrectly declares this branch empty.
                continue
            page_node = {"page_id": key, "parent_id": parent, "title": node["title"],
                         "title_path": path, "is_leaf": not children}
            if include_page_data:
                page_node["page"] = node
                page_node["child_pages"] = children
            if not body_failed:
                nodes.append(page_node)
            queue.extend((child, key, path) for child in reversed(children))
        if include_page_data:
            titles = {item['page_id']: item['title'] for item in nodes}
            for item in nodes:
                for related in item['page'].get('ancestors', []) + item['child_pages']:
                    related['title'] = titles.get(str(related['id']), related.get('title', ''))
        return {"root_id": root_id, "pages": nodes, "failures": failures}
