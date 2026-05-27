"""AgentState 定义 — 15 节点共享的 TypedDict。"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def _merge_sections(
    old: dict[str, str], new: dict[str, str]
) -> dict[str, str]:
    """sections dict 合并 reducer：同名章节追加内容（去重标题），新章节直接添加。"""
    result = dict(old)
    for key, val in new.items():
        if not val:
            continue
        if key in result and result[key]:
            lines = val.split("\n", 1)
            if lines[0].startswith("## "):
                new_content = lines[1].strip() if len(lines) > 1 else ""
            else:
                new_content = val
            if new_content:
                result[key] = result[key] + "\n\n" + new_content
        else:
            result[key] = val
    return result


def _merge_pages(
    old: list[dict], new: list[dict]
) -> list[dict]:
    """scraped_pages 合并 reducer：按 URL 去重追加。"""
    seen = set()
    for p in old:
        u = p.get("url", "")
        if u:
            seen.add(u)
    result = list(old)
    for p in new:
        u = p.get("url", "")
        if u and u not in seen:
            seen.add(u)
            result.append(p)
        elif not u:
            result.append(p)
    return result


class AgentState(TypedDict):
    """LangGraph Agent 全局状态。"""
    
    session_id: str
    task_id: int
    
    # 输入
    query: str
    resolved_query: str
    intent: str  # deep_research / refine_section / new_search_topic / simple_llm
    
    # 对话历史
    messages: Annotated[list[dict[str, str]], add_messages]
    
    # 规划
    scope: dict[str, Any]  # 用户选择的范围
    sub_queries: list[str]  # 子搜索查询
    outline: list[dict[str, Any]]  # 报告大纲
    
    # 搜索结果
    search_results: list[dict[str, Any]]
    scraped_pages: Annotated[list[dict[str, Any]], _merge_pages]
    page_summaries: list[dict[str, Any]]  # 逐页简报列表（含 key_points_json + page_abstract）
    
    # 发现
    findings: list[dict[str, Any]]  # 合并去重后的本轮发现（供 report 使用）
    
    # 报告
    report: str  # 累积报告（用于上下文）
    turn_report: str  # 本轮独立输出内容（写 chat_history）
    sections: Annotated[dict[str, str], _merge_sections]  # 章节名→内容，用于 patch
    
    # 控制
    deep_search_count: int
    assess_round: int  # assess 轮次计数器（用于跨 intent 循环终止）
    refine_section_name: str  # refine 路径指定章节
    
    # HITL 条件标记
    need_scope: bool
    need_adjust: bool
    has_conflict: bool
    conflict_description: str
    coverage_score: int
    
    # 搜索 Fallback 标记
    search_all_failed: bool
    _llm_fallback: bool
    _report_streamed: bool


def make_initial_state(
    session_id: str,
    task_id: int,
    query: str,
) -> AgentState:
    return {
        "session_id": session_id,
        "task_id": task_id,
        "query": query,
        "resolved_query": query,
        "intent": "",
        "messages": [],
        "scope": {},
        "sub_queries": [],
        "outline": [],
        "search_results": [],
        "scraped_pages": [],
        "page_summaries": [],
        "findings": [],
        "report": "",
        "turn_report": "",
        "sections": {},
        "deep_search_count": 0,
        "assess_round": 0,
        "refine_section_name": "",
        "need_scope": False,
        "need_adjust": False,
        "has_conflict": False,
        "conflict_description": "",
        "coverage_score": 0,
        "search_all_failed": False,
        "_llm_fallback": False,
        "_report_streamed": False,
    }
