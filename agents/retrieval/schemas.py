from typing import Any

from pydantic import BaseModel, Field


class RetrievalResult(BaseModel):
    document_id: str
    library_type: str = "unknown"
    title: str = ""
    content: str = ""
    score: float = 0.0
    base_score: float = 0.0
    rrf_score: float = 0.0
    soft_filter_bonus: float = 0.0
    exact_match_score: float = 0.0
    source: str
    sources: list[str] = Field(default_factory=list)
    rrf_contributions: list[dict[str, Any]] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    matched_exact_terms: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
