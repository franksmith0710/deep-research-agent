"""LangGraph 节点：15 个节点 + intent 4 分支逻辑 + 3 HITL 中断节点。"""

from __future__ import annotations

import contextvars
import json
from typing import Any

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
from src.search.duckduckgo import DuckDuckGoSearch
from src.search.scraper import Scraper
from src.page_brief.brief import brief_from_page
from src.synthesize.ranker import rank_findings
from src.local_models.reranker import rerank, release_gpu as _release_reranker
from src.report.writer import generate_outline, generate_report_stream
from src.memory.retriever import retrieve_l1
from src.memory.updater import update_memory as memory_update
from src.memory.credibility import update_source_credibility, get_credibility, get_source_tag
from src.db.postgres import (
    update_task, insert_chat_message,
    get_recent_tasks_by_session,
)
from src.local_models.embedder import embed_text, release_gpu as _release_embedder
from src.utils.dedup import max_cosine_similarity, SEMANTIC_DUP_THRESHOLD

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

返回 JSON：{{"related_indices": [条目索引, ...], "summary": "历史信息摘要"}}
"""

_ASSESS_PROMPT = """你是研究覆盖度评估专家。请依次完成以下三个职责。

## 职责一：大纲规划

根据已有 findings 实际覆盖的维度生成 4-8 章大纲，每章 2-4 个要点。
如果已有大纲，判断是否仍然合理；合理则复用，不合理才重写。
**无论是否复用，都必须输出当前最合理的大纲。**

## 职责二：量化覆盖度评估

按以下四个维度计算总分（满分 100）。注意：每个维度的分数**必须从给定值中选择，不得使用其他数值**。

① 用户问题匹配度 (0-30)
判断 findings 整体上回答用户查询的完整度：
- 查询所有关键方面都有直接回答 → 30
- 主要方面已回答，少数细节点不足 → 20
- 回答了部分方面，有明显缺口 → 10
- 发现内容与查询关联度低 → 5
**必须从 [30, 20, 10, 5] 中选择，不得使用其他数值。**

② 内容-大纲匹配度 (0-25)
逐章检查每一条 findings 是否支撑某一章节：
- 统计**有直接支撑的章节数**（≥1 条相关 finding 即算），记作 covered
- 统计总章节数，记作 total
- 输出时不要自行计算分数，只需将 covered 章节数填入 score_detail.outline_covered，total 章节数填入 score_detail.outline_total
- 路由将按公式计算：25 × outline_covered / outline_total

③ 覆盖范围 (0-25)
按 findings 去重后的独特 topic 维度数量：
- ≥ 5 个独特维度 → 25
- 3-4 个 → 15
- 1-2 个 → 5
**必须从 [25, 15, 5] 中选择，不得使用其他数值。**

④ 来源可信度 (0-20)
- ≥ 50% 来自高权威域名（学术/官方/主流媒体/行业权威）→ 20
- ≥ 25% → 15
- < 25% → 5
**必须从 [20, 15, 5] 中选择，不得使用其他数值。**

总分 = ① + ③ + ④ + (25 × covered / total)
coverage_score = 总分
sufficient = (总分 ≥ 60) 或 (当前轮次 ≥ 3)
**如果轮次 ≥ 3，必须设置 sufficient=true。**

## 职责三：缺口处理（仅在 sufficient=false 时执行）

1. 定位缺口章节：在 gaps 中列出无直接支撑的章节名
2. 优先翻查"已有已抓取页面概况"能否填补缺口：
   - 能 → 填入 gap_fillable_pages，**info_needed 必须写具体缺失的信息方向（如"包含 ChatGPT 响应速度的评测数据"），不得写模糊描述**
   - need_re_extract=true
3. 无可用页面 → 为缺口章节生成 new_sub_queries（每章 1-2 条）

## 当前输入

已有大纲：
{outline_text}

当前轮次：第 {search_count} 轮（最多 3 轮）
已收集发现数：{findings_count}
覆盖的维度：{dimensions}

已有发现（详细内容）：
{findings_detail}

已有已抓取页面概况：
{pages}

用户查询：{query}

## 输出 JSON 格式

{{
    "outline": [{{"section": "章节名", "points": ["要点1", ...]}}, ...],
    "coverage_score": 0-100,
    "score_detail": {{
        "query_match": ①分数,
        "outline_covered": ②covered章节数,
        "outline_total": ②总章节数,
        "scope": ③分数,
        "credibility": ④分数
    }},
    "sufficient": true/false,
    "reason": "判断依据",
    "gaps": ["缺口章节1", ...],
    "gap_fillable_pages": [{{"url": "...", "info_needed": "具体缺失信息", "target_section": "..."}}],
    "need_re_extract": true/false,
    "new_sub_queries": ["搜索查询", ...],
    "has_conflict": false,
    "conflict_description": ""
}}
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
    """检查历史记忆：检索长期记忆（user/memories）并合并到 findings。"""
    logger.debug(f"check_history_node resolved_query='{state.get('resolved_query', '')}'")
    query = state.get("resolved_query", state["query"])
    memories = retrieve_l1(query, limit=settings.memory_retrieval_limit)
    existing = state.get("findings", [])
    if not memories:
        return {"findings": existing}

    memory_text = "\n".join(
        f"[{i}] {m.get('content', '')[:200]}"
        for i, m in enumerate(memories)
    )
    result = chat_json([
        {"role": "system", "content": "你是记忆检索专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _CHECK_HISTORY_PROMPT.format(
            query=query, memory_items=memory_text
        )},
    ])
    related_indices = result.get("related_indices", [])
    if not related_indices:
        return {"findings": existing}

    historical_findings = []
    for idx in related_indices:
        if 0 <= idx < len(memories):
            m = memories[idx]
            historical_findings.append({
                "content": m.get("content", ""),
                "source_url": m.get("source_url", ""),
                "topic": m.get("name", "history"),
            })
    return {"findings": existing + historical_findings}


# ── Node 4: clarify ──────────────────────────────────────────────────

def clarify_node(state: AgentState) -> dict[str, Any]:
    """判断是否需要用户补充范围（在搜索之前，基于查询本身判断范围是否明确）。"""
    logger.debug(f"clarify_node resolved_query='{state.get('resolved_query', '')}'")
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
    all_results: list[dict[str, Any]] = []

    def _try_engine(engine, label: str) -> bool:
        nonlocal all_results
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(5, len(sub_queries))) as pool:
            fut_map = {pool.submit(_search_one_engine, sq, engine): sq for sq in sub_queries}
            for future in as_completed(fut_map):
                sq = fut_map[future]
                try:
                    results.extend(future.result())
                except RuntimeError as e:
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
            scraped.append(future.result())

    scrape_errors = sum(1 for s in scraped if not s["success"])
    logger.info(f"scrape_node: 共 {len(scraped)} 页，失败 {scrape_errors} 页（已 fallback {len(scraped) - scrape_errors} 页）")
    return {"scraped_pages": scraped}


# ── Node 8: context_mgr ─────────────────────────────────────────────

def context_mgr_node(state: AgentState) -> dict[str, Any]:
    """上下文管理：提取关键要点并检查同话题重复。跳过过短页面。"""
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
        page_query = state.get("resolved_query", state.get("query", ""))
        if page_query:
            judge_text = (page.get("snippet", "") + " " + content[:300]).strip()
            scores = rerank(page_query, [judge_text], top_k=1)
            if scores and scores[0][1] < 0.2:
                logger.debug(f"页面不相关，跳过提取 url={page['url']} score={scores[0][1]:.4f}")
                return None
        brief = brief_from_page(page.get("title", ""), content)
        key_points = brief.get("key_points", [])
        topic_list = list(set(kp.get("topic", "") for kp in key_points if kp.get("topic")))
        return {
            "url": page["url"],
            "title": page.get("title", ""),
            "topic": topic_list[0] if topic_list else "",
            "key_points_json": json.dumps(key_points, ensure_ascii=False),
            "page_abstract": "; ".join(kp.get("content")[:80] for kp in key_points[:3]),
            "findings": key_points,
        }

    processed_urls = {s.get("url", "") for s in state.get("page_summaries", []) if s.get("url")}
    eligible = [p for p in pages if p.get("success") and p.get("content") and len(p["content"]) >= 100 and p.get("url", "") not in processed_urls]
    with ThreadPoolExecutor(max_workers=5) as pool:
        fut_map = {pool.submit(_extract_page, p): p for p in eligible}
        for future in as_completed(fut_map):
            result = future.result()
            if result:
                new_summaries.append(result)

    return {"page_summaries": state.get("page_summaries", []) + new_summaries}


# ── Node 9: dedup ──────────────────────────────────────────────────

def dedup_rerank_node(state: AgentState) -> dict[str, Any]:
    """合并去重+重排：将 page_summaries 中的 findings 合并到 state.findings，再 dedup + rerank。"""
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

    all_findings: list[dict[str, Any]] = []
    for i, (si, fi) in enumerate(batch_meta):
        emb = embeddings[i]
        # 轮内去重：检查与当前 session 已有 findings 的语义相似度
        if existing_embs and max_cosine_similarity(emb, existing_embs) >= SEMANTIC_DUP_THRESHOLD:
            continue
        f = dict(summaries[si]["findings"][fi])
        f["source_url"] = summaries[si].get("url", "")
        all_findings.append(f)

    merged = existing + all_findings

    query = state.get("resolved_query", state["query"])
    if merged:
        ranked = rank_findings(query, merged, top_k=15)
        ranked = [r for r in ranked if r.get("score", 0) >= 0.3]
        logger.debug(f"dedup_rerank_node after threshold={len(ranked)}")
        return {"findings": ranked}
    return {"findings": merged}


# ── Node 11: assess ─────────────────────────────────────────────────

def assess_node(state: AgentState) -> dict[str, Any]:
    """动态大纲 + 逐章覆盖度评估 + 冲突检测。"""
    logger.debug(f"assess_node deep_search_count={state.get('deep_search_count', 0)}")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    deep_count = state.get("deep_search_count", 0)

    dimensions = list(set(f.get("topic", "") for f in findings if f.get("topic")))
    extract_text = "\n".join(
        f"- [{f.get('topic', 'general')}] {f.get('content', '')[:100]}"
        for f in findings[:10]
    ) if findings else "暂无发现"

    details_text = "\n".join(
        f"- [{f.get('topic', 'general')}] {f.get('content', '')[:300]}"
        for f in findings[:15]
    ) if findings else "暂无发现"

    scraped_pages = state.get("scraped_pages", [])
    pages_text = "\n".join(
        f"- {p.get('title', '')} | 来源: {p.get('url', '')} | 长度: {len(p.get('content', ''))} 字"
        for p in scraped_pages if p.get("success") and p.get("content")
    ) if scraped_pages else "暂无已抓取页面"
    
    # 处理已有大纲：格式化或标注为空
    existing_outline = state.get("outline", [])
    if existing_outline:
        outline_lines = []
        for i, o in enumerate(existing_outline, 1):
            points = ", ".join(o.get("points", []))
            outline_lines.append(f"[{i}] {o.get('section', '')}: {points}")
        outline_text = "\n".join(outline_lines)
    else:
        outline_text = "暂无大纲，请根据查询主题生成"
    
    result = chat_json([
        {"role": "system", "content": "你是评估专家。请用 JSON 格式回答。"},
        {"role": "user", "content": _ASSESS_PROMPT.format(
            outline_text=outline_text,
            search_count=deep_count,
            findings_count=len(findings),
            dimensions=", ".join(dimensions) if dimensions else "暂无",
            extracts=extract_text or "暂无",
            findings_detail=details_text or "暂无",
            pages=pages_text or "暂无",
            query=query,
        )},
    ])
    
    logger.info(f"assess coverage={result.get('coverage_score', 0)} detail={result.get('score_detail', {})} sufficient={result.get('sufficient', False)}")

    # 解析大纲（新生成的或更新后的）
    outline = result.get("outline", existing_outline) or existing_outline
    
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
            page_by_url = {p.get("url", ""): p for p in state.get("scraped_pages", []) if p.get("success") and p.get("content")}
            conflict_contents = []
            for cf in conflict_findings[:3]:
                url = cf["url"]
                page = page_by_url.get(url)
                if page and page.get("content"):
                    conflict_contents.append(
                        f"来源: {url}\n"
                        f"可信度: {cf['credibility_score']}/100（{cf['credibility_tag']}）\n"
                        f"标题: {page.get('title', '')}\n"
                        f"内容:\n{page['content'][:2000]}"
                    )
                else:
                    conflict_contents.append(
                        f"来源: {url}\n"
                        f"可信度: {cf['credibility_score']}/100（{cf['credibility_tag']}）\n"
                        f"（无全文缓存，使用摘要信息）\n"
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

    coverage_score = result.get("coverage_score", 0)

    llm_fallback = state.get("search_all_failed") and not state.get("findings", [])

    return {
        "outline": outline,
        "findings": state.get("findings", []),
        "coverage_score": coverage_score,
        "has_conflict": has_conflict,
        "conflict_description": conflict_description,
        "sub_queries": new_sub if new_sub else state.get("sub_queries", []),
        "assess_round": state.get("assess_round", 0) + 1,
        "_llm_fallback": llm_fallback,
        "sufficient": result.get("sufficient", False),
        "score_detail": result.get("score_detail", {}),
        "gaps": result.get("gaps", []),
    }


# ── Node 12: synthesize ─────────────────────────────────────────────

def synthesize_node(state: AgentState) -> dict[str, Any]:
    """准备 findings 和话题。LLM 内容生成已移到 report_node。"""
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
        return {"sections": {}}

    refine_name = state.get("refine_section_name", "")
    intent = state.get("intent", "")
    if refine_name and intent == "refine_section":
        topic = refine_name
    elif intent == "new_search_topic":
        topic = state.get("resolved_query", state["query"])[:100]
    else:
        topic = state.get("resolved_query", state["query"])[:100]

    return {"turn_topic": topic}


# ── Node 13: report ─────────────────────────────────────────────────

def _build_footnotes(state: AgentState) -> str:
    """从 scraped_pages 构建引用脚注（含 snippet 预览）。"""
    from src.memory.credibility import get_source_tag

    seen = set()
    notes = []
    for p in state.get("scraped_pages", []):
        url = p.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)

        tag = get_source_tag(url)
        title = p.get("title", "") or ""
        snippet = (p.get("content", "") or "")[:200]

        if snippet:
            notes.append(f"- [{tag}] **{title}**\n  {snippet}\n  [{url}]")
        else:
            notes.append(f"- [{tag}] {url}")

    if not notes:
        return ""
    body = "\n".join(notes)
    return f"<details><summary>📚 来源（{len(notes)} 个）</summary>\n\n{body}\n\n</details>"


async def report_node(state: AgentState) -> dict[str, Any]:
    """统一流式报告生成。所有意图路径都流式输出，每轮生成独立内容。"""
    logger.debug(f"report_node intent={state.get('intent', '')}")
    intent = state.get("intent", "")
    query = state.get("resolved_query", state["query"])
    findings = state.get("findings", [])
    cb = stream_callback_var.get()
    existing_report = state.get("report", "")

    # _llm_fallback: 内容已在 synthesize_node 中生成，直接使用
    sections_from_synth = state.get("sections", {})
    if state.get("_llm_fallback") and not findings and sections_from_synth:
        turn_report = "\n\n".join(v for v in sections_from_synth.values() if v)
        report = turn_report
        return {
            "report": report,
            "turn_report": turn_report,
            "sections": sections_from_synth,
            "_report_streamed": False,
        }

    # 正常路径：流式生成本轮内容
    if intent in ("refine_section", "new_search_topic"):
        topic = state.get("refine_section_name", query)
        outline = [{"section": topic, "points": []}]
        footnotes = _build_footnotes(state)
        result = await generate_report_stream(query, outline, findings, on_token=cb)
        turn_report = result.get("report", "")
        if footnotes:
            await cb(footnotes) if cb else None
            turn_report += footnotes
        sections = result.get("sections", {})
        outline_used = outline
    else:
        # deep_research
        outline = state.get("outline")
        if not outline:
            outline = generate_outline(query)
        footnotes = _build_footnotes(state)
        result = await generate_report_stream(query, outline, findings, on_token=cb)
        turn_report = result.get("report", "")
        if footnotes:
            await cb(footnotes) if cb else None
            turn_report += footnotes
        sections = result.get("sections", {})
        outline_used = outline

    # 构建累积报告（内部上下文用，不展示）
    if existing_report and turn_report:
        report = existing_report + "\n\n---\n\n" + turn_report
    else:
        report = turn_report

    return {
        "report": report,
        "turn_report": turn_report,
        "sections": sections,
        "outline": outline_used,
        "_report_streamed": True,
    }


_L0_COMPRESS_PROMPT = """将以下用户问题和研究报告压缩为一句摘要（≤100字）。

用户问题：{query}

报告内容：
{report}

要求：
1. 保留用户问题的核心意图
2. 提取报告中最关键的发现/结论
3. 一句话，中文，≤100字

返回 JSON：{{"summary": "..."}}
"""

def _compress_l0(query: str, report: str) -> str:
    """LLM 压缩 query + report → ≤100 字 L0 摘要。"""
    if not report or len(report.strip()) < 50:
        return query[:100]
    try:
        result = chat_json([
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

def memory_node(state: AgentState) -> dict[str, Any]:
    """持久化：research_tasks + chat_history + 长期记忆（L1）。"""
    logger.debug(f"memory_node task_id={state.get('task_id', 0)}")
    task_id = state.get("task_id", 0)
    if not task_id:
        return {}
    session_id = state["session_id"]
    query = state.get("resolved_query", state["query"])
    report = state.get("report", "")

    # ── 阶段 1：L0 压缩摘要 + 写表 ──
    l0 = _compress_l0(query, report)
    update_task(task_id, status="completed", report=report, l0_summary=l0)

    turn_report = state.get("turn_report", report)
    try:
        insert_chat_message(session_id, "assistant", turn_report)
    except Exception:
        logger.exception("memory_node chat history write failed")

    # ── 阶段 2：写入长期记忆（L1 user/memories）──
    _write_long_term_memory(session_id, task_id, report)

    return {"status": "completed"}


def _write_long_term_memory(session_id: str, task_id: int, report: str) -> None:
    """写入长期记忆：从报告提取知识到 user/memories/。"""
    if not report or len(report.strip()) < 200:
        return
    memory_update(session_id=session_id, task_id=task_id, report=report)


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
    """轻量版持久化：仅写 chat_history，不写入长期记忆。"""
    try:
        insert_chat_message(state["session_id"], "assistant", state.get("report", ""))
    except Exception:
        logger.exception("memory_llm_node chat history failed")
    return {"status": "completed"}


# ── HITL 中断节点（Nodes 17-19）──────────────────────────────────────

def hitl_scope_node(state: AgentState) -> dict[str, Any]:
    """HITL 范围选择 — 中断等待用户选择调研维度。选择后过滤子查询避免重复规划。"""
    logger.debug(f"hitl_scope_node session_id='{state['session_id']}'")
    dimensions = state.get("scope", {}).get("suggested_dimensions", [])
    resume_data = interrupt({
        "mode": "scope_select",
        "session_id": state["session_id"],
        "options": {"dimensions": dimensions},
    })
    selected = resume_data.get("selectedDimensions", []) if isinstance(resume_data, dict) else []
    # 根据选择的维度过滤已有子查询，避免重新规划浪费一轮搜索
    original = state.get("sub_queries", [])
    if selected and original:
        filtered = [sq for sq in original if any(dim in sq for dim in selected)]
        sub_queries = filtered if filtered else original
    else:
        sub_queries = original
    return {"need_scope": False, "sub_queries": sub_queries}


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



# ── 路由函数 ─────────────────────────────────────────────────────────

def route_after_planner(state: AgentState) -> str:
    """planner 后分叉：refine 跳过 clarify 直接搜索，deep_research 走 clarify。"""
    intent = state.get("intent", "")
    if intent == "refine_section":
        return "search"
    if state.get("need_adjust", False):
        return "hitl_adjust"
    return "clarify"


def route_by_intent(state: AgentState) -> str:
    """根据 intent 选择路径。refine 也走 check_history → planner 生成新子查询。"""
    intent = state.get("intent", "simple_llm")
    logger.debug(f"route_by_intent intent={intent}")
    if intent in ("deep_research", "refine_section", "new_search_topic"):
        return "check_history"
    else:
        return "simple_llm"


def route_after_rerank(state: AgentState) -> str:
    """rerank 后分叉。refine_section / new_search_topic 跳过 assess 直连 synthesize。"""
    intent = state.get("intent", "")
    logger.debug(f"route_after_rerank intent={intent}")
    if intent in ("refine_section", "new_search_topic"):
        return "synthesize"
    return "assess"


def route_assess(state: AgentState) -> str:
    """assess 后决策：仅 deep_research 走搜索回环，其余路径此时不应进入 assess。"""
    intent = state.get("intent", "")
    coverage_score = state.get("coverage_score", 0)
    assess_round = state.get("assess_round", 0)
    has_conflict = state.get("has_conflict", False)
    sufficient = state.get("sufficient", False)
    logger.debug(f"route_assess intent={intent} coverage_score={coverage_score} assess_round={assess_round} has_conflict={has_conflict} sufficient={sufficient}")

    if has_conflict:
        return "hitl_conflict"
    if state.get("_llm_fallback"):
        return "synthesize"
    if sufficient:
        return "synthesize"
    if intent != "deep_research":
        return "synthesize"
    if coverage_score < 60 and assess_round < 3:
        return "search"
    return "synthesize"
