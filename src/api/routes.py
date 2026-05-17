"""API 路由：会话管理 + 研究请求 + HITL 回调 + SSE 流 + interrupt 恢复。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from src.models import ResearchRequest, HITLRequest
from src.db.postgres import (
    init_pool, get_all_sessions, get_task_by_session,
    get_chat_history, create_task, insert_chat_message,
)
from src.agent.state import make_initial_state
from src.agent.graph import build_graph
from src.api.sse_manager import SSEManager

router = APIRouter()

# 全局 graph 实例
_graph = None

# 中断会话跟踪：session_id → {sse, config}
_interrupted_sessions: dict[str, dict[str, Any]] = {}


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


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
    }


@router.post("/api/sessions")
async def create_session():
    import uuid
    session_id = str(uuid.uuid4())[:8]
    return {"session_id": session_id}


@router.post("/api/research")
async def start_research(req: ResearchRequest):
    """启动研究：创建任务 → 运行 LangGraph → SSE 流式输出。"""
    try:
        task_id = create_task(req.session_id, req.query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建任务失败: {e}")

    insert_chat_message(req.session_id, "user", req.query)

    sse = SSEManager()
    state = make_initial_state(req.session_id, task_id, req.query)

    asyncio.create_task(_run_agent(state, sse))

    return sse.get_response()


async def _run_agent(state: dict[str, Any], sse: SSEManager):
    """异步运行 Agent，检测 interrupt 后挂起等待 HITL 回调。"""
    session_id = state["session_id"]
    _interrupted_sessions[session_id] = {"sse": sse}

    graph = get_graph()
    config = {"configurable": {"thread_id": session_id}}
    _interrupted_sessions[session_id]["config"] = config

    try:
        await sse.put_chain("action", "resolve_context", "正在理解你的问题...")

        for event in graph.stream(state, config):
            for node_name, node_output in event.items():
                if node_output:
                    interrupt_data = _detect_interrupt(node_output)
                    if interrupt_data:
                        await sse.put_hitl(
                            mode=interrupt_data.get("mode", "scope_select"),
                            session_id=session_id,
                            options=interrupt_data.get("options", {}),
                        )
                        await sse.put_done()
                        return  # 挂起，等待回调

                    await sse.put_chain("thought", node_name, str(node_output)[:200])

        # 正常结束
        final = graph.get_state(config)
        final_data = final.values if final else {}

        report = final_data.get("report", "")
        sections = final_data.get("sections", {})

        if sections:
            for section_name, section_content in sections.items():
                if section_content:
                    await sse.put_patch(section_name, section_content, append=False)

        if report:
            await sse.put_text(report)

        await sse.put_chain("action_result", "memory", "研究完成，结果已保存。")

    except Exception as e:
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
    finally:
        await sse.put_done()
        _interrupted_sessions.pop(session_id, None)


@router.post("/api/hitl/callback")
async def hitl_callback(req: HITLRequest):
    """HITL 回调：接收用户在前端的确认/选择，恢复中断的图执行。"""
    session_data = _interrupted_sessions.get(req.session_id)
    if not session_data:
        raise HTTPException(status_code=400, detail="会话不在 HITL 等待状态")

    config = session_data.get("config")
    sse = session_data.get("sse")
    if not config or not sse:
        raise HTTPException(status_code=400, detail="会话状态不完整")

    # 恢复图执行
    asyncio.create_task(_resume_agent(sse, config))

    return {"status": "resumed", "session_id": req.session_id, "mode": req.mode.value}


async def _resume_agent(sse: SSEManager, config: dict[str, Any]):
    """从 HITL 中断点恢复图执行。"""
    graph = get_graph()
    session_id = config["configurable"]["thread_id"]

    try:
        for event in graph.stream(Command(resume={}), config):
            for node_name, node_output in event.items():
                if node_output:
                    interrupt_data = _detect_interrupt(node_output)
                    if interrupt_data:
                        await sse.put_hitl(
                            mode=interrupt_data.get("mode", "scope_select"),
                            session_id=session_id,
                            options=interrupt_data.get("options", {}),
                        )
                        await sse.put_done()
                        return  # 再次挂起

                    await sse.put_chain("thought", node_name, str(node_output)[:200])

        # 正常结束
        final = graph.get_state(config)
        final_data = final.values if final else {}

        report = final_data.get("report", "")
        sections = final_data.get("sections", {})

        if sections:
            for section_name, section_content in sections.items():
                if section_content:
                    await sse.put_patch(section_name, section_content, append=False)

        if report:
            await sse.put_text(report)

        await sse.put_chain("action_result", "memory", "研究完成，结果已保存。")

    except Exception as e:
        await sse.put_chain("action_result", "error", f"研究过程出错: {e}")
    finally:
        await sse.put_done()
        _interrupted_sessions.pop(session_id, None)
