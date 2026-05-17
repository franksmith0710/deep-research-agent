"""LangGraph 节点：15 个节点 + intent 4 分支逻辑 + 4 HITL 中断节点。"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from langgraph.types import interrupt

from src.agent.state import AgentState
from src.llm.client import chat, chat_json
from src.planner.planner import resolve_query, need_clarification, generate_sub_queries
from src.search.duckduckgo import DuckDuckGoSearch
from src.search.scraper import Scraper
from src.extract.extractor import extract_from_content
from src.synthesize.deduplicator import dedup_check
from src.synthesize.ranker import rank_findings
from src.synthesize.synthesizer import synthesize_section
from src.report.writer import generate_outline, generate_report
from src.memory.retriever import retrieve_l1
from src.memory.credibility import update_source_credibility
from src.db.postgres import (
    update_task, insert_chat_message,
    insert_memory, upsert_credibility,
)

from src.local_models.embedder import embed_text

_INTENT_PROMPT = """你是一个意图分类器。分析用户查询和已有报告上下文，判断用户意图。

已有报告结构：{report_sections}
deep_search_count（已使用搜索轮次）：{deep_search_count} / 2

用户查询：{query}

可选意图：
1. deep_research — 全新话题的深度调研，首次提问时使用
2. refine_section — 对已有章节的追问（如"再说详细点"），需要补充数据
3. new_search_topic — 提出一个新方面，需要新增章节
4. simple_llm — 总结/改写/追问细节，不需要搜索

返回 JSON：{{"intent": "deep_research", "reason": "..."}}
"""

_INTENT_FALLBACK = "simple_llm"

_SIMPLE_LLM_PROMPT = """你是一个调研助手。基于已有报告和对话历史回答用户问题。

已有报告：
{report}

对话历史（最近 5 条）：
{history}

用户问题：{query}

请直接回答，不需要搜索。保持客观准确。"""

_CHECK_HISTORY_PROMPT = """你是一个记忆检索专家。根据用户查询，判断历史记忆中的哪些信息最相关。

用户查询：{query}

历史相关记忆：
{memory_items}

返回 JSON：{{"related_memories": [记忆ID列表], "summary": "历史信息摘要"}}
"""

_ASSESS_PROMPT = """你是一个研究覆盖度评估专家。判断当前收集的信息是否足够生成一份完整的报告。

当前进度：已完成 {search_count} 轮深度搜索（上限 2 轮）
已收集发现数：{findings_count}
覆盖的维度：{dimensions}
L0 摘要列表：
{l0_summaries}

判断标准：
- 如果信息覆盖了核心维度且有足够数据点 → 可以结束搜索
- 如果缺少关键维度或数据点不足 → 需要继续搜索

用户查询：{query}

返回 JSON：{{
    "sufficient": true/false,
    "reason": "...",
    "missing_dimensions": ["..."],
    "new_sub_queries": ["..."],
    "has_conflict": true/false,
    "conflict_description": "..."
}}
"""

_MEMORY_NODE_PROMPT = """你是一个信息压缩专家。将本次研究的最新发现压缩为 L0 摘要（≤100 字）。

最新发现列表：
{new_findings}

返回 JSON：{{"l0_summary": "..."}}
"""


# ── Node 1: resolve_context ──────────────────────────────────────────

def resolve_context_node(state: AgentState) -> dict[str, Any]:
    """指代消解：结合已有报告消解当前查询中的指代。"""
    l0 = ""
    if state.get("page_summaries"):
        l0 = state["page_summaries"][-1].get("l0_summary", "") if state["page_summaries"] else ""
    resolved = resolve_query(state["query"], l0)
    return {"resolved_query": resolved}


# ── Node 2: intent_classifier ────────────────────────────────────────

def intent_classifier_node(state: AgentState) -> dict[str, Any]:
    """意图分类：4 分支路由。"""
    sections = list(state.get("sections", {}).keys())
    deep_count = state.get("deep_search_count", 0)
    result = chat_json([
        {"role": "system", "content": "你是意图分类器。请用 JSON 格式回答。"},
        {"role": "user", "content": _INTENT_PROMPT.format(
            report_sections=", ".join(sections) if sections else "无已有报告",
            deep_search_count=deep_count,
            query=state.get("resolved_query", state["query"]),
        )},
    ])
    intent = result.get("intent", _INTENT_FALLBACK)
    if intent not in ("deep_research", "refine_section", "new_search_topic", "simple_llm"):
        intent = _INTENT_FALLBACK
    return {"intent": intent}


# ── Node 3: check_history ────────────────────────────────────────────

def check_history_node(state: AgentState) -> dict[str, Any]:
    """检查历史记忆：检索 L1 相关记忆。"""
    query = state.get("resolved_query", state["query"])
    memories = retrieve_l1(query, limit=3, min_score=0.7)
    if not memories:
        return {"findings": state.get("findings", [])}

    memory_text = "\n".join(
        f"[{m['id']}] {m.get('content', '')[:200]}"
        for m in memories
    )
    result = chat_json([
        {"role": "system", "content": "你是记忆检索专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _CHECK_HISTORY_PROMPT.format(
            query=query, memory_items=memory_text
        )},
    ])
    return {"findings": state.get("findings", [])}


# ── Node 4: clarify ──────────────────────────────────────────────────

def clarify_node(state: AgentState) -> dict[str, Any]:
    """判断是否需要用户补充范围。"""
    query = state.get("resolved_query", state["query"])
    result = need_clarification(query)
    return {
        "need_scope": result.get("need_scope", False),
        "scope": {
            "suggested_dimensions": result.get("suggested_dimensions", []),
            "need_hitl": result.get("need_scope", False),
        },
    }


# ── Node 5: planner ─────────────────────────────────────────────────

def planner_node(state: AgentState) -> dict[str, Any]:
    """规划：生成子搜索查询，检查是否需要微调。"""
    query = state.get("resolved_query", state["query"])
    sub_queries = generate_sub_queries(query)
    return {
        "sub_queries": sub_queries,
        "need_adjust": False,
    }


# ── Node 6: search ──────────────────────────────────────────────────

_searcher: DuckDuckGoSearch | None = None


def search_node(state: AgentState) -> dict[str, Any]:
    """执行搜索：对每个子查询搜索。"""
    global _searcher
    if _searcher is None:
        _searcher = DuckDuckGoSearch()
    
    sub_queries = state.get("sub_queries", [state.get("resolved_query", state["query"])])
    all_results: list[dict[str, Any]] = []
    
    try:
        for sq in sub_queries:
            try:
                items = _searcher.search(sq, num_results=5)
                all_results.extend([
                    {"title": r.title, "url": r.url, "snippet": r.snippet, "query": sq}
                    for r in items
                ])
            except RuntimeError:
                continue
    except Exception:
        # Fallback: 搜索全失败
        pass

    increment = 1 if state.get("intent") == "deep_research" else 0
    return {"search_results": all_results, "deep_search_count": state.get("deep_search_count", 0) + increment}


# ── Node 7: scrape ──────────────────────────────────────────────────

_scraper: Scraper | None = None


def scrape_node(state: AgentState) -> dict[str, Any]:
    """抓取搜索结果中的网页。"""
    global _scraper
    if _scraper is None:
        _scraper = Scraper()
    
    results = state.get("search_results", [])
    urls = list(dict.fromkeys(r["url"] for r in results if r.get("url")))
    
    scraped: list[dict[str, Any]] = []
    for url in urls[:10]:  # 最多抓取 10 个
        page = _scraper.scrape(url)
        scraped.append({
            "url": page.url,
            "title": page.title,
            "content": page.content,
            "success": page.success,
            "error": page.error,
        })
        # 更新信誉
        update_source_credibility(url, page.success)
    
    return {"scraped_pages": scraped}


# ── Node 8: context_mgr ─────────────────────────────────────────────

def context_mgr_node(state: AgentState) -> dict[str, Any]:
    """上下文管理：提取 L1/L0 并检查同话题重复。"""
    pages = state.get("scraped_pages", [])
    new_summaries: list[dict[str, Any]] = []
    
    for page in pages:
        if not page.get("success") or not page.get("content"):
            continue
        extracted = extract_from_content(page.get("title", ""), page["content"])
        new_summaries.append({
            "url": page["url"],
            "title": page.get("title", ""),
            "topic": "",
            "l1_content": json.dumps(extracted.get("findings", []), ensure_ascii=False),
            "l0_summary": extracted.get("l0_summary", ""),
            "findings": extracted.get("findings", []),
        })
    
    return {"page_summaries": state.get("page_summaries", []) + new_summaries}


# ── Node 9: dedup ──────────────────────────────────────────────────

def dedup_node(state: AgentState) -> dict[str, Any]:
    """对页面摘要中的发现去重。"""
    summaries = state.get("page_summaries", [])
    all_findings: list[dict[str, Any]] = []
    
    for s in summaries:
        for f in s.get("findings", []):
            content = f.get("content", "")
            if not content:
                continue
            check = dedup_check(content)
            if not check["is_duplicate"]:
                f["source_url"] = s.get("url", "")
                all_findings.append(f)
    
    existing = state.get("findings", [])
    return {"findings": existing + all_findings}


# ── Node 10: rerank ────────────────────────────────────────────────

def rerank_node(state: AgentState) -> dict[str, Any]:
    """用 bge-reranker 对发现重排序。"""
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    if findings:
        ranked = rank_findings(query, findings)
        return {"findings": ranked}
    return {}


# ── Node 11: assess ─────────────────────────────────────────────────

def assess_node(state: AgentState) -> dict[str, Any]:
    """覆盖度评估 + 冲突检测。"""
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    summaries = state.get("page_summaries", [])
    deep_count = state.get("deep_search_count", 0)
    
    dimensions = list(set(f.get("topic", "") for f in findings if f.get("topic")))
    l0_text = "\n".join(
        f"- {s.get('l0_summary', '')}" for s in summaries if s.get("l0_summary")
    )
    
    result = chat_json([
        {"role": "system", "content": "你是评估专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _ASSESS_PROMPT.format(
            search_count=deep_count,
            findings_count=len(findings),
            dimensions=", ".join(dimensions) if dimensions else "暂无",
            l0_summaries=l0_text or "暂无",
            query=query,
        )},
    ])
    
    return {
        "sufficient": result.get("sufficient", True),
        "has_conflict": result.get("has_conflict", False),
        "conflict_description": result.get("conflict_description", ""),
        "sub_queries": result.get("new_sub_queries", state.get("sub_queries", [])),
    }


# ── Node 12: synthesize ─────────────────────────────────────────────

def synthesize_node(state: AgentState) -> dict[str, Any]:
    """综合生成新章节内容。"""
    findings = state.get("findings", [])
    if not findings:
        return {}
    
    topic = state.get("query", "")[:100]
    result = synthesize_section(findings, topic)
    return {
        "sections": {result.get("section_title", topic): result.get("content", "")},
        "need_outline_review": False,
    }


# ── Node 13: report ─────────────────────────────────────────────────

def report_node(state: AgentState) -> dict[str, Any]:
    """生成完整报告（首次）或增量更新章节（后续）。"""
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    existing_report = state.get("report", "")
    sections = state.get("sections", {})
    
    if existing_report and sections:
        # 增量 patch 更新
        return {}
    
    # 首次生成：大纲 + 完整报告
    outline = state.get("outline")
    if not outline:
        outline = generate_outline(query)
    
    result = generate_report(query, outline, findings)
    return {
        "report": result.get("report", ""),
        "sections": result.get("sections", {}),
        "outline": outline,
    }


# ── Node 14: memory ─────────────────────────────────────────────────

def memory_node(state: AgentState) -> dict[str, Any]:
    """记忆持久化：research_tasks + memory_store(L1+L2) + source_credibility + chat_history。"""
    task_id = state.get("task_id", 0)
    if not task_id:
        return {}

    # 更新任务状态和报告
    update_task(task_id, status="completed", report=state.get("report", ""))

    # 更新 L0
    findings = state.get("findings", [])
    if findings:
        try:
            result = chat_json([
                {"role": "system", "content": "你是信息压缩专家。请用 JSON 格式回答。"},
                {"role": "user", "content": _MEMORY_NODE_PROMPT.format(
                    new_findings="\n".join(f.get("content", "") for f in findings[:5])
                )},
            ])
            l0 = result.get("l0_summary", "")
            if l0:
                update_task(task_id, l0_summary=l0)
        except Exception:
            pass

    # 写 L1 到 memory_store（含 embedding 用于跨会话语义检索）
    for f in findings:
        content = f.get("content", "")
        if not content:
            continue
        try:
            emb = embed_text(content)
            insert_memory(
                task_id=task_id,
                level="L1",
                content=content,
                source_url=f.get("source_url", ""),
                topic=f.get("topic", ""),
                embedding=emb,
            )
        except Exception:
            pass

    # 写 L2 到 memory_store
    for p in state.get("scraped_pages", []):
        if p.get("success") and p.get("content"):
            try:
                insert_memory(
                    task_id=task_id,
                    level="L2",
                    content=p["content"][:5000],
                    source_url=p.get("url", ""),
                )
            except Exception:
                pass

    # 更新来源信誉
    for p in state.get("scraped_pages", []):
        try:
            upsert_credibility(p["url"], urlparse(p["url"]).netloc, p.get("success", False))
        except Exception:
            pass

    # 写聊天记录
    try:
        insert_chat_message(state["session_id"], "assistant", state.get("report", "")[:500])
    except Exception:
        pass

    return {"status": "completed"}


# ── Node 15: simple_llm ─────────────────────────────────────────────

def simple_llm_node(state: AgentState) -> dict[str, Any]:
    """简单 LLM 回答，不搜索不写记忆。"""
    query = state.get("query", "")
    report = state.get("report", "")
    history = state.get("messages", [])
    history_text = "\n".join(
        f"{m.get('role', '')}: {m.get('content', '')[:200]}"
        for m in history[-5:]
    )
    
    response = chat([
        {"role": "system", "content": "你是调研助手。"},
        {"role": "user", "content": _SIMPLE_LLM_PROMPT.format(
            report=report[:3000] if report else "暂无已有报告",
            history=history_text,
            query=query,
        )},
    ])
    
    return {"report": response, "status": "completed"}


# ── HITL 中断节点（Nodes 16-19）──────────────────────────────────────

def hitl_scope_node(state: AgentState) -> dict[str, Any]:
    """HITL 范围选择 — 中断等待用户选择调研维度。"""
    dimensions = state.get("scope", {}).get("suggested_dimensions", [])
    interrupt({
        "mode": "scope_select",
        "session_id": state["session_id"],
        "options": {"dimensions": dimensions},
    })
    return {"need_scope": False}


def hitl_adjust_node(state: AgentState) -> dict[str, Any]:
    """HITL 方向微调 — 中断等待用户调整搜索方向。"""
    sub_queries = state.get("sub_queries", [])
    interrupt({
        "mode": "direction_adjust",
        "session_id": state["session_id"],
        "options": {"sub_queries": sub_queries},
    })
    return {"need_adjust": False}


def hitl_conflict_node(state: AgentState) -> dict[str, Any]:
    """HITL 冲突采信 — 中断等待用户选择采信哪方观点。"""
    interrupt({
        "mode": "conflict_resolve",
        "session_id": state["session_id"],
        "options": {"conflict": state.get("conflict_description", "")},
    })
    return {"has_conflict": False}


def hitl_outline_node(state: AgentState) -> dict[str, Any]:
    """HITL 大纲微调 — 中断等待用户调整报告大纲。"""
    interrupt({
        "mode": "outline_edit",
        "session_id": state["session_id"],
        "options": {"outline": state.get("outline", [])},
    })
    return {"need_outline_review": False}


# ── 路由函数 ─────────────────────────────────────────────────────────

def route_by_intent(state: AgentState) -> str:
    """根据 intent 选择路径。"""
    intent = state.get("intent", "simple_llm")
    if intent == "deep_research":
        return "check_history"
    elif intent == "refine_section":
        return "search"
    elif intent == "new_search_topic":
        return "search"
    else:
        return "simple_llm"


def route_after_rerank(state: AgentState) -> str:
    """rerank 后根据 intent 分叉。"""
    intent = state.get("intent", "")
    if intent == "refine_section":
        return "report"
    if intent == "new_search_topic":
        return "synthesize"
    return "assess"


def route_assess(state: AgentState) -> str:
    """assess 后决定：信息不足→回搜索，有冲突→HITL，足够→综合。"""
    sufficient = state.get("sufficient", True)
    deep_count = state.get("deep_search_count", 0)
    has_conflict = state.get("has_conflict", False)

    if not sufficient and deep_count < 2:
        return "search"
    if has_conflict:
        return "hitl_conflict"
    return "synthesize"
