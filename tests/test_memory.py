"""Memory 模块测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.credibility import update_source_credibility, get_source_tag

update_source_credibility("https://example.com", True)
tag = get_source_tag("https://example.com")
print(f"Credibility tag: {tag}")

print("Memory tests done")
