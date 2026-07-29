from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class AgentSkill:
    """A project-level business skill backed by a reusable prompt template."""

    name: str
    description: str
    prompt_file: str
    use_rag_context: bool = False
    include_memory: bool = False
    actions: List[str] = field(default_factory=list)
    version: str = "v1"

    @property
    def prompt_path(self) -> Path:
        return PROMPT_DIR / self.prompt_file

    @property
    def prompt_template(self) -> str:
        return _load_prompt(str(self.prompt_path))

    def to_trace_validation(self) -> Dict[str, str]:
        return {
            "skill_name": self.name,
            "skill_version": self.version,
            "skill_prompt_file": self.prompt_file,
        }


@lru_cache(maxsize=32)
def _load_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()
