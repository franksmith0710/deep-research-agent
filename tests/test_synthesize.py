"""synthesize 模块测试"""
from src.synthesize.deduplicator import dedup_check
from src.synthesize.ranker import rank_findings

# ranker 测试
findings = [
    {"topic": "市场规模", "content": "2025年AI Agent市场达100亿美元"},
    {"topic": "技术架构", "content": "主流方案基于大模型+工具调用"},
]
ranked = rank_findings("AI Agent市场", findings)
print(f"Ranked: {len(ranked)} items")
for f in ranked:
    print(f"  score={f['score']}: {f['content'][:40]}")

print("synthesize tests done")
