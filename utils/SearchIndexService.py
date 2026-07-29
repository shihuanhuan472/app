import asyncio
import base64
import json
import os
import ssl
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib import error as urlerror
from urllib import request as urlrequest

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

SEARCH_BACKENDS = {"opensearch", "elasticsearch"}


class _SearchResponse:
    def __init__(self, status_code: int, body: bytes = b""):
        self.status_code = status_code
        self.body = body or b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Dict[str, Any]:
        if not self.body:
            return {}
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"search index HTTP {self.status_code}: {self.text[:500]}")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_library_type(library_type: str) -> str:
    return "knowledge" if str(library_type or "").strip().lower() == "knowledge" else "breakdown"


def _datetime_to_iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value:
        return str(value)
    return None


def _text_or_empty(value: Any) -> str:
    return str(value or "").strip()


def _tag_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item).strip() for item in value if str(item or "").strip())
    if isinstance(value, dict):
        return " ".join(str(item).strip() for item in value.values() if str(item or "").strip())
    return str(value or "").strip()


def _first_image_url(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if isinstance(value, str):
        return value.split(",")[0].strip() if value.strip() else ""
    return ""


def _field(document: Any, name: str, default: Any = None) -> Any:
    if isinstance(document, dict):
        return document.get(name, default)
    return getattr(document, name, default)


def _breakdown_content(document: Any) -> str:
    parts = [
        _text_or_empty(_field(document, "problem_intro")),
        _text_or_empty(_field(document, "causes")),
        _text_or_empty(_field(document, "evaluation")),
        _text_or_empty(_field(document, "inspection")),
        _text_or_empty(_field(document, "solutions")),
        _text_or_empty(_field(document, "key_points")),
    ]
    return "\n".join(part for part in parts if part).strip()


class SearchIndexService:
    def __init__(self):
        backend = os.getenv("LEXICAL_SEARCH_BACKEND", os.getenv("SEARCH_INDEX_BACKEND", "opensearch"))
        self.backend = str(backend or "opensearch").strip().lower()
        self.enabled = self.backend in SEARCH_BACKENDS

        self.base_url = os.getenv("SEARCH_INDEX_URL", "http://localhost:9200").rstrip("/")
        self.index_name = os.getenv("SEARCH_INDEX_NAME", "maintenance_documents")
        self.username = os.getenv("SEARCH_INDEX_USERNAME", "").strip()
        self.password = os.getenv("SEARCH_INDEX_PASSWORD", "").strip()
        self.verify_certs = _env_bool("SEARCH_INDEX_VERIFY_CERTS", False)
        self.timeout = float(os.getenv("SEARCH_INDEX_TIMEOUT", 10))
        self.required = _env_bool("SEARCH_INDEX_REQUIRED", False)
        self.text_analyzer = os.getenv("SEARCH_INDEX_TEXT_ANALYZER", "standard").strip() or "standard"
        self._index_checked = False

    def _auth(self):
        if self.username:
            return self.username, self.password
        return None

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _text_field_mapping(self) -> Dict[str, Any]:
        return {
            "type": "text",
            "analyzer": self.text_analyzer,
            "fields": {
                "raw": {
                    "type": "keyword",
                    "ignore_above": 512,
                }
            },
        }

    def _index_body(self) -> Dict[str, Any]:
        return {
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "doc_id": {"type": "integer"},
                    "library_type": {"type": "keyword"},
                    "content_type": {"type": "keyword"},
                    "title": self._text_field_mapping(),
                    "section_title": self._text_field_mapping(),
                    "tag_text": self._text_field_mapping(),
                    "content": self._text_field_mapping(),
                    "section_id": {"type": "integer"},
                    "section_index": {"type": "integer"},
                    "section_type": {"type": "keyword"},
                    "image_url": {"type": "keyword"},
                    "is_deleted": {"type": "boolean"},
                    "updated_at": {"type": "date"},
                }
            }
        }

    def _request_sync(self, method: str, path: str, **kwargs) -> _SearchResponse:
        headers = dict(kwargs.pop("headers", {}) or {})
        json_body = kwargs.pop("json", None)
        content = kwargs.pop("content", None)
        body = None

        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif content is not None:
            body = content.encode("utf-8") if isinstance(content, str) else content

        auth = self._auth()
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"

        request = urlrequest.Request(
            self._url(path),
            data=body,
            headers=headers,
            method=method.upper(),
        )
        context = None if self.verify_certs else ssl._create_unverified_context()
        try:
            with urlrequest.urlopen(request, timeout=self.timeout, context=context) as response:
                return _SearchResponse(response.getcode(), response.read())
        except urlerror.HTTPError as error:
            return _SearchResponse(error.code, error.read())

    async def _request(self, method: str, path: str, **kwargs) -> _SearchResponse:
        return await asyncio.to_thread(self._request_sync, method, path, **kwargs)

    async def ensure_index(self) -> bool:
        if not self.enabled:
            return False
        if self._index_checked:
            return True

        try:
            response = await self._request("HEAD", self.index_name)
            if response.status_code == 200:
                self._index_checked = True
                return True
            if response.status_code not in {404}:
                response.raise_for_status()

            create_response = await self._request("PUT", self.index_name, json=self._index_body())
            if create_response.status_code not in {200, 201}:
                create_response.raise_for_status()
            self._index_checked = True
            return True
        except Exception as error:
            if self.required:
                raise
            print(f"[search-index] ensure index failed: {error}")
            return False

    def build_chunks(self, document: Any, sections: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        library_type = _normalize_library_type(_field(document, "library_type", "breakdown"))
        doc_id = int(_field(document, "id"))
        title = _text_or_empty(_field(document, "title"))
        tag_text = _tag_text(_field(document, "tag"))
        updated_at = _datetime_to_iso(_field(document, "vector_update_time") or _field(document, "first_edit_date"))

        if library_type == "knowledge":
            chunks: List[Dict[str, Any]] = []
            for section in sections or _field(document, "knowledge_sections", []) or []:
                section_id = _field(section, "id")
                section_title = _text_or_empty(_field(section, "section_title"))
                section_text = _text_or_empty(_field(section, "plain_text"))
                section_index = _field(section, "section_index", 0) or 0
                chunk_id = f"knowledge-{doc_id}-section-{section_id or section_index}"
                chunks.append({
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "library_type": "knowledge",
                    "content_type": "search_section",
                    "title": title,
                    "section_title": section_title,
                    "tag_text": tag_text,
                    "content": section_text,
                    "section_id": section_id,
                    "section_index": section_index,
                    "section_type": str(_field(section, "section_type", "") or ""),
                    "image_url": _first_image_url(_field(section, "image_urls", [])),
                    "is_deleted": False,
                    "updated_at": updated_at,
                })
            if chunks:
                return chunks

            return [{
                "chunk_id": f"knowledge-{doc_id}-main",
                "doc_id": doc_id,
                "library_type": "knowledge",
                "content_type": "search_document",
                "title": title,
                "section_title": "",
                "tag_text": tag_text,
                "content": title,
                "section_id": None,
                "section_index": 0,
                "section_type": "",
                "image_url": _first_image_url(_field(document, "image_urls", [])),
                "is_deleted": False,
                "updated_at": updated_at,
            }]

        content = _breakdown_content(document)
        return [{
            "chunk_id": f"breakdown-{doc_id}-main",
            "doc_id": doc_id,
            "library_type": "breakdown",
            "content_type": "search_document",
            "title": title,
            "section_title": "",
            "tag_text": tag_text,
            "content": content,
            "section_id": None,
            "section_index": 0,
            "section_type": "",
            "image_url": _first_image_url(_field(document, "image_urls", "")),
            "is_deleted": False,
            "updated_at": updated_at,
        }]

    async def index_document(self, document: Any, sections: Optional[List[Any]] = None) -> bool:
        if not self.enabled:
            return False
        if not await self.ensure_index():
            return False

        chunks = self.build_chunks(document, sections)
        if not chunks:
            return False

        await self.delete_document(chunks[0]["doc_id"], chunks[0]["library_type"])

        lines: List[str] = []
        for chunk in chunks:
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": chunk["chunk_id"]}}, ensure_ascii=False))
            lines.append(json.dumps(chunk, ensure_ascii=False))
        body = "\n".join(lines) + "\n"

        try:
            response = await self._request(
                "POST",
                "_bulk",
                content=body,
                headers={"Content-Type": "application/x-ndjson"},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("errors"):
                raise RuntimeError(f"bulk index returned errors: {data.get('items', [])[:3]}")
            return True
        except Exception as error:
            if self.required:
                raise
            print(f"[search-index] index document failed: {error}")
            return False

    async def delete_document(self, doc_id: int, library_type: str = "breakdown") -> bool:
        if not self.enabled:
            return False
        if not await self.ensure_index():
            return False

        body = {
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"doc_id": int(doc_id)}},
                        {"term": {"library_type": _normalize_library_type(library_type)}},
                    ]
                }
            }
        }
        try:
            response = await self._request("POST", f"{self.index_name}/_delete_by_query", json=body)
            response.raise_for_status()
            return True
        except Exception as error:
            if self.required:
                raise
            print(f"[search-index] delete document failed: {error}")
            return False

    def _search_query(self, query: str) -> Dict[str, Any]:
        should: List[Dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^5",
                        "section_title^4",
                        "tag_text^3",
                        "content",
                    ],
                    "type": "best_fields",
                }
            },
            {"match_phrase": {"title": {"query": query, "boost": 3}}},
            {"match_phrase": {"section_title": {"query": query, "boost": 3}}},
            {"match_phrase": {"content": {"query": query, "boost": 1.5}}},
        ]
        return {
            "query": {
                "bool": {
                    "filter": [{"term": {"is_deleted": False}}],
                    "should": should,
                    "minimum_should_match": 1,
                }
            },
            "_source": [
                "chunk_id",
                "doc_id",
                "library_type",
                "content_type",
                "title",
                "section_title",
                "content",
                "image_url",
                "section_id",
                "section_index",
                "section_type",
            ],
        }

    async def search(
        self,
        query: str,
        limit: int,
        seed_score: float,
        score_span: float,
        vector_bonus_max: float,
    ) -> Optional[List[Dict[str, Any]]]:
        if not self.enabled or not str(query or "").strip():
            return None
        if not await self.ensure_index():
            return None

        body = self._search_query(query)
        body["size"] = max(int(limit or 1), 1)

        try:
            response = await self._request("POST", f"{self.index_name}/_search", json=body)
            response.raise_for_status()
            data = response.json()
            hits = data.get("hits", {}).get("hits", []) or []
            if not hits:
                return []

            max_score = max(float(hit.get("_score") or 0.0) for hit in hits) or 1.0
            results: List[Dict[str, Any]] = []
            for hit in hits:
                source = hit.get("_source") or {}
                raw_score = float(hit.get("_score") or 0.0)
                normalized_score = raw_score / max_score if max_score > 0 else 0.0
                score = min(1.0, seed_score + normalized_score * score_span)
                metadata = {
                    "content_type": source.get("content_type") or "search_index",
                    "retrieval_source": "lexical",
                    "lexical_backend": self.backend,
                    "lexical_signals": ["search_index", "search_engine_bm25"],
                    "section_id": source.get("section_id"),
                    "section_title": source.get("section_title"),
                    "section_index": source.get("section_index"),
                    "section_type": source.get("section_type"),
                }
                results.append({
                    "doc_id": source.get("doc_id"),
                    "library_type": source.get("library_type") or "breakdown",
                    "title": source.get("title") or "",
                    "content": "\n".join(
                        part for part in [source.get("section_title"), source.get("content")] if part
                    ).strip(),
                    "image_url": source.get("image_url") or "",
                    "score": score,
                    "vector_score": 0.0,
                    "term_bonus": min(vector_bonus_max, normalized_score * score_span),
                    "bm25_score": raw_score,
                    "bm25_normalized_score": normalized_score,
                    "matched_terms": [],
                    "metadata": metadata,
                })
            return results
        except Exception as error:
            if self.required:
                raise
            print(f"[search-index] search failed: {error}")
            return None
