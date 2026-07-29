from typing import Optional

from agents.memory import MemoryPack

from .registry import SkillRegistry
from .schemas import AgentSkill


class SkillPromptBuilder:
    """Composes the final model prompt from a selected business skill."""

    def __init__(self, registry: Optional[SkillRegistry] = None):
        self.registry = registry or SkillRegistry()

    def build_completion_prompt(
        self,
        question: str,
        skill: Optional[AgentSkill] = None,
        memory_pack: Optional[MemoryPack] = None,
        memory_prompt: str = "",
        rag_prompt: str = "",
        retrieval_query: Optional[str] = None,
    ) -> str:
        skill = skill or self.registry.select_for_memory(memory_pack)
        question = str(question or "").strip()
        retrieval_query = str(retrieval_query or "").strip()

        blocks = [self._skill_block(skill)]
        if skill.include_memory and memory_prompt:
            blocks.append(f"【短期记忆】\n{memory_prompt.strip()}")
        if skill.use_rag_context:
            blocks.append(self._rag_block(rag_prompt))

        user_lines = [f"用户原问题：{question or '无'}"]
        if retrieval_query and retrieval_query != question:
            user_lines.append(f"结合上下文后的检索问题：{retrieval_query}")
        blocks.append("【当前输入】\n" + "\n".join(user_lines))
        blocks.append("请按照业务 Skill 的要求生成最终回复。")
        return "\n\n".join(block for block in blocks if block).strip()

    def build_audit_prompt(
        self,
        question: str,
        audit_context: str,
        skill: Optional[AgentSkill] = None,
        memory_pack: Optional[MemoryPack] = None,
        memory_prompt: str = "",
    ) -> str:
        skill = skill or self.registry.select_for_memory(memory_pack)
        blocks = [self._skill_block(skill)]
        if skill.include_memory and memory_prompt:
            blocks.append(f"【短期记忆】\n{memory_prompt.strip()}")
        blocks.append(f"【审计事实】\n{str(audit_context or '').strip() or '没有可用审计事实。'}")
        blocks.append(f"【用户追问】\n{str(question or '').strip() or '无'}")
        blocks.append("请基于审计事实反思并生成面向用户的回复。")
        return "\n\n".join(block for block in blocks if block).strip()

    def _skill_block(self, skill: AgentSkill) -> str:
        return (
            f"【业务 Skill：{skill.name}】\n"
            f"说明：{skill.description}\n\n"
            f"{skill.prompt_template}"
        )

    @staticmethod
    def _rag_block(rag_prompt: str) -> str:
        rag_prompt = str(rag_prompt or "").strip()
        if rag_prompt:
            return f"【知识库检索结果】\n{rag_prompt}"
        return "【知识库检索结果】\n本轮没有检索到可用知识文档。"
