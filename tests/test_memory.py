"""Memory 模块测试"""
from src.db.postgres import init_pool, close_pool
init_pool()
from src.memory.retriever import retrieve_l1
from src.memory.credibility import update_source_credibility, get_source_tag

update_source_credibility("https://example.com", True)
tag = get_source_tag("https://example.com")
print(f"Credibility tag: {tag}")

close_pool()
print("Memory tests done")
