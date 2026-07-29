import json
from types import SimpleNamespace

from agents.intent.schemas import RouteDecision
from agents.intent.taxonomy import IntentRoute
from routers.message import (
    _compare_trace_pair,
    _filter_reference_documents_by_query_terms,
    _last_reference_documents_from_history,
    _rewrite_query_heuristically,
    _select_answer_audit_traces,
    _trace_doc_ids,
    _trace_images,
)


def _message(role: int, text: str):
    return SimpleNamespace(role=role, content_text=text)


def _trace(
    retrieval_query: str,
    docs,
    images,
    actions=None,
    route: str = "knowledge_search",
):
    return SimpleNamespace(
        route=route,
        reason="knowledge_signal",
        original_question=retrieval_query,
        query_rewrite=None,
        retrieval_query=retrieval_query,
        reference_docs_json=json.dumps(docs, ensure_ascii=False),
        reference_images_json=json.dumps(images, ensure_ascii=False),
        actions_json=json.dumps(actions or ["rag_search"], ensure_ascii=False),
        used_previous_refs=0,
    )


def test_contextual_followup_query_rewrite_uses_previous_user_question():
    history = [
        _message(1, "注射泵测序过程中报注射器运行不到指定位置？"),
        _message(0, "根据知识文档，可以先检查注射器固定螺丝。"),
    ]

    rewritten = _rewrite_query_heuristically(
        "上面我提到的相关问题的解决方案你再给我说一下",
        history,
    )

    assert rewritten == "注射泵测序过程中报注射器运行不到指定位置的解决方案"


def test_contextual_reference_filter_removes_unrelated_documents():
    decision = RouteDecision(
        route=IntentRoute.KNOWLEDGE_SEARCH,
        use_rag=True,
        confidence=0.96,
        reason="contextual_followup",
        query_rewrite="上面我提到的相关问题的解决方案你再给我说一下",
    )
    docs = [
        {
            "doc_id": 1,
            "title": "拍照丢图问题解决方案",
            "score": 0.72,
            "chunks": [{"content": "重新建立CXP链接，优化走线布局。"}],
        },
        {
            "doc_id": 2,
            "title": "注射泵测序故障处理",
            "score": 0.69,
            "chunks": [{"content": "注射器运行不到指定位置时，检查注射器固定螺丝。"}],
        },
    ]

    filtered = _filter_reference_documents_by_query_terms(
        docs,
        "注射泵测序过程中报注射器运行不到指定位置的解决方案",
        decision,
    )

    assert [doc["doc_id"] for doc in filtered] == [2]


def test_previous_reference_documents_keep_saved_images():
    history = [
        _message(1, "注射泵测序过程中报注射器运行不到指定位置？"),
        SimpleNamespace(
            role=0,
            content_text="可参考下方图片。",
            ai_reference_doc_ids=json.dumps([
                {
                    "doc_id": 9,
                    "library_type": "knowledge",
                    "title": "注射泵运行不到指定位置",
                    "score": 0.88,
                    "chunks": [{"preview": "检查注射器固定螺丝。"}],
                    "image_urls": ["/upload/images/injection-pump.png"],
                }
            ], ensure_ascii=False),
        ),
    ]

    reference_docs = _last_reference_documents_from_history(history)

    assert reference_docs[0]["doc_id"] == 9
    assert reference_docs[0]["reused_from_history"] is True
    assert reference_docs[0]["chunks"][0]["content"] == "检查注射器固定螺丝。"
    assert reference_docs[0]["evidence_image_urls"] == ["/upload/images/injection-pump.png"]


def test_trace_comparison_detects_query_doc_and_image_changes():
    first = _trace(
        "注射泵测序过程中报注射器运行不到指定位置",
        [{"doc_id": 1, "library_type": "knowledge", "title": "注射泵故障", "score": 0.9}],
        ["/upload/images/pump-a.png"],
    )
    second = _trace(
        "拍照丢图问题解决方案",
        [{"doc_id": 2, "library_type": "knowledge", "title": "拍照丢图", "score": 0.85}],
        ["/upload/images/camera-b.png"],
    )

    comparison = _compare_trace_pair(first, second)

    assert _trace_doc_ids(first) == ["knowledge:1"]
    assert _trace_images(second) == ["/upload/images/camera-b.png"]
    assert comparison["query_changed"] is True
    assert comparison["docs_changed"] is True
    assert comparison["images_changed"] is True


def test_answer_audit_filters_out_casual_traces():
    knowledge_trace = _trace(
        "注射泵测序过程中报注射器运行不到指定位置",
        [{"doc_id": 1, "library_type": "knowledge", "title": "Injection pump", "score": 0.9}],
        ["/upload/images/pump-a.png"],
        route="knowledge_search",
    )
    casual_trace = _trace(
        "你好",
        [],
        [],
        actions=["direct_generate_answer"],
        route="casual_chat",
    )

    selected = _select_answer_audit_traces(
        [knowledge_trace, casual_trace],
        {
            "active_query": "注射泵测序过程中报注射器运行不到指定位置",
            "active_reference_docs": [{"doc_id": 1, "library_type": "knowledge"}],
        },
    )

    assert selected == [knowledge_trace]


def obsolete_answer_confidence_response_uses_last_related_trace():
    trace = _trace(
        "注射泵测序过程中报注射器运行不到指定位置",
        [{"doc_id": 1, "library_type": "knowledge", "title": "Injection pump", "score": 0.9}],
        ["/upload/images/pump-a.png"],
        route="knowledge_search",
    )
    memory_pack = SimpleNamespace(
        recent_traces=[trace],
        active_context={
            "active_query": "注射泵测序过程中报注射器运行不到指定位置",
            "active_reference_docs": [{"doc_id": 1, "library_type": "knowledge"}],
        },
        to_trace_validation=lambda: {},
    )
    message = SimpleNamespace(session_id=1)

    answer, actions, validation = __import__("asyncio").run(
        _build_answer_confidence_response(None, message, memory_pack=memory_pack)
    )

    assert "不能说绝对确定" in answer
    assert "知识库依据" in answer
    assert "两轮" not in answer
    assert validation["mode"] == "answer_confidence_check"
