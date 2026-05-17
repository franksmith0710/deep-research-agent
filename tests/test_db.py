"""数据库模块快速测试"""
from src.db.postgres import (
    init_pool, close_pool, create_task, get_task_by_session,
    get_all_sessions, insert_chat_message, get_chat_history,
    insert_memory, search_memory_by_vector, upsert_credibility,
    get_credibility, update_task,
)

init_pool()

# 1. 建任务
task_id = create_task("test-session", "测试问题")
print(f"create_task -> {task_id}")
assert task_id > 0

# 2. 查询任务
task = get_task_by_session("test-session")
assert task is not None
print(f"Task status: {task['status']}, task_id: {task['task_id']}")

# 3. 更新任务
update_task(task_id, status="running", resolved_query="测试（已消解）")
task2 = get_task_by_session("test-session")
assert task2["status"] == "running"
print(f"Updated status: {task2['status']}")

# 4. 聊天记录
cid = insert_chat_message("test-session", "user", "你好")
print(f"chat_message id: {cid}")
msgs = get_chat_history("test-session")
assert len(msgs) == 1
assert msgs[0]["role"] == "user"

# 5. 记忆
mem_id = insert_memory(
    task_id, "L1", "测试发现",
    source_url="https://example.com", topic="测试",
    embedding=[0.1] * 1024,
)
print(f"memory id: {mem_id}")
assert mem_id > 0

# 6. 向量搜索
results = search_memory_by_vector([0.1] * 1024, limit=5, min_score=0.5)
print(f"vector search results: {len(results)}")
assert len(results) >= 1

# 7. 信誉
upsert_credibility("https://example.com", "example.com", True)
cred = get_credibility("https://example.com")
assert cred is not None
print(f"credibility score: {cred['score']}")

# 8. 会话列表
sessions = get_all_sessions()
print(f"sessions: {len(sessions)}")

close_pool()
print("\nALL TESTS PASSED")
