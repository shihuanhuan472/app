import json
from typing import Any

from ..query_analysis.schemas import RetrievalMode, RetrievalQueries, RetrievalStrategy
from ..query_analysis.schemas import QueryAnalysis
from .observer import ObservationDecision
from .schemas import RetrievalResult


class QueryRefiner:
    """Build one bounded follow-up retrieval plan from an observation."""

    def __init__(self, *, verbose: bool = True) -> None:
        self.verbose = verbose

    def refine(
        self,
        analysis: QueryAnalysis,
        observation: ObservationDecision,
        results: list[RetrievalResult],
    ) -> RetrievalStrategy:
        self._print("input", {
            "next_action": observation.next_action,
            "reason": observation.reason,
            "missing_aspects": observation.missing_aspects,
            "result_count": len(results),
        })
        old = analysis.retrieval_strategy
        primary = list(dict.fromkeys(old.primary_terms))
        symptoms = list(analysis.understanding.symptoms)
        requested = list(analysis.understanding.requested_information)
        entity = " ".join(primary or symptoms[:1]).strip() or analysis.original_question
        missing_text = " ".join(observation.missing_aspects).lower()
        target_terms = []
        for label, terms in (("报警代码", ("报警", "错误代码", "报错代码")), ("日志分析", ("日志", "log")), ("软件异常", ("软件", "程序", "崩溃")), ("通信异常", ("通信", "通讯", "连接")), ("硬件故障 软件故障 区分", ("硬件", "软件", "区分"))):
            if any(term in missing_text for term in terms):
                target_terms.append(label)
        target_terms.extend(requested[:3])
        target_terms = list(dict.fromkeys(target_terms)) or ["故障原因", "排查步骤", "处理措施"]

        broad_keyword = list(primary) or [entity]
        broad_semantic = [entity, f"{entity} {' '.join(symptoms)}".strip()]
        precise_keyword = []
        precise_semantic = []
        for target in target_terms[:5]:
            precise_keyword.append(f"{entity} {target}")
            precise_semantic.append(f"{entity} {target}")
        if symptoms:
            precise_semantic.append(f"{entity} {' '.join(symptoms)}")

        mode = RetrievalMode.HYBRID
        if not broad_keyword:
            mode = RetrievalMode.SEMANTIC
        strategy = RetrievalStrategy(
            mode=mode,
            reason=f"第1轮结果需要{observation.next_action}，根据缺失信息进行一次查询修正",
            broad_queries=RetrievalQueries(
                keyword=broad_keyword[:3], semantic=broad_semantic[:3]
            ),
            precise_queries=RetrievalQueries(
                keyword=precise_keyword[:3], semantic=precise_semantic[:3]
            ),
            primary_terms=primary,
            hard_filters=old.hard_filters,
            soft_filters=old.soft_filters,
        )
        self._print("output", strategy.model_dump(mode="json"))
        return strategy

    def _print(self, stage: str, payload: dict[str, Any]) -> None:
        if self.verbose:
            print(f"[QueryRefiner][{stage}] {json.dumps(payload, ensure_ascii=False, default=str)}")
