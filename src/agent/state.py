"""AgentState 定义 — 15 节点共享的 TypedDict。"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


def _merge_sections(
    old: dict[str, str], new: dict[str, str]
) -> dict[str, str]:
    """sections dict 合并 reducer：新章节覆盖旧章节，旧章节保留。"""
    return {**old, **new}


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
    scraped_pages: list[dict[str, Any]]
    page_summaries: list[dict[str, Any]]  # L0 摘要列表
    
    # 发现
    findings: list[dict[str, Any]]  # 全部 L1 发现
    
    # 报告
    report: str
    sections: Annotated[dict[str, str], _merge_sections]  # 章节名→内容，用于 patch
    
    # 控制
    deep_search_count: int
    assess_round: int  # assess 轮次计数器（用于跨 intent 循环终止）
    refine_section_name: str  # refine 路径指定章节
    
    # HITL 条件标记
    need_scope: bool
    _clarify_done: bool
    need_adjust: bool
    has_conflict: bool
    conflict_description: str
    sufficient: bool
    coverage_score: int
    need_outline_review: bool
    hitl_result: dict[str, Any]  # 用户 HITL 输入
    
    # 状态
    status: str  # pending / running / hitl_waiting / completed / error
    error: Optional[str]
    chain_events: list[dict[str, Any]]  # 累积 chain 事件


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
        "sections": {},
        "deep_search_count": 0,
        "assess_round": 0,
        "refine_section_name": "",
        "need_scope": False,
        "_clarify_done": False,
        "need_adjust": False,
        "has_conflict": False,
        "conflict_description": "",
        "sufficient": True,
        "coverage_score": 0,
        "need_outline_review": False,
        "hitl_result": {},
        "status": "pending",
        "error": None,
        "chain_events": [],
    }
