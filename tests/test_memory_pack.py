from types import SimpleNamespace

from agents.intent.schemas import RouteDecision
from agents.intent.taxonomy import IntentRoute
from agents.memory.pack_builder import MemoryPackBuilder
from agents.memory.service import normalize_stored_reference_doc
from routers.message import _merge_reference_documents


def _message(text: str, images: str = ""):
    return SimpleNamespace(content_text=text, user_uploaded_images=images)


def _decision(route=IntentRoute.KNOWLEDGE_SEARCH, use_rag=True, reason="knowledge_signal"):
    return RouteDecision(
        route=route,
        use_rag=use_rag,
        confidence=0.9,
        reason=reason,
    )


def test_simple_knowledge_question_uses_smaller_rag(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_RAG_SIMPLE_TOP_K", "5")
    monkeypatch.setenv("ADAPTIVE_RAG_SIMPLE_TOP_K_DOCUMENTS", "1")

    plan = MemoryPackBuilder(None)._build_adaptive_rag_plan(
        _message("Q30是什么"),
        _decision(),
        active_context=None,
    )

    assert plan.strategy == "rag_once"
    assert plan.complexity == "simple_knowledge"
    assert plan.top_k == 5
    assert plan.top_k_documents == 1
    assert plan.iterative_retrieval is False


def test_complex_question_uses_react_rag():
    plan = MemoryPackBuilder(None)._build_adaptive_rag_plan(
        _message("请结合日志和图片完整分析根因，并给出解决方案"),
        _decision(),
        active_context={"active_issue": "注射泵运行不到指定位置"},
    )

    assert plan.strategy == "react_rag"
    assert plan.complexity == "complex"
    assert plan.iterative_retrieval is True


def test_trace_style_reference_doc_recovers_chunk_previews_and_images():
    normalized = normalize_stored_reference_doc({
        "doc_id": 3,
        "library_type": "knowledge",
        "title": "Injection pump issue",
        "score": 0.82,
        "chunk_previews": ["Check the injector fixing screw."],
        "reference_images": ["upload/images/pump.png"],
    })

    assert normalized["doc_id"] == 3
    assert normalized["chunks"][0]["content"] == "Check the injector fixing screw."
    assert normalized["evidence_image_urls"] == ["upload/images/pump.png"]


def test_merge_reference_documents_deduplicates_docs_chunks_and_images():
    first = [{
        "doc_id": 1,
        "library_type": "knowledge",
        "score": 0.7,
        "chunks": [{"content": "first chunk"}],
        "image_urls": ["upload/images/a.png"],
    }]
    second = [{
        "doc_id": 1,
        "library_type": "knowledge",
        "score": 0.9,
        "chunks": [{"content": "first chunk"}, {"content": "second chunk"}],
        "image_urls": ["upload/images/b.png"],
    }]

    merged = _merge_reference_documents(first, second)

    assert len(merged) == 1
    assert merged[0]["score"] == 0.9
    assert [chunk["content"] for chunk in merged[0]["chunks"]] == ["first chunk", "second chunk"]
    assert merged[0]["image_urls"] == ["upload/images/a.png", "upload/images/b.png"]
