"""共享语义去重工具。"""

from __future__ import annotations

import torch
import torch.nn.functional as F

SEMANTIC_DUP_THRESHOLD = 0.88


def max_cosine_similarity(
    emb: list[float],
    candidates: list[list[float]],
) -> float:
    """计算 emb 与 candidates 中每条向量的余弦相似度，返回最大值。"""
    if not candidates:
        return 0.0
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    emb_t = torch.tensor(emb, device=device).unsqueeze(0)
    cand_t = torch.tensor(candidates, device=device)
    return F.cosine_similarity(emb_t, cand_t).max().item()
