"""LangGraph 图构建：12 functional + 1 HITL + intent 3 分支 + assess 回环 + clarify 前置。"""

from __future__ import annotations

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from src.agent.state import AgentState
from src.agent.nodes import (
    resolve_context_node,
    intent_classifier_node,
    clarify_node,
    planner_node,
    search_node,
    scrape_node,
    context_mgr_node,
    dedup_rerank_node,
    assess_node,
    report_node,
    memory_node,
    simple_llm_node,
    hitl_scope_node,

    route_by_intent,
    route_after_rerank,
    route_assess,
)


def build_graph() -> StateGraph:
    """构建完整的 LangGraph。"""
    builder = StateGraph(AgentState)

    # ── 注册节点（12 functional + 1 HITL = 13 个） ─────────────────
    builder.add_node("resolve_context", resolve_context_node)
    builder.add_node("intent_classifier", intent_classifier_node)
    builder.add_node("clarify", clarify_node)
    builder.add_node("planner", planner_node)
    builder.add_node("search", search_node)
    builder.add_node("scrape", scrape_node)
    builder.add_node("context_mgr", context_mgr_node)
    builder.add_node("dedup_rerank", dedup_rerank_node)
    builder.add_node("assess", assess_node)
    builder.add_node("report", report_node)
    builder.add_node("memory", memory_node)
    builder.add_node("simple_llm", simple_llm_node)
    builder.add_node("hitl_scope", hitl_scope_node)

    # ── 入口 ──────────────────────────────────────────────────────
    builder.set_entry_point("resolve_context")
    builder.add_edge("resolve_context", "intent_classifier")

    # ── intent 3 分支 ─────────────────────────────────────────────
    builder.add_conditional_edges(
        "intent_classifier",
        route_by_intent,
        {
            "clarify": "clarify",
            "planner": "planner",
            "simple_llm": "simple_llm",
        },
    )
    builder.add_edge("simple_llm", "memory")

    # ── deep_research / refine_section 路径 ─────────────────────────

    # clarify → hitl_scope（如需范围选择）/ planner
    builder.add_conditional_edges(
        "clarify",
        lambda s: "hitl_scope" if s.get("need_scope", False) else "planner",
        {"hitl_scope": "hitl_scope", "planner": "planner"},
    )
    builder.add_edge("hitl_scope", "planner")
    builder.add_edge("planner", "search")

    # ── 搜索路径共用 ───────────────────────────────────────────────
    builder.add_edge("search", "scrape")
    builder.add_edge("scrape", "context_mgr")
    builder.add_edge("context_mgr", "dedup_rerank")

    # dedup_rerank → assess / synthesize（refine 跳过 assess）
    builder.add_conditional_edges(
        "dedup_rerank",
        route_after_rerank,
        {
            "assess": "assess",
            "report": "report",
        },
    )

    # ── assess 回环（仅 deep_research） ─────────────────────────
    builder.add_conditional_edges(
        "assess",
        route_assess,
        {
            "search": "search",
            "report": "report",
        },
    )

    # ── 报告 → 记忆 → 结束 ──────────────────────────────────────
    builder.add_edge("report", "memory")
    builder.add_edge("memory", END)

    # 编译
    graph = builder.compile(checkpointer=MemorySaver(), name="DeepResearch")
    return graph
