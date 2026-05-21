"""API 路由：会话管理 + 研究请求 + HITL 回调 + SSE 流 + interrupt 恢复。"""

from __future__ import annotations

import asyncio
import contextvars
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from src.models import ResearchRequest, HITLRequest
from src.db.postgres import (
    init_pool, get_all_sessions, get_task_by_session,
    get_chat_history, create_task, insert_chat_message,
    update_task, load_task_context, delete_session_data,
)
from src.agent.state import make_initial_state
from src.agent.graph import build_graph
from src.api.sse_manager import SSEManager
from src.api.contexts import stream_callback_var
from src.logging_config import get_logger

logger = get_logger("routes")

router = APIRouter()

# 全局 graph 实例
_graph = None

# 中断会话跟踪：session_id → {sse, config}
_interrupted_sessions: dict[str, dict[str, Any]] = {}

# 每会话的排队列
_research_locks: dict[str, asyncio.Lock] = {}

# 正在 resume 中的会话，防止并发重复 resume
_resuming_sessions: set[str] = set()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _get_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _research_locks:
        _research_locks[session_id] = asyncio.Lock()
    return _research_locks[session_id]


_NODE_DESCRIPTIONS = {
    "resolve_context": "正在理解你的问题",
    "intent_classifier": "正在分析研究意图",
    "check_history": "正在检索历史记忆",
    "clarify": "正在判断是否需要补充信息",
    "planner": "正在规划搜索方向",
    "search": "正在搜索相关信息...",
    "scrape": "正在抓取网页内容",
    "context_mgr": "正在整理提炼内容",
    "dedup": "正在去重检查",
    "rerank": "正在排序筛选",
    "assess": "正在评估覆盖度",
    "synthesize": "正在综合生成内容",
    "report": "正在生成研究报告",
    "memory": "正在保存研究结果",
    "simple_llm": "正在直接回答问题",
}


async def _put_readable_chain(sse: SSEManager, node_name: str) -> None:
    desc = _NODE_DESCRIPTIONS.get(node_name, f"正在执行: {node_name}")
    await sse.put_chain("thought", node_name, desc)


def _detect_interrupt(node_output: Any) -> dict | None:
    """检查节点输出是否包含 LangGraph __interrupt__。"""
    if isinstance(node_output, dict) and "__interrupt__" in node_output:
        items = node_output["__interrupt__"]
        if items and len(items) > 0:
            int_item = items[0]
            if hasattr(int_item, "value"):
                return int_item.value
    return None


def _push_final_events(sse: SSEManager, final_data: dict[str, Any], is_first: bool) -> None:
    """推送最终报告/章节事件，确保 text/patch 互斥。"""
    report = final_data.get("report", "")
    sections = final_data.get("sections", {})

    if is_first:
        if report:
            return report
    else:
        for section_name, section_content in sections.items():
            if section_content:
                return None  # Will send patch

    return report


@router.on_event("startup")
async def startup():
    init_pool()


@router.get("/api/sessions")
async def list_sessions():
    sessions = get_all_sessions()
    return {"sessions": sessions}


@router.get("/api/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    messages = get_chat_history(session_id)
    task = get_task_by_session(session_id)
    return {
        "messages": messages,
        "report": task.get("report", "") if task else None,
        "report_sections": [],
    }


@router.post("/api/sessions")
async def create_session():
    import uuid
    session_id = str(uuid.uuid4())[:8]
    return {"session_id": session_id}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    delete_session_data(session_id)
    return {"status": "deleted", "session_id": session_id}


@router.post("/api/research")
async def start_research(req: ResearchRequest):
    """启动研究：复用已有 task_id / 创建新 task，运行 LangGraph。"""
    logger.info(f"Research started session_id={req.session_id} query='{req.query[:50]}...'")
    lock = _get_lock(req.session_id)
    if lock.locked():
        logger.warning(f"Session {req.session_id} busy, returning 429")
        raise HTTPException(status_code=429, detail="该会话正在处理中，请等待")
    await lock.acquire()

    ctx = load_task_context(req.session_id)
    if ctx:
        task_id = ctx["task_id"]
        update_task(task_id, status="running")
    else:
        task_id = create_task(req.session_id, req.query)

    insert_chat_message(req.session_id, "user", req.query)

    sse = SSEManager()
    state = make_initial_state(req.session_id, task_id, req.query)

    if ctx:
        state["report"] = ctx["report"]
        state["sections"] = ctx["sections"]
        state["outline"] = ctx["outline"]
        state["deep_search_count"] = ctx["deep_search_count"]

    asyncio.create_task(_run_agent(state, sse, lock, ctx))

    return sse.get_response()


async def _stream_text(sse: SSEManager, text: str, chunk_size: int = 100, delay: float = 0.005) -> None:
    for i in range(0, len(text), chunk_size):
        await sse.put_text(text[i:i + chunk_size])
        await asyncio.sleep(delay)


async def _run_agent(state: dict[str, Any], sse: SSEManager, lock: asyncio.Lock, ctx: dict[str, Any] | None):
    """异步运行 Agent，检测 interrupt 后挂起等待 HITL 回调。"""
    session_id = state["session_id"]
    is_first = ctx is None
    _interrupted_sessions[session_id] = {"sse": sse, "ctx": ctx, "lock": lock}

    graph = get_graph()
    config = {
        "configurable": {"thread_id": session_id},
        "metadata": {
            "session_id": session_id,
            "query": state["query"][:100],
            "intent": state.get("intent", "unknown"),
            "is_resume": ctx is not None,
        },
        "tags": ["deep_research"],
    }
    _interrupted_sessions[session_id]["config"] = config

    stream_callback_var.set(lambda token: asyncio.create_task(sse.put_text(token)))

    hitl_triggered = False
    try:
        async for event in graph.astream(state, config):
            if "__interrupt__" in event:
                items = event["__interrupt__"]
                if items and len(items) > 0:
                    int_item = items[0]
                    payload = int_item.value if hasattr(int_item, "value") else int_item
                    logger.info(f"HITL interrupt session_id={session_id} mode={payload.get('mode')}")
                    await sse.put_hitl(
                        mode=payload.get("mode", "scope_select"),
                        session_id=session_id,
                        options=payload.get("options", {}),
                    )
                    hitl_triggered = True
                    return

            for node_name, node_output in event.items():
                if node_name == "__interrupt__":
                    continue
                if node_output:
                    interrupt_data = _detect_interrupt(node_output)
                    if interrupt_data:
                        logger.info(f"HITL interrupt (nested) session_id={session_id} mode={interrupt_data.get('mode')}")
                        await sse.put_hitl(
                            mode=interrupt_data.get("mode", "scope_select"),
                            session_id=session_id,
                            options=interrupt_data.get("options", {}),
                        )
                        hitl_triggered = True
                        return
                    await _put_readable_chain(sse, node_name)

        final = graph.get_state(config)
        final_data = final.values if final else {}

        report = final_data.get("report", "")
        sections = final_data.get("sections", {})
        report_streamed = final_data.get("_report_streamed", False)

        logger.info(f"Agent run completed session_id={session_id}")

        if is_first:
            if report:
                await _stream_text(sse, report)
        else:
            for section_name, section_content in sections.items():
                if section_content:
                    await sse.put_patch(section_name, section_content, append=False)
            if report and not report_streamed:
                await _stream_text(sse, report)

    except Exception as e:
        logger.error(f"Agent run failed session_id={session_id}: {e}")
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
    finally:
        stream_callback_var.set(None)
        if not hitl_triggered:
            await sse.put_done()
            _interrupted_sessions.pop(session_id, None)
            if lock.locked():
                lock.release()


@router.post("/api/hitl/callback")
async def hitl_callback(req: HITLRequest):
    """HITL 回调：接收用户在前端的确认/选择，恢复中断的图执行。"""
    logger.info(f"HITL callback session_id={req.session_id} mode={req.mode.value}")
    session_data = _interrupted_sessions.get(req.session_id)
    if not session_data:
        logger.warning(f"Session {req.session_id} 不在 HITL 等待状态，忽略")
        return {"status": "skipped", "session_id": req.session_id, "mode": req.mode.value}

    config = session_data.get("config")
    sse = session_data.get("sse")
    lock = session_data.get("lock")
    if not config or not sse:
        logger.warning(f"Session {req.session_id} 状态不完整，忽略")
        return {"status": "skipped", "session_id": req.session_id, "mode": req.mode.value}

    if req.session_id in _resuming_sessions:
        logger.warning(f"Session {req.session_id} 正在 resume 中，忽略重复 HITL 回调")
        return {"status": "skipped", "session_id": req.session_id, "mode": req.mode.value, "reason": "会话正在处理中"}

    _resuming_sessions.add(req.session_id)
    ctx = session_data.get("ctx")
    asyncio.create_task(_resume_agent(sse, config, ctx, req.data, lock, req.session_id))

    return {"status": "resumed", "session_id": req.session_id, "mode": req.mode.value}


async def _resume_agent(sse: SSEManager, config: dict[str, Any], ctx: dict[str, Any] | None, resume_data: dict[str, Any] | None = None, lock: asyncio.Lock | None = None, session_id: str | None = None):
    """从 HITL 中断点恢复图执行。"""
    if session_id is None:
        session_id = config["configurable"]["thread_id"]
    logger.info(f"Resuming agent session_id={session_id} resume_data={resume_data}")
    graph = get_graph()
    is_first = ctx is None

    hitl_triggered = False
    try:
        async for event in graph.astream(Command(resume=resume_data or {}), config):
            if "__interrupt__" in event:
                items = event["__interrupt__"]
                if items and len(items) > 0:
                    int_item = items[0]
                    payload = int_item.value if hasattr(int_item, "value") else int_item
                    logger.info(f"HITL interrupt (resume) session_id={session_id} mode={payload.get('mode')}")
                    await sse.put_hitl(
                        mode=payload.get("mode", "scope_select"),
                        session_id=session_id,
                        options=payload.get("options", {}),
                    )
                    hitl_triggered = True
                    return

            for node_name, node_output in event.items():
                if node_name == "__interrupt__":
                    continue
                if node_output:
                    interrupt_data = _detect_interrupt(node_output)
                    if interrupt_data:
                        logger.info(f"HITL interrupt (resume nested) session_id={session_id} mode={interrupt_data.get('mode')}")
                        await sse.put_hitl(
                            mode=interrupt_data.get("mode", "scope_select"),
                            session_id=session_id,
                            options=interrupt_data.get("options", {}),
                        )
                        hitl_triggered = True
                        return

        final = graph.get_state(config)
        final_data = final.values if final else {}

        report = final_data.get("report", "")
        sections = final_data.get("sections", {})
        report_streamed = final_data.get("_report_streamed", False)

        logger.info(f"Agent resume completed session_id={session_id}")

        if is_first:
            if report:
                await _stream_text(sse, report)
        else:
            for section_name, section_content in sections.items():
                if section_content:
                    await sse.put_patch(section_name, section_content, append=False)
            if report and not report_streamed:
                await _stream_text(sse, report)

    except Exception as e:
        logger.error(f"Agent resume failed session_id={session_id}: {e}")
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
    finally:
        _resuming_sessions.discard(session_id)
        if not hitl_triggered:
            _interrupted_sessions.pop(session_id, None)
        await sse.put_done()
        if lock and lock.locked():
            lock.release()