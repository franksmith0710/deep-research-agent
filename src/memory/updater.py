"""记忆更新器：从报告中提取知识，双阈值去重（ADD/APPEND/UPDATE）。"""

from __future__ import annotations

import asyncio
from typing import Any

from src.fs import filesystem as fs
from src.utils.dedup import SEMANTIC_DUP_THRESHOLD
from src.fs.uri import VikingURI
from src.local_models.embedder import embed_text
from src.llm.client import chat_json_async
from src.logging_config import get_logger

logger = get_logger("memory_updater")

_UPDATE_THRESHOLD_LOW = 0.6

_EXTRACT_PROMPT = """你是一个记忆提取专家。从以下完整研究报告中提取可复用的结构化知识。

报告完整内容：
{report}

要求：
1. 提取 3-5 条跨时间仍有复用价值的核心知识（每条 ≤200 字）
2. 每条知识标注所属分类：
   - entities：实体知识（人物/公司/产品/概念/技术）
   - patterns：可复用结论/模式/最佳实践
3. 每条知识附带 2-5 个标签 tags，覆盖不同领域维度
   例如 ["量子计算", "纠错码"]、["深度学习", "Transformer"]、["生物医药", "基因编辑"]
4. 保留原文中的数字、日期、人名等关键信息
5. 每条知识附带原文中提及的来源 URL（如果有）
6. 只写事实，不添加评价或推测
7. 不要提取时效性信息：新闻快讯、股价行情、天气预报、游戏版本攻略、体育赛事结果、短期政策解读、产品发布公告等具有明确时间窗口的内容

返回 JSON：
{{
    "knowledge": [
        {{
            "category": "entities|patterns",
            "name": "实体或模式名称，如 surface_code、transformers架构、CRISPR基因编辑",
            "content": "结构化知识正文",
            "source_urls": ["https://..."],
            "tags": ["量子计算", "纠错码"]
        }}
    ]
}}
"""


async def extract_knowledge(report: str) -> list[dict[str, Any]]:
    """LLM 从报告中提取可复用知识（并行分块提取 + 去重）。"""
    if not report or len(report.strip()) < 100:
        return []

    CHUNK_SIZE = 40000
    MAX_CHUNKS = 3
    chunks = [report[i:i+CHUNK_SIZE] for i in range(0, len(report), CHUNK_SIZE)][:MAX_CHUNKS]

    async def _extract_chunk(chunk: str) -> list[dict[str, Any]]:
        result = await chat_json_async([
            {"role": "system", "content": "你是记忆提取专家。请用 JSON 格式回答。"},
            {"role": "user", "content": _EXTRACT_PROMPT.format(report=chunk)},
        ])
        return result.get("knowledge", [])

    results = await asyncio.gather(*[_extract_chunk(c) for c in chunks])

    seen_names = set()
    all_knowledge = []
    for items in results:
        for item in items:
            name = item.get("name", "").strip().lower().replace(" ", "_").replace("/", "_")
            if name and name not in seen_names:
                seen_names.add(name)
                all_knowledge.append(item)
    return all_knowledge[:5]


def _names_overlap(a: str, b: str) -> bool:
    """检查两个 name 是否有字面重叠。无 name 时保守允许合并。"""
    if not a or not b:
        return True
    return a in b or b in a


def _search_similar(content: str, category: str, limit: int = 3) -> list[dict[str, Any]]:
    """在对应分类下搜索语义相似的已有记忆。"""
    vec = embed_text(content)
    root = f"viking://user/memories/{category}"
    results = fs.search(vec, root_uri=root, limit=limit, min_score=0.0)
    return results


async def update_memory(
    session_id: str,
    task_id: int,
    report: str,
) -> None:
    """两阶段记忆更新：从报告提取知识 → 双阈值去重写入。"""
    knowledge = await extract_knowledge(report)
    if not knowledge:
        logger.info("memory_updater: 无可提取的知识")
        return

    for item in knowledge:
        content = item.get("content", "").strip()
        if not content:
            continue
        name = item.get("name", "").strip().lower().replace(" ", "_").replace("/", "_") or "general"
        category = item.get("category", "entities")
        source_urls = item.get("source_urls", [])
        source_url = source_urls[0] if source_urls else None

        # 搜索已有记忆
        similar = _search_similar(content, category, limit=3)

        if not similar:
            # ADD: 全新知识
            _do_add(session_id, task_id, content, name, category, source_url)
            continue

        best = similar[0]
        best_score = best.get("_score", best.get("similarity", 0.0)) or 0.0
        best_uri = best.get("uri", "")
        best_name = best.get("name", "").strip().lower().replace(" ", "_").replace("/", "_")

        # 主题名无字面重叠 → 跨主题误匹配，降级为 ADD
        if not _names_overlap(name, best_name):
            _do_add(session_id, task_id, content, name, category, source_url)
            logger.debug(f"ADD (topic_mismatch) existing={best_name} new={name} score={best_score:.3f}")
            continue

        if best_score >= SEMANTIC_DUP_THRESHOLD:
            # UPDATE: 直接覆盖
            _do_update(best_uri, content, source_url)
            logger.debug(f"UPDATE {best_uri} (score={best_score:.3f})")
        elif best_score >= _UPDATE_THRESHOLD_LOW:
            # APPEND: 新旧拼接
            existing_content = fs.read(best_uri) or ""
            merged = existing_content + "\n\n" + content
            _do_update(best_uri, merged, source_url)
            logger.debug(f"APPEND {best_uri} (score={best_score:.3f})")
        else:
            # INSERT: 低相似度，写新条目
            _do_add(session_id, task_id, content, name, category, source_url)

def _do_add(
    session_id: str,
    task_id: int,
    content: str,
    name: str,
    category: str,
    source_url: str | None,
) -> None:
    """插入新知识节点。"""
    base_uri = f"viking://user/memories/{category}/{name}"
    fs.ensure_dir(f"viking://user/memories/{category}")

    existing = fs.read(base_uri)
    if existing:
        base_uri = f"{base_uri}_{task_id}"

    vec = embed_text(content)
    abstract_text = _make_abstract(content)
    fs.write(
        uri=base_uri,
        content=content,
        abstract_text=abstract_text,
        level="L1",
        context_type="user_memory",
        source_url=source_url,
        embedding=vec,
        metadata={"session_id": session_id, "task_id": task_id},
    )
    logger.debug(f"ADD {base_uri}")


def _do_update(uri: str, content: str, source_url: str | None = None) -> None:
    """更新已有节点。"""
    vec = embed_text(content)
    abstract_text = _make_abstract(content)
    vuri = VikingURI.parse(uri)
    fs.write(
        uri=uri,
        content=content,
        abstract_text=abstract_text,
        level="L1",
        context_type="user_memory",
        source_url=source_url or None,
        embedding=vec,
    )


def _make_abstract(content: str, max_len: int = 100) -> str:
    """从正文中提取 L0 摘要（截取首句）。"""
    first_line = content.split("\n")[0].strip()
    if len(first_line) > max_len:
        return first_line[:max_len] + "..."
    return first_line



