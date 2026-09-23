import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from utils.ai_endpoint import get_ai_base_url
from utils.openai_client import (
    create_async_openai_client,
    parse_chat_completion_json,
)


logger = logging.getLogger(__name__)
NCMR_FILENAME_PATTERN = re.compile(
    r"^\(\s*(?P<model>[^()]+?)\s*-\s*TS相关\s*-\s*NCMR\s*报告",
    re.IGNORECASE,
)


def _alias_pattern(alias: str) -> re.Pattern:
    escaped = re.escape(alias.strip())
    # Machine model aliases usually contain letters, digits, '+' and '-'.
    # These boundaries prevent e.g. MGISEQ-200 matching MGISEQ-2000.
    return re.compile(rf"(?<![A-Z0-9+]){escaped}(?![A-Z0-9+])", re.IGNORECASE)


def match_filename_tag_ids(file_name: str, tag_snapshot: list[dict]) -> list[int]:
    stem = Path(str(file_name or "").replace("\\", "/")).stem
    matches = []
    for tag in tag_snapshot:
        aliases = [tag.get("name", ""), *(tag.get("aliases") or [])]
        aliases = sorted({str(value).strip() for value in aliases if str(value).strip()}, key=len, reverse=True)
        if any(_alias_pattern(alias).search(stem) for alias in aliases):
            matches.append(int(tag["id"]))
    if matches:
        return matches

    # NCMR filenames often abbreviate MGISEQ-200/MGISEQ-2000 as 200/2000.
    # Only the leading "(model-TS相关-NCMR报告...)" segment uses this rule.
    ncmr_match = NCMR_FILENAME_PATTERN.search(stem)
    if not ncmr_match:
        return matches
    model = ncmr_match.group("model").strip().casefold()
    for tag in tag_snapshot:
        aliases = [tag.get("name", ""), *(tag.get("aliases") or [])]
        alias_models = {
            str(alias).strip().rsplit("-", 1)[-1].casefold()
            for alias in aliases
            if str(alias).strip()
        }
        if model in alias_models:
            matches.append(int(tag["id"]))
    return matches


def format_ncmr_title(
    file_name: str, current_title: str, tag_values, tag_snapshot: list[dict]
) -> str:
    stem = Path(str(file_name or "").replace("\\", "/")).stem
    ncmr_match = NCMR_FILENAME_PATTERN.search(stem)
    if not ncmr_match:
        return current_title

    tag_by_id = {int(tag["id"]): tag["name"] for tag in tag_snapshot}
    names = []
    for value in tag_values or []:
        try:
            name = tag_by_id.get(int(value))
        except (TypeError, ValueError):
            name = str(value or "").strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return current_title

    topic = str(current_title or "").strip()
    topic = re.sub(r"^NCMR\s*报告\s*[:：-]?\s*", "", topic, flags=re.IGNORECASE).strip()
    filename_model = ncmr_match.group("model").strip()
    full_models = []
    selected_ids = {
        int(value) for value in tag_values or [] if str(value).strip().isdigit()
    }
    for tag in tag_snapshot:
        if int(tag["id"]) not in selected_ids:
            continue
        matching_aliases = [
            str(alias).strip()
            for alias in tag.get("aliases") or []
            if str(alias).strip().rsplit("-", 1)[-1].casefold() == filename_model.casefold()
        ]
        if matching_aliases:
            matching_aliases.sort(key=lambda value: (not value.upper().startswith("MGISEQ-"), len(value)))
            full_models.append(matching_aliases[0])

    display_models = full_models or names
    prefix = f"NCMR 报告：{'、'.join(display_models)}"
    if not topic or topic == stem:
        return prefix
    return f"{prefix} - {topic}"[:255]


def document_text_for_tagging(document: Any, max_chars: int = 24000) -> str:
    parts = []
    for field in (
        "title", "summary", "content", "problem_intro", "causes",
        "evaluation", "inspection", "solutions", "key_points",
    ):
        value = getattr(document, field, None)
        if value:
            parts.append(f"{field}: {value}")
    for section in getattr(document, "sections", None) or []:
        title = getattr(section, "section_title", "") or ""
        text = getattr(section, "plain_text", "") or ""
        if title or text:
            parts.append(f"章节 {title}: {text}")
    return "\n".join(parts)[:max_chars]


def validate_tag_ids(values, tag_snapshot: list[dict]) -> list[int]:
    allowed = {int(tag["id"]) for tag in tag_snapshot}
    result = []
    for value in values or []:
        try:
            tag_id = int(value.get("id") if isinstance(value, dict) else value)
        except (TypeError, ValueError):
            continue
        if tag_id in allowed and tag_id not in result:
            result.append(tag_id)
    return result


async def classify_document_tag_ids(
    document: Any,
    tag_snapshot: list[dict],
    file_name: str = "",
    filename_candidate_ids: list[int] | None = None,
) -> list[int]:
    content = document_text_for_tagging(document)
    if not content.strip() or not tag_snapshot:
        return []

    candidates = [
        {
            "id": tag["id"],
            "name": tag["name"],
            "aliases": tag.get("aliases") or [],
            "description": tag.get("description") or "",
        }
        for tag in tag_snapshot
    ]
    prompt = f"""你正在为设备维修文档识别机器类型标签。
只能选择候选列表中已有的标签，不得创建新标签。别名与标签名称含义相同。
文件名和其中的匹配词只作为候选线索，不能单独决定标签。
只选择文档主要描述或明确适用的机器类型；如果文件名中的词是日期、编号、数量，或正文明确描述其他设备，不要选择该标签。
即使正文没有出现标签词，只要上下文明确表明文档主要描述该设备，也可以选择。
对比、排除、偶然提及的机器不要选择。
明确适用于多种机器时可以选择多个；无法可靠判断时返回空数组。
仅返回 JSON：{{"tag_ids": [标签ID]}}。

文件名：
{file_name}

文件名匹配到的候选标签 ID（仅供参考）：
{json.dumps(filename_candidate_ids or [], ensure_ascii=False)}

候选标签：
{json.dumps(candidates, ensure_ascii=False)}

文档结构化内容：
{content}
"""
    client = None
    try:
        client = create_async_openai_client(
            base_url=get_ai_base_url(),
            api_key=os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY") or "EMPTY",
            timeout=float(os.getenv("AI_REQUEST_TIMEOUT", "300")),
        )
        response = await client.chat.completions.create(
            model=os.getenv("MODEL_AI"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        payload = parse_chat_completion_json(response)
        return validate_tag_ids(payload.get("tag_ids", []), tag_snapshot)
    except Exception:
        logger.exception("automatic tag classification failed")
        return []
    finally:
        if client is not None:
            await client.close()


async def resolve_automatic_tag_ids(
    *, manual_tags, file_name: str, document: Any, tag_snapshot: list[dict]
) -> tuple[list[Any], str]:
    if manual_tags:
        return validate_tag_ids(manual_tags, tag_snapshot), "manual"
    filename_ids = match_filename_tag_ids(file_name, tag_snapshot)
    ai_ids = await classify_document_tag_ids(
        document,
        tag_snapshot,
        file_name=file_name,
        filename_candidate_ids=filename_ids,
    )
    return ai_ids, "content" if ai_ids else "unresolved"
