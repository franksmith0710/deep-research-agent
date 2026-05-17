"""LangGraph 图构建：15 节点 + intent 4 分支 + 4 HITL 中断点 + assess 回环 + intent 分叉。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from src.agent.state import AgentState
from src.agent.nodes import (
    resolve_context_node,
    intent_classifier_node,
    check_history_node,
    clarify_node,
    planner_node,
    search_node,
    scrape_node,
    context_mgr_node,
    dedup_node,
    rerank_node,
    assess_node,
    synthesize_node,
    report_node,
    memory_node,
    simple_llm_node,
    hitl_scope_node,
    hitl_adjust_node,
    hitl_conflict_node,
    hitl_outline_node,
    route_by_intent,
    route_after_rerank,
    route_assess,
)


def build_graph() -> StateGraph:
    """构建完整的 LangGraph。"""
    builder = StateGraph(AgentState)

    # ── 注册节点（15 + 4 HITL = 19 个） ─────────────────────────────
    builder.add_node("resolve_context", resolve_context_node)
    builder.add_node("intent_classifier", intent_classifier_node)
    builder.add_node("check_history", check_history_node)
    builder.add_node("clarify", clarify_node)
    builder.add_node("planner", planner_node)
    builder.add_node("search", search_node)
    builder.add_node("scrape", scrape_node)
    builder.add_node("context_mgr", context_mgr_node)
    builder.add_node("dedup", dedup_node)
    builder.add_node("rerank", rerank_node)
    builder.add_node("assess", assess_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_node("report", report_node)
    builder.add_node("memory", memory_node)
    builder.add_node("simple_llm", simple_llm_node)
    builder.add_node("hitl_scope", hitl_scope_node)
    builder.add_node("hitl_adjust", hitl_adjust_node)
    builder.add_node("hitl_conflict", hitl_conflict_node)
    builder.add_node("hitl_outline", hitl_outline_node)

    # ── 入口 ──────────────────────────────────────────────────────
    builder.set_entry_point("resolve_context")
    builder.add_edge("resolve_context", "intent_classifier")

    # ── intent 4 分支 ─────────────────────────────────────────────
    builder.add_conditional_edges(
        "intent_classifier",
        route_by_intent,
        {
            "check_history": "check_history",
            "search": "search",
            "simple_llm": "simple_llm",
        },
    )
    builder.add_edge("simple_llm", END)

    # ── deep_research 路径 ────────────────────────────────────────
    builder.add_edge("check_history", "clarify")

    # clarifiy → hitl_scope（如需范围选择）/ planner
    builder.add_conditional_edges(
        "clarify",
        lambda s: "hitl_scope" if s.get("need_scope", False) else "planner",
        {"hitl_scope": "hitl_scope", "planner": "planner"},
    )
    builder.add_edge("hitl_scope", "planner")

    # planner → hitl_adjust（如需方向微调）/ search
    builder.add_conditional_edges(
        "planner",
        lambda s: "hitl_adjust" if s.get("need_adjust", False) else "search",
        {"hitl_adjust": "hitl_adjust", "search": "search"},
    )
    builder.add_edge("hitl_adjust", "search")

    # ── 搜索路径（三条路径共用） ──────────────────────────────────
    builder.add_edge("search", "scrape")
    builder.add_edge("scrape", "context_mgr")
    builder.add_edge("context_mgr", "dedup")
    builder.add_edge("dedup", "rerank")

    # rerank → assess / synthesize / report（由 intent 决定）
    builder.add_conditional_edges(
        "rerank",
        route_after_rerank,
        {
            "assess": "assess",
            "synthesize": "synthesize",
            "report": "report",
        },
    )

    # ── assess 回环（仅 deep_research） ─────────────────────────
    builder.add_conditional_edges(
        "assess",
        route_assess,
        {
            "search": "search",
            "hitl_conflict": "hitl_conflict",
            "synthesize": "synthesize",
        },
    )
    builder.add_edge("hitl_conflict", "synthesize")

    # synthesize → hitl_outline（如需大纲微调）/ report
    builder.add_conditional_edges(
        "synthesize",
        lambda s: "hitl_outline" if s.get("need_outline_review", False) else "report",
        {"hitl_outline": "hitl_outline", "report": "report"},
    )
    builder.add_edge("hitl_outline", "report")

    # ── 报告 → 记忆 → 结束 ──────────────────────────────────────
    builder.add_edge("report", "memory")
    builder.add_edge("memory", END)

    # 编译
    graph = builder.compile(checkpointer=MemorySaver())
    return graph
