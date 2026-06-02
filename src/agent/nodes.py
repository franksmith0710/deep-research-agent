"""LangGraph 节点：12 functional + 1 HITL + intent 3 分支逻辑。"""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

from langgraph.types import interrupt

from src.agent.state import AgentState
from src.config import settings
from src.logging_config import get_logger

logger = get_logger("nodes")
from src.llm.client import chat_async, chat_json_async
from src.api.contexts import stream_callback_var
from src.planner.planner import resolve_query, need_clarification, generate_sub_queries
from src.search.metaso import MetasoSearch
from src.search.duckduckgo import DuckDuckGoSearch
from src.search.scraper import Scraper
from src.page_brief.brief import batch_brief_from_pages
from src.synthesize.ranker import rank_findings
from src.local_models.reranker import rerank
from src.report.writer import generate_outline, generate_report_stream
from src.memory.retriever import retrieve_l1
from src.memory.updater import update_memory as memory_update
from src.memory.credibility import update_source_credibility, get_source_tag
from src.db.postgres import (
    update_task, insert_chat_message,
    get_recent_tasks_by_session,
)
from src.local_models.embedder import embed_text
from src.utils.dedup import max_cosine_similarity, SEMANTIC_DUP_THRESHOLD

_INTENT_PROMPT = """你是一个意图分类器。分析用户查询和已有报告上下文，判断用户意图。

已有报告结构：{report_sections}
deep_search_count（已使用搜索轮次）：{deep_search_count} / 2

用户查询：{query}

可选意图：
1. deep_research — 全新或新增话题的深度调研，首次提问或提出新方面时使用
2. refine_section — 对已有章节的追问（如"再说详细点""展开讲讲第三点"），需要补充数据
3. simple_llm — 总结/改写/追问细节/询问助手功能与能力/自我介绍/问候，或查询实时信息（天气、时间、日期、简单百科问答等），不需要搜索

注意：问候、自我介绍、询问助手功能等问题应选择 simple_llm。如果不确定意图，优先选 simple_llm，避免不必要的搜索。

如果意图为 refine_section，请同时指出要细化哪个已有章节（如"技术路线""市场规模"）。

返回 JSON：
{{"intent": "deep_research", "reason": "...", "section_name": ""}}
或
{{"intent": "refine_section", "reason": "...", "section_name": "具体章节名"}}
"""

_INTENT_FALLBACK = "simple_llm"

_SIMPLE_LLM_PROMPT = """你是一个调研助手。基于历史调研摘要回答用户问题。

历史调研摘要：
{history}

用户问题：{query}

请直接回答，不需要搜索。保持客观准确。"""


_ASSESS_PROMPT = """你是研究质量评估专家。评估以下搜索结果。

当前轮次：第 {search_count} 轮（最多 3 轮）
已收集发现数：{findings_count}

已有发现：
{findings_detail}

用户查询：{query}

## 评分

① 用户问题匹配度 (0-30)
- 所有关键方面都有直接回答 → 30
- 主要方面已回答，少数细节点不足 → 20
- 回答了部分方面，有明显缺口 → 10
- 发现内容与查询关联度低 → 5
从 [30, 20, 10, 5] 中选择，不得使用其他数值。

## 输出 JSON

{{
    "score_detail": {{
        "query_match": 分数
    }},
    "reason": "判断依据"
}}
"""


# ── Node 1: resolve_context ──────────────────────────────────────────

async def resolve_context_node(state: AgentState) -> dict[str, Any]:
    """指代消解：结合当前 session 历史 L0 摘要消解指代。"""
    logger.debug(f"resolve_context_node query='{state['query']}'")
    session_id = state["session_id"]
    tasks = get_recent_tasks_by_session(session_id, limit=10)
    l0s = [t.get("l0_summary", "") for t in tasks if t.get("l0_summary")]
    session_history = "\n".join(f"- {s}" for s in l0s) if l0s else "无历史摘要"
    resolved = await resolve_query(state["query"], session_history)
    return {"resolved_query": resolved}


# ── Node 2: intent_classifier ────────────────────────────────────────

async def intent_classifier_node(state: AgentState) -> dict[str, Any]:
    """意图分类：4 分支路由。"""
    logger.debug(f"intent_classifier_node resolved_query='{state.get('resolved_query', '')}'")
    sections = list(state.get("sections", {}).keys())
    deep_count = state.get("deep_search_count", 0)
    result = await chat_json_async([
        {"role": "system", "content": "你是意图分类器。请用 JSON 格式回答。"},
        {"role": "user", "content": _INTENT_PROMPT.format(
            report_sections=", ".join(sections) if sections else "无已有报告",
            deep_search_count=deep_count,
            query=state.get("resolved_query", state["query"]),
        )},
    ])
    intent = result.get("intent", _INTENT_FALLBACK)
    if intent not in ("deep_research", "refine_section", "simple_llm"):
        intent = _INTENT_FALLBACK
    section_name = result.get("section_name", "") if intent == "refine_section" else ""
    return {"intent": intent, "refine_section_name": section_name}


# ── Node 3: clarify ──────────────────────────────────────────────────

async def clarify_node(state: AgentState) -> dict[str, Any]:
    """判断是否需要用户补充范围或细节（在搜索之前，基于查询本身判断范围是否明确）。"""
    logger.debug(f"clarify_node resolved_query='{state.get('resolved_query', '')}'")

    if state.get("scope", {}).get("suggested_dimensions"):
        logger.debug("clarify_node scope already exists, skipping LLM")
        return {"need_scope": False, "scope": state["scope"]}

    query = state.get("resolved_query", state["query"])
    result = await need_clarification(query)
    return {
        "need_scope": result.get("need_scope", False),
        "scope": {
            "suggested_dimensions": result.get("suggested_dimensions", []),
            "details_to_add": result.get("details_to_add", []),
            "need_hitl": result.get("need_scope", False),
        },
    }


# ── Node 5: planner ─────────────────────────────────────────────────

async def planner_node(state: AgentState) -> dict[str, Any]:
    """检索长期记忆 → 规划子搜索查询。"""
    logger.debug(f"planner_node resolved_query='{state.get('resolved_query', '')}'")
    query = state.get("resolved_query", state["query"])

    # 检索 L1 记忆合并到 findings
    memories = retrieve_l1(query, limit=settings.memory_retrieval_limit, min_score=settings.memory_min_score)
    existing = state.get("findings", [])
    if memories:
        historical_findings = [
            {"content": m["content"], "source_url": m.get("source_url", ""), "topic": m.get("name", "history")}
            for m in memories
        ]
        existing = existing + historical_findings
        logger.debug(f"planner_node: {len(historical_findings)} 条记忆合并到 findings")

    supplement = state.get("user_supplement", "")
    if supplement:
        query = f"{query} {supplement}"
    sub_queries = await generate_sub_queries(query)
    return {"sub_queries": sub_queries, "findings": existing}


# ── Node 6: search ──────────────────────────────────────────────────

_primary_searcher: MetasoSearch | None = None
_fallback_searcher: DuckDuckGoSearch | None = None


def _search_one_engine(
    sq: str, engine: MetasoSearch | DuckDuckGoSearch
) -> list[dict[str, Any]]:
    items = engine.search(sq, num_results=3)
    return [
        {"title": r.title, "url": r.url, "snippet": r.snippet, "query": sq}
        for r in items
    ]


def search_node(state: AgentState) -> dict[str, Any]:
    """执行搜索：优先 MetasoSearch，全部失败后 fallback 到 DuckDuckGoSearch。"""
    logger.debug(f"search_node sub_queries={state.get('sub_queries', [])}")
    global _primary_searcher, _fallback_searcher
    if _primary_searcher is None:
        _primary_searcher = MetasoSearch()
    if _fallback_searcher is None:
        _fallback_searcher = DuckDuckGoSearch()

    sub_queries = state.get("sub_queries", [state.get("resolved_query", state["query"])])

    # 回环跳过已搜过的 query
    searched = state.get("_searched_queries", [])
    new_sqs = [sq for sq in sub_queries if sq not in searched]
    if not new_sqs:
        logger.debug("search_node: 所有子查询已搜过，跳过")
        return {
            "search_results": [],
            "search_all_failed": state.get("search_all_failed", False),
            "deep_search_count": state.get("deep_search_count", 0) + (1 if state.get("intent") == "deep_research" else 0),
            "_searched_queries": searched,
        }

    all_results: list[dict[str, Any]] = []

    def _try_engine(engine, label: str) -> bool:
        nonlocal all_results
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(5, len(new_sqs))) as pool:
            fut_map = {pool.submit(_search_one_engine, sq, engine): sq for sq in new_sqs}
            for future in as_completed(fut_map):
                sq = fut_map[future]
                try:
                    results.extend(future.result())
                except Exception as e:
                    logger.warning(f"[{label}] 搜索失败 query='{sq}': {e}")
                    continue
        if results:
            all_results = results
            logger.info(f"[{label}] 成功返回 {len(results)} 条结果")
            return True
        return False

    if not _try_engine(_primary_searcher, "MetasoSearch"):
        logger.warning("MetasoSearch 全部失败，fallback 到 DuckDuckGoSearch")
        if not _try_engine(_fallback_searcher, "DuckDuckGo"):
            fallback_query = state.get("resolved_query", state["query"])
            logger.warning(f"DuckDuckGo 也失败，fallback 到原始 query='{fallback_query}'")
            try:
                items = _fallback_searcher.search(fallback_query, num_results=3)
                all_results.extend([
                    {"title": r.title, "url": r.url, "snippet": r.snippet, "query": fallback_query}
                    for r in items
                ])
            except Exception as e:
                logger.warning(f"最终 fallback 也失败: {e}")

    search_all_failed = not all_results
    increment = 1 if state.get("intent") == "deep_research" else 0

    # 回环过滤：过滤掉 scraped_pages 中已有的 URL，避免重复抓取
    existing_urls = {p.get("url", "") for p in state.get("scraped_pages", []) if p.get("url")}
    if existing_urls and not state.get("search_all_failed"):
        before = len(all_results)
        all_results = [r for r in all_results if r.get("url") not in existing_urls]
        if before != len(all_results):
            logger.info(f"search_node: 过滤 {before - len(all_results)} 个已抓取 URL，剩余 {len(all_results)} 个")
    search_all_failed = not all_results

    return {
        "search_results": all_results,
        "search_all_failed": search_all_failed,
        "deep_search_count": state.get("deep_search_count", 0) + increment,
        "_searched_queries": searched + new_sqs,
    }


# ── Node 7: scrape ──────────────────────────────────────────────────

_scraper: Scraper | None = None
_scrape_cache: dict[str, tuple[float, dict]] = {}
_SCRAPE_CACHE_TTL = 600  # 10 分钟


def cleanup_scraper() -> None:
    """关闭全局爬虫的 httpx 连接池。"""
    global _scraper
    if _scraper is not None:
        _scraper.close()
        _scraper = None


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
        now = time.monotonic()
        cached = _scrape_cache.get(url)
        if cached and now - cached[0] < _SCRAPE_CACHE_TTL:
            logger.debug(f"scrape cache hit url={url}")
            return cached[1]

        page = _scraper.scrape(url)
        update_source_credibility(url, page.success)
        if not page.success:
            snippet = snippet_map.get(url, "")
            if snippet:
                logger.info(f"抓取失败但使用 search snippet 降级 url={url}")
                return {
                    "url": url, "title": page.title or url,
                    "content": snippet, "success": False, "is_snippet": True, "error": page.error,
                }
            logger.warning(f"抓取失败且无 snippet 降级 url={url}: {page.error}")
            return {
                "url": url, "title": page.title or "",
                "content": "", "success": False, "error": page.error,
            }
        snippet = snippet_map.get(url, "")
        return {
            "url": page.url, "title": page.title,
            "content": page.content, "snippet": snippet,
            "success": True, "error": "",
        }

    scraped: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_scrape_one, url): url for url in urls[:5]}
        for future in as_completed(fut_map):
            url = fut_map[future]
            result = future.result()
            _scrape_cache[url] = (time.monotonic(), result)
            scraped.append(result)

    scrape_errors = sum(1 for s in scraped if not s["success"])
    logger.info(f"scrape_node: 共 {len(scraped)} 页，失败 {scrape_errors} 页（已 fallback {len(scraped) - scrape_errors} 页）")
    existing = state.get("scraped_pages", [])
    return {"scraped_pages": existing + scraped}


# ── Node 8: context_mgr ─────────────────────────────────────────────

async def context_mgr_node(state: AgentState) -> dict[str, Any]:
    """上下文管理：提取关键要点并检查同话题重复。跳过过短页面。"""
    logger.debug(f"context_mgr_node scraped_pages={len(state.get('scraped_pages', []))}")
    pages = state.get("scraped_pages", [])
    new_summaries: list[dict[str, Any]] = []

    processed_urls = {s.get("url", "") for s in state.get("page_summaries", []) if s.get("url")}
    eligible = [p for p in pages if p.get("success") and p.get("content") and len(p["content"]) >= 100 and p.get("url", "") not in processed_urls]

    if not eligible:
        return {"page_summaries": state.get("page_summaries", []) + new_summaries}

    # Step 1: Relevance filter per page (fast, using rerank)
    page_query = state.get("resolved_query", state.get("query", ""))
    relevant: list[dict] = []
    for p in eligible:
        if page_query:
            judge_text = (p.get("snippet", "") + " " + p["content"][:2000]).strip()
            scores = rerank(page_query, [judge_text], top_k=1)
            if scores and scores[0][1] < 0.8:
                logger.debug(f"页面不相关，跳过提取 url={p['url']} score={scores[0][1]:.4f}")
                continue
        relevant.append(p)

    if not relevant:
        return {"page_summaries": state.get("page_summaries", []) + new_summaries}

    # Step 2: Batch LLM call for all relevant pages
    batch_results = await batch_brief_from_pages(
        [{"title": p.get("title", ""), "content": p["content"], "url": p["url"]} for p in relevant],
        query=page_query,
    )

    # Step 3: Parse batch results
    url_map = {p["url"]: p for p in relevant}
    for item in batch_results:
        url = item.get("url", "")
        page = url_map.get(url)
        if not page:
            continue
        key_points = item.get("key_points", [])
        topic_list = list(set(kp.get("topic", "") for kp in key_points if kp.get("topic")))
        new_summaries.append({
            "url": url,
            "title": page.get("title", ""),
            "topic": topic_list[0] if topic_list else "",
            "key_points_json": json.dumps(key_points, ensure_ascii=False),
            "page_abstract": "; ".join(kp.get("content")[:80] for kp in key_points[:3]),
            "findings": key_points,
        })

    # 裁剪已处理页面的 content（减少 memory footprint）
    processed_urls = {s.get("url", "") for s in new_summaries if s.get("url")}
    trimmed_pages = [
        {k: v for k, v in p.items() if k != "content"}
        if p.get("url") in processed_urls else p
        for p in pages
    ]

    return {
        "page_summaries": state.get("page_summaries", []) + new_summaries,
        "scraped_pages": trimmed_pages,
    }


# ── Node 9: dedup ──────────────────────────────────────────────────

def dedup_rerank_node(state: AgentState) -> dict[str, Any]:
    """增量合并去重+重排：新 findings 嵌入去重 + 增量 CrossEncoder，旧 findings 保留已有 score。"""
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
        return {"findings": state.get("findings", [])}

    # 只嵌入新 findings
    new_embs = embed_text(batch)

    existing = state.get("findings", [])
    existing_embs = state.get("_findings_embeddings", [])

    # 去重：新 findings vs 缓存嵌入
    new_findings: list[dict[str, Any]] = []
    kept_embs: list[list[float]] = []
    for i, (si, fi) in enumerate(batch_meta):
        emb = new_embs[i]
        if existing_embs and max_cosine_similarity(emb, existing_embs) >= SEMANTIC_DUP_THRESHOLD:
            continue
        f = dict(summaries[si]["findings"][fi])
        f["source_url"] = summaries[si].get("url", "")
        new_findings.append(f)
        kept_embs.append(emb)

    # CrossEncoder 只过新 findings（增量打分）
    query = state.get("resolved_query", state["query"])
    if new_findings:
        scored_new = rank_findings(query, new_findings, top_k=15)
        scored_new = [f for f in scored_new if f.get("score", 0) >= 0.6]
    else:
        scored_new = []

    # 合并：旧 findings 保留上轮 score，新 findings 已有本轮 score
    merged = existing + scored_new
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)
    merged = merged[:15]

    merged_embeddings = (
        embed_text([f.get("content", "") for f in merged])
        if merged else []
    )

    return {"findings": merged, "_findings_embeddings": merged_embeddings}


# ── Node 11: assess ─────────────────────────────────────────────────

async def assess_node(state: AgentState) -> dict[str, Any]:
    """搜索结果质量评估 + 冲突检测。大纲已移至 report_node。"""
    logger.debug(f"assess_node deep_search_count={state.get('deep_search_count', 0)}")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    deep_count = state.get("deep_search_count", 0)

    details_text = "\n".join(
        f"- [{f.get('topic', 'general')}] {f.get('content', '')[:300]}"
        for f in findings[:15]
    ) if findings else "暂无发现"

    result = await chat_json_async([
        {"role": "system", "content": "你是研究质量评估专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _ASSESS_PROMPT.format(
            search_count=deep_count,
            findings_count=len(findings),
            findings_detail=details_text or "暂无",
            query=query,
        )},
    ])

    logger.info(f"assess query_match={result.get('score_detail', {}).get('query_match', 0)}")

    llm_fallback = state.get("search_all_failed") and not state.get("findings", [])

    # ── 代码计算的硬指标 ──────────────────────────────────────────────
    score_detail = result.get("score_detail", {})
    query_match = score_detail.get("query_match", 20)

    # ③ 覆盖范围: 按 topic 去重计数
    topics = set(f.get("topic", "") for f in findings if f.get("topic"))
    scope_score = 20 if len(topics) >= 5 else (15 if len(topics) >= 3 else 5)

    # ④ 来源可信度: 统计高权威域名占比
    high_sources = 0
    total_sources = 0
    for f in findings:
        url = f.get("source_url", "")
        if url:
            total_sources += 1
            if get_source_tag(url) == "高":
                high_sources += 1
    credibility_ratio = high_sources / total_sources if total_sources else 0
    credibility_score = 15 if credibility_ratio >= 0.5 else (10 if credibility_ratio >= 0.25 else 5)

    # ⑤ 主题一致性: BGE 余弦相似度 vs query
    contents = [f.get("content", "")[:500] for f in findings if f.get("content")]
    off_topic = 0
    if contents:
        try:
            query_emb = embed_text(query)
            finding_embs = embed_text(contents)
            for emb in finding_embs:
                dot = sum(ai * bi for ai, bi in zip(query_emb, emb))
                nq = math.sqrt(sum(ai * ai for ai in query_emb))
                ne = math.sqrt(sum(bi * bi for bi in emb))
                sim = dot / (nq * ne + 1e-8)
                if sim < 0.4:
                    off_topic += 1
        except Exception:
            logger.exception("主题一致性计算失败，使用默认值 10")
    off_topic_ratio = off_topic / len(findings) if findings else 0
    consistency_score = 15 if off_topic_ratio < 0.1 else (10 if off_topic_ratio < 0.3 else 5)

    coverage_score = query_match + scope_score + credibility_score + consistency_score
    sufficient = (coverage_score >= 50) or (state.get("assess_round", 0) + 1 >= 3)
    logger.info(f"assess coverage={coverage_score} (L:{query_match}+S:{scope_score}+C:{credibility_score}+T:{consistency_score}) sufficient={sufficient}")

    score_detail.update({
        "query_match": query_match,
        "scope": scope_score,
        "credibility": credibility_score,
        "topic_consistency": consistency_score,
        "off_topic_count": off_topic,
    })

    return {
        "coverage_score": coverage_score,
        "assess_round": state.get("assess_round", 0) + 1,
        "_llm_fallback": llm_fallback,
        "sufficient": sufficient,
        "score_detail": score_detail,
    }


# ── Node 12: report ─────────────────────────────────────────────────

async def report_node(state: AgentState) -> dict[str, Any]:
    """统一流式报告生成。所有意图路径都流式输出，每轮生成独立内容。"""
    logger.debug(f"report_node intent={state.get('intent', '')}")
    intent = state.get("intent", "")
    query = state["query"]
    findings = state.get("findings", [])
    cb = stream_callback_var.get()
    existing_report = state.get("report", "")

    # refine_section 无搜索结果：基于已有知识直接回答
    if intent == "refine_section" and not findings:
        logger.info("refine_section 搜索无结果，使用 LLM 直接回答（基于已有知识）")
        response = await chat_async([
            {"role": "system", "content": "你是一个智能助手。搜索未返回新结果，请基于已有知识回答用户问题。"},
            {"role": "user", "content": query},
        ])
        return {"report": response, "turn_report": response, "sections": {"answer": response}, "_report_streamed": False}

    # _llm_fallback: 搜索全部失败，LLM 直答
    if state.get("_llm_fallback") and not findings:
        logger.info("使用 LLM 直接回答（搜索失败 fallback）")
        response = await chat_async([
            {"role": "system", "content": "你是一个智能助手。请直接回答用户问题。"},
            {"role": "user", "content": query},
        ])
        return {"report": response, "turn_report": response, "sections": {"answer": response}, "_report_streamed": False}

    # 正常路径：流式生成本轮内容
    if intent == "refine_section":
        topic = state.get("refine_section_name", query)
        outline = [{"section": topic, "points": []}]
        result = await generate_report_stream(query, outline, findings, on_token=cb)
        turn_report = result.get("report", "")
        sections = result.get("sections", {})
        outline_used = outline
    else:
        # deep_research
        outline = state.get("outline")
        if not outline:
            outline = await generate_outline(query, findings)
        result = await generate_report_stream(query, outline, findings, on_token=cb)
        turn_report = result.get("report", "")
        sections = result.get("sections", {})
        outline_used = outline

    # 构建累积报告（内部上下文用，不展示）
    if existing_report and turn_report:
        report = existing_report + "\n\n---\n\n" + turn_report
    else:
        report = turn_report

    # 追加参考资料段
    refs = []
    for i, f in enumerate(findings):
        url = f.get("source_url", "")
        tag = get_source_tag(url) if url else "未知"
        domain = urlparse(url).netloc if url else ""
        refs.append(f"[{i+1}] [{f.get('topic','')}]({url})（{tag} · {domain}）")
    if refs:
        ref_block = "\n\n---\n## 参考资料\n" + "\n".join(refs)
        turn_report += ref_block

    return {
        "report": report,
        "turn_report": turn_report,
        "sections": sections,
        "outline": outline_used,
        "_report_streamed": True,
    }


_L0_COMPRESS_PROMPT = """将以下用户问题和研究报告压缩为一句摘要（≤150字）。

用户问题：{query}

报告内容：
{report}

要求：
1. 摘要必须以"关于【原始问题】的研究发现："开头，明确包含用户问题的核心意图
   例如："关于ChatGPT与Claude功能对比的研究发现：Claude在长文本处理上更优..."
2. 再提炼报告中最关键的1-2个发现/结论
3. 中文，一句话，≤150字

返回 JSON：{{"summary": "..."}}
"""

async def _compress_l0(query: str, report: str) -> str:
    """LLM 压缩 query + report → ≤150 字 L0 摘要。"""
    if not report or len(report.strip()) < 50:
        return query[:100]
    try:
        result = await chat_json_async([
            {"role": "system", "content": "你是摘要专家。请用 JSON 格式回答。"},
            {"role": "user", "content": _L0_COMPRESS_PROMPT.format(
                query=query, report=report[:2000]
            )},
        ])
        summary = (result.get("summary") or query)[:100]
        logger.debug(f"_compress_l0 summary='{summary}'")
        return summary
    except Exception:
        logger.exception("L0 compress failed, fallback to query")
        return query[:100]


# ── Node 14: memory ─────────────────────────────────────────────────

async def memory_node(state: AgentState) -> dict[str, Any]:
    """持久化：research_tasks + chat_history + 长期记忆（L1）。"""
    logger.debug(f"memory_node task_id={state.get('task_id', 0)}")
    task_id = state.get("task_id", 0)
    if not task_id:
        return {}
    session_id = state["session_id"]
    query = state.get("resolved_query", state["query"])
    report = state.get("report", "")

    # ── 阶段 1：L0 压缩摘要 + 写表 ──
    l0 = await _compress_l0(query, report)
    update_task(task_id, status="completed", report=report, l0_summary=l0)

    turn_report = state.get("turn_report", report)
    try:
        insert_chat_message(session_id, "assistant", turn_report)
    except Exception:
        logger.exception("memory_node chat history write failed")

    # ── 阶段 2：写入长期记忆（L1 user/memories，仅 deep_research/refine_section）──
    if state.get("intent") in ("deep_research", "refine_section"):
        try:
            await _write_long_term_memory(session_id, task_id, report)
        except Exception:
            logger.exception("memory_node L1 memory write failed, skipped")

    return {"status": "completed"}


async def _write_long_term_memory(session_id: str, task_id: int, report: str) -> None:
    """写入长期记忆：从报告提取知识到 user/memories/。"""
    if not report or len(report.strip()) < 200:
        return
    await memory_update(session_id=session_id, task_id=task_id, report=report)


# ── Node 15: simple_llm ─────────────────────────────────────────────

async def simple_llm_node(state: AgentState) -> dict[str, Any]:
    """简单 LLM 回答，基于 L0 历史摘要。"""
    logger.debug(f"simple_llm_node query='{state.get('query', '')}'")
    query = state.get("query", "")
    session_id = state["session_id"]

    tasks = get_recent_tasks_by_session(session_id, limit=5)
    l0s = [t.get("l0_summary", "") for t in tasks if t.get("l0_summary")]
    history_text = "\n".join(f"- {s}" for s in l0s) if l0s else "暂无历史"

    response = await chat_async([
        {"role": "system", "content": "你是调研助手。"},
        {"role": "user", "content": _SIMPLE_LLM_PROMPT.format(
            history=history_text,
            query=query,
        )},
    ])

    return {"report": response, "status": "completed"}


# ── HITL 中断节点 ──────────────────────────────────────

def hitl_scope_node(state: AgentState) -> dict[str, Any]:
    """HITL 用户补充 — 中断等待用户选择维度 + 自由输入，然后重新规划。"""
    logger.debug(f"hitl_scope_node session_id='{state['session_id']}'")
    scope = state.get("scope", {})
    query = state.get("resolved_query", state.get("query", ""))
    dimensions = scope.get("suggested_dimensions", [])
    details = scope.get("details_to_add", [])
    resume_data = interrupt({
        "mode": "scope_select",
        "session_id": state["session_id"],
        "options": {
            "query": query,
            "dimensions": dimensions,
            "details_to_add": details,
        },
    })
    selected = resume_data.get("selectedDimensions", []) if isinstance(resume_data, dict) else []
    supplement = resume_data.get("user_supplement", "") if isinstance(resume_data, dict) else ""
    # 维度过滤
    original = state.get("sub_queries", [])
    if selected and original:
        filtered = [sq for sq in original if any(dim in sq for dim in selected)]
        sub_queries = filtered if filtered else original
    else:
        sub_queries = original
    return {"need_scope": False, "sub_queries": sub_queries, "user_supplement": supplement}




# ── 路由函数 ─────────────────────────────────────────────────────────

def route_by_intent(state: AgentState) -> str:
    """根据 intent 选择路径。deep 先 clarify，refine 直接 planner，simple 直答。"""
    intent = state.get("intent", "simple_llm")
    logger.debug(f"route_by_intent intent={intent}")
    if intent == "deep_research":
        return "clarify"
    if intent == "refine_section":
        return "planner"
    return "simple_llm"


def route_after_rerank(state: AgentState) -> str:
    """rerank 后分叉。refine_section 跳过 assess。deep_research 走 assess。"""
    intent = state.get("intent", "")
    logger.debug(f"route_after_rerank intent={intent}")
    if intent == "refine_section":
        return "report"
    return "assess"


def route_assess(state: AgentState) -> str:
    """assess 后决策：仅 deep_research 走搜索回环，其余路径此时不应进入 assess。"""
    intent = state.get("intent", "")
    coverage_score = state.get("coverage_score", 0)
    assess_round = state.get("assess_round", 0)
    sufficient = state.get("sufficient", False)
    logger.debug(f"route_assess intent={intent} coverage_score={coverage_score} assess_round={assess_round} sufficient={sufficient}")

    if state.get("_llm_fallback"):
        return "report"
    if sufficient:
        return "report"
    if intent != "deep_research":
        return "report"
    if coverage_score < 50 and assess_round < 3:
        return "search"
    return "report"
