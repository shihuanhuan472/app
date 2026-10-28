"""Recognize corpus counts separately from questions about document contents."""
import re


def is_statistics_question(question: str) -> bool:
    text = str(question or "")
    corpus = re.search(r"文档|知识库|故障库|资料库|源文件|源文档", text)
    quantity = re.search(
        r"多少[篇份个]|几[篇份个]|数量|总数|总量|统计|概况|概览|"
        r"(?:文档|知识库|故障库|资料库|源文件|源文档)(?:里|中|的|当前|现在|目前|总共|一共|总计|共有|有|是|为)*多少(?:[？?。！!\s]|$)",
        text,
    )
    return bool(corpus and quantity)


def statistics_scope(question: str) -> str:
    text = str(question or "")
    if re.search(r"全系统|所有资料|全部资料|两个库|两库|知识库和故障库|故障库和知识库", text):
        return "all"
    knowledge = "知识库" in text
    breakdown = "故障库" in text
    if knowledge and not breakdown:
        return "knowledge"
    if breakdown and not knowledge:
        return "breakdown"
    return "all"


def has_unsupported_statistics_filter(question: str) -> bool:
    # Unknown words must never silently turn a filtered question into a global count.
    text = re.sub(r"[\s，。？！、：；,.?!:;（）()]+", "", str(question or ""))
    words = (
        "知识库和故障库", "故障库和知识库", "各个分类", "按分类", "分类统计", "分类数量",
        "未完成索引", "未向量化", "已向量化", "未索引", "已索引", "索引状态",
        "待审核", "源文档", "源文件", "正式文档", "文档", "知识库", "故障库", "资料库",
        "全系统", "系统", "所有资料", "全部资料", "资料", "两个库", "两库",
        "当前", "现在", "目前", "总共", "一共", "总计", "共有", "总数", "总量", "数量",
        "多少", "几篇", "几份", "几个", "统计", "概况", "概览", "分类", "各类", "分别",
        "帮我", "请问", "告诉我", "查询", "查看", "查一下", "看一下", "一下", "请",
        "有", "的", "里", "中", "篇", "份", "个", "是", "为", "吗", "呢", "和", "及", "与",
    )
    for word in words:
        text = text.replace(word, "")
    return bool(text)
