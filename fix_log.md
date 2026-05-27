# Fix Log

记录重大问题的根因分析与修复方案。

---

## 2026-05-26: 链式过程不同步 + 展开按钮消失 + currentEvent/currentStep 冲突

### 问题 1：链式过程不同步 + 展开按钮消失

**根因：** 两处清空了 `allEvents`，导致 `allEvents.length > 1` 永远为 false。

| 清除位置 | 触发时机 |
|---|---|
| `src/stores/chatStore.ts` — `finalizeStream()` | SSE 流结束 → `onDone()` → 清空 `allEvents` 和 `currentEvent` |
| `src/components/QueryInput.vue` — completed handler | 状态轮询收到 `status === 'completed'` → 再次清空 |

**时序：**
```
SSE 流 → allEvents 累积 6+ 条 chain 事件
  ↓ SSE 结束
finalizeStream() → 清空 allEvents（首次清除）
  ↓ 3 秒内
状态轮询看到 completed → 再次清空 allEvents（二次清除）
  ↓ 最终
allEvents.length = 0 → 展开按钮不显示
```

### 问题 2：currentEvent 与 currentStep 不同步

**根因：** 两个数据源竞争同一个 UI 插槽。

| 数据源 | 来源 | 更新频率 |
|---|---|---|
| `currentEvent` | SSE `chain` 事件（`addChainEvent` 实时设置） | 实时 |
| `currentStep` | REST 轮询 `/api/sessions/{id}/status` 的 `current_step` | 每 3 秒 |

SSE 结束后 `finalizeStream()` 清空 `currentEvent`，模板 fallthrough 到 `currentStep`，显示过时状态。

### 修复

**改动 1 — `src/stores/chatStore.ts`:**
- `finalizeStream()` 不再清空 `allEvents` 和 `currentEvent`

**改动 2 — `src/components/QueryInput.vue`:**
- completed handler 不再清空 `allEvents` 和 `currentEvent`
- `runStatus = ''` 改为 `runStatus = 'completed'`（供 ChatHistory 判断）

**改动 3 — `src/components/ChatHistory.vue`:**
- `v-if` 优先级调整：`hitl_waiting` > `currentEvent` > `running + currentStep` > `completed`
- 增加 `completed` 状态显示（绿色圆点 + "研究完成"）
- `.latest` 样式改为仅在 `runStatus === 'running'` 时生效

### 相关文件

- `frontend/src/stores/chatStore.ts:69-70` — 删除 `allEvents.value = []` 和 `currentEvent.value = null`
- `frontend/src/components/QueryInput.vue:58-67` — 删除清空 + `runStatus` 改 `'completed'`
- `frontend/src/components/ChatHistory.vue:45-58` — 条件顺序调整
- `frontend/src/components/ChatHistory.vue:69` — latest 条件加 `runStatus === 'running'`
- `frontend/src/components/ChatHistory.vue:165-170` — 新增 `.done` 样式

---

## 2026-05-26: HITL 确认后卡在"正在恢复执行中..."

### 根因

HITL 回调 (`/api/hitl/callback`) 返回 JSON，前端拿到 `{"status":"resumed"}` 后关闭弹窗。恢复执行的事件通过**原始 SSE 连接**推送，但如果原始连接已关闭（浏览器空闲超时/网络波动），事件进入无人读取的队列，前端永远收不到。

### 修复

**后端 `src/api/routes.py`:**

1. `_run_agent.finally` — `sse.put_done()` 和 `_hitl_payloads.pop()` 移出 `if not hitl_triggered`，HITL 触发时也关闭原始 SSE 连接
2. `hitl_callback` — 创建新 `SSEManager`，返回 `new_sse.get_response()`（SSE 流），前端通过此新连接接收恢复执行的全部事件
3. `_resume_agent.finally` — 同 `_run_agent`，SSE 和 payload 清理移到外面

**前端 `src/api/index.ts`:**
- 新增 `researchHITL_SSE()` — 类似 `researchSSE` 但 POST 到 `/api/hitl/callback`，接收 SSE 流

**前端 `src/composables/useHITL.ts`:**
- `submit()` 改为调用 `researchHITL_SSE`，直接在 XHR 的 `onprogress` 中解析 chain/text 事件
- 新增 `abort()` 方法用于会话切换时中止连接

**前端 `src/components/MainArea.vue`:**
- 会话切换时调用 `props.hitl.abort()` 中止 HITL SSE 连接

### 场景验证

| 场景 | 修复前 | 修复后 |
|---|---|---|
| SSE 连接在 HITL 等待期间存活 | ✅ 正常显示 | ✅ 正常显示（不变） |
| SSE 连接在 HITL 等待期间死亡 | ❌ 恢复后无事件，永远卡住 | ✅ HITL callback 建立新 SSE，事件正常到达 |
| 多轮 HITL | ❌ 锁可能泄露 | ✅ 每个 callback 创建新 SSE，锁始终释放 |
| 锁/SSE 在异常时泄露 | ❌ `finally` 条件执行 | ✅ `put_done()` / `pop()` 始终执行 |

---

## 2026-05-26: Pending 查询跨会话泄漏

### 根因

`QueryInput.vue` 的 `pending` 是模块级闭包变量，不受会话切换/删除/新建影响。

```
1. 用户研究"黑神话悟空" → HITL/执行中
2. 用户输入"小米汽车" → pending = "小米汽车"（输入框不可用时）
3. 用户删除当前会话 → pending 仍然存在
4. 用户新建会话 → pending 仍然存在
5. 黑神话悟空完成 → onDone() → sendImmediate("小米汽车")
   → 在当前会话执行"小米汽车"研究 → 报告混合了两个话题 ❌
```

### 修复

将 `pending` 从 `QueryInput.vue` 的局部变量移到 `chatStore.ts` 的 `pendingQuery` 状态中，`reset()` 自动清空。

**`src/stores/chatStore.ts`:**
- 新增 `pendingQuery` ref
- 新增 `clearPending()` 方法
- `reset()` 中清空 `pendingQuery`

**`src/components/QueryInput.vue`:**
- 删除局部 `pending` ref
- 使用 `chatStore.pendingQuery` 替代

### 效果

| 时机 | 行为 |
|---|---|
| 切换会话（MainArea watch） | `chatStore.reset()` → `pendingQuery = null` ✅ |
| 新建会话（Sidebar） | `chatStore.reset()` → `pendingQuery = null` ✅ |
| 删除当前会话（SessionItem） | `chatStore.reset()` → `pendingQuery = null` ✅ |
| 正常发送完成 | `onDone()` → 消费 `pendingQuery` 并置空 ✅ |

---

## 2026-05-26: Chain 事件不显示（事件名匹配错误 + payload 被提前清除）

### 根因

**问题 A — 事件名不匹配：** LangGraph `astream_events(version="v2")` 使用 LangChain 标准事件命名，节点事件名为 `on_chain_start` / `on_chain_end`。代码中写的是 `on_node_start` / `on_node_end`，条件永远不命中，导致：
- `_current_node[session_id]` 从未设置 → 状态端点 `current_step` 一直为空
- `_put_readable_chain(sse, name)` 从未调用 → 前端 chain 事件不显示
- `on_node_end` 不触发 → interrupt 检测走不到 → 只能靠 `get_state()` 兜底

**问题 B — payload 被提前清除：** 上次修复将 `_hitl_payloads.pop()` 移到了 `finally` 块的 `if not hitl_triggered` 外面，导致 HITL payload 刚设完就被 finally 清空 → 状态端点 `in_hitl` 永远为 False → `out_status` 始终返回 `running` → 前端弹窗不出现。

### 修复

**`src/api/routes.py`:**
1. `_run_agent` L251/L255：`on_node_start` → `on_chain_start`，`on_node_end` → `on_chain_end`
2. `_resume_agent` L397/L401：同上
3. `_run_agent.finally` L322-323：`_hitl_payloads.pop()` 移回 `if not hitl_triggered` 内部
4. `_resume_agent.finally` L457-458：同上

### 完整改动

```python
# 之前（从不匹配）
if kind == "on_node_start" and name in _NODE_DESCRIPTIONS:
elif kind == "on_node_end" and name in _NODE_DESCRIPTIONS:

# 之后（正确匹配 v2 事件）
if kind == "on_chain_start" and name in _NODE_DESCRIPTIONS:
elif kind == "on_chain_end" and name in _NODE_DESCRIPTIONS:
```

### 数据流验证

```
astream_events(version="v2")
  → on_chain_start (node="resolve_context")   ✅ 现在匹配
    → _current_node["730c5124"] = "resolve_context"
    → sse.put_chain("thought", "resolve_context", "正在理解你的问题")
    → 前端收到 chain 事件 → allEvents 累积 → 展开按钮显示
  → on_chain_end (node="resolve_context")     ✅ 现在匹配
    → 检查 interrupt
    → 无 interrupt → 继续
  → ...

HITL 触发时：
  → on_chain_end / on_chain_end("DeepResearch") 检测到 __interrupt__
  → _hitl_payloads[session_id] = {...}           ✅ 设置成功
  → hitl_triggered = True; return
  → finally:
      sse.put_done()                              ✅ 关闭原始 SSE
      if not hitl_triggered:    ← hitl_triggered=True, 不执行
        _hitl_payloads.pop()    ← 跳过，payload 保留  ✅
  → 状态端点: in_hitl=True → out_status="hitl_waiting"  ✅
  → 前端弹窗出现
```
