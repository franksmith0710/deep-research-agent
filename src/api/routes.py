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
from src.local_models.embedder import release_gpu as _release_embedder
from src.local_models.reranker import release_gpu as _release_reranker
from src.api.sse_manager import SSEManager
from src.api.contexts import stream_callback_var
from src.logging_config import get_logger

logger = get_logger("routes")

router = APIRouter()

# 全局 graph 实例
_graph = None

# 中断会话跟踪：session_id → {sse, config}
_interrupted_sessions: dict[str, dict[str, Any]] = {}

# HITL payload 缓存（刷新后恢复对话框用）：session_id → {mode, options}
_hitl_payloads: dict[str, dict[str, Any]] = {}

# 每会话的排队列
_research_locks: dict[str, asyncio.Lock] = {}

# 当前运行中的节点（session_id → node_name）
_current_node: dict[str, str] = {}

# 正在 resume 中的会话，防止并发重复 resume
_resuming_sessions: set[str] = set()

# HITL 超时（秒）
_HITL_TIMEOUT = 600

# HITL 超时清理任务
_hitl_cleanup_tasks: dict[str, asyncio.Task] = {}

# 运行中的 agent 任务（用于取消）
_running_tasks: dict[str, asyncio.Task] = {}

# 已发射的 chain event 步骤标签（用于过滤 checkpoint replay）
_emitted_chain_steps: dict[str, set[tuple[str, str]]] = {}


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
    "clarify": "正在判断是否需要补充信息（搜索前）",
    "planner": "正在规划搜索方向",
    "search": "正在搜索相关信息...",
    "scrape": "正在抓取网页内容",
    "context_mgr": "正在整理提炼内容",
    "dedup_rerank": "正在去重排序",
    "assess": "正在评估覆盖度",
    "synthesize": "正在综合生成内容",
    "simple_llm": "正在直接回答问题",
    "hitl_scope": "等待你选择调研范围",
    "hitl_adjust": "等待你调整搜索方向",
    "hitl_conflict": "等待你确认信息冲突",
}


async def _put_readable_chain(sse: SSEManager, node_name: str) -> None:
    desc = _NODE_DESCRIPTIONS.get(node_name, f"正在执行: {node_name}")
    await sse.put_chain("thought", node_name, desc)


def _get_step_tag(event: dict[str, Any]) -> str | None:
    tags = event.get("tags", []) or []
    for tag in tags:
        if isinstance(tag, str) and tag.startswith("graph:step:"):
            return tag
    return None


def _should_emit_chain(session_id: str, name: str, event: dict[str, Any]) -> bool:
    """检查该 chain event 是否已发射过（用于过滤 checkpoint replay）。"""
    step_tag = _get_step_tag(event)
    if step_tag is None:
        return True
    key = (name, step_tag)
    if session_id not in _emitted_chain_steps:
        _emitted_chain_steps[session_id] = set()
    if key in _emitted_chain_steps[session_id]:
        return False
    _emitted_chain_steps[session_id].add(key)
    return True


def _detect_interrupt(node_output: Any) -> dict | None:
    """检查节点输出是否包含 LangGraph __interrupt__。"""
    if isinstance(node_output, dict) and "__interrupt__" in node_output:
        items = node_output["__interrupt__"]
        if items and len(items) > 0:
            int_item = items[0]
            if hasattr(int_item, "value"):
                return int_item.value
    return None


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
        "status": task.get("status", "") if task else "",
    }


@router.get("/api/sessions/{session_id}/status")
async def get_session_status(session_id: str):
    task = get_task_by_session(session_id)
    if not task:
        return {"status": "not_found"}
    current_node = _current_node.get(session_id, "")
    node_desc = _NODE_DESCRIPTIONS.get(current_node, "")
    db_status = task.get("status", "")

    # 如果在 resume 中，返回独立状态避免前端误判为 HITL 等待
    if session_id in _resuming_sessions:
        return {
            "status": "resuming",
            "current_node": current_node,
            "current_step": node_desc or "正在恢复执行中...",
            "report": task.get("report", "") or "",
            "hitl": None,
        }

    in_hitl = session_id in _hitl_payloads and db_status == "running"
    hitl_data = _hitl_payloads.get(session_id) if in_hitl else None
    out_status = "hitl_waiting" if in_hitl else db_status
    logger.debug(f"status_sid={session_id} out={out_status} node={current_node} step={node_desc} db={db_status}")
    return {
        "status": out_status,
        "current_node": current_node,
        "current_step": node_desc,
        "report": task.get("report", "") or "",
        "hitl": hitl_data,
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
        update_task(task_id, status="running")

    insert_chat_message(req.session_id, "user", req.query)

    sse = SSEManager()
    state = make_initial_state(req.session_id, task_id, req.query)

    if ctx:
        state["report"] = ctx["report"]
        state["sections"] = ctx["sections"]
        state["outline"] = ctx["outline"]
        state["deep_search_count"] = ctx["deep_search_count"]

    task = asyncio.create_task(_run_agent(state, sse, lock, ctx))
    _running_tasks[req.session_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(req.session_id, None))

    return sse.get_response()


async def _cleanup_hitl_timeout(session_id: str, delay: int) -> None:
    """HITL 超时后自动清理泄漏的资源。"""
    await asyncio.sleep(delay)
    data = _interrupted_sessions.pop(session_id, None)
    _hitl_payloads.pop(session_id, None)
    _hitl_cleanup_tasks.pop(session_id, None)
    if data:
        lock = data.get("lock")
        if lock and lock.locked():
            lock.release()
        logger.warning(f"HITL session {session_id} timed out after {delay}s, cleaned up")


async def _run_agent(state: dict[str, Any], sse: SSEManager, lock: asyncio.Lock, ctx: dict[str, Any] | None):
    """异步运行 Agent，检测 interrupt 后挂起等待 HITL 回调。"""
    session_id = state["session_id"]
    _interrupted_sessions[session_id] = {"sse": sse, "ctx": ctx, "lock": lock}
    _hitl_payloads.pop(session_id, None)

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

    stream_callback_var.set(lambda token: sse.put_text(token))

    hitl_triggered = False
    try:
        async for event in graph.astream_events(state, config, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_chain_start" and name in _NODE_DESCRIPTIONS:
                _current_node[session_id] = name
                if _should_emit_chain(session_id, name, event):
                    await _put_readable_chain(sse, name)

            elif kind == "on_chain_end" and name in _NODE_DESCRIPTIONS:
                output = event.get("data", {}).get("output", {}) or {}
                if output:
                    if name == "assess":
                        score = output.get("coverage_score", 0)
                        detail = output.get("score_detail", {})
                        suf = output.get("sufficient", False)
                        status = "充足" if suf else "不足"
                        c = detail.get('outline_covered', 0)
                        t = detail.get('outline_total', 1)
                        outline_match = round(25 * c / max(t, 1))
                        await sse.put_chain("thought", "assess_score",
                            f"覆盖度: {score}分 | 问题匹配{detail.get('query_match',0)} "
                            f"大纲匹配{outline_match} 范围{detail.get('scope',0)} "
                            f"来源{detail.get('credibility',0)} | {status}")
                    interrupt_data = _detect_interrupt(output)
                    if interrupt_data:
                        logger.info(f"HITL interrupt session_id={session_id} mode={interrupt_data.get('mode')}")
                        _hitl_payloads[session_id] = {
                            "mode": interrupt_data.get("mode", "scope_select"),
                            "options": interrupt_data.get("options", {}),
                        }
                        hitl_triggered = True
                        t = asyncio.create_task(_cleanup_hitl_timeout(session_id, _HITL_TIMEOUT))
                        _hitl_cleanup_tasks[session_id] = t
                        return

            elif kind == "on_chain_end" and name == "DeepResearch":
                output = event.get("data", {}).get("output", {}) or {}
                interrupt_data = _detect_interrupt(output)
                if interrupt_data:
                    logger.info(f"HITL interrupt session_id={session_id} mode={interrupt_data.get('mode')}")
                    _hitl_payloads[session_id] = {
                        "mode": interrupt_data.get("mode", "scope_select"),
                        "options": interrupt_data.get("options", {}),
                    }
                    hitl_triggered = True
                    t = asyncio.create_task(_cleanup_hitl_timeout(session_id, _HITL_TIMEOUT))
                    _hitl_cleanup_tasks[session_id] = t
                    return
                break

        # astream_events 在 v2 下使用 on_chain_start/on_chain_end 而非 on_node_start/on_node_end
        # 兜底：用 graph.get_state() 检测图是否实际处于中断挂起状态
        snapshot = graph.get_state(config)
        if snapshot.next:
            tasks = snapshot.tasks
            if tasks and tasks[0].interrupts:
                interrupt_val = tasks[0].interrupts[0].value
                mode = interrupt_val.get("mode", "scope_select")
                logger.info(f"HITL interrupt (post-stream) session_id={session_id} mode={mode}")
                _hitl_payloads[session_id] = {
                    "mode": mode,
                    "options": interrupt_val.get("options", {}),
                }
                hitl_triggered = True
                t = asyncio.create_task(_cleanup_hitl_timeout(session_id, _HITL_TIMEOUT))
                _hitl_cleanup_tasks[session_id] = t
                return

        logger.info(f"Agent run completed session_id={session_id}")
        _release_embedder()
        _release_reranker()

    except asyncio.CancelledError:
        logger.warning(f"Agent cancelled session_id={session_id}")
        await sse.put_chain("action_result", "cancelled", "研究已取消")
    except Exception as e:
        logger.error(f"Agent run failed session_id={session_id}: {e}")
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
        task_record = get_task_by_session(session_id)
        if task_record:
            update_task(task_record["task_id"], status="error")
        _hitl_payloads.pop(session_id, None)
        _interrupted_sessions.pop(session_id, None)
        _emitted_chain_steps.pop(session_id, None)
        await sse.put_done()
        if lock and lock.locked():
            lock.release()
        hitl_triggered = True
    finally:
        _release_embedder()
        _release_reranker()
        stream_callback_var.set(None)
        _current_node.pop(session_id, None)
        await sse.put_done()
        if not hitl_triggered:
            _emitted_chain_steps.pop(session_id, None)
            _hitl_payloads.pop(session_id, None)
            task_record = get_task_by_session(session_id)
            if task_record:
                update_task(task_record["task_id"], status="completed")
            _interrupted_sessions.pop(session_id, None)
            if lock and lock.locked():
                lock.release()


@router.post("/api/research/{session_id}/cancel")
async def cancel_research(session_id: str):
    """取消正在运行的研究任务。"""
    cancel_task = _hitl_cleanup_tasks.pop(session_id, None)
    if cancel_task:
        cancel_task.cancel()
    task = _running_tasks.pop(session_id, None)
    if task:
        task.cancel()
        logger.info(f"Research cancelled session_id={session_id}")
        return {"status": "cancelled"}
    return {"status": "no_task"}


@router.post("/api/hitl/callback")
async def hitl_callback(req: HITLRequest):
    """HITL 回调：建立新 SSE 连接，恢复中断的图执行并流式推送后续事件。"""
    logger.info(f"HITL callback session_id={req.session_id} mode={req.mode.value}")
    session_data = _interrupted_sessions.get(req.session_id)
    if not session_data:
        logger.warning(f"Session {req.session_id} 不在 HITL 等待状态，忽略")
        raise HTTPException(status_code=400, detail="Session not in HITL state")

    config = session_data.get("config")
    lock = session_data.get("lock")
    if not config:
        logger.warning(f"Session {req.session_id} 状态不完整，忽略")
        raise HTTPException(status_code=400, detail="Session state incomplete")

    if req.session_id in _resuming_sessions:
        logger.warning(f"Session {req.session_id} 正在 resume 中，忽略重复 HITL 回调")
        raise HTTPException(status_code=429, detail="Session already resuming")

    cancel_task = _hitl_cleanup_tasks.pop(req.session_id, None)
    if cancel_task:
        cancel_task.cancel()

    # 创建新 SSE 管理器，恢复执行的事件通过此连接推送
    new_sse = SSEManager()
    _interrupted_sessions[req.session_id] = {**session_data, "sse": new_sse}
    _resuming_sessions.add(req.session_id)

    ctx = session_data.get("ctx")
    task = asyncio.create_task(_resume_agent(new_sse, config, ctx, req.data, lock, req.session_id))
    _running_tasks[req.session_id] = task
    task.add_done_callback(lambda t: _running_tasks.pop(req.session_id, None))

    return new_sse.get_response()


async def _resume_agent(sse: SSEManager, config: dict[str, Any], ctx: dict[str, Any] | None, resume_data: dict[str, Any] | None = None, lock: asyncio.Lock | None = None, session_id: str | None = None):
    """从 HITL 中断点恢复图执行。"""
    if session_id is None:
        session_id = config["configurable"]["thread_id"]
    logger.info(f"Resuming agent session_id={session_id} resume_data={resume_data}")
    stream_callback_var.set(lambda token: sse.put_text(token))
    graph = get_graph()

    hitl_triggered = False
    replayed_interrupt_skipped = False
    try:
        async for event in graph.astream_events(Command(resume=resume_data or {}), config, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            if kind == "on_chain_start" and name in _NODE_DESCRIPTIONS:
                _current_node[session_id] = name
                if _should_emit_chain(session_id, name, event):
                    await _put_readable_chain(sse, name)

            elif kind == "on_chain_end" and name in _NODE_DESCRIPTIONS:
                output = event.get("data", {}).get("output", {}) or {}
                if output:
                    if name == "assess":
                        score = output.get("coverage_score", 0)
                        detail = output.get("score_detail", {})
                        suf = output.get("sufficient", False)
                        status = "充足" if suf else "不足"
                        c = detail.get('outline_covered', 0)
                        t = detail.get('outline_total', 1)
                        outline_match = round(25 * c / max(t, 1))
                        await sse.put_chain("thought", "assess_score",
                            f"覆盖度: {score}分 | 问题匹配{detail.get('query_match',0)} "
                            f"大纲匹配{outline_match} 范围{detail.get('scope',0)} "
                            f"来源{detail.get('credibility',0)} | {status}")
                    interrupt_data = _detect_interrupt(output)
                    if interrupt_data:
                        if not replayed_interrupt_skipped:
                            replayed_interrupt_skipped = True
                            continue
                        logger.info(f"HITL interrupt (resume) session_id={session_id} mode={interrupt_data.get('mode')}")
                        _interrupted_sessions[session_id] = {"sse": sse, "config": config, "lock": lock}
                        _hitl_payloads[session_id] = {
                            "mode": interrupt_data.get("mode", "scope_select"),
                            "options": interrupt_data.get("options", {}),
                        }
                        hitl_triggered = True
                        t = asyncio.create_task(_cleanup_hitl_timeout(session_id, _HITL_TIMEOUT))
                        _hitl_cleanup_tasks[session_id] = t
                        return

            elif kind == "on_chain_end" and name == "DeepResearch":
                output = event.get("data", {}).get("output", {}) or {}
                interrupt_data = _detect_interrupt(output)
                if interrupt_data:
                    if not replayed_interrupt_skipped:
                        replayed_interrupt_skipped = True
                        continue
                    logger.info(f"HITL interrupt (resume) session_id={session_id} mode={interrupt_data.get('mode')}")
                    _interrupted_sessions[session_id] = {"sse": sse, "config": config, "lock": lock}
                    _hitl_payloads[session_id] = {
                        "mode": interrupt_data.get("mode", "scope_select"),
                        "options": interrupt_data.get("options", {}),
                    }
                    hitl_triggered = True
                    t = asyncio.create_task(_cleanup_hitl_timeout(session_id, _HITL_TIMEOUT))
                    _hitl_cleanup_tasks[session_id] = t
                    return
                break

        logger.info(f"Agent resume completed session_id={session_id}")

    except asyncio.CancelledError:
        logger.warning(f"Agent resume cancelled session_id={session_id}")
    except Exception as e:
        logger.error(f"Agent resume failed session_id={session_id}: {e}")
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
        task_record = get_task_by_session(session_id)
        if task_record:
            update_task(task_record["task_id"], status="error")
        _hitl_payloads.pop(session_id, None)
        _interrupted_sessions.pop(session_id, None)
        _emitted_chain_steps.pop(session_id, None)
        await sse.put_done()
        if lock and lock.locked():
            lock.release()
        hitl_triggered = True
    finally:
        _release_embedder()
        _release_reranker()
        _resuming_sessions.discard(session_id)
        _current_node.pop(session_id, None)
        await sse.put_done()
        if not hitl_triggered:
            _emitted_chain_steps.pop(session_id, None)
            _hitl_payloads.pop(session_id, None)
            task_record = get_task_by_session(session_id)
            if task_record:
                update_task(task_record["task_id"], status="completed")
            _interrupted_sessions.pop(session_id, None)
            if lock and lock.locked():
                lock.release()
        stream_callback_var.set(None)