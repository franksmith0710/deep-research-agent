"""Writer 测试"""
from src.report.writer import generate_outline, generate_report

outline = generate_outline("2025年国内AI Agent市场规模")
print(f"Outline chapters: {len(outline)}")
for o in outline:
    print(f"  {o['section']}")
