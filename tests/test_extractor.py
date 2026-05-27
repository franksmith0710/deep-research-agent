"""Page brief 测试"""
from src.page_brief.brief import brief_from_page

result = brief_from_page(
    "测试文章",
    "2025年AI Agent市场规模达到100亿美元，同比增长45%。主要应用领域包括客服、医疗和金融。"
)
print(f"Key points count: {len(result['key_points'])}")
for f in result["key_points"]:
    print(f"  [{f['topic']}] {f['content'][:80]}")
