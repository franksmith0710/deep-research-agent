"""LangGraph 节点：15 个节点 + intent 4 分支逻辑 + 4 HITL 中断节点。"""

from __future__ import annotations

import contextvars
import json
from typing import Any
from urllib.parse import urlparse

from langgraph.types import interrupt

from src.agent.state import AgentState
from src.config import settings
from src.logging_config import get_logger

logger = get_logger("nodes")
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.llm.client import chat, chat_json
from src.api.contexts import stream_callback_var
from src.planner.planner import resolve_query, need_clarification, generate_sub_queries
from src.search.metaso import MetasoSearch
from src.search.scraper import Scraper
from src.extract.extractor import extract_from_content
from src.synthesize.deduplicator import dedup_check
from src.synthesize.ranker import rank_findings
from src.synthesize.synthesizer import synthesize_section
from src.report.writer import generate_outline, generate_report_stream
from src.memory.retriever import retrieve_l1
from src.memory.credibility import update_source_credibility, get_credibility, get_source_tag
from src.db.postgres import (
    update_task, insert_chat_message,
    insert_memory, upsert_credibility, update_memory,
    get_l2_by_url, get_recent_tasks_by_session,
    search_memory_by_vector,
)

from src.local_models.embedder import embed_text

import torch
import torch.nn.functional as F

_INTENT_PROMPT = """你是一个意图分类器。分析用户查询和已有报告上下文，判断用户意图。

已有报告结构：{report_sections}
deep_search_count（已使用搜索轮次）：{deep_search_count} / 2

用户查询：{query}

可选意图：
1. deep_research — 全新话题的深度调研，首次提问时使用
2. refine_section — 对已有章节的追问（如"再说详细点"），需要补充数据
3. new_search_topic — 提出一个新方面，需要新增章节
4. simple_llm — 总结/改写/追问细节，或查询实时信息（天气、时间、日期、简单百科问答等），不需要搜索

如果意图为 refine_section，请同时指出要细化哪个已有章节（如"核心现状与关键数据"）。
如果意图为 new_search_topic，请同时指出新主题名称（如"中美政策对比"）。

返回 JSON：
{{"intent": "deep_research", "reason": "...", "section_name": ""}}
或
{{"intent": "refine_section", "reason": "...", "section_name": "具体章节名"}}
或
{{"intent": "new_search_topic", "reason": "...", "section_name": "新章节名"}}
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

_ASSESS_PROMPT = """你是一个研究覆盖度评估专家。判断当前收集的信息是否足够生成一份完整的报告，并给出信息覆盖率评分。

当前进度：已完成 {search_count} 轮深度搜索
已收集发现数：{findings_count}
覆盖的维度：{dimensions}
L0 摘要列表：
{l0_summaries}

评分标准（覆盖率 0-100）：
- 0-30：严重不足，大部分核心维度缺失
- 31-50：部分覆盖，关键维度仍有缺口
- 51-70：基本覆盖，但某些方面不够深入
- 71-85：良好覆盖，主要维度有足够数据点
- 86-100：全面覆盖，所有维度信息充分

决策规则：
- 覆盖率 < 70 → 需要继续搜索（返回 new_sub_queries）
- 覆盖率 ≥ 70 → 信息足够，可以结束搜索

用户查询：{query}

返回 JSON：{{
    "coverage_score": 0-100,
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

_MEMORY_FILTER_PROMPT = """你是一个记忆质量评估专家。判断以下发现是否适合存入跨会话长期记忆。

判断标准（满足任一即排除）：
- ❌ 时效性信息：天气、股价、汇率、今日新闻、比赛比分、会议活动等，时间过去后价值归零
- ❌ 碎片化：无具体数据、无结论、纯情绪表达
- ❌ 重复内容：与已有发现高度重复
- ✅ 适合：结构化事实、数据、观点、结论、政策、流程、教程等，跨时间仍有复用价值

用户原始查询：{query}

发现列表：
{findings}

返回 JSON：{{"keep_indices": [0, 2, ...], "reasons": {{"0": "时效性信息", "2": "高质量事实"}}}}
"""

_ADJUST_PROMPT = """你是一个搜索规划专家。判断是否需要用户调整搜索方向。

用户需求：{resolved_query}
生成的子查询：
{sub_queries_text}

判断标准：
- 子查询覆盖全面且有针对性 → need_adjust=false
- 子查询不够精准或用户可能有更明确的方向 → need_adjust=true

返回 JSON：{{"need_adjust": true/false, "reason": "..."}}
"""


# ── Node 1: resolve_context ──────────────────────────────────────────

def resolve_context_node(state: AgentState) -> dict[str, Any]:
    """指代消解：结合当前 session 历史 L0 摘要消解指代。"""
    logger.debug(f"resolve_context_node query='{state['query']}'")
    session_id = state["session_id"]
    tasks = get_recent_tasks_by_session(session_id, limit=10)
    l0s = [t.get("l0_summary", "") for t in tasks if t.get("l0_summary")]
    session_history = "\n".join(f"- {s}" for s in l0s) if l0s else "无历史摘要"
    resolved = resolve_query(state["query"], session_history)
    return {"resolved_query": resolved}


# ── Node 2: intent_classifier ────────────────────────────────────────

def intent_classifier_node(state: AgentState) -> dict[str, Any]:
    """意图分类：4 分支路由。"""
    logger.debug(f"intent_classifier_node resolved_query='{state.get('resolved_query', '')}'")
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
    section_name = result.get("section_name", "") if intent in ("refine_section", "new_search_topic") else ""
    return {"intent": intent, "refine_section_name": section_name}


# ── Node 3: check_history ────────────────────────────────────────────

def check_history_node(state: AgentState) -> dict[str, Any]:
    """检查历史记忆：检索 L1 相关记忆并合并到 findings。"""
    logger.debug(f"check_history_node resolved_query='{state.get('resolved_query', '')}'")
    query = state.get("resolved_query", state["query"])
    memories = retrieve_l1(query, limit=settings.memory_retrieval_limit, min_score=settings.memory_min_score)
    existing = state.get("findings", [])
    if not memories:
        return {"findings": existing}

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
    related_ids = result.get("related_memories", [])
    if not related_ids:
        return {"findings": existing}

    memory_map = {m["id"]: m for m in memories}
    historical_findings = []
    for mid in related_ids:
        m = memory_map.get(mid)
        if m:
            historical_findings.append({
                "content": m.get("content", ""),
                "source_url": m.get("source_url", ""),
                "topic": m.get("topic", "history"),
            })
    return {"findings": existing + historical_findings}


# ── Node 4: clarify ──────────────────────────────────────────────────

def clarify_node(state: AgentState) -> dict[str, Any]:
    """判断是否需要用户补充范围（基于初步搜索结果，信息不足再问用户）。"""
    logger.debug(f"clarify_node resolved_query='{state.get('resolved_query', '')}'")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    summaries = state.get("page_summaries", [])
    search_context = ""
    if findings:
        sample = findings[:5]
        search_context = "已获取的初步信息：\n" + "\n".join(
            f"- {f.get('content', '')[:200]}" for f in sample
        )
    if summaries:
        l0_lines = [s.get("l0_summary", "") for s in summaries if s.get("l0_summary")]
        if l0_lines:
            search_context += "\n\n页面摘要：\n" + "\n".join(f"- {l}" for l in l0_lines[:5])
    result = need_clarification(query, preliminary_findings=search_context)
    return {
        "need_scope": result.get("need_scope", False),
        "scope": {
            "suggested_dimensions": result.get("suggested_dimensions", []),
            "need_hitl": result.get("need_scope", False),
        },
        "_clarify_done": True,
    }


# ── Node 5: planner ─────────────────────────────────────────────────

def planner_node(state: AgentState) -> dict[str, Any]:
    """规划：生成子搜索查询，判断是否需要微调。"""
    logger.debug(f"planner_node resolved_query='{state.get('resolved_query', '')}'")
    query = state.get("resolved_query", state["query"])
    sub_queries = generate_sub_queries(query)

    result = chat_json([
        {"role": "system", "content": "你是搜索规划专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _ADJUST_PROMPT.format(
            resolved_query=query,
            sub_queries_text="\n".join(f"- {sq}" for sq in sub_queries),
        )},
    ])

    return {
        "sub_queries": sub_queries,
        "need_adjust": result.get("need_adjust", False),
    }


# ── Node 6: search ──────────────────────────────────────────────────

_searcher: MetasoSearch | None = None


def search_node(state: AgentState) -> dict[str, Any]:
    """执行搜索：对每个子查询搜索。"""
    logger.debug(f"search_node sub_queries={state.get('sub_queries', [])}")
    global _searcher
    if _searcher is None:
        _searcher = MetasoSearch()
    
    sub_queries = state.get("sub_queries", [state.get("resolved_query", state["query"])])
    all_results: list[dict[str, Any]] = []
    
    def _search_one(sq: str) -> list[dict[str, Any]]:
        items = _searcher.search(sq, num_results=3)
        return [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "query": sq}
            for r in items
        ]
    
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_search_one, sq): sq for sq in sub_queries}
        for future in as_completed(fut_map):
            sq = fut_map[future]
            try:
                all_results.extend(future.result())
            except RuntimeError as e:
                logger.warning(f"搜索失败 query='{sq}': {e}")
                continue
    
    if not all_results:
        fallback_query = state.get("resolved_query", state["query"])
        logger.warning(f"所有 sub_queries 都失败，fallback 到原始 query='{fallback_query}'")
        try:
            items = _searcher.search(fallback_query, num_results=3)
            all_results.extend([
                {"title": r.title, "url": r.url, "snippet": r.snippet, "query": fallback_query}
                for r in items
            ])
        except Exception as e:
            logger.warning(f"fallback 也失败: {e}")

    search_all_failed = not all_results
    increment = 1 if state.get("intent") == "deep_research" else 0
    return {
        "search_results": all_results,
        "search_all_failed": search_all_failed,
        "deep_search_count": state.get("deep_search_count", 0) + increment,
    }


# ── Node 7: scrape ──────────────────────────────────────────────────

_scraper: Scraper | None = None


def scrape_node(state: AgentState) -> dict[str, Any]:
    """抓取搜索结果中的网页。抓取失败时 fallback 到 search snippet。"""
    logger.debug(f"scrape_node results_count={len(state.get('search_results', []))}")
    global _scraper
    if _scraper is None:
        _scraper = Scraper()
    
    results = state.get("search_results", [])
    urls = list(dict.fromkeys(r["url"] for r in results if r.get("url")))
    
    # 建立 url → snippet 映射
    snippet_map = {r["url"]: r.get("snippet", "") for r in results if r.get("url")}
    
    def _scrape_one(url: str) -> dict[str, Any]:
        page = _scraper.scrape(url)
        update_source_credibility(url, page.success)
        if not page.success:
            snippet = snippet_map.get(url, "")
            if snippet:
                logger.info(f"抓取失败但使用 search snippet 降级 url={url}")
                return {
                    "url": url, "title": page.title or url,
                    "content": snippet, "success": True, "error": page.error,
                }
            logger.warning(f"抓取失败且无 snippet 降级 url={url}: {page.error}")
            return {
                "url": url, "title": page.title or "",
                "content": "", "success": False, "error": page.error,
            }
        return {
            "url": page.url, "title": page.title,
            "content": page.content, "success": True, "error": "",
        }

    scraped: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_scrape_one, url): url for url in urls[:5]}
        for future in as_completed(fut_map):
            scraped.append(future.result())

    scrape_errors = sum(1 for s in scraped if not s["success"])
    logger.info(f"scrape_node: 共 {len(scraped)} 页，失败 {scrape_errors} 页（已 fallback {len(scraped) - scrape_errors} 页）")
    return {"scraped_pages": scraped}


# ── Node 8: context_mgr ─────────────────────────────────────────────

def context_mgr_node(state: AgentState) -> dict[str, Any]:
    """上下文管理：提取 L1/L0 并检查同话题重复。跳过过短页面。"""
    logger.debug(f"context_mgr_node scraped_pages={len(state.get('scraped_pages', []))}")
    pages = state.get("scraped_pages", [])
    new_summaries: list[dict[str, Any]] = []

    def _extract_page(page: dict) -> dict | None:
        if not page.get("success") or not page.get("content"):
            return None
        content = page["content"]
        if len(content) < 100:
            logger.debug(f"跳过过短页面 url={page['url']} len={len(content)}")
            return None
        extracted = extract_from_content(page.get("title", ""), content)
        return {
            "url": page["url"],
            "title": page.get("title", ""),
            "topic": "",
            "l1_content": json.dumps(extracted.get("findings", []), ensure_ascii=False),
            "l0_summary": extracted.get("l0_summary", ""),
            "findings": extracted.get("findings", []),
        }

    eligible = [p for p in pages if p.get("success") and p.get("content") and len(p["content"]) >= 100]
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_extract_page, p): p for p in eligible}
        for future in as_completed(fut_map):
            result = future.result()
            if result:
                new_summaries.append(result)

    return {"page_summaries": state.get("page_summaries", []) + new_summaries}


# ── Node 9: dedup ──────────────────────────────────────────────────

def dedup_rerank_node(state: AgentState) -> dict[str, Any]:
    """合并去重+重排：先 dedup 再 rerank，减少一次 state 读写。"""
    logger.debug(f"dedup_rerank_node summaries={len(state.get('page_summaries', []))}")
    summaries = state.get("page_summaries", [])
    batch: list[str] = []
    batch_meta: list[tuple[int, int]] = []

    for si, s in enumerate(summaries):
        for fi, f in enumerate(s.get("findings", [])):
            content = f.get("content", "")
            if not content:
                continue
            batch.append(content)
            batch_meta.append((si, fi))

    if not batch:
        merged = state.get("findings", [])
        return {"findings": merged}

    # 批量嵌入（单次模型推理）
    embeddings = embed_text(batch)

    existing = state.get("findings", [])
    existing_batch = [f.get("content", "") for f in existing if f.get("content")]
    existing_embs = embed_text(existing_batch) if existing_batch else []
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    all_findings: list[dict[str, Any]] = []
    for i, (si, fi) in enumerate(batch_meta):
        emb = embeddings[i]
        results = search_memory_by_vector(emb, limit=1, min_score=0.88)
        if results:
            continue
        # 跨轮次去重：检查与当前 session 已有 findings 的语义相似度
        if existing_embs:
            emb_t = torch.tensor(emb, device=device).unsqueeze(0)
            existing_t = torch.tensor(existing_embs, device=device)
            if (F.cosine_similarity(emb_t, existing_t).max().item() >= 0.88):
                continue
        f = summaries[si]["findings"][fi]
        f["source_url"] = summaries[si].get("url", "")
        all_findings.append(f)

    merged = existing + all_findings

    query = state.get("resolved_query", state["query"])
    if merged:
        ranked = rank_findings(query, merged, top_k=15)
        return {"findings": ranked}
    return {"findings": merged}


# ── Node 11: assess ─────────────────────────────────────────────────

def assess_node(state: AgentState) -> dict[str, Any]:
    """覆盖度评估 + 冲突检测。"""
    logger.debug(f"assess_node deep_search_count={state.get('deep_search_count', 0)}")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    summaries = state.get("page_summaries", [])
    deep_count = state.get("deep_search_count", 0)

    if state.get("search_all_failed") and not findings:
        logger.info("搜索全部失败且无已有 findings，标记为需要 LLM fallback")
        return {
            "sufficient": True,
            "has_conflict": False,
            "_llm_fallback": True,
            "sub_queries": state.get("sub_queries", []),
            "assess_round": state.get("assess_round", 0) + 1,
        }
    
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
    
    new_sub = result.get("new_sub_queries", [])
    has_conflict = result.get("has_conflict", False)
    conflict_description = result.get("conflict_description", "")

    if has_conflict and findings:
        logger.info("检测到信息冲突，尝试带可信度打分的自动裁决")
        # 收集各冲突方的来源可信度
        conflict_findings = []
        seen_urls = set()
        for f in findings:
            url = f.get("source_url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                cred = get_credibility(url) if url else None
                score = cred["score"] if cred else 50
                tag = get_source_tag(url) if url else "未知"
                conflict_findings.append({
                    "content": f.get("content", ""),
                    "url": url,
                    "credibility_score": score,
                    "credibility_tag": tag,
                })
        try:
            from src.search.scraper import Scraper
            scraper = Scraper()
            conflict_contents = []
            for cf in conflict_findings[:3]:
                url = cf["url"]
                try:
                    page = scraper.scrape(url)
                    if page.success and page.content:
                        conflict_contents.append(
                            f"来源: {url}\n"
                            f"可信度: {cf['credibility_score']}/100（{cf['credibility_tag']}）\n"
                            f"标题: {page.title}\n"
                            f"内容:\n{page.content[:2000]}"
                        )
                    else:
                        conflict_contents.append(
                            f"来源: {url}\n"
                            f"可信度: {cf['credibility_score']}/100（{cf['credibility_tag']}）\n"
                            f"（原文抓取失败，使用摘要信息）\n"
                            f"{cf['content'][:500]}"
                        )
                except Exception:
                    logger.warning(f"冲突源抓取失败: {url}")
                    conflict_contents.append(
                        f"来源: {url}\n"
                        f"可信度: {cf['credibility_score']}/100（{cf['credibility_tag']}）\n"
                        f"{cf['content'][:500]}"
                    )
            if conflict_contents:
                conflict_context = "\n\n---\n\n".join(conflict_contents)
                resolve_result = chat_json([
                    {"role": "system", "content": "你是一个事实核查专家。根据可信度评分和多方来源原文进行综合裁决。"},
                    {"role": "user", "content": f"""用户查询：{query}
冲突描述：{conflict_description}

各来源原文及可信度：
{conflict_context}

裁决流程：
1. 对比各来源的可信度评分和权威性
2. 对比各来源的内容一致性和逻辑合理性
3. 优先采信可信度高、有数据支撑、逻辑自洽的来源
4. 给出推荐结论和置信度

返回 JSON：
{{"conclusion": "裁决结论", "reason": "推理过程（含可信度对比）", "confidence": 0-100, "has_conflict": false}}
如果无法裁决，返回 {{"has_conflict": true, "reason": "无法裁决的原因", "confidence": 0}}"""},
                ])
                if not resolve_result.get("has_conflict", True):
                    logger.info(f"冲突已自动解决（置信度={resolve_result.get('confidence', 0)}）")
                    has_conflict = False
                    conflict_description = resolve_result.get("conclusion", "")
        except Exception:
            logger.exception("冲突裁决失败，保持原冲突状态")

    return {
        "sufficient": result.get("sufficient", True),
        "coverage_score": result.get("coverage_score", 0),
        "has_conflict": has_conflict,
        "conflict_description": conflict_description,
        "sub_queries": new_sub if new_sub else state.get("sub_queries", []),
        "assess_round": state.get("assess_round", 0) + 1,
    }


# ── Node 12: synthesize ─────────────────────────────────────────────

def synthesize_node(state: AgentState) -> dict[str, Any]:
    """综合生成新章节内容。"""
    logger.debug(f"synthesize_node findings={len(state.get('findings', []))}")
    findings = state.get("findings", [])

    if state.get("_llm_fallback") and not findings:
        logger.info("使用 LLM 直接回答（搜索失败 fallback）")
        query = state.get("resolved_query", state["query"])
        response = chat([
            {"role": "system", "content": "你是一个智能助手。请直接回答用户问题。"},
            {"role": "user", "content": query},
        ])
        return {"sections": {"answer": response}}

    if not findings:
        return {}

    refine_name = state.get("refine_section_name", "")
    intent = state.get("intent", "")
    if refine_name and intent == "refine_section":
        topic = refine_name
    elif intent == "new_search_topic":
        topic = state.get("resolved_query", state["query"])[:100]
    else:
        topic = state.get("resolved_query", state["query"])[:100]

    result = synthesize_section(findings, topic)
    return {
        "sections": {result.get("section_title", topic): result.get("content", "")},
        "need_outline_review": False,
    }


# ── Node 13: report ─────────────────────────────────────────────────

def _build_footnotes(state: AgentState) -> str:
    """从 memory_store L2 构建引用脚注（含 snippet 预览）。"""
    from src.memory.credibility import get_source_tag
    from src.db.postgres import get_l2_by_url

    seen = set()
    notes = []
    for p in state.get("scraped_pages", []):
        url = p.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)

        tag = get_source_tag(url)
        l2_records = get_l2_by_url(url)
        snippet = ""
        title = ""
        if l2_records and l2_records[0].get("content"):
            snippet = l2_records[0]["content"][:200]
            title = l2_records[0].get("topic", "")

        if snippet:
            notes.append(f"- [{tag}] **{title}**\n  {snippet}\n  [{url}]")
        else:
            notes.append(f"- [{tag}] {url}")

    if not notes:
        return ""
    return "\n\n---\n### 来源\n" + "\n".join(notes)


async def report_node(state: AgentState) -> dict[str, Any]:
    """生成完整报告（首次）或增量更新章节（后续）。支持流式输出。"""
    logger.debug(f"report_node sections_count={len(state.get('sections', {}))}")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    existing_report = state.get("report", "")
    sections = state.get("sections", {})

    if existing_report and sections:
        report_parts = [sections[ch] for ch in sections if sections.get(ch)]
        return {
            "report": "\n\n".join(report_parts),
            "outline": state.get("outline", []),
        }

    outline = state.get("outline")
    if not outline:
        outline = generate_outline(query)

    footnotes = _build_footnotes(state)

    cb = stream_callback_var.get()
    result = await generate_report_stream(query, outline, findings, on_token=cb)
    report = result.get("report", "")
    if footnotes:
        report += footnotes
    return {
        "report": report,
        "sections": result.get("sections", {}),
        "outline": outline,
        "_report_streamed": True,
    }


# ── Node 14: memory ─────────────────────────────────────────────────

def memory_node(state: AgentState) -> dict[str, Any]:
    """记忆持久化：research_tasks + memory_store(L1+L2) + source_credibility + chat_history。"""
    logger.debug(f"memory_node task_id={state.get('task_id', 0)}")
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
            logger.exception("memory_node L0 compression failed")

    # 写 L1 到 memory_store（含 embedding 用于跨会话语义检索）
    # 先用 LLM 过滤掉不适合长期存储的发现
    high_quality_findings = []
    for f in findings:
        content = f.get("content", "")
        if content and len(content) >= 50:
            high_quality_findings.append(f)

    keep_indices = []
    if high_quality_findings:
        try:
            result = chat_json([
                {"role": "system", "content": "你是一个记忆质量评估专家。请用 JSON 格式回答。"},
                {"role": "user", "content": _MEMORY_FILTER_PROMPT.format(
                    query=state.get("resolved_query", state.get("query", "")),
                    findings="\n".join(f"{i}. {f['content'][:300]}" for i, f in enumerate(high_quality_findings))
                )},
            ])
            keep_indices = result.get("keep_indices", [])
            logger.info(f"memory_node LLM 过滤: 原始 {len(findings)} 条, 通过 {len(keep_indices)} 条")
        except Exception:
            logger.exception("memory_node LLM 过滤失败，回退到全部写入")
            keep_indices = list(range(len(high_quality_findings)))

    # 写入通过过滤的 L1
    for i in keep_indices:
        if 0 <= i < len(high_quality_findings):
            f = high_quality_findings[i]
            content = f.get("content", "")
            try:
                check = dedup_check(content)
                if check["is_duplicate"] and check["matched_id"]:
                    update_memory(check["matched_id"], content)
                else:
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
                logger.exception("memory_node L1 insert failed")

    # 写 L2 到 memory_store（只存 url + title + snippet，用于前端引用展示）
    for p in state.get("scraped_pages", []):
        if p.get("success"):
            try:
                snippet = p.get("snippet", "")[:500] if p.get("snippet") else ""
                if not snippet and p.get("content"):
                    snippet = p.get("content", "")[:500]
                insert_memory(
                    task_id=task_id,
                    level="L2",
                    content=snippet,
                    source_url=p.get("url", ""),
                    topic=(p.get("title", "") or "")[:255],
                    embedding=None,
                )
            except Exception:
                logger.exception("memory_node L2 insert failed")

    # 更新来源信誉
    for p in state.get("scraped_pages", []):
        try:
            upsert_credibility(p["url"], urlparse(p["url"]).netloc, p.get("success", False))
        except Exception:
            logger.exception("memory_node credibility update failed")

    # 写聊天记录
    try:
        insert_chat_message(state["session_id"], "assistant", state.get("report", ""))
    except Exception:
        logger.exception("memory_node chat history write failed")

    return {"status": "completed"}


# ── Node 15: simple_llm ─────────────────────────────────────────────

def simple_llm_node(state: AgentState) -> dict[str, Any]:
    """简单 LLM 回答，不搜索不写记忆。"""
    logger.debug(f"simple_llm_node query='{state.get('query', '')}'")
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


# ── Node 16: memory_llm ──────────────────────────────────────────────

def memory_llm_node(state: AgentState) -> dict[str, Any]:
    """轻量版记忆持久化：chat_history + query+回答写入 L1。"""
    try:
        insert_chat_message(state["session_id"], "assistant", state.get("report", ""))
    except Exception:
        logger.exception("memory_llm_node chat history failed")
    return {"status": "completed"}


# ── HITL 中断节点（Nodes 17-20）──────────────────────────────────────

def hitl_scope_node(state: AgentState) -> dict[str, Any]:
    """HITL 范围选择 — 中断等待用户选择调研维度。"""
    logger.debug(f"hitl_scope_node session_id='{state['session_id']}'")
    dimensions = state.get("scope", {}).get("suggested_dimensions", [])
    resume_data = interrupt({
        "mode": "scope_select",
        "session_id": state["session_id"],
        "options": {"dimensions": dimensions},
    })
    selected = resume_data.get("selectedDimensions", []) if isinstance(resume_data, dict) else []
    return {"need_scope": False, "selected_dimensions": selected}


def hitl_adjust_node(state: AgentState) -> dict[str, Any]:
    """HITL 方向微调 — 中断等待用户调整搜索方向。"""
    logger.debug(f"hitl_adjust_node session_id='{state['session_id']}'")
    sub_queries = state.get("sub_queries", [])
    resume_data = interrupt({
        "mode": "direction_adjust",
        "session_id": state["session_id"],
        "options": {"sub_queries": sub_queries},
    })
    selected = resume_data.get("selectedSubQueries", []) if isinstance(resume_data, dict) else []
    return {"need_adjust": False, "sub_queries": selected if selected else sub_queries}


def hitl_conflict_node(state: AgentState) -> dict[str, Any]:
    """HITL 冲突采信 — 中断等待用户选择采信哪方观点。"""
    logger.debug(f"hitl_conflict_node session_id='{state['session_id']}'")
    resume_data = interrupt({
        "mode": "conflict_resolve",
        "session_id": state["session_id"],
        "options": {"conflict": state.get("conflict_description", "")},
    })
    choice = resume_data.get("selectedChoice", "") if isinstance(resume_data, dict) else ""
    return {"has_conflict": False, "conflict_resolution": choice}


def hitl_outline_node(state: AgentState) -> dict[str, Any]:
    """HITL 大纲微调 — 中断等待用户调整报告大纲。"""
    logger.debug(f"hitl_outline_node session_id='{state['session_id']}'")
    resume_data = interrupt({
        "mode": "outline_edit",
        "session_id": state["session_id"],
        "options": {"outline": state.get("outline", [])},
    })
    outline = resume_data.get("outline", "") if isinstance(resume_data, dict) else ""
    return {"need_outline_review": False, "outline": outline}


# ── 路由函数 ─────────────────────────────────────────────────────────

def route_by_intent(state: AgentState) -> str:
    """根据 intent 选择路径。"""
    intent = state.get("intent", "simple_llm")
    logger.debug(f"route_by_intent intent={intent}")
    if intent in ("deep_research", "new_search_topic"):
        return "check_history"
    elif intent == "refine_section":
        return "search"
    else:
        return "simple_llm"


def route_after_rerank(state: AgentState) -> str:
    """rerank 后根据 intent 和阶段分叉。"""
    intent = state.get("intent", "")
    logger.debug(f"route_after_rerank intent={intent} clarify_done={state.get('_clarify_done', False)}")
    if intent == "refine_section":
        return "synthesize"
    # deep_research / new_search_topic: 首次搜索后先去 clarify（有搜索结果支撑），后续循环直接 assess
    if not state.get("_clarify_done", False):
        return "clarify"
    return "assess"


def route_assess(state: AgentState) -> str:
    """assess 后决定：覆盖率不足→回搜索，有冲突→HITL，足够→综合。
   动态阈值：覆盖率 < 70 且轮次 < 3 继续搜索，否则进入综合。"""
    coverage_score = state.get("coverage_score", 0)
    assess_round = state.get("assess_round", 0)
    has_conflict = state.get("has_conflict", False)
    logger.debug(f"route_assess coverage_score={coverage_score} assess_round={assess_round} has_conflict={has_conflict}")

    if has_conflict:
        return "hitl_conflict"
    if coverage_score < 70 and assess_round < 3:
        return "search"
    return "synthesize"
