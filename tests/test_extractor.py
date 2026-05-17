"""Extractor 测试"""
from src.extract.extractor import extract_from_content

result = extract_from_content(
    "测试文章",
    "2025年AI Agent市场规模达到100亿美元，同比增长45%。主要应用领域包括客服、医疗和金融。"
)
print(f"L0: {result['l0_summary'][:80]}")
print(f"Findings count: {len(result['findings'])}")
for f in result["findings"]:
    print(f"  [{f['topic']}] {f['content'][:80]}")
